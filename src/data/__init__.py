"""Data ingestion layer — MLB Stats API, Baseball Savant, and odds."""

from src.data.mlb_api import (
    HittingStatsSnapshot,
    MLBStatsAPI,
    PitchingStatsSnapshot,
)
from src.data.odds import (
    CompositeOddsProvider,
    OddsAPIProvider,
    OddsLoader,
    OddsLoaderSettings,
    OddsProvider,
    OddsSettings,
)
from src.data.savant import SavantClient

__all__ = [
    "CompositeOddsProvider",
    "HittingStatsSnapshot",
    "MLBStatsAPI",
    "OddsAPIProvider",
    "OddsLoader",
    "OddsLoaderSettings",
    "OddsProvider",
    "OddsSettings",
    "PitchingStatsSnapshot",
    "SavantClient",
]