"""
Streamlit dashboard for the soccer analytics engine.

Run with:  streamlit run app.py
The ETL pipeline (`python main.py --mock`) must have populated the database first.
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


def main() -> None:
    st.title("⚽ Soccer Match Analytics")

    leagues = competitions()
    if leagues.empty:
        command = "APP_MODE=test python main.py" if config.USE_MOCK else "python main.py"
        st.warning(f"No data in `{config.DB_FILENAME}` yet. Populate it first:  `{command}`")
        return

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
