"""Data ingestion layer — MLB Stats API, Baseball Savant, and odds.

Keep package initialization side-effect free.  The raw Statcast source path
imports a focused submodule and must not load the unrelated MLB API client (or
its ``requests`` dependency) merely because Python initializes ``src.data``.
The public names remain available through lazy attribute resolution below.
"""

from importlib import import_module


_EXPORT_MODULES = {
    "HittingStatsSnapshot": "src.data.mlb_api",
    "MLBStatsAPI": "src.data.mlb_api",
    "PitchingStatsSnapshot": "src.data.mlb_api",
    "CompositeOddsProvider": "src.data.odds",
    "OddsAPIProvider": "src.data.odds",
    "OddsLoader": "src.data.odds",
    "OddsLoaderSettings": "src.data.odds",
    "OddsProvider": "src.data.odds",
    "OddsSettings": "src.data.odds",
    "SavantClient": "src.data.savant",
}

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


def __getattr__(name: str):
    """Resolve legacy public exports only when a caller actually requests one."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value
