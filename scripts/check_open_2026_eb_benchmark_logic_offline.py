#!/usr/bin/env python3
"""Offline logic checks for open-2026 benchmark scoring and chronology."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.open_2026_probability_benchmark import (  # noqa: E402
    actual_markets, binary_metrics, paired_interval, period_mask,
    rolling_pa_probabilities,
)


def history_row(player_id: int, hits: int, pa: int = 10) -> dict:
    return {
        "player_id": player_id, "out_pa": pa, "out_ab": pa, "out_hits": hits,
        "out_doubles": 0, "out_triples": 0, "out_hr": 0, "out_bb": 0,
        "out_k": 0,
    }


def main() -> int:
    metrics = binary_metrics(np.array([0, 1]), np.array([0.1, 0.9]))
    assert metrics["binary_brier"] < 0.02 and metrics["binary_log_loss"] < 0.11
    interval = paired_interval(
        np.array([0, 1, 0, 1]), np.array([0.1, 0.9, 0.1, 0.9]),
        np.array([0.4, 0.6, 0.4, 0.6]), pd.Series(["a", "a", "b", "b"]),
        metric="binary_brier", draws=1000, seed=1,
    )
    assert interval["upper"] < 0
    outcomes = pd.DataFrame({"hits": [2], "home_runs": [1], "total_bases": [5]})
    actual = actual_markets(outcomes)
    assert actual["hits_1.5"][0] == 1 and actual["home_runs_0.5"][0] == 1
    assert actual["total_bases_4.5"][0] == 1 and actual["total_bases_5.5"][0] == 0
    dates = pd.Series(["2026-03-25", "2026-04-30", "2026-06-01"])
    assert period_mask(dates, "march_april").tolist() == [True, True, False]
    assert period_mask(dates, "june").tolist() == [False, False, True]

    history = pd.DataFrame([history_row(1, 2), history_row(2, 2)])
    targets = pd.DataFrame([
        {"player_id": 1, "game_date": "2026-03-25"},
        {"player_id": 1, "game_date": "2026-03-26"},
    ])
    official = pd.DataFrame([
        {"player_id": 1, "game_date": "2026-03-25", "pa": 5, "ab": 5, "hits": 5,
         "doubles": 0, "triples": 0, "home_runs": 0, "walks": 0, "strikeouts": 0},
        {"player_id": 1, "game_date": "2026-03-26", "pa": 5, "ab": 5, "hits": 0,
         "doubles": 0, "triples": 0, "home_runs": 0, "walks": 0, "strikeouts": 0},
    ])
    _, before, _ = rolling_pa_probabilities(history, targets, official, prior_strength=200)
    future_changed = official.copy()
    future_changed.loc[future_changed["game_date"] == "2026-03-26", "hits"] = 5
    _, after, _ = rolling_pa_probabilities(history, targets, future_changed, prior_strength=200)
    assert np.array_equal(before[0], after[0]) and np.array_equal(before[1], after[1])
    past_changed = official.copy()
    past_changed.loc[past_changed["game_date"] == "2026-03-25", "hits"] = 0
    _, changed, _ = rolling_pa_probabilities(history, targets, past_changed, prior_strength=200)
    assert np.array_equal(before[0], changed[0]) and not np.array_equal(before[1], changed[1])
    print("9/9")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
