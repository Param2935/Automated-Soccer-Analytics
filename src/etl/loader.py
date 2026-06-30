"""
loader.py
─────────
Defines the normalized SQLite schema via SQLAlchemy ORM and handles
bulk loading of transformed DataFrames into the database.

Schema:
  competitions   one row per competition
  teams          one row per team
  matches        one row per match (FK → competitions, teams)
  match_metrics  derived metrics (FK → matches)
"""

import os
import logging
from typing import List

import pandas as pd
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, DateTime,
    ForeignKey, Boolean, Index, UniqueConstraint, text,
)
from sqlalchemy.orm import declarative_base, relationship, Session

import config

logger = logging.getLogger(__name__)

Base = declarative_base()


# ORM Models ───────────────────────────────────────────────────────────────

class Competition(Base):
    __tablename__ = "competitions"

    id   = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), unique=True, nullable=False)
    name = Column(String(100), nullable=False)

    matches = relationship("Match", back_populates="competition")


class Team(Base):
    __tablename__ = "teams"

    id        = Column(Integer, primary_key=True)   # from API
    name      = Column(String(100), nullable=False)

    home_matches = relationship("Match", foreign_keys="Match.home_team_id", back_populates="home_team")
    away_matches = relationship("Match", foreign_keys="Match.away_team_id", back_populates="away_team")


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        Index("ix_matches_competition", "competition_id"),
        Index("ix_matches_date",        "match_date"),
        Index("ix_matches_home_team",   "home_team_id"),
        Index("ix_matches_away_team",   "away_team_id"),
    )

    id               = Column(Integer, primary_key=True)   # from API
    competition_id   = Column(Integer, ForeignKey("competitions.id"), nullable=False)
    home_team_id     = Column(Integer, ForeignKey("teams.id"),        nullable=False)
    away_team_id     = Column(Integer, ForeignKey("teams.id"),        nullable=False)
    match_date       = Column(String(20))
    matchday         = Column(Integer)
    status           = Column(String(20))
    home_score_ft    = Column(Integer)
    away_score_ft    = Column(Integer)
    home_score_ht    = Column(Integer)
    away_score_ht    = Column(Integer)
    winner           = Column(String(20))
    referee          = Column(String(100))

    competition = relationship("Competition", back_populates="matches")
    home_team   = relationship("Team", foreign_keys=[home_team_id], back_populates="home_matches")
    away_team   = relationship("Team", foreign_keys=[away_team_id], back_populates="away_matches")
    metrics     = relationship("MatchMetrics", back_populates="match", uselist=False)


class MatchMetrics(Base):
    """All derived / computed metrics for a match stored separately."""
    __tablename__ = "match_metrics"
    __table_args__ = (
        Index("ix_metrics_match", "match_id"),
    )

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    match_id            = Column(Integer, ForeignKey("matches.id"), nullable=False, unique=True)
    total_goals         = Column(Integer)
    goal_differential   = Column(Integer)
    home_win            = Column(Boolean)
    away_win            = Column(Boolean)
    is_draw             = Column(Boolean)
    both_teams_scored   = Column(Boolean)
    clean_sheet_home    = Column(Boolean)
    clean_sheet_away    = Column(Boolean)
    high_scoring        = Column(Boolean)
    halftime_home_lead  = Column(Boolean)
    halftime_away_lead  = Column(Boolean)
    comeback_home       = Column(Boolean)
    comeback_away       = Column(Boolean)
    second_half_goals   = Column(Integer)
    match_month         = Column(Integer)
    match_weekday       = Column(Integer)

    match = relationship("Match", back_populates="metrics")


# Loader ───────────────────────────────────────────────────────────────────

class Loader:
    """
    Loads transformed DataFrames into the SQLite database.
    Handles upserts to support weekly re-runs without duplicates.
    """

    def __init__(self, db_url: str = config.DATABASE_URL):
        db_dir = os.path.dirname(db_url.replace("sqlite:///", ""))
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.engine = create_engine(db_url, echo=False)
        Base.metadata.create_all(self.engine)
        logger.info(f"[LOADER] Database ready at {db_url}")

    def load(self, df: pd.DataFrame, teams_df: pd.DataFrame):
        """Main entry — loads competitions, teams, matches, and metrics."""
        if df.empty:
            logger.warning("[LOADER] Empty DataFrame — nothing to load.")
            return

        with Session(self.engine) as session:
            comp_map  = self._upsert_competitions(session, df)
            team_map  = self._upsert_teams(session, teams_df)
            loaded    = self._upsert_matches(session, df, comp_map, team_map)
            self._upsert_metrics(session, df)
            session.commit()

        logger.info(
            f"[LOAD] Loaded {loaded} matches | "
            f"{len(teams_df)} teams | "
            f"{df['competition_code'].nunique()} competitions"
        )

    # Private helpers

    def _upsert_competitions(self, session: Session, df: pd.DataFrame) -> dict:
        comp_map = {}
        for _, row in df[["competition_code", "competition_name"]].drop_duplicates().iterrows():
            obj = session.query(Competition).filter_by(code=row["competition_code"]).first()
            if not obj:
                obj = Competition(code=row["competition_code"], name=row["competition_name"])
                session.add(obj)
                session.flush()
            comp_map[row["competition_code"]] = obj.id
        return comp_map

    def _upsert_teams(self, session: Session, teams_df: pd.DataFrame) -> dict:
        team_map = {}
        for _, row in teams_df.iterrows():
            obj = session.get(Team, int(row["team_id"]))
            if not obj:
                obj = Team(id=int(row["team_id"]), name=row["team_name"])
                session.add(obj)
            team_map[int(row["team_id"])] = int(row["team_id"])
        session.flush()
        return team_map

    def _upsert_matches(self, session: Session, df: pd.DataFrame,
                        comp_map: dict, team_map: dict) -> int:
        loaded = 0
        for _, row in df.iterrows():
            existing = session.get(Match, int(row["match_id"]))
            if existing:
                continue  # Skip — already in DB (idempotent)

            match = Match(
                id             = int(row["match_id"]),
                competition_id = comp_map.get(row["competition_code"]),
                home_team_id   = int(row["home_team_id"]),
                away_team_id   = int(row["away_team_id"]),
                match_date     = row.get("match_date"),
                matchday       = int(row["matchday"]) if pd.notna(row.get("matchday")) else None,
                status         = row.get("status"),
                home_score_ft  = int(row["home_score_ft"]),
                away_score_ft  = int(row["away_score_ft"]),
                home_score_ht  = int(row["home_score_ht"]),
                away_score_ht  = int(row["away_score_ht"]),
                winner         = row.get("winner"),
                referee        = row.get("referee"),
            )
            session.add(match)
            loaded += 1

        session.flush()
        return loaded

    def _upsert_metrics(self, session: Session, df: pd.DataFrame):
        metric_cols = [
            "total_goals", "goal_differential", "home_win", "away_win",
            "is_draw", "both_teams_scored", "clean_sheet_home", "clean_sheet_away",
            "high_scoring", "halftime_home_lead", "halftime_away_lead",
            "comeback_home", "comeback_away", "second_half_goals",
            "match_month", "match_weekday",
        ]
        for _, row in df.iterrows():
            existing = session.query(MatchMetrics).filter_by(match_id=int(row["match_id"])).first()
            if existing:
                continue
            # Only insert if parent match exists
            parent = session.get(Match, int(row["match_id"]))
            if not parent:
                continue
            metrics = MatchMetrics(
                match_id=int(row["match_id"]),
                **{col: (bool(row[col]) if row[col] in [0, 1] else int(row[col]))
                   if col not in ["total_goals", "goal_differential", "second_half_goals",
                                  "match_month", "match_weekday"]
                   else int(row[col])
                   for col in metric_cols}
            )
            session.add(metrics)