"""
transformer.py
Cleans raw JSON match data and computes 15+ derived metrics using pandas.

Derived metrics include:
  1.  total_goals            home + away goals
  2.  goal_differential      home goals minus away goals
  3.  home_win               1 if home team won, else 0
  4.  away_win               1 if away team won, else 0
  5.  is_draw                1 if draw, else 0
  6.  both_teams_scored      1 if both scored ≥ 1 goal
  7.  clean_sheet_home       1 if away scored 0
  8.  clean_sheet_away       1 if home scored 0
  9.  high_scoring           1 if total goals ≥ threshold (default 4)
 10.  halftime_home_lead     1 if home led at halftime
 11.  halftime_away_lead     1 if away led at halftime
 12.  comeback_home          home team behind at HT but won FT
 13.  comeback_away          away team behind at HT but won FT
 14.  second_half_goals      goals scored after halftime
 15.  match_month            month of the match date
 16.  match_weekday          day of week (0=Mon … 6=Sun)
"""

import logging
from typing import List, Dict, Any

import pandas as pd

import config

logger = logging.getLogger(__name__)


class Transformer:
    """
    Transforms raw match records (list of dicts) into a clean DataFrame
    with derived metrics ready for loading into the database.
    """

    HIGH_SCORE_THRESHOLD = config.HIGH_SCORE_THRESHOLD
    COMPLETED_STATUSES = ("FINISHED", "AWARDED")

    # Public API ────────────────────────────────────────────────────────────

    def transform(self, raw_matches: List[Dict[str, Any]]) -> pd.DataFrame:
        if not raw_matches:
            logger.warning("[TRANSFORM] No matches to transform.")
            return pd.DataFrame()

        df = self._normalize(raw_matches)
        df = self._clean(df)
        df = self._add_derived_metrics(df)
        logger.info(
            f"[TRANSFORM] {len(df)} matches transformed | "
            f"columns: {len(df.columns)} | "
            f"competitions: {df['competition_code'].nunique()}"
        )
        return df

    def get_teams(self, df: pd.DataFrame) -> pd.DataFrame:
        """Extract unique teams from the transformed match DataFrame."""
        home = df[["home_team_id", "home_team_name"]].rename(
            columns={"home_team_id": "team_id", "home_team_name": "team_name"}
        )
        away = df[["away_team_id", "away_team_name"]].rename(
            columns={"away_team_id": "team_id", "away_team_name": "team_name"}
        )
        teams = (
            pd.concat([home, away])
            .drop_duplicates(subset=["team_id"])
            .reset_index(drop=True)
        )
        logger.info(f"[TRANSFORM] Extracted {len(teams)} unique teams.")
        return teams

    # Private helpers

    def _normalize(self, raw: List[Dict]) -> pd.DataFrame:
        """Flatten nested JSON into a flat DataFrame."""
        records = []
        for m in raw:
            score = m.get("score", {})
            ft = score.get("fullTime", {})
            ht = score.get("halfTime", {})
            refs = m.get("referees", [])
            referee = refs[0]["name"] if refs else None

            records.append({
                "match_id":          m.get("id"),
                "competition_code":  m.get("competition", {}).get("code"),
                "competition_name":  m.get("competition", {}).get("name"),
                "season_start":      m.get("season", {}).get("startDate"),
                "season_end":        m.get("season", {}).get("endDate"),
                "utc_date":          m.get("utcDate"),
                "status":            m.get("status"),
                "matchday":          m.get("matchday"),
                "home_team_id":      m.get("homeTeam", {}).get("id"),
                "home_team_name":    m.get("homeTeam", {}).get("name"),
                "away_team_id":      m.get("awayTeam", {}).get("id"),
                "away_team_name":    m.get("awayTeam", {}).get("name"),
                "home_score_ft":     ft.get("home"),
                "away_score_ft":     ft.get("away"),
                "home_score_ht":     ht.get("home"),
                "away_score_ht":     ht.get("away"),
                "winner":            score.get("winner"),
                "referee":           referee,
            })
        return pd.DataFrame(records)

    def _clean(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean types, parse dates, drop incomplete rows."""
        df["utc_date"] = pd.to_datetime(df["utc_date"], errors="coerce", utc=True)

        # Keep only played matches (those carrying a final result) with valid scores.
        df = df[df["status"].isin(self.COMPLETED_STATUSES)].copy()
        df = df.dropna(subset=["home_score_ft", "away_score_ft", "utc_date"])

        # Cast score columns to int
        for col in ["home_score_ft", "away_score_ft", "home_score_ht", "away_score_ht"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

        df = df.drop_duplicates(subset=["match_id"]).reset_index(drop=True)
        return df

    def _add_derived_metrics(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute all 15+ derived metrics."""

        # 1. Total goals
        df["total_goals"] = df["home_score_ft"] + df["away_score_ft"]

        # 2. Goal differential (home perspective)
        df["goal_differential"] = df["home_score_ft"] - df["away_score_ft"]

        # 3-5. Match outcome flags
        df["home_win"] = (df["winner"] == "HOME_TEAM").astype(int)
        df["away_win"] = (df["winner"] == "AWAY_TEAM").astype(int)
        df["is_draw"]  = (df["winner"] == "DRAW").astype(int)

        # 6. Both teams scored
        df["both_teams_scored"] = (
            (df["home_score_ft"] >= 1) & (df["away_score_ft"] >= 1)
        ).astype(int)

        # 7-8. Clean sheets
        df["clean_sheet_home"] = (df["away_score_ft"] == 0).astype(int)
        df["clean_sheet_away"] = (df["home_score_ft"] == 0).astype(int)

        # 9. High-scoring match
        df["high_scoring"] = (df["total_goals"] >= self.HIGH_SCORE_THRESHOLD).astype(int)

        # 10-11. Half-time leads
        df["halftime_home_lead"] = (df["home_score_ht"] > df["away_score_ht"]).astype(int)
        df["halftime_away_lead"] = (df["away_score_ht"] > df["home_score_ht"]).astype(int)

        # 12-13. Comebacks
        df["comeback_home"] = (
            (df["halftime_away_lead"] == 1) & (df["home_win"] == 1)
        ).astype(int)
        df["comeback_away"] = (
            (df["halftime_home_lead"] == 1) & (df["away_win"] == 1)
        ).astype(int)

        # 14. Second-half goals
        ht_total = df["home_score_ht"] + df["away_score_ht"]
        df["second_half_goals"] = (df["total_goals"] - ht_total).clip(lower=0)

        # 15. Temporal features
        df["match_month"]   = df["utc_date"].dt.month
        df["match_weekday"] = df["utc_date"].dt.dayofweek   # 0=Mon, 6=Sun
        df["match_date"]    = df["utc_date"].dt.date.astype(str)

        return df