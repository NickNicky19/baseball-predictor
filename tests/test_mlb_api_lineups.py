"""Tests for MLB API projected lineup parsing."""

from __future__ import annotations

import pytest

from src.data.mlb_api import MLBStatsAPI, _projected_order_from_game
from src.utils.errors import DataFetchError


def test_projected_order_from_schedule_lineups():
    game = {
        "lineups": {
            "awayPlayers": [{"id": 101}, {"id": 102}],
            "homePlayers": [{"id": 201}, {"id": 202}, {"id": 203}],
        }
    }
    assert _projected_order_from_game(game, "away") == [101, 102]
    assert _projected_order_from_game(game, "home") == [201, 202, 203]


def test_projected_order_handles_missing_or_malformed():
    assert _projected_order_from_game({}, "away") == []
    assert _projected_order_from_game({"lineups": "bad"}, "home") == []
    assert _projected_order_from_game({"lineups": {"awayPlayers": [{"id": None}]}}, "away") == []


def test_schedule_projected_lineup_source_is_quarantined_before_network_access():
    with pytest.raises(DataFetchError, match="REFUSING schedule-hydrated projected lineups"):
        MLBStatsAPI().get_hitters_for_date("2026-07-24", include_projected=True)
