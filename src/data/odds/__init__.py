"""Odds ingestion — extensible provider interface for file and API sources."""

from src.data.odds.base import FileOddsSettings, OddsAPISettings, OddsProvider, OddsSettings
from src.data.odds.composite import CompositeOddsProvider
from src.data.odds.file_provider import FileOddsProvider
from src.data.odds.odds_api_provider import OddsAPIProvider

# Backward-compatible aliases from Phase 6
OddsLoader = CompositeOddsProvider
OddsLoaderSettings = OddsSettings

__all__ = [
    "CompositeOddsProvider",
    "FileOddsProvider",
    "FileOddsSettings",
    "OddsAPIProvider",
    "OddsAPISettings",
    "OddsLoader",
    "OddsLoaderSettings",
    "OddsProvider",
    "OddsSettings",
]