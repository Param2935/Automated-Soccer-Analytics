"""
End-to-end ETL orchestration: extract raw matches, transform into clean records
with derived metrics, and load them into the database.
"""

import logging

import config
from src.etl.extractor import Extractor
from src.etl.loader import Loader
from src.etl.transformer import Transformer

logger = logging.getLogger(__name__)


class Pipeline:
    """Wires the extractor, transformer, and loader into one runnable flow."""

    def __init__(self, use_mock: bool = config.USE_MOCK):
        self.extractor = Extractor(use_mock=use_mock)
        self.transformer = Transformer()
        self.loader = Loader()

    def run(self) -> int:
        """Run extract → transform → load and return the match count loaded."""
        raw_matches = self.extractor.extract()
        if not raw_matches:
            logger.warning("Extraction returned no matches; skipping load.")
            return 0

        matches = self.transformer.transform(raw_matches)
        teams = self.transformer.get_teams(matches)
        self.loader.load(matches, teams)
        return len(matches)
