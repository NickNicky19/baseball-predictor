#!/usr/bin/env python3
"""Mutation checks for strict-market residual construction."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.market_residuals import (  # noqa: E402
    PROBABILITY_EDGES,
    build_scored_pairs,
    global_metrics,
    reliability_bins,
)


def frames():
    market = pd.DataFrame([
        (100, 10, "hits", 0.5, "2026-06-01", 0.55),
        (100, 10, "hits", 1.5, "2026-06-01", 0.35),
        (101, 11, "hits", 0.5, "2026-06-02", 0.50),
    ], columns=["mlb_game_pk", "player_id", "category", "line",
                "official_game_date", "entry_p_over"])
    frozen = pd.DataFrame([
        (100, 10, "hits", 0.5, "2026-06-01", 0.60),
        (100, 10, "hits", 1.5, "2026-06-01", 0.30),
        (101, 11, "hits", 0.5, "2026-06-02", 0.45),
    ], columns=["mlb_game_pk", "player_id", "category", "line",
                "game_date", "sim_p_over"])
    candidate = frozen.copy()
    candidate.loc[:, "sim_p_over"] = [0.70, 0.25, 0.40]
    official = pd.DataFrame([
        (100, 10, "hits", "2026-06-01", 1.0),
        (101, 11, "hits", "2026-06-02", 0.0),
    ], columns=["mlb_game_pk", "player_id", "category", "game_date", "actual_value"])
    return market, frozen, candidate, official


def main() -> int:
    market, frozen, candidate, official = frames()
    pairs = build_scored_pairs(market, frozen, candidate, official)
    assert len(pairs) == 3
    assert pairs.over_outcome.tolist() == [1, 0, 0]
    assert np.allclose(PROBABILITY_EDGES, np.linspace(0.0, 1.0, 11))
    assert set(reliability_bins(pairs, "frozen").line) == {0.5, 1.5}
    print("[OK] exact strict keys join to MLB truth without losing either line")
    print("[OK] reliability metadata uses probability edges 0.0 through 1.0")

    base_brier = global_metrics(pairs, "frozen")["brier"]
    # M1: perturbing official truth must move the residual metric.  A metric
    # that ignores actual outcomes cannot diagnose calibration.
    altered = official.copy()
    altered.loc[altered.mlb_game_pk.eq(101), "actual_value"] = 1.0
    altered_pairs = build_scored_pairs(market, frozen, candidate, altered)
    assert global_metrics(altered_pairs, "frozen")["brier"] != base_brier
    print("[OK] MUTATION official target changes the calibration metric — CAUGHT")

    # M2: deleting one candidate row must fail rather than inner-joining the
    # clean remainder and producing a deceptively tidy residual table.
    caught = False
    try:
        build_scored_pairs(market, frozen, candidate.iloc[:-1], official)
    except ValueError as exc:
        caught = "MODEL_KEY" in str(exc) or "model arms" in str(exc)
    assert caught
    print("[OK] MUTATION missing candidate key hard-fails — no silent subset")

    # M3: a date disagreement is not a display issue; it invalidates the block
    # universe used by the market A/B bootstrap.
    wrong_date = frozen.copy()
    wrong_date.loc[0, "game_date"] = "2026-06-03"
    caught = False
    try:
        build_scored_pairs(market, wrong_date, candidate, official)
    except ValueError as exc:
        caught = "dates disagree" in str(exc)
    assert caught
    print("[OK] MUTATION canonical-date disagreement hard-fails")
    print("4/4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
