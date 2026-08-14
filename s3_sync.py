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

# Tables to merge, in dependency order (parents before children).
# These match the schema your loader.py creates.
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
        if e.response["Error"]["Code"] == "404":
            logger.warning("Not found: s3://%s/%s (league may not have run yet)", bucket, s3_key)
            return None
        raise


def _merge_databases(db_paths: list[str], output_path: str) -> str:
    """
    Read every table from each per-league DB, concatenate, and write into
    one unified SQLite database. Deduplicates on primary keys so re-runs
    or overlapping data are safe.
    """
    merged: dict[str, pd.DataFrame] = {}

    for db_path in db_paths:
        con = sqlite3.connect(db_path)
        try:
            # Discover which tables actually exist in this DB
            existing = pd.read_sql(
                "SELECT name FROM sqlite_master WHERE type='table'", con
            )["name"].tolist()

            for table in TABLES:
                if table not in existing:
                    continue
                df = pd.read_sql(f"SELECT * FROM {table}", con)
                if table in merged:
                    merged[table] = pd.concat([merged[table], df], ignore_index=True)
                else:
                    merged[table] = df
        finally:
            con.close()

    # Deduplicate each table on its primary key
    pk_map = {
        "competitions": ["code"],      # unique on code, not auto-id
        "teams":        ["id"],
        "matches":      ["id"],
        "match_metrics": ["match_id"],
    }

    out_con = sqlite3.connect(output_path)
    try:
        for table, df in merged.items():
            pk = pk_map.get(table)
            if pk:
                df = df.drop_duplicates(subset=pk, keep="last")
            df.to_sql(table, out_con, if_exists="replace", index=False)

        # Recreate the indexes the queries layer expects
        cur = out_con.cursor()
        cur.execute("CREATE INDEX IF NOT EXISTS ix_matches_competition ON matches(competition_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_matches_date        ON matches(match_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_matches_home_team   ON matches(home_team_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_matches_away_team   ON matches(away_team_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_metrics_match       ON match_metrics(match_id)")
        out_con.commit()
    finally:
        out_con.close()

    logger.info(
        "Merged %d league DBs → %s (%d bytes) | tables: %s",
        len(db_paths), output_path, os.path.getsize(output_path),
        {t: len(df) for t, df in merged.items()},
    )
    return output_path


def sync_from_s3() -> str:
    """
    Main entry point. Downloads all per-league DBs from S3, merges them
    into one unified file, and returns its path.

    The merged DB is written to a persistent temp location so it survives
    across Streamlit reruns within the same server process.
    """
    bucket = st.secrets["S3_BUCKET"]
    s3 = _get_s3_client()

    tmp_dir = tempfile.mkdtemp(prefix="soccer_sync_")
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