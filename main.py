"""
Command-line entry point: run the ETL pipeline and print the analytics reports.

The data source and target database are chosen by the APP_MODE environment
variable (see config.py): `live` (default) hits the Football-Data.org API and
writes live_soccer.db; `test` uses the deterministic mock generator and writes
test_soccer.db.

Usage:
    python main.py                       # live API  -> live_soccer.db
    APP_MODE=test python main.py         # mock data -> test_soccer.db
    python main.py --analytics-only      # skip ETL, re-run reports on existing DB
    python main.py --verbose             # DEBUG-level logging
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import config
from src.analytics import reports
from src.etl.pipeline import Pipeline


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quieten noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Soccer Match Analytics ETL Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--analytics-only", action="store_true",
        help="Skip ETL; run analytics on the existing database."
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG-level logging."
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(verbose=args.verbose)
    log = logging.getLogger(__name__)

    log.info("Mode: %s | database: %s", config.APP_MODE, config.DB_FILENAME)
    if not args.analytics_only:
        loaded = Pipeline().run()
        log.info("ETL finished — %d matches in this run.", loaded)

    reports.run_all()


if __name__ == "__main__":
    main()