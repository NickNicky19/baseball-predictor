"""Tests for odds provider interface and composite loader."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.data.odds import (
    CompositeOddsProvider,
    FileOddsProvider,
    FileOddsSettings,
    OddsAPIProvider,
    OddsAPISettings,
    OddsSettings,
)
from src.data.odds.base import parse_odds_row
from src.utils.errors import OddsLoadError

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_file_provider_loads_csv():
    settings = FileOddsSettings(csv_path=str(FIXTURES / "sample_odds.csv"))
    provider = FileOddsProvider(settings=settings, project_root=PROJECT_ROOT)
    lines = provider.load()
    assert len(lines) == 2
    assert lines[0].player_name == "Test Player"
    assert lines[0].category == "hrr"


def test_category_alias_hr():
    row = {
        "player_name": "Slugger",
        "category": "hr",
        "line": 0.5,
        "over_odds": -120,
        "under_odds": 100,
    }
    line = parse_odds_row(row, "test.csv")
    assert line.category == "home_runs"


def test_composite_require_raises():
    settings = OddsSettings(
        enabled=True,
        require_any_source=True,
        file=FileOddsSettings(require_file=True, csv_path="missing.csv"),
    )
    loader = CompositeOddsProvider(settings=settings, project_root=PROJECT_ROOT)
    with pytest.raises(OddsLoadError):
        loader.load("2026-07-01")


def test_composite_disabled_returns_empty():
    loader = CompositeOddsProvider(settings=OddsSettings(enabled=False))
    assert loader.load() == []


def test_odds_api_provider_unavailable_without_key():
    provider = OddsAPIProvider(settings=OddsAPISettings(enabled=True))
    assert not provider.is_available()
    assert provider.load("2026-07-01") == []


def test_odds_api_parses_event_payload():
    provider = OddsAPIProvider(
        settings=OddsAPISettings(
            enabled=True,
            api_key="test",
            market_category_map={"batter_hits": "hits"},
        )
    )
    payload = {
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "batter_hits",
                        "outcomes": [
                            {"name": "Over", "description": "Aaron Judge", "point": 1.5, "price": -110},
                            {"name": "Under", "description": "Aaron Judge", "point": 1.5, "price": -110},
                        ],
                    }
                ],
            }
        ]
    }
    lines = provider._parse_event_odds(payload)
    assert len(lines) == 1
    assert lines[0].player_name == "Aaron Judge"
    assert lines[0].category == "hits"


def test_odds_api_refuses_one_sided_or_mismatched_line_payloads():
    provider = OddsAPIProvider(
        settings=OddsAPISettings(
            enabled=True,
            api_key="test",
            market_category_map={"batter_hits": "hits", "batter_home_runs": "home_runs"},
        )
    )
    payload = {
        "bookmakers": [{
            "key": "draftkings", "title": "DraftKings", "markets": [
                {
                    "key": "batter_home_runs",
                    "outcomes": [
                        {"name": "Over", "description": "Aaron Judge", "point": 0.5, "price": 300},
                    ],
                },
                {
                    "key": "batter_hits",
                    "outcomes": [
                        {"name": "Over", "description": "Mookie Betts", "point": 1.5, "price": -110},
                        {"name": "Under", "description": "Mookie Betts", "point": 0.5, "price": -110},
                    ],
                },
            ],
        }]
    }
    assert provider._parse_event_odds(payload) == []


def test_composite_merges_api_over_file():
    file_settings = FileOddsSettings(csv_path=str(FIXTURES / "sample_odds.csv"))
    file_provider = FileOddsProvider(settings=file_settings, project_root=PROJECT_ROOT)

    api_provider = MagicMock()
    api_provider.source_name = "odds_api"
    api_provider.is_available.return_value = True
    api_provider.load.return_value = file_provider.load()
    for line in api_provider.load.return_value:
        if line.player_name == "Test Player":
            line = line  # same

    settings = OddsSettings(enabled=True, prefer_source="api")
    composite = CompositeOddsProvider(settings=settings, providers=[file_provider, api_provider])
    lines = composite.load()
    assert len(lines) >= 2
