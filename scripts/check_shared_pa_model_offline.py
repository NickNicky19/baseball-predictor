#!/usr/bin/env python3
"""Offline mathematical checks for shared PA probabilities and baselines."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.learning.shared_pa_model import (  # noqa: E402
    binary_class_metrics,
    derived_market_probabilities,
    fit_rate_baseline,
    fit_temperature,
    paired_date_block_interval,
    proper_scores,
    temperature_scale,
    weighted_event_rows,
)


def main() -> int:
    frame = pd.DataFrame([
        {"player_id": 1, "lineup_slot": 1, "x": 2.0, "out_pa": 4, "out_ab": 3,
         "out_hits": 1, "out_doubles": 0, "out_triples": 0, "out_hr": 0,
         "out_bb": 1, "out_k": 1},
        {"player_id": 2, "lineup_slot": 2, "x": 3.0, "out_pa": 4, "out_ab": 4,
         "out_hits": 2, "out_doubles": 1, "out_triples": 0, "out_hr": 0,
         "out_bb": 0, "out_k": 1},
    ])
    league = fit_rate_baseline(frame, kind="league_rate")
    probs, fallback = league.predict(frame)
    assert probs.shape == (2, len(PA_OUTCOMES)) and np.allclose(probs.sum(axis=1), 1.0)
    assert fallback.all()
    zero_exposure = frame.iloc[[0]].copy()
    zero_exposure.loc[:, ["out_pa", "out_ab", "out_hits", "out_doubles", "out_triples", "out_hr", "out_bb", "out_k"]] = 0
    mixed = pd.concat([frame, zero_exposure], ignore_index=True)
    mixed_model = fit_rate_baseline(mixed, kind="league_rate")
    assert np.allclose(mixed_model.league_probability, league.league_probability)
    print("[OK] zero-PA source rows have exactly zero training exposure")
    score = proper_scores(
        pd.DataFrame([
            [1, 1, 1, 0, 0, 0, 1, 0],
            [1, 0, 1, 1, 0, 0, 1, 0],
        ], columns=PA_OUTCOMES),
        probs,
    )
    assert score["multiclass_log_loss"] > 0 and score["multiclass_brier"] > 0
    class_metrics = binary_class_metrics(
        pd.DataFrame([
            [1, 1, 1, 0, 0, 0, 1, 0],
            [1, 0, 1, 1, 0, 0, 1, 0],
        ], columns=PA_OUTCOMES),
        probs,
    )
    assert class_metrics["single"]["sufficient_evidence"] is True
    assert class_metrics["triple"]["sufficient_evidence"] is False
    print("[OK] exact per-class scoring flags degenerate evidence")
    events, labels, weights = weighted_event_rows(frame, ["x"])
    assert len(events) == len(labels) == len(weights) == 8
    assert weights.sum() == 8
    assert np.allclose(temperature_scale(probs, 1.0), probs)
    fitted_temperature = fit_temperature(
        pd.DataFrame([
            [1, 1, 1, 0, 0, 0, 1, 0],
            [1, 0, 1, 1, 0, 0, 1, 0],
        ], columns=PA_OUTCOMES),
        probs,
        log_temperature_bounds=(-2.0, 2.0),
    )
    assert np.isfinite(fitted_temperature) and fitted_temperature > 0
    interval = paired_date_block_interval(
        pd.DataFrame([
            [1, 1, 1, 0, 0, 0, 1, 0],
            [1, 0, 1, 1, 0, 0, 1, 0],
        ], columns=PA_OUTCOMES),
        probs,
        np.roll(probs, 1, axis=1),
        pd.Series(["2024-04-01", "2024-04-02"]),
        metric="log_loss",
        draws=100,
        seed=1,
    )
    assert interval["dates"] == 2
    market = derived_market_probabilities(
        probs,
        pd.Series([1, 2]),
        {"1": {"4": 1.0}, "2": {"4": 1.0}},
    )
    assert market["hits_0.5"][0] >= market["hits_1.5"][0]
    assert market["total_bases_0.5"][0] >= market["total_bases_5.5"][0]
    print("[OK] coherent probabilities, exact weighted expansion, and proper scores")

    bad = probs.copy()
    bad[0, 0] += 0.1
    try:
        proper_scores(pd.DataFrame(np.ones((2, 8)), columns=PA_OUTCOMES), bad)
    except ValueError:
        print("[OK] MUTATION non-normalized probabilities fail")
    else:
        raise AssertionError("non-normalized probabilities passed")
    try:
        temperature_scale(probs, 0.0)
    except ValueError:
        print("[OK] MUTATION nonpositive temperature fails")
    else:
        raise AssertionError("nonpositive temperature passed")
    try:
        fit_temperature(
            pd.DataFrame(np.ones((2, 8)), columns=PA_OUTCOMES),
            probs,
            log_temperature_bounds=(2.0, -2.0),
        )
    except ValueError:
        print("[OK] MUTATION invalid temperature bounds fail")
    else:
        raise AssertionError("invalid temperature bounds passed")
    print("[OK] exact PA-volume mixture produces monotone market tails")
    print("8/8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
