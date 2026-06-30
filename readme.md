# Soccer Match Analytics

> ETL pipeline and SQL analytics for the five major European football leagues — Football-Data.org → SQLite → Streamlit.

**[🔗 Live Demo](#)** &nbsp;·&nbsp; `https://automated-soccer-analytics-82zqd9icqr7zzmqn5gkkw6.streamlit.app/`

A pipeline that pulls a full season of matches (≈1,750 across 5 leagues), derives per-match metrics in pandas, loads them into a normalized SQLite schema, and serves eight analytical reports through a Streamlit dashboard. The same SQL layer backs both the dashboard and a headless CLI report.

## Data pipeline

- **Token-bucket rate limiting** to stay strictly within the API's 10-requests/minute budget, with paced delays between leagues.
- **429-aware ingestion**: a rate-limit response triggers a hard cooldown to clear the server's rolling window, then retries the *same* league rather than dropping it.
- **Idempotent bulk loads** via SQLAlchemy — re-running the pipeline upserts on primary key, so duplicate runs never double-count.
- **Season pinning** so every league resolves to the same campaign, instead of silently mixing finished and not-yet-played fixtures.
- **Deterministic mock mode** that generates schema-faithful data, so the project runs end-to-end offline with no API key.

## Analytical Insights & Schema Depth

Data is split across four normalized tables — `competitions`, `teams`, `matches`, and a dedicated `match_metrics` table holding 16 derived per-match flags (clean sheets, comebacks, both-teams-scored, half-time leads, temporal features). Storing computed metrics separately keeps the `matches` table to raw facts and lets the analytics layer aggregate without recomputation. Foreign keys and indexes on competition, date, and both team columns keep the window-function queries fast.

All analytics live in one parameterized query module (`src/analytics/queries.py`) that returns DataFrames, so the dashboard and CLI never duplicate SQL. The reports lean on CTEs and window functions rather than application-side loops:

- **Schedule Density & Fatigue** — `LAG()` over each team's match dates computes days of rest before every fixture, then win % and points-per-game are bucketed into a congested band (<4 days rest) versus a rested band (6+ days) to quantify performance decay under fixture congestion.
- **Bounce-Back Resilience Index** — `LAG()` on the result flag isolates every match played *immediately after a loss*, then measures points-per-game in those games against the team's seasonal baseline PPG — separating sides that respond from those that spiral.
- **League Parity Index** — cumulative points are accumulated per team by matchweek with a running-sum window, and the standard deviation of those totals across the league is tracked over the season; a tightening spread signals a competitive league, a widening one a top-heavy table.
- **Defensive Solidity vs Opponent Strength** — opponents are tiered by attacking output with `RANK()` partitioned per league, then goals conceded are split by the tier faced, exposing defenses that hold up against elite attacks rather than padding stats against weak ones.

Standings (`RANK()`), rolling form (`ROW_NUMBER()`), home/away splits, and an attack-vs-defence efficiency matrix round out the eight reports, organized into "League Dynamics" and "Advanced Performance Insights" tabs and filterable by league and gameweek.

## Architecture

```
soccer_analytics_pipeline/
├── app.py                  # Streamlit dashboard (presentation)
├── main.py                 # CLI: run the ETL pipeline + console reports
├── config.py               # configuration (env-driven)
├── dashboard/
│   └── views.py            # Streamlit render helpers
├── src/
│   ├── etl/
│   │   ├── extractor.py    # API client: retries, rate limiting, mock generator
│   │   ├── transformer.py  # pandas cleaning + 16 derived metrics
│   │   ├── loader.py       # SQLAlchemy models + idempotent bulk load
│   │   └── pipeline.py     # extract → transform → load orchestration
│   └── analytics/
│       ├── queries.py      # parameterized SQL, returns DataFrames (single source of truth)
│       └── reports.py      # console renderer for headless runs
├── sql/queries.sql         # reference SQL
└── tests/                  # pytest suite
```

The dashboard and the CLI reports both read through `src/analytics/queries.py`,
so the SQL lives in exactly one place.

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your key (free from football-data.org):

```bash
cp .env.example .env
# edit .env -> FOOTBALL_API_KEY=...
```

## Run modes

`APP_MODE` selects the data source and target database; the two never mix:

| `APP_MODE` | Source | Database | API key |
|------------|--------|----------|---------|
| `live` (default) | Football-Data.org API | `data/live_soccer.db` | required |
| `test` | deterministic mock generator | `data/test_soccer.db` | not needed |

## Usage

Load data and view the analytics in the terminal:

```bash
python main.py                   # live API  -> live_soccer.db
APP_MODE=test python main.py     # mock data -> test_soccer.db
python main.py --analytics-only  # re-run reports against the existing database
```

Launch the dashboard (after the matching database has been populated):

```bash
streamlit run app.py                   # reads live_soccer.db
APP_MODE=test streamlit run app.py     # reads test_soccer.db
```

Run the tests:

```bash
python -m pytest
```

## Tech stack

Python · pandas · SQLAlchemy · SQLite · Streamlit · requests · tenacity · pytest
