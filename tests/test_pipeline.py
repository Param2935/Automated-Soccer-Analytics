"""Unit tests for the extractor, transformer, loader, and analytics queries."""

import os
import sys

import pandas as pd
import pytest
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
from src.analytics import queries
from src.etl.extractor import (
    Extractor, FootballDataClient, RateLimiter, RateLimitedError, generate_mock_matches,
)
from src.etl.loader import Loader
from src.etl.transformer import Transformer


# ─── Fixtures ─────────────────────────────────────────────────────────────────

TEST_DB = "sqlite:///:memory:"


@pytest.fixture
def raw_matches():
    return generate_mock_matches(n=50, competition="PL")


@pytest.fixture
def transformer():
    return Transformer()


@pytest.fixture
def transformed(raw_matches, transformer):
    return transformer.transform(raw_matches)


@pytest.fixture
def teams(transformed, transformer):
    return transformer.get_teams(transformed)


@pytest.fixture
def loader():
    return Loader(db_url=TEST_DB)


@pytest.fixture
def loaded_db(loader, transformed, teams):
    loader.load(transformed, teams)
    return loader


# ─── Extractor Tests ──────────────────────────────────────────────────────────

class _FakeResponse:
    """Minimal stand-in for requests.Response in client tests."""

    def __init__(self, status_code: int, payload: dict = None, headers: dict = None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class TestExtractor:
    def test_mock_generates_matches(self):
        matches = generate_mock_matches(n=20)
        assert len(matches) == 20

    def test_mock_match_schema(self):
        matches = generate_mock_matches(n=5)
        m = matches[0]
        assert "id" in m
        assert "homeTeam" in m
        assert "awayTeam" in m
        assert "score" in m
        assert "utcDate" in m

    def test_extractor_mock_mode(self):
        ext = Extractor(use_mock=True)
        result = ext.extract({"PL": "Premier League"})
        assert len(result) > 0

    def test_extractor_multiple_competitions(self):
        ext = Extractor(use_mock=True)
        result = ext.extract({"PL": "Premier League", "PD": "La Liga"})
        codes = {m["competition"]["code"] for m in result}
        assert "PL" in codes
        assert "PD" in codes

    def test_rate_limiter_does_not_block_below_limit(self):
        limiter = RateLimiter(max_calls=100, period=60)
        import time
        t0 = time.time()
        for _ in range(5):
            limiter.wait()
        assert time.time() - t0 < 1.0  # Should be near-instant

    def test_get_raises_ratelimited_on_429(self, monkeypatch):
        monkeypatch.setattr(config, "FOOTBALL_API_KEY", "test-key")
        client = FootballDataClient()
        monkeypatch.setattr(client.session, "get", lambda *a, **k: _FakeResponse(429))
        with pytest.raises(RateLimitedError):
            client._get("/whatever")

    def test_get_does_not_retry_client_error(self, monkeypatch):
        monkeypatch.setattr(config, "FOOTBALL_API_KEY", "test-key")
        client = FootballDataClient()
        attempts = {"n": 0}

        def always_404(*args, **kwargs):
            attempts["n"] += 1
            return _FakeResponse(404)

        monkeypatch.setattr(client.session, "get", always_404)
        with pytest.raises(requests.HTTPError):
            client._get("/whatever")
        assert attempts["n"] == 1  # 404 is not retried

    def test_loop_retries_same_league_after_cooldown(self, monkeypatch):
        monkeypatch.setattr(config, "FOOTBALL_API_KEY", "test-key")
        monkeypatch.setattr(config, "RATE_LIMIT_COOLDOWN", 0)
        monkeypatch.setattr(config, "LEAGUE_REQUEST_DELAY", 0)
        extractor = Extractor(use_mock=False)
        attempts: dict[str, int] = {}

        def fetch(competition_code, season=None):
            attempts[competition_code] = attempts.get(competition_code, 0) + 1
            if competition_code == "PL" and attempts["PL"] == 1:
                raise RateLimitedError()
            return [{"competition": {"code": competition_code}}]

        monkeypatch.setattr(extractor.client, "fetch_matches", fetch)
        result = extractor.extract({"PL": "Premier League", "SA": "Serie A"})
        assert attempts["PL"] == 2                                  # retried, not skipped
        assert {m["competition"]["code"] for m in result} == {"PL", "SA"}

    def test_persistent_429_drops_league_after_max_cooldowns(self, monkeypatch):
        monkeypatch.setattr(config, "FOOTBALL_API_KEY", "test-key")
        monkeypatch.setattr(config, "RATE_LIMIT_COOLDOWN", 0)
        monkeypatch.setattr(config, "RATE_LIMIT_MAX_COOLDOWNS", 2)
        monkeypatch.setattr(config, "LEAGUE_REQUEST_DELAY", 0)
        extractor = Extractor(use_mock=False)
        attempts = {"n": 0}

        def always_limited(competition_code, season=None):
            attempts["n"] += 1
            raise RateLimitedError()

        monkeypatch.setattr(extractor.client, "fetch_matches", always_limited)
        assert extractor.extract({"PL": "Premier League"}) == []
        assert attempts["n"] == 3  # initial attempt + 2 cooldown retries

    def test_fatal_error_drops_league_but_continues(self, monkeypatch):
        monkeypatch.setattr(config, "FOOTBALL_API_KEY", "test-key")
        monkeypatch.setattr(config, "LEAGUE_REQUEST_DELAY", 0)
        extractor = Extractor(use_mock=False)

        def fetch(competition_code, season=None):
            if competition_code == "PL":
                raise requests.ConnectionError("boom")
            return [{"competition": {"code": competition_code}}]

        monkeypatch.setattr(extractor.client, "fetch_matches", fetch)
        result = extractor.extract({"PL": "Premier League", "SA": "Serie A"})
        assert {m["competition"]["code"] for m in result} == {"SA"}


# ─── Transformer Tests ────────────────────────────────────────────────────────

def _api_match(status: str, fulltime: dict, winner: str = None) -> dict:
    return {
        "id": 999001, "competition": {"code": "FL1", "name": "Ligue 1"},
        "season": {"startDate": "2025-08-17", "endDate": "2026-05-30"},
        "utcDate": "2025-09-01T13:00:00Z", "status": status, "matchday": 4,
        "homeTeam": {"id": 1, "name": "Home"}, "awayTeam": {"id": 2, "name": "Away"},
        "score": {"fullTime": fulltime, "halfTime": {"home": 0, "away": 0}, "winner": winner},
        "referees": [],
    }


class TestTransformer:
    def test_transform_returns_dataframe(self, raw_matches, transformer):
        df = transformer.transform(raw_matches)
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_awarded_matches_are_kept(self, transformer):
        match = _api_match("AWARDED", {"home": 3, "away": 0}, "HOME_TEAM")
        df = transformer.transform([match])
        assert len(df) == 1
        assert df.iloc[0]["home_win"] == 1

    def test_unplayed_matches_are_dropped(self, transformer):
        match = _api_match("SCHEDULED", {"home": None, "away": None})
        assert transformer.transform([match]).empty

    def test_derived_metrics_present(self, transformed):
        expected_cols = [
            "total_goals", "goal_differential", "home_win", "away_win",
            "is_draw", "both_teams_scored", "clean_sheet_home", "clean_sheet_away",
            "high_scoring", "halftime_home_lead", "halftime_away_lead",
            "comeback_home", "comeback_away", "second_half_goals",
            "match_month", "match_weekday",
        ]
        for col in expected_cols:
            assert col in transformed.columns, f"Missing metric: {col}"

    def test_total_goals_correct(self, transformed):
        row = transformed.iloc[0]
        assert row["total_goals"] == row["home_score_ft"] + row["away_score_ft"]

    def test_goal_differential_correct(self, transformed):
        row = transformed.iloc[0]
        assert row["goal_differential"] == row["home_score_ft"] - row["away_score_ft"]

    def test_winner_flags_mutually_exclusive(self, transformed):
        assert ((transformed["home_win"] + transformed["away_win"] + transformed["is_draw"]) == 1).all()

    def test_no_duplicate_match_ids(self, transformed):
        assert transformed["match_id"].nunique() == len(transformed)

    def test_clean_sheet_logic(self, transformed):
        cs_home = transformed[transformed["clean_sheet_home"] == 1]
        assert (cs_home["away_score_ft"] == 0).all()

    def test_high_scoring_threshold(self, transformed):
        import config
        hs = transformed[transformed["high_scoring"] == 1]
        assert (hs["total_goals"] >= config.HIGH_SCORE_THRESHOLD).all()

    def test_empty_input_returns_empty_df(self, transformer):
        result = transformer.transform([])
        assert result.empty

    def test_teams_extraction(self, transformed, transformer):
        teams = transformer.get_teams(transformed)
        assert "team_id" in teams.columns
        assert "team_name" in teams.columns
        assert teams["team_id"].nunique() == len(teams)


# ─── Loader Tests ─────────────────────────────────────────────────────────────

class TestLoader:
    def test_load_creates_tables(self, loader):
        from sqlalchemy import inspect
        inspector = inspect(loader.engine)
        tables = inspector.get_table_names()
        assert "matches" in tables
        assert "teams" in tables
        assert "competitions" in tables
        assert "match_metrics" in tables

    def test_load_inserts_records(self, loaded_db):
        from sqlalchemy.orm import Session
        from src.etl.loader import Match, Team
        with Session(loaded_db.engine) as s:
            match_count = s.query(Match).count()
            team_count  = s.query(Team).count()
        assert match_count > 0
        assert team_count > 0

    def test_load_is_idempotent(self, loader, transformed, teams):
        """Loading the same data twice should not duplicate records."""
        loader.load(transformed, teams)
        loader.load(transformed, teams)
        from sqlalchemy.orm import Session
        from src.etl.loader import Match
        with Session(loader.engine) as s:
            count = s.query(Match).count()
        assert count == len(transformed)

    def test_load_empty_df(self, loader):
        """Loading empty dataframe should not raise."""
        loader.load(pd.DataFrame(), pd.DataFrame())  # Should not crash


# ─── Analytics Query Tests ────────────────────────────────────────────────────

class TestAnalyticsQueries:
    """Exercise the shared query layer against an isolated, populated database."""

    @pytest.fixture
    def analytics_db(self, tmp_path, transformed, teams, monkeypatch):
        db_url = f"sqlite:///{tmp_path / 'analytics.db'}"
        Loader(db_url=db_url).load(transformed, teams)
        monkeypatch.setattr(config, "DATABASE_URL", db_url)
        queries._engine.cache_clear()
        yield
        queries._engine.cache_clear()

    def test_summary_reports_match_count(self, analytics_db):
        assert queries.summary()["matches"] > 0

    def test_standings_ranked_by_points(self, analytics_db):
        table = queries.standings("PL")
        assert not table.empty
        assert table.iloc[0]["position"] == 1
        assert table["pts"].is_monotonic_decreasing

    def test_form_window_is_respected(self, analytics_db):
        form = queries.team_form("PL", window=3)
        assert not form.empty
        assert (form["wins"] + form["draws"] + form["losses"] == 3).all()

    def test_home_away_splits_present(self, analytics_db):
        assert not queries.home_away_splits().empty