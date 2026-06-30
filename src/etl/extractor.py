"""
extractor.py
────────────
Extracts match data from the Football-Data.org REST API with retry/backoff,
rate-limit handling, and per-competition error recovery. In test mode the API
client is bypassed for a deterministic generator (see ``generate_mock_matches``).
"""

import logging
import random
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

import config

logger = logging.getLogger(__name__)


class RateLimitedError(Exception):
    """Raised on HTTP 429. Handled by the league loop, not by tenacity, so the
    full rolling window can be cleared before retrying."""


# Rate Limiter ─────────────────────────────────────────────────────────────

class RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self._calls: List[float] = []

    def wait(self):
        now = time.time()
        # Purge calls outside the window
        self._calls = [t for t in self._calls if now - t < self.period]
        if len(self._calls) >= self.max_calls:
            sleep_for = self.period - (now - self._calls[0])
            if sleep_for > 0:
                logger.info(f"[RATE LIMIT] Sleeping {sleep_for:.1f}s …")
                time.sleep(sleep_for)
        self._calls.append(time.time())


# API Client ───────────────────────────────────────────────────────────────

class FootballDataClient:
    """
    Client for the Football-Data.org v4 API.
    Handles auth headers, rate limiting, and retries automatically.
    """

    def __init__(self, api_key: str | None = None):
        api_key = api_key or config.FOOTBALL_API_KEY
        if not api_key:
            raise RuntimeError(
                "FOOTBALL_API_KEY is not set. Add it to .env (see .env.example) "
                "or run in test mode with APP_MODE=test."
            )
        self.base_url = config.FOOTBALL_API_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "X-Auth-Token": api_key,
            "Accept": "application/json",
        })
        self.rate_limiter = RateLimiter(
            max_calls=config.RATE_LIMIT_CALLS,
            period=config.RATE_LIMIT_PERIOD,
        )

    @retry(
        stop=stop_after_attempt(config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=config.RETRY_WAIT_MIN, max=config.RETRY_WAIT_MAX),
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _get(self, endpoint: str, params: Dict = None) -> Dict:
        self.rate_limiter.wait()
        url = f"{self.base_url}{endpoint}"
        logger.debug(f"GET {url} params={params}")

        try:
            response = self.session.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            if exc.response.status_code == 429:
                raise RateLimitedError() from exc
            if exc.response.status_code == 403:
                logger.error("[403] Invalid API key or competition not on your plan.")
            raise

    def fetch_matches(self, competition_code: str, season: int = None) -> List[Dict]:
        """Fetch all matches for a competition."""
        params = {}
        if season:
            params["season"] = season

        data = self._get(f"/competitions/{competition_code}/matches", params=params)
        matches = data.get("matches", [])
        logger.info(f"  Fetched {len(matches)} matches for {competition_code}")
        return matches

    def fetch_standings(self, competition_code: str) -> List[Dict]:
        """Fetch league standings for a competition."""
        data = self._get(f"/competitions/{competition_code}/standings")
        standings = data.get("standings", [])
        return standings


# Mock Data Generator ──────────────────────────────────────────────────────

LEAGUE_TEAMS = {
    "PL": [
        "Arsenal", "Manchester City", "Liverpool", "Manchester United", "Chelsea",
        "Tottenham Hotspur", "Newcastle United", "Aston Villa", "Brighton", "West Ham United",
    ],
    "PD": [
        "Real Madrid", "Barcelona", "Atlético Madrid", "Sevilla", "Real Sociedad",
        "Villarreal", "Real Betis", "Valencia", "Athletic Bilbao", "Girona",
    ],
    "BL1": [
        "Bayern München", "Borussia Dortmund", "RB Leipzig", "Bayer Leverkusen", "Eintracht Frankfurt",
        "VfB Stuttgart", "VfL Wolfsburg", "SC Freiburg", "Union Berlin", "Borussia Mönchengladbach",
    ],
    "SA": [
        "Inter Milan", "AC Milan", "Juventus", "Napoli", "AS Roma",
        "Lazio", "Atalanta", "Fiorentina", "Bologna", "Torino",
    ],
    "FL1": [
        "Paris Saint-Germain", "Marseille", "Monaco", "Lille", "Lyon",
        "Nice", "Lens", "Rennes", "Reims", "Toulouse",
    ],
}

REFEREES = ["Michael Oliver", "Anthony Taylor", "Stuart Attwell", "Simon Hooper", "Craig Pawson"]

_HOME_GOAL_WEIGHTS = [15, 30, 25, 15, 8, 4, 3]
_AWAY_GOAL_WEIGHTS = [20, 30, 22, 15, 7, 4, 2]


def generate_mock_matches(competition: str = "PL", n: int | None = None,
                          seed: int = 42) -> List[Dict[str, Any]]:
    """
    Generate deterministic, schema-faithful mock matches for one competition.

    Team and match IDs are namespaced by competition so leagues never collide,
    fixtures follow a shuffled double round-robin, and ``n`` optionally caps the
    number of matches returned.
    """
    teams = LEAGUE_TEAMS.get(competition)
    if not teams:
        return []

    rng = random.Random(f"{competition}:{seed}")
    namespace = (list(LEAGUE_TEAMS).index(competition) + 1) * 1000
    team_ids = {name: namespace + i for i, name in enumerate(teams, start=1)}

    fixtures = [(home, away) for home in teams for away in teams if home != away]
    rng.shuffle(fixtures)
    if n is not None:
        fixtures = fixtures[:n]

    games_per_round = max(1, len(teams) // 2)
    base_date = datetime(2024, 8, 10)
    matches: List[Dict[str, Any]] = []

    for index, (home, away) in enumerate(fixtures):
        home_score = rng.choices(range(7), weights=_HOME_GOAL_WEIGHTS)[0]
        away_score = rng.choices(range(7), weights=_AWAY_GOAL_WEIGHTS)[0]
        match_date = base_date + timedelta(weeks=index // games_per_round, days=rng.randint(0, 2))

        matches.append({
            "id": namespace * 100 + index + 1,
            "competition": {"code": competition,
                            "name": config.COMPETITIONS.get(competition, competition)},
            "season": {"startDate": "2024-08-01", "endDate": "2025-05-31"},
            "utcDate": match_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "FINISHED",
            "matchday": index // games_per_round + 1,
            "homeTeam": {"id": team_ids[home], "name": home, "shortName": home.split()[0]},
            "awayTeam": {"id": team_ids[away], "name": away, "shortName": away.split()[0]},
            "score": {
                "fullTime": {"home": home_score, "away": away_score},
                "halfTime": {"home": rng.randint(0, home_score),
                             "away": rng.randint(0, away_score)},
                "winner": (
                    "HOME_TEAM" if home_score > away_score
                    else "AWAY_TEAM" if away_score > home_score
                    else "DRAW"
                ),
            },
            "referees": [{"name": rng.choice(REFEREES), "type": "REFEREE"}],
        })

    return matches


# Extractor Orchestrator ───────────────────────────────────────────────────

class Extractor:
    """
    Orchestrates data extraction across multiple competitions.
    Falls back to mock data if use_mock=True.
    """

    def __init__(self, use_mock: bool = False):
        self.use_mock = use_mock
        if not use_mock:
            self.client = FootballDataClient()

    def extract(self, competitions: Dict[str, str] = None) -> List[Dict]:
        if self.use_mock:
            return self._extract_mock(competitions)
        return self._extract_api(competitions)

    def _extract_mock(self, competitions: Dict[str, str] = None) -> List[Dict]:
        competitions = competitions or config.COMPETITIONS
        all_matches = []
        for code, name in competitions.items():
            logger.info(f"  [MOCK] Generating matches for {name} ({code}) …")
            all_matches.extend(generate_mock_matches(competition=code))
        logger.info(f"[EXTRACT] Generated {len(all_matches)} mock matches total.")
        return all_matches

    def _extract_api(self, competitions: Dict[str, str] = None) -> List[Dict]:
        leagues = list((competitions or config.COMPETITIONS).items())
        all_matches: List[Dict] = []
        for index, (code, name) in enumerate(leagues):
            logger.info("  Fetching %s (%s) …", name, code)
            all_matches.extend(self._fetch_league(code))
            if index < len(leagues) - 1:
                time.sleep(config.LEAGUE_REQUEST_DELAY)
        logger.info("[EXTRACT] Fetched %d matches across %d leagues.", len(all_matches), len(leagues))
        return all_matches

    def _fetch_league(self, code: str) -> List[Dict]:
        """
        Fetch one competition, absorbing rate limits at the league level: a 429
        triggers a full window-reset cooldown and a retry of the same league
        rather than skipping it. Only a fatal request error drops the league.
        """
        for cooldown in range(config.RATE_LIMIT_MAX_COOLDOWNS + 1):
            try:
                return self.client.fetch_matches(competition_code=code, season=config.SEASON)
            except RateLimitedError:
                if cooldown == config.RATE_LIMIT_MAX_COOLDOWNS:
                    break
                logger.warning(
                    "  [429] %s rate limited; sleeping %ds to clear the window, then retrying (%d/%d).",
                    code, config.RATE_LIMIT_COOLDOWN, cooldown + 1, config.RATE_LIMIT_MAX_COOLDOWNS,
                )
                time.sleep(config.RATE_LIMIT_COOLDOWN)
            except requests.RequestException as exc:
                logger.error("  Dropping %s after a fatal request error: %s", code, exc)
                return []
        logger.error("  Dropping %s: still rate limited after %d cooldowns.",
                     code, config.RATE_LIMIT_MAX_COOLDOWNS)
        return []