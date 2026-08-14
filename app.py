"""
Streamlit dashboard for the soccer analytics engine.

CLOUD MODE (Streamlit Community Cloud + AWS S3):
    Pulls the latest per-league SQLite databases from S3, merges them into
    one unified DB, and points the analytics layer at it. Requires AWS
    credentials in Streamlit secrets.

LOCAL MODE (development):
    Falls back to the local database file at config.DB_PATH, exactly as
    before. No AWS credentials needed.

Run with:  streamlit run app.py
"""

import os
import sys

import pandas as pd
import streamlit as st
from sqlalchemy.exc import OperationalError

sys.path.insert(0, os.path.dirname(__file__))

import config
from dashboard import views
from src.analytics import queries

st.set_page_config(page_title="Soccer Match Analytics", page_icon="⚽", layout="wide")

ALL_LEAGUES = "All leagues"


# S3 sync 

@st.cache_data(ttl=1800, show_spinner="Syncing latest data from S3…")
def _sync_db_from_s3() -> str | None:
    """
    Download and merge the per-league databases from S3.
    Cached for 30 minutes so the app doesn't re-download on every rerun.
    Returns the path to the merged DB, or None if sync isn't available.
    """
    try:
        from s3_sync import sync_from_s3
        return sync_from_s3()
    except Exception as e:
        st.warning(f"S3 sync failed — falling back to local database. Error: {e}")
        return None


def _setup_data_source():
    """
    If AWS secrets are configured, sync from S3 and override config so the
    analytics layer reads from the merged cloud database. Otherwise, use
    the local file path (normal dev workflow).
    """
    has_secrets = (
        "AWS_ACCESS_KEY_ID" in st.secrets
        and "AWS_SECRET_ACCESS_KEY" in st.secrets
        and "S3_BUCKET" in st.secrets
    )

    if has_secrets:
        merged_path = _sync_db_from_s3()
        if merged_path and os.path.exists(merged_path):
            config.DB_PATH = merged_path
            config.DATABASE_URL = f"sqlite:///{merged_path}"
            # Re-initialize the query layer's engine to point at the new path
            if hasattr(queries, "_engine"):
                queries._engine = None
            return "cloud"

    return "local"


# Data loading

@st.cache_data(ttl=300)
def competitions() -> pd.DataFrame:
    if not os.path.exists(config.DB_PATH):
        return pd.DataFrame()
    try:
        return queries.competitions()
    except OperationalError:
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load(report: str, code: str | None, window: int = 5, through_matchday: int | None = None):
    """Dispatch to the analytics layer; cached per (report, filter) combination."""
    dispatch = {
        "summary": lambda: queries.summary(code),
        "standings": lambda: queries.standings(code, through_matchday),
        "efficiency": lambda: queries.efficiency_matrix(code),
        "form": lambda: queries.team_form(code, window=window),
        "splits": lambda: queries.home_away_splits(code),
        "fatigue": lambda: queries.schedule_density(code),
        "resilience": lambda: queries.bounce_back(code),
        "parity": lambda: queries.competitive_balance(code),
        "defense": lambda: queries.defensive_solidity(code),
    }
    try:
        return dispatch[report]()
    except KeyError:
        raise ValueError(f"Unknown report: {report}")


# Dashboard

def league_dynamics(code: str | None, window: int) -> None:
    views.kpis(load("summary", code))
    st.divider()

    low, high = queries.matchday_bounds(code)
    through = st.slider("Standings through gameweek", low, high, high) if high > low else None
    views.standings(load("standings", code, through_matchday=through))
    st.divider()

    views.efficiency_matrix(load("efficiency", code))
    st.divider()

    views.team_form(load("form", code, window=window), window)
    st.divider()

    views.home_away_splits(load("splits", code))


def advanced_insights(code: str | None) -> None:
    left, right = st.columns(2)
    with left:
        views.schedule_density(load("fatigue", code))
    with right:
        views.competitive_balance(load("parity", code))
    st.divider()

    views.bounce_back(load("resilience", code))
    st.divider()

    views.defensive_solidity(load("defense", code))


# Main

def main() -> None:
    st.title("⚽ Soccer Match Analytics")

    # Decide data source: S3 (cloud) or local file
    source = _setup_data_source()

    leagues = competitions()
    if leagues.empty:
        if source == "cloud":
            st.warning(
                "No data found in S3. Has the Lambda ETL pipeline run at least once? "
                "Check your S3 bucket for `databases/PL/latest.db` etc."
            )
        else:
            command = "APP_MODE=test python main.py" if config.USE_MOCK else "python main.py"
            st.warning(f"No data in `{config.DB_FILENAME}` yet. Populate it first:  `{command}`")
        return

    # Show data source indicator in sidebar
    if source == "cloud":
        st.sidebar.success("📡 Live from AWS S3")
    else:
        st.sidebar.info(f"💾 Local: {config.DB_FILENAME}")

    st.sidebar.header("Filters")
    choice = st.sidebar.selectbox("League", [ALL_LEAGUES, *leagues["name"]])
    window = st.sidebar.slider("Form window (matches)", min_value=3, max_value=10, value=5)
    code = None if choice == ALL_LEAGUES else leagues.loc[leagues["name"] == choice, "code"].iloc[0]

    dynamics, advanced = st.tabs(["League Dynamics", "Advanced Performance Insights"])
    with dynamics:
        league_dynamics(code, window)
    with advanced:
        advanced_insights(code)


main()