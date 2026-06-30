4-- soccer_analytics/sql/queries.sql
-- Reference SQL queries used by the analytics engine

-- League Standings
WITH team_results AS (
    SELECT
        m.home_team_id       AS team_id,
        t.name               AS team_name,
        c.name               AS competition,
        mm.home_win          AS win,
        mm.is_draw           AS draw,
        mm.away_win          AS loss,
        m.home_score_ft      AS goals_for,
        m.away_score_ft      AS goals_against
    FROM matches m
    JOIN match_metrics mm ON mm.match_id = m.id
    JOIN teams t          ON t.id = m.home_team_id
    JOIN competitions c   ON c.id = m.competition_id

    UNION ALL

    SELECT
        m.away_team_id, t.name, c.name,
        mm.away_win, mm.is_draw, mm.home_win,
        m.away_score_ft, m.home_score_ft
    FROM matches m
    JOIN match_metrics mm ON mm.match_id = m.id
    JOIN teams t          ON t.id = m.away_team_id
    JOIN competitions c   ON c.id = m.competition_id
)
SELECT
    competition,
    team_name,
    COUNT(*)                             AS MP,
    SUM(win)                             AS W,
    SUM(draw)                            AS D,
    SUM(loss)                            AS L,
    SUM(goals_for)                       AS GF,
    SUM(goals_against)                   AS GA,
    SUM(goals_for) - SUM(goals_against)  AS GD,
    SUM(win)*3 + SUM(draw)               AS Pts
FROM team_results
GROUP BY competition, team_id, team_name
ORDER BY competition, Pts DESC, GD DESC, GF DESC;


-- 2. Top Scoring Teams
WITH team_goals AS (
    SELECT home_team_id AS team_id, home_score_ft AS goals FROM matches
    UNION ALL
    SELECT away_team_id, away_score_ft FROM matches
)
SELECT
    t.name                       AS team,
    ROUND(AVG(tg.goals), 2)     AS avg_goals_per_game,
    SUM(tg.goals)               AS total_goals,
    COUNT(*)                    AS matches_played
FROM team_goals tg
JOIN teams t ON t.id = tg.team_id
GROUP BY tg.team_id, t.name
HAVING matches_played >= 5
ORDER BY avg_goals_per_game DESC
LIMIT 10;


-- High-Scoring Matches (most goals)
SELECT
    m.match_date,
    ht.name          AS home_team,
    m.home_score_ft,
    m.away_score_ft,
    at.name          AS away_team,
    mm.total_goals,
    c.name           AS competition
FROM matches m
JOIN match_metrics mm ON mm.match_id = m.id
JOIN teams ht         ON ht.id = m.home_team_id
JOIN teams at         ON at.id = m.away_team_id
JOIN competitions c   ON c.id = m.competition_id
ORDER BY mm.total_goals DESC, m.match_date DESC
LIMIT 20;


-- Clean Sheet Leaders
WITH cs AS (
    SELECT home_team_id AS team_id FROM matches m
    JOIN match_metrics mm ON mm.match_id = m.id WHERE mm.clean_sheet_home = 1
    UNION ALL
    SELECT away_team_id FROM matches m
    JOIN match_metrics mm ON mm.match_id = m.id WHERE mm.clean_sheet_away = 1
)
SELECT t.name AS team, COUNT(*) AS clean_sheets
FROM cs JOIN teams t ON t.id = cs.team_id
GROUP BY cs.team_id, t.name
ORDER BY clean_sheets DESC
LIMIT 10;


-- Home vs Away Win Rates
SELECT
    COUNT(*)                                              AS total_matches,
    SUM(mm.home_win)                                      AS home_wins,
    SUM(mm.away_win)                                      AS away_wins,
    SUM(mm.is_draw)                                       AS draws,
    ROUND(100.0 * SUM(mm.home_win) / COUNT(*), 1)        AS home_win_pct,
    ROUND(100.0 * SUM(mm.away_win) / COUNT(*), 1)        AS away_win_pct,
    ROUND(100.0 * SUM(mm.is_draw)  / COUNT(*), 1)        AS draw_pct,
    ROUND(AVG(mm.total_goals), 2)                        AS avg_goals_per_game
FROM match_metrics mm;


-- Goals by Month (Scoring Patterns)
SELECT
    mm.match_month                     AS month,
    COUNT(*)                           AS matches,
    ROUND(AVG(mm.total_goals), 2)     AS avg_goals,
    SUM(mm.total_goals)               AS total_goals
FROM match_metrics mm
GROUP BY mm.match_month
ORDER BY mm.match_month;


-- Comeback Wins (behind at HT, won at FT)
SELECT
    ht.name AS home_team,
    m.home_score_ht, m.away_score_ht,
    m.home_score_ft, m.away_score_ft,
    at.name AS away_team,
    m.match_date
FROM matches m
JOIN match_metrics mm ON mm.match_id = m.id
JOIN teams ht ON ht.id = m.home_team_id
JOIN teams at ON at.id = m.away_team_id
WHERE mm.comeback_home = 1 OR mm.comeback_away = 1
ORDER BY m.match_date DESC
LIMIT 20;


-- Team Performance Summary (all metrics)
WITH all_games AS (
    SELECT
        m.home_team_id AS team_id,
        mm.home_win AS win, mm.is_draw AS draw, mm.away_win AS loss,
        m.home_score_ft AS gf, m.away_score_ft AS ga,
        mm.clean_sheet_home AS cs, mm.both_teams_scored AS btts,
        mm.high_scoring AS hs
    FROM matches m JOIN match_metrics mm ON mm.match_id = m.id
    UNION ALL
    SELECT
        m.away_team_id,
        mm.away_win, mm.is_draw, mm.home_win,
        m.away_score_ft, m.home_score_ft,
        mm.clean_sheet_away, mm.both_teams_scored, mm.high_scoring
    FROM matches m JOIN match_metrics mm ON mm.match_id = m.id
)
SELECT
    t.name              AS team,
    COUNT(*)            AS played,
    SUM(win)            AS wins,
    SUM(draw)           AS draws,
    SUM(loss)           AS losses,
    SUM(gf)             AS goals_for,
    SUM(ga)             AS goals_against,
    SUM(gf)-SUM(ga)     AS goal_diff,
    SUM(win)*3+SUM(draw) AS points,
    SUM(cs)             AS clean_sheets,
    SUM(btts)           AS both_scored_games,
    ROUND(100.0*SUM(win)/COUNT(*),1) AS win_pct
FROM all_games ag
JOIN teams t ON t.id = ag.team_id
GROUP BY ag.team_id, t.name
ORDER BY points DESC;