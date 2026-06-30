# Soccer Match Analytics

An end-to-end data pipeline for the five major European football leagues. It
extracts match data from the [Football-Data.org](https://www.football-data.org/)
API, transforms it into clean records with derived metrics, loads it into a
normalized SQLite database, and surfaces the analytics through a Streamlit
dashboard.

## Highlights

- **ETL pipeline** with retry/backoff, token-bucket rate limiting, and idempotent loads.
- **Normalized schema** (`competitions`, `teams`, `matches`, `match_metrics`) with strategic indexing.
- **SQL analytics** built on window functions — league position via `RANK()`, rolling form via `ROW_NUMBER()`.
- **Streamlit dashboard** with a single shared query layer, filterable by league.
- **Mock mode** that generates realistic data, so the whole project runs offline with no API key.

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
