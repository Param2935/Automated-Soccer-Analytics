"""
Read-side analytics over the match database.

Every query is parameterised on an optional competition ``code``; passing
``None`` (the default) spans all leagues, while a code such as ``"PL"`` scopes
the result to a single competition. Results come back as tidy DataFrames so the
Streamlit dashboard and the CLI reports can share one source of truth.
"""

from __future__ import annotations

from functools import lru_cache
from textwrap import dedent

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine, create_engine

import config

MONTH_NAMES = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


@lru_cache(maxsize=1)
def _engine() -> Engine:
    return create_engine(config.DATABASE_URL)


def _read(sql: str, **params) -> pd.DataFrame:
    with _engine().connect() as conn:
        return pd.read_sql(text(dedent(sql)), conn, params=params)


def competitions() -> pd.DataFrame:
    """All competitions present in the database, for filter controls."""
    return _read(
        """
        SELECT c.code, c.name
        FROM competitions c
        JOIN matches m ON m.competition_id = c.id
        GROUP BY c.code, c.name
        ORDER BY c.name
        """
    )


def summary(code: str | None = None) -> dict:
    """Headline KPIs across the (optionally filtered) match set."""
    df = _read(
        """
        SELECT
            COUNT(*)                                            AS matches,
            ROUND(AVG(mm.total_goals), 2)                       AS avg_goals,
            ROUND(100.0 * SUM(mm.home_win) / COUNT(*), 1)       AS home_win_pct,
            ROUND(100.0 * SUM(mm.away_win) / COUNT(*), 1)       AS away_win_pct,
            ROUND(100.0 * SUM(mm.is_draw)  / COUNT(*), 1)       AS draw_pct,
            ROUND(100.0 * SUM(mm.high_scoring) / COUNT(*), 1)   AS high_scoring_pct,
            SUM(mm.both_teams_scored)                           AS btts
        FROM match_metrics mm
        JOIN matches m      ON m.id = mm.match_id
        JOIN competitions c ON c.id = m.competition_id
        WHERE (:code IS NULL OR c.code = :code)
        """,
        code=code,
    )
    if df.empty or not df.at[0, "matches"]:
        return {}
    return df.iloc[0].to_dict()


def standings(code: str | None = None, through_matchday: int | None = None) -> pd.DataFrame:
    """
    Full league table built from both home and away perspectives, with the
    league position assigned via a window function over points, then goal
    difference, then goals scored. ``through_matchday`` snapshots the table as
    it stood up to and including a given gameweek.
    """
    return _read(
        """
        WITH team_results AS (
            SELECT
                c.name          AS competition,
                c.code          AS competition_code,
                m.home_team_id  AS team_id,
                t.name          AS team,
                mm.home_win     AS w,
                mm.is_draw      AS d,
                mm.away_win     AS l,
                m.home_score_ft AS gf,
                m.away_score_ft AS ga
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN teams t          ON t.id = m.home_team_id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
              AND (:through_matchday IS NULL OR m.matchday <= :through_matchday)

            UNION ALL

            SELECT
                c.name, c.code, m.away_team_id, t.name,
                mm.away_win, mm.is_draw, mm.home_win,
                m.away_score_ft, m.home_score_ft
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN teams t          ON t.id = m.away_team_id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
              AND (:through_matchday IS NULL OR m.matchday <= :through_matchday)
        ),
        table_rows AS (
            SELECT
                competition,
                competition_code,
                team,
                COUNT(*)            AS played,
                SUM(w)              AS won,
                SUM(d)              AS drawn,
                SUM(l)              AS lost,
                SUM(gf)             AS gf,
                SUM(ga)             AS ga,
                SUM(gf) - SUM(ga)   AS gd,
                SUM(w) * 3 + SUM(d) AS pts
            FROM team_results
            GROUP BY competition, competition_code, team
        )
        SELECT
            RANK() OVER (
                PARTITION BY competition
                ORDER BY pts DESC, gd DESC, gf DESC
            ) AS position,
            competition, competition_code, team,
            played, won, drawn, lost, gf, ga, gd, pts
        FROM table_rows
        ORDER BY competition, position
        """,
        code=code,
        through_matchday=through_matchday,
    )


def matchday_bounds(code: str | None = None) -> tuple[int, int]:
    """Min and max gameweek available, for the standings gameweek control."""
    df = _read(
        """
        SELECT MIN(m.matchday) AS lo, MAX(m.matchday) AS hi
        FROM matches m
        JOIN competitions c ON c.id = m.competition_id
        WHERE (:code IS NULL OR c.code = :code) AND m.matchday IS NOT NULL
        """,
        code=code,
    )
    if df.empty or pd.isna(df.at[0, "hi"]):
        return (1, 1)
    return (int(df.at[0, "lo"]), int(df.at[0, "hi"]))


def team_form(code: str | None = None, window: int = 5) -> pd.DataFrame:
    """
    Rolling form over each team's most recent ``window`` matches. Uses
    ROW_NUMBER() to rank matches per team by date, then collapses the latest
    window into a chronological W/D/L string and a points tally.
    """
    return _read(
        """
        WITH results AS (
            SELECT
                m.home_team_id AS team_id, t.name AS team, m.match_date,
                CASE WHEN mm.home_win = 1 THEN 'W'
                     WHEN mm.is_draw  = 1 THEN 'D' ELSE 'L' END AS result,
                mm.home_win AS w, mm.is_draw AS d, mm.away_win AS l
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN teams t          ON t.id = m.home_team_id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)

            UNION ALL

            SELECT
                m.away_team_id, t.name, m.match_date,
                CASE WHEN mm.away_win = 1 THEN 'W'
                     WHEN mm.is_draw  = 1 THEN 'D' ELSE 'L' END,
                mm.away_win, mm.is_draw, mm.home_win
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN teams t          ON t.id = m.away_team_id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
        ),
        ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY team_id ORDER BY match_date DESC
            ) AS recency
            FROM results
        ),
        recent AS (
            SELECT * FROM ranked WHERE recency <= :window ORDER BY team, match_date
        )
        SELECT
            team,
            GROUP_CONCAT(result, '')        AS form,
            SUM(w)                          AS wins,
            SUM(d)                          AS draws,
            SUM(l)                          AS losses,
            SUM(w) * 3 + SUM(d)             AS points
        FROM recent
        GROUP BY team
        HAVING COUNT(*) = :window
        ORDER BY points DESC, wins DESC
        """,
        code=code,
        window=window,
    )


def home_away_splits(code: str | None = None) -> pd.DataFrame:
    """Home vs away vs draw outcome rates, one row per competition."""
    return _read(
        """
        SELECT
            c.name                                          AS competition,
            COUNT(*)                                        AS matches,
            ROUND(100.0 * SUM(mm.home_win) / COUNT(*), 1)   AS home_win_pct,
            ROUND(100.0 * SUM(mm.away_win) / COUNT(*), 1)   AS away_win_pct,
            ROUND(100.0 * SUM(mm.is_draw)  / COUNT(*), 1)   AS draw_pct,
            ROUND(AVG(mm.total_goals), 2)                   AS avg_goals
        FROM match_metrics mm
        JOIN matches m      ON m.id = mm.match_id
        JOIN competitions c ON c.id = m.competition_id
        WHERE (:code IS NULL OR c.code = :code)
        GROUP BY c.name
        ORDER BY c.name
        """,
        code=code,
    )


def top_scoring_teams(code: str | None = None, limit: int = 10) -> pd.DataFrame:
    """Teams ranked by average goals per match, split by venue."""
    return _read(
        """
        WITH team_goals AS (
            SELECT m.home_team_id AS team_id, m.home_score_ft AS goals, 'home' AS venue
            FROM matches m JOIN competitions c ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
            UNION ALL
            SELECT m.away_team_id, m.away_score_ft, 'away'
            FROM matches m JOIN competitions c ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
        )
        SELECT
            t.name                                                      AS team,
            ROUND(AVG(tg.goals), 2)                                     AS avg_goals,
            ROUND(AVG(CASE WHEN tg.venue = 'home' THEN tg.goals END), 2) AS avg_home,
            ROUND(AVG(CASE WHEN tg.venue = 'away' THEN tg.goals END), 2) AS avg_away,
            SUM(tg.goals)                                               AS total_goals,
            COUNT(*)                                                    AS played
        FROM team_goals tg
        JOIN teams t ON t.id = tg.team_id
        GROUP BY tg.team_id, t.name
        HAVING played >= 5
        ORDER BY avg_goals DESC
        LIMIT :limit
        """,
        code=code,
        limit=limit,
    )


def goals_by_month(code: str | None = None) -> pd.DataFrame:
    """Scoring volume and average by calendar month."""
    df = _read(
        """
        SELECT
            mm.match_month                AS month,
            COUNT(*)                      AS matches,
            ROUND(AVG(mm.total_goals), 2) AS avg_goals,
            SUM(mm.total_goals)           AS total_goals
        FROM match_metrics mm
        JOIN matches m      ON m.id = mm.match_id
        JOIN competitions c ON c.id = m.competition_id
        WHERE (:code IS NULL OR c.code = :code)
        GROUP BY mm.match_month
        ORDER BY mm.match_month
        """,
        code=code,
    )
    if not df.empty:
        df.insert(1, "month_name", df["month"].map(MONTH_NAMES))
    return df


def high_scoring_matches(code: str | None = None, limit: int = 10) -> pd.DataFrame:
    """Highest-scoring individual fixtures."""
    return _read(
        """
        SELECT
            m.match_date    AS date,
            ht.name         AS home_team,
            m.home_score_ft AS home_goals,
            m.away_score_ft AS away_goals,
            at.name         AS away_team,
            mm.total_goals  AS total_goals,
            c.name          AS competition
        FROM matches m
        JOIN match_metrics mm ON mm.match_id = m.id
        JOIN teams ht         ON ht.id = m.home_team_id
        JOIN teams at         ON at.id = m.away_team_id
        JOIN competitions c   ON c.id = m.competition_id
        WHERE (:code IS NULL OR c.code = :code)
        ORDER BY mm.total_goals DESC, m.match_date DESC
        LIMIT :limit
        """,
        code=code,
        limit=limit,
    )


def clean_sheet_leaders(code: str | None = None, limit: int = 10) -> pd.DataFrame:
    """Teams keeping the most clean sheets across both venues."""
    return _read(
        """
        WITH clean_sheets AS (
            SELECT m.home_team_id AS team_id
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE mm.clean_sheet_home = 1 AND (:code IS NULL OR c.code = :code)
            UNION ALL
            SELECT m.away_team_id
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE mm.clean_sheet_away = 1 AND (:code IS NULL OR c.code = :code)
        )
        SELECT t.name AS team, COUNT(*) AS clean_sheets
        FROM clean_sheets cs
        JOIN teams t ON t.id = cs.team_id
        GROUP BY cs.team_id, t.name
        ORDER BY clean_sheets DESC
        LIMIT :limit
        """,
        code=code,
        limit=limit,
    )


def schedule_density(code: str | None = None) -> pd.DataFrame:
    """
    Performance decay under fixture congestion. Days of rest before each match
    come from LAG() over the team's match dates; results are bucketed into a
    congested band (<4 days) and a rested band (6+ days) and compared per league.
    """
    return _read(
        """
        WITH appearances AS (
            SELECT c.name AS competition, m.home_team_id AS team_id, m.match_date,
                   mm.home_win AS won, mm.is_draw AS drew, m.home_score_ft AS goals_for
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
            UNION ALL
            SELECT c.name, m.away_team_id, m.match_date,
                   mm.away_win, mm.is_draw, m.away_score_ft
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
        ),
        rested AS (
            SELECT competition, won, drew, goals_for,
                   julianday(match_date) - julianday(
                       LAG(match_date) OVER (PARTITION BY team_id ORDER BY match_date)
                   ) AS rest_days
            FROM appearances
        ),
        bucketed AS (
            SELECT competition, won, drew, goals_for,
                   CASE WHEN rest_days < 4  THEN 'Congested (<4d)'
                        WHEN rest_days >= 6 THEN 'Rested (6+d)' END AS rest_band
            FROM rested
            WHERE rest_days IS NOT NULL
        )
        SELECT competition, rest_band,
               COUNT(*)                              AS matches,
               ROUND(100.0 * SUM(won) / COUNT(*), 1) AS win_pct,
               ROUND(AVG(won * 3 + drew), 2)         AS ppg,
               ROUND(AVG(goals_for), 2)              AS avg_goals_for
        FROM bucketed
        WHERE rest_band IS NOT NULL
        GROUP BY competition, rest_band
        ORDER BY competition, rest_band
        """,
        code=code,
    )


def bounce_back(code: str | None = None, limit: int = 10) -> pd.DataFrame:
    """
    Resilience after dropping a match. LAG() flags every game that immediately
    follows a loss; the points won in those games give a bounce-back PPG, which
    is contrasted with the team's baseline PPG to isolate genuine mentality.
    """
    return _read(
        """
        WITH appearances AS (
            SELECT c.name AS competition, t.name AS team, m.home_team_id AS team_id,
                   m.match_date, m.id AS match_id, mm.away_win AS lost,
                   CASE WHEN mm.home_win = 1 THEN 3 WHEN mm.is_draw = 1 THEN 1 ELSE 0 END AS points
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN teams t          ON t.id = m.home_team_id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
            UNION ALL
            SELECT c.name, t.name, m.away_team_id, m.match_date, m.id, mm.home_win,
                   CASE WHEN mm.away_win = 1 THEN 3 WHEN mm.is_draw = 1 THEN 1 ELSE 0 END
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN teams t          ON t.id = m.away_team_id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
        ),
        sequenced AS (
            SELECT competition, team, team_id, points,
                   LAG(lost) OVER (PARTITION BY team_id ORDER BY match_date, match_id) AS preceded_by_loss
            FROM appearances
        ),
        baseline AS (
            SELECT team_id, ROUND(AVG(points), 2) AS overall_ppg
            FROM appearances
            GROUP BY team_id
        )
        SELECT s.competition, s.team,
               COUNT(*)                                AS games_after_loss,
               ROUND(AVG(s.points), 2)                 AS bounce_back_ppg,
               b.overall_ppg,
               ROUND(AVG(s.points) - b.overall_ppg, 2) AS resilience_delta
        FROM sequenced s
        JOIN baseline b ON b.team_id = s.team_id
        WHERE s.preceded_by_loss = 1
        GROUP BY s.team_id, s.team, s.competition, b.overall_ppg
        HAVING games_after_loss >= 3
        ORDER BY bounce_back_ppg DESC, resilience_delta DESC
        LIMIT :limit
        """,
        code=code,
        limit=limit,
    )


def competitive_balance(code: str | None = None) -> pd.DataFrame:
    """
    Competitive balance across the season. Cumulative points are accumulated per
    team by games played (a robust matchweek proxy), and the dispersion of those
    totals across the league is returned as variance; the square root is taken in
    pandas so the metric is portable across SQLite builds. Lower spread = parity,
    higher spread = a top-heavy league.
    """
    df = _read(
        """
        WITH appearances AS (
            SELECT c.name AS competition, c.code AS competition_code, m.match_date, m.id AS match_id,
                   m.home_team_id AS team_id,
                   CASE WHEN mm.home_win = 1 THEN 3 WHEN mm.is_draw = 1 THEN 1 ELSE 0 END AS points
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
            UNION ALL
            SELECT c.name, c.code, m.match_date, m.id, m.away_team_id,
                   CASE WHEN mm.away_win = 1 THEN 3 WHEN mm.is_draw = 1 THEN 1 ELSE 0 END
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
        ),
        running AS (
            SELECT competition, competition_code,
                   ROW_NUMBER() OVER (
                       PARTITION BY competition_code, team_id ORDER BY match_date, match_id
                   ) AS matchweek,
                   SUM(points) OVER (
                       PARTITION BY competition_code, team_id ORDER BY match_date, match_id
                       ROWS UNBOUNDED PRECEDING
                   ) AS cumulative_points
            FROM appearances
        )
        SELECT competition, matchweek,
               COUNT(*) AS teams,
               AVG(cumulative_points * cumulative_points)
                   - AVG(cumulative_points) * AVG(cumulative_points) AS points_variance
        FROM running
        GROUP BY competition, competition_code, matchweek
        HAVING teams >= 2
        ORDER BY competition, matchweek
        """,
        code=code,
    )
    if not df.empty:
        df["points_stdev"] = df["points_variance"].clip(lower=0).pow(0.5).round(2)
        df = df.drop(columns="points_variance")
    return df


def efficiency_matrix(code: str | None = None) -> pd.DataFrame:
    """
    Attack vs defence quadrant: goals scored and conceded per match per team,
    with points-per-game for sizing. (The feed carries no shots or xG, so this
    is an output-based efficiency view rather than a finishing-quality one.)
    """
    return _read(
        """
        WITH appearances AS (
            SELECT c.name AS competition, t.name AS team, m.home_team_id AS team_id,
                   m.home_score_ft AS scored, m.away_score_ft AS conceded,
                   CASE WHEN mm.home_win = 1 THEN 3 WHEN mm.is_draw = 1 THEN 1 ELSE 0 END AS points
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN teams t          ON t.id = m.home_team_id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
            UNION ALL
            SELECT c.name, t.name, m.away_team_id, m.away_score_ft, m.home_score_ft,
                   CASE WHEN mm.away_win = 1 THEN 3 WHEN mm.is_draw = 1 THEN 1 ELSE 0 END
            FROM matches m
            JOIN match_metrics mm ON mm.match_id = m.id
            JOIN teams t          ON t.id = m.away_team_id
            JOIN competitions c   ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
        )
        SELECT competition, team,
               COUNT(*)                  AS played,
               ROUND(AVG(scored), 2)     AS goals_for_pg,
               ROUND(AVG(conceded), 2)   AS goals_against_pg,
               ROUND(AVG(points), 2)     AS points_pg
        FROM appearances
        GROUP BY team_id, team, competition
        HAVING played >= 5
        ORDER BY points_pg DESC
        """,
        code=code,
    )


def defensive_solidity(code: str | None = None, limit: int = 10) -> pd.DataFrame:
    """
    Defensive quality adjusted for opposition. Teams are tiered by attacking
    output (RANK over goals-for per game within each league); each defensive
    appearance is then attributed to the opponent's tier, exposing which
    back lines hold up against elite attacks rather than padding stats on weak
    ones. Ranked by goals conceded per game versus top-5 attacks.
    """
    return _read(
        """
        WITH scoring AS (
            SELECT c.code AS competition_code, m.home_team_id AS team_id, m.home_score_ft AS scored
            FROM matches m JOIN competitions c ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
            UNION ALL
            SELECT c.code, m.away_team_id, m.away_score_ft
            FROM matches m JOIN competitions c ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
        ),
        attack_tier AS (
            SELECT competition_code, team_id,
                   RANK() OVER (PARTITION BY competition_code ORDER BY AVG(scored) DESC) AS attack_rank,
                   COUNT(*) OVER (PARTITION BY competition_code)                          AS league_size
            FROM scoring
            GROUP BY competition_code, team_id
        ),
        defensive AS (
            SELECT c.name AS competition, c.code AS competition_code, t.name AS team,
                   m.home_team_id AS team_id, m.away_team_id AS opponent_id,
                   m.away_score_ft AS conceded
            FROM matches m
            JOIN teams t        ON t.id = m.home_team_id
            JOIN competitions c ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
            UNION ALL
            SELECT c.name, c.code, t.name, m.away_team_id, m.home_team_id, m.home_score_ft
            FROM matches m
            JOIN teams t        ON t.id = m.away_team_id
            JOIN competitions c ON c.id = m.competition_id
            WHERE (:code IS NULL OR c.code = :code)
        ),
        graded AS (
            SELECT d.competition, d.team_id, d.team, d.conceded,
                   CASE WHEN a.attack_rank <= 5                    THEN 'top'
                        WHEN a.attack_rank > a.league_size - 5     THEN 'bottom' END AS opponent_tier
            FROM defensive d
            JOIN attack_tier a
              ON a.competition_code = d.competition_code AND a.team_id = d.opponent_id
        )
        SELECT competition, team,
               SUM(opponent_tier = 'top')                                       AS games_vs_top,
               ROUND(AVG(CASE WHEN opponent_tier = 'top'    THEN conceded END), 2) AS conceded_vs_top,
               ROUND(AVG(CASE WHEN opponent_tier = 'bottom' THEN conceded END), 2) AS conceded_vs_bottom,
               ROUND(100.0 * AVG(CASE WHEN opponent_tier = 'top' THEN (conceded = 0) END), 1) AS clean_sheet_pct_vs_top
        FROM graded
        WHERE opponent_tier IS NOT NULL
        GROUP BY team_id, team, competition
        HAVING games_vs_top >= 2
        ORDER BY conceded_vs_top ASC, clean_sheet_pct_vs_top DESC
        LIMIT :limit
        """,
        code=code,
        limit=limit,
    )
