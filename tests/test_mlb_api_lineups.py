"""Tests for MLB API projected lineup parsing."""

from __future__ import annotations

from src.data.mlb_api import _projected_order_from_game


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