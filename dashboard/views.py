"""Streamlit render helpers"""

from __future__ import annotations

import pandas as pd
import streamlit as st

_STANDINGS_LABELS = {
    "position": "Pos", "team": "Team", "played": "P", "won": "W", "drawn": "D",
    "lost": "L", "gf": "GF", "ga": "GA", "gd": "GD", "pts": "Pts",
}


def kpis(summary: dict) -> None:
    if not summary:
        return
    cols = st.columns(5)
    cols[0].metric("Matches", f"{int(summary['matches']):,}")
    cols[1].metric("Goals / match", f"{summary['avg_goals']:.2f}")
    cols[2].metric("Home wins", f"{summary['home_win_pct']:.0f}%")
    cols[3].metric("Away wins", f"{summary['away_win_pct']:.0f}%")
    cols[4].metric("Draws", f"{summary['draw_pct']:.0f}%")


def standings(table: pd.DataFrame) -> None:
    st.subheader("League standings")
    if table.empty:
        st.info("No standings for the current selection.")
        return
    multi_league = table["competition"].nunique() > 1
    for competition, group in table.groupby("competition", sort=False):
        if multi_league:
            st.caption(competition)
        display = group[list(_STANDINGS_LABELS)].rename(columns=_STANDINGS_LABELS)
        st.dataframe(display, hide_index=True, use_container_width=True)


def efficiency_matrix(data: pd.DataFrame) -> None:
    st.subheader("Attack vs defence efficiency")
    st.caption("Goals scored vs conceded per match (not xG) · point size = points per game")
    if data.empty:
        st.info("No data for this selection.")
        return
    st.scatter_chart(
        data, x="goals_for_pg", y="goals_against_pg", size="points_pg", color="competition",
        x_label="Goals scored / match", y_label="Goals conceded / match",
    )


def team_form(form: pd.DataFrame, window: int) -> None:
    st.subheader(f"Rolling form · last {window} matches")
    if form.empty:
        st.info("Not enough matches to compute form for this selection.")
        return
    display = form.head(15).rename(columns={
        "team": "Team", "form": "Form", "wins": "W",
        "draws": "D", "losses": "L", "points": "Pts",
    })
    st.dataframe(display, hide_index=True, use_container_width=True)


def home_away_splits(splits: pd.DataFrame) -> None:
    st.subheader("Home vs away")
    if splits.empty:
        st.info("No outcome data for this selection.")
        return
    chart = splits.set_index("competition")[["home_win_pct", "away_win_pct", "draw_pct"]]
    chart.columns = ["Home win %", "Away win %", "Draw %"]
    st.bar_chart(chart, stack=False, y_label="Win %")


def schedule_density(data: pd.DataFrame) -> None:
    st.subheader("Schedule density & fatigue")
    st.caption("Win % on a congested schedule (<4 days rest) vs fully rested (6+ days)")
    if data.empty:
        st.info("Not enough scheduling history for this selection.")
        return
    chart = data.pivot(index="competition", columns="rest_band", values="win_pct")
    st.bar_chart(chart, stack=False, y_label="Win %")


def bounce_back(data: pd.DataFrame) -> None:
    st.subheader("Bounce-back resilience index")
    st.caption("Points per game in the match immediately following a loss, vs the team's baseline")
    if data.empty:
        st.info("No qualifying teams for this selection.")
        return
    display = data.rename(columns={
        "competition": "League", "team": "Team", "games_after_loss": "Games",
        "bounce_back_ppg": "PPG after loss", "overall_ppg": "Baseline PPG",
        "resilience_delta": "Δ",
    })
    st.dataframe(display, hide_index=True, use_container_width=True)


def competitive_balance(data: pd.DataFrame) -> None:
    st.subheader("League parity index")
    st.caption("Spread of cumulative points across teams by matchweek — lower = more competitive")
    if data.empty:
        st.info("No data for this selection.")
        return
    chart = data.pivot(index="matchweek", columns="competition", values="points_stdev")
    st.line_chart(chart, x_label="Matchweek", y_label="Points std. dev.")


def defensive_solidity(data: pd.DataFrame) -> None:
    st.subheader("Defensive solidity vs opponent strength")
    st.caption("Goals conceded per game against each league's top-5 vs bottom-5 attacks")
    if data.empty:
        st.info("No qualifying teams for this selection.")
        return
    display = data.rename(columns={
        "competition": "League", "team": "Team", "games_vs_top": "Games vs top 5",
        "conceded_vs_top": "Conceded vs top 5", "conceded_vs_bottom": "Conceded vs bottom 5",
        "clean_sheet_pct_vs_top": "Clean sheet % vs top 5",
    })
    st.dataframe(display, hide_index=True, use_container_width=True)
