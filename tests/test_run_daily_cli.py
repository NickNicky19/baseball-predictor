"""Tests for run_daily CLI helpers."""

import json
from datetime import date

from run_daily import (
    category_key,
    parse_args,
    projection_rows,
    resolve_corrections,
    resolve_edges,
    value_play_rows,
)
from src.models.dataclasses import DailyPrediction, EdgeRecommendation, EdgeResult, PropProjection


def test_category_key_aliases():
    assert category_key("hr") == "home_runs"
    assert category_key("hrr") == "hrr"


def test_parse_args_defaults():
    args = parse_args(["--date", "2026-07-01"])
    assert args.date == "2026-07-01"
    assert args.category == "hrr"
    assert resolve_corrections(args) is None
    assert resolve_edges(args) is None


def test_parse_args_corrections_flags():
    args_on = parse_args(["--apply-corrections"])
    args_off = parse_args(["--no-corrections"])
    assert resolve_corrections(args_on) is True
    assert resolve_corrections(args_off) is False


def test_projection_rows_shape():
    proj = PropProjection(1, "Test", "hrr", "2026-07-01", 2.1, 0.7)
    rows = projection_rows([proj], "2026-07-01")
    assert rows[0]["player"] == "Test"
    assert rows[0]["projected"] == 2.1


def test_value_play_rows():
    edge = EdgeResult(
        player_name="Test",
        category="hrr",
        line=1.5,
        projected_value=2.2,
        implied_prob_over=0.52,
        model_prob_over=0.62,
        edge_pct=10.0,
        recommendation=EdgeRecommendation.LEAN_OVER,
        confidence=0.7,
    )
    prediction = DailyPrediction(game_date=date(2026, 7, 1), value_plays=[edge])
    rows = value_play_rows(prediction)
    assert rows[0]["edge_pct"] == 10.0