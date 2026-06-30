"""
Headless console rendering of the analytics, sharing the same query layer as
the dashboard. Useful for CI, logs, and a quick look without launching Streamlit.
"""

from __future__ import annotations

import pandas as pd

from src.analytics import queries

_RULE = "─" * 64


def _section(title: str) -> None:
    print(f"\n{_RULE}\n  {title}\n{_RULE}")


def _table(df: pd.DataFrame) -> None:
    print(df.to_string(index=False) if not df.empty else "  (no data)")


def standings(top_n: int = 10) -> None:
    table = queries.standings()
    if table.empty:
        return
    _section("LEAGUE STANDINGS")
    columns = ["position", "team", "played", "won", "drawn", "lost", "gf", "ga", "gd", "pts"]
    for competition, group in table.groupby("competition", sort=False):
        print(f"\n  {competition}")
        _table(group.head(top_n)[columns])


def recent_form(window: int = 5) -> None:
    form = queries.team_form(window=window)
    if form.empty:
        return
    _section(f"RECENT FORM (last {window})")
    _table(form.head(15))


def home_away() -> None:
    splits = queries.home_away_splits()
    if splits.empty:
        return
    _section("HOME vs AWAY SPLITS")
    _table(splits)


def top_scorers() -> None:
    scorers = queries.top_scoring_teams()
    if scorers.empty:
        return
    _section("TOP SCORING TEAMS (avg goals / match)")
    _table(scorers)


def scoring_by_month() -> None:
    months = queries.goals_by_month()
    if months.empty:
        return
    _section("SCORING BY MONTH")
    _table(months.drop(columns="month"))


def run_all() -> None:
    if not queries.summary():
        print("No data in the database yet — run the ETL pipeline first.")
        return
    standings()
    recent_form()
    home_away()
    top_scorers()
    scoring_by_month()
