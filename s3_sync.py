"""
s3_sync.py
Downloads per-league SQLite databases from S3, merges them into a single
unified database that the existing queries.py layer can read unchanged.

Called once at app startup (cached by Streamlit), so the dashboard always
shows the latest data.
"""

import logging
import os
import sqlite3
import tempfile

import boto3
import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

LEAGUE_CODES = ["PL", "PD", "BL1", "SA", "FL1"]
S3_KEY_PREFIX = "databases"

TABLES = ["competitions", "teams", "matches", "match_metrics"]


def _get_s3_client():
    """Build a boto3 S3 client from Streamlit secrets."""
    return boto3.client(
        "s3",
        aws_access_key_id=st.secrets["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=st.secrets["AWS_SECRET_ACCESS_KEY"],
        region_name=st.secrets.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def _download_league_db(s3, bucket: str, league_code: str, tmp_dir: str) -> str | None:
    """
    Download one league's latest.db from S3 into a temp directory.
    Returns the local file path, or None if the object doesn't exist.
    """
    s3_key = f"{S3_KEY_PREFIX}/{league_code}/latest.db"
    local_path = os.path.join(tmp_dir, f"{league_code}.db")
    try:
        s3.download_file(bucket, s3_key, local_path)
        logger.info("Downloaded s3://%s/%s (%d bytes)", bucket, s3_key, os.path.getsize(local_path))
        return local_path
    except s3.exceptions.ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code in ("404", "NoSuchKey", "403"):
            logger.warning(
                "Skipping %s (error %s): s3://%s/%s",
                league_code, error_code, bucket, s3_key,
            )
            return None
        raise


def _merge_databases(db_paths: list[str], output_path: str) -> str:
    """
    Read every table from each per-league DB, concatenate, and write into
    one unified SQLite database.
    """
    all_competitions = []
    all_teams = []
    all_matches = []
    all_metrics = []
 
    for db_path in db_paths:
        con = sqlite3.connect(db_path)
        try:
            existing = pd.read_sql(
                "SELECT name FROM sqlite_master WHERE type='table'", con
            )["name"].tolist()
 
            # Read this DB's competitions to build id→code mapping
            if "competitions" not in existing:
                continue
            comp_df = pd.read_sql("SELECT * FROM competitions", con)
            id_to_code = dict(zip(comp_df["id"], comp_df["code"]))
            all_competitions.append(comp_df)
 
            if "teams" in existing:
                all_teams.append(pd.read_sql("SELECT * FROM teams", con))
 
            if "matches" in existing:
                matches_df = pd.read_sql("SELECT * FROM matches", con)
                # Tag each match with its competition code using THIS DB's
                # id→code mapping, so we can remap after merge
                matches_df["_comp_code"] = matches_df["competition_id"].map(id_to_code)
                all_matches.append(matches_df)
 
            if "match_metrics" in existing:
                all_metrics.append(pd.read_sql("SELECT * FROM match_metrics", con))
        finally:
            con.close()
 
    if not all_matches:
        raise RuntimeError("No match data found in any downloaded database.")
 
    # Merge and deduplicate
 
    # Competitions: deduplicate on code, assign fresh unique IDs
    comp_merged = pd.concat(all_competitions, ignore_index=True)
    comp_merged = comp_merged.drop_duplicates(subset=["code"], keep="last").reset_index(drop=True)
    comp_merged["id"] = range(1, len(comp_merged) + 1)
    code_to_new_id = dict(zip(comp_merged["code"], comp_merged["id"]))
 
    # Teams: deduplicate on id
    teams_merged = pd.concat(all_teams, ignore_index=True)
    teams_merged = teams_merged.drop_duplicates(subset=["id"], keep="last")
 
    # Matches: remap competition_id using the fresh IDs, then drop the temp column
    matches_merged = pd.concat(all_matches, ignore_index=True)
    matches_merged = matches_merged.drop_duplicates(subset=["id"], keep="last")
    matches_merged["competition_id"] = matches_merged["_comp_code"].map(code_to_new_id)
    matches_merged = matches_merged.drop(columns=["_comp_code"])
 
    # Metrics: deduplicate on match_id
    if all_metrics:
        metrics_merged = pd.concat(all_metrics, ignore_index=True)
        metrics_merged = metrics_merged.drop_duplicates(subset=["match_id"], keep="last")
    else:
        metrics_merged = pd.DataFrame()
 
    # Write unified database
 
    out_con = sqlite3.connect(output_path)
    try:
        comp_merged.to_sql("competitions", out_con, if_exists="replace", index=False)
        teams_merged.to_sql("teams", out_con, if_exists="replace", index=False)
        matches_merged.to_sql("matches", out_con, if_exists="replace", index=False)
        if not metrics_merged.empty:
            metrics_merged.to_sql("match_metrics", out_con, if_exists="replace", index=False)
 
        cur = out_con.cursor()
        cur.execute("CREATE INDEX IF NOT EXISTS ix_matches_competition ON matches(competition_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_matches_date        ON matches(match_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_matches_home_team   ON matches(home_team_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_matches_away_team   ON matches(away_team_id)")
        if not metrics_merged.empty:
            cur.execute("CREATE INDEX IF NOT EXISTS ix_metrics_match   ON match_metrics(match_id)")
        out_con.commit()
    finally:
        out_con.close()

    logger.info(
        "Merged %d league DBs → %s (%d bytes) | %d competitions, %d teams, %d matches",
        len(db_paths), output_path, os.path.getsize(output_path),
        len(comp_merged), len(teams_merged), len(matches_merged),
    )
    return output_path


def sync_from_s3() -> str:
    """
    Main entry point. Downloads all per-league DBs from S3, merges them
    into one unified file, and returns its path.
    """
    bucket = st.secrets["S3_BUCKET"]
    s3 = _get_s3_client()
 
    tmp_dir = os.path.join(tempfile.gettempdir(), "soccer_sync")
    os.makedirs(tmp_dir, exist_ok=True)
 
    db_paths = []

    for code in LEAGUE_CODES:
        path = _download_league_db(s3, bucket, code, tmp_dir)
        if path:
            db_paths.append(path)

    if not db_paths:
        raise RuntimeError(
            f"No league databases found in s3://{bucket}/{S3_KEY_PREFIX}/*/latest.db. "
            "Has the Lambda pipeline run at least once?"
        )

    output_path = os.path.join(tmp_dir, "merged_soccer.db")
    _merge_databases(db_paths, output_path)
    return output_path