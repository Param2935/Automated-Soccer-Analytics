"""
lambda_function.py

STAGGERED LEAGUE DESIGN

Each EventBridge rule passes a JSON payload naming ONE league:

    {"league_code": "PL", "league_name": "Premier League"}

The handler extracts + transforms + loads only that league, then uploads the
resulting SQLite DB to S3. Five rules fire 15 minutes apart, so each invocation
stays well under Lambda's 15-minute ceiling.

A sixth "all leagues" fallback exists for manual testing: send {} or omit
league_code, and it processes every league in one shot (fine for mock mode).

ENVIRONMENT VARIABLES  (set in the Lambda console)
    APP_MODE           test | live
    S3_BUCKET          your bucket name
    S3_KEY_PREFIX      folder prefix (default: databases)
    FOOTBALL_API_KEY   required for live mode
    SEASON             e.g. 2025
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

import boto3

sys.path.insert(0, os.path.dirname(__file__))

# Override config beofre importing 
os.environ.setdefault("APP_MODE", "test")

import config

# Force the database into /tmp.
config.DB_PATH = "/tmp/soccer_data.db"
config.DB_FILENAME = "soccer_data.db"
config.DATABASE_URL = f"sqlite:///{config.DB_PATH}"

from src.etl.extractor import Extractor
from src.etl.transformer import Transformer
from src.etl.loader import Loader

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

S3_BUCKET  = os.environ["S3_BUCKET"]
KEY_PREFIX = os.environ.get("S3_KEY_PREFIX", "databases").strip("/")


def upload_to_s3(local_path: str, league_code: str | None = None):
    """
    Upload the SQLite DB to S3 with two keys:
      - A dated key for history:   databases/PL/soccer_2026-08-13.db
      - A 'latest' key for easy retrieval: databases/PL/latest.db
    When processing all leagues, the subfolder is 'all'.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tag = league_code or "all"
    dated_key  = f"{KEY_PREFIX}/{tag}/soccer_{today}.db"
    latest_key = f"{KEY_PREFIX}/{tag}/latest.db"

    s3.upload_file(local_path, S3_BUCKET, dated_key)
    s3.upload_file(local_path, S3_BUCKET, latest_key)

    logger.info("Uploaded to s3://%s/%s  and  …/%s", S3_BUCKET, dated_key, latest_key)
    return dated_key, latest_key


def lambda_handler(event, context):
    """
    Entry point for AWS Lambda.

    event examples:
      {"league_code": "PL",  "league_name": "Premier League"}  - single league
      {}                                                       - all leagues (mock/test)
    """
    league_code = event.get("league_code")
    league_name = event.get("league_name")

    if league_code:
        competitions = {league_code: league_name or config.COMPETITIONS.get(league_code, league_code)}
        logger.info("Processing single league: %s (%s)", league_code, competitions[league_code])
    else:
        competitions = None  # signals "use all from config.COMPETITIONS"
        logger.info("Processing ALL leagues (mode=%s)", config.APP_MODE)

    # Clean up any leftover DB from a prior warm invocation
    if os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)

    # Extract
    extractor = Extractor(use_mock=config.USE_MOCK)
    raw_matches = extractor.extract(competitions=competitions)

    if not raw_matches:
        logger.warning("Extraction returned 0 matches; nothing to load.")
        return {
            "statusCode": 204,
            "body": json.dumps({"matches_loaded": 0, "league": league_code or "all"}),
        }

    #Transform
    transformer = Transformer()
    df = transformer.transform(raw_matches)
    teams_df = transformer.get_teams(df)

    #Load into /tmp SQLite
    loader = Loader(db_url=config.DATABASE_URL)
    loader.load(df, teams_df)

    #Upload to S3
    dated_key, latest_key = upload_to_s3(config.DB_PATH, league_code)

    result = {
        "statusCode": 200,
        "body": json.dumps({
            "mode": config.APP_MODE,
            "league": league_code or "all",
            "matches_loaded": len(df),
            "teams": len(teams_df),
            "s3_bucket": S3_BUCKET,
            "s3_key": dated_key,
            "latest_key": latest_key,
            "db_size_bytes": os.path.getsize(config.DB_PATH),
        }),
    }
    logger.info("Done: %s", result["body"])
    return result