import os

from dotenv import load_dotenv

load_dotenv()

# Run Mode ─────────────────────────────────────────────────────────────────
# APP_MODE=test  -> deterministic mock data, written to test_soccer.db
# APP_MODE=live  -> Football-Data.org API,  written to live_soccer.db (default)
APP_MODE = os.getenv("APP_MODE", "live").strip().lower()
USE_MOCK = APP_MODE == "test"

# API Configuration ────────────────────────────────────────────────────────
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY")
FOOTBALL_API_BASE_URL = "https://api.football-data.org/v4"

# Competitions to fetch (Football-Data.org competition codes)
COMPETITIONS = {
    "PL":  "Premier League",
    "PD":  "La Liga",
    "BL1": "Bundesliga",
    "SA":  "Serie A",
    "FL1": "Ligue 1",
}

# Starting year of the season to ingest. Pinning this keeps every league on the
# same campaign — without it, leagues whose "current" season has rolled over to
# next year return unplayed fixtures that carry no results.
SEASON = int(os.getenv("SEASON", "2025"))

# ETL Configuration ────────────────────────────────────────────────────────
REQUEST_TIMEOUT      = 30        # seconds
MAX_RETRIES          = 3
RETRY_WAIT_MIN       = 2         # seconds (exponential backoff)
RETRY_WAIT_MAX       = 10
RATE_LIMIT_CALLS     = 9         # one below the free tier's 10/min, for margin
RATE_LIMIT_PERIOD    = 60        # seconds
LEAGUE_REQUEST_DELAY = 12        # paced wait between leagues to stay under budget
RATE_LIMIT_COOLDOWN  = 65        # hard sleep on a 429 to clear the rolling window
RATE_LIMIT_MAX_COOLDOWNS = 3     # window-reset attempts before a league is dropped

# Database Configuration ───────────────────────────────────────────────────
# Mock and live data never share a database file.
DB_FILENAME = "test_soccer.db" if USE_MOCK else "live_soccer.db"
DB_PATH = os.path.join(os.path.dirname(__file__), "data", DB_FILENAME)
DATABASE_URL = f"sqlite:///{DB_PATH}"

# Analytics Configuration ──────────────────────────────────────────────────
TOP_N_TEAMS          = 10
RECENT_FORM_GAMES    = 5
HIGH_SCORE_THRESHOLD = 4         # total goals to be considered "high scoring"