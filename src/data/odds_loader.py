"""Backward-compatible re-exports — use src.data.odds for new code."""

from src.data.odds import (
    CompositeOddsProvider,
    FileOddsProvider,
    OddsAPIProvider,
    OddsLoader,
    OddsLoaderSettings,
    OddsProvider,
    OddsSettings,
)
from src.data.odds.base import CATEGORY_ALIASES, parse_odds_row

# Legacy alias used in tests
_parse_row = parse_odds_row

__all__ = [
    "CATEGORY_ALIASES",
    "CompositeOddsProvider",
    "FileOddsProvider",
    "OddsAPIProvider",
    "OddsLoader",
    "OddsLoaderSettings",
    "OddsProvider",
    "OddsSettings",
    "_parse_row",
    "parse_odds_row",
]