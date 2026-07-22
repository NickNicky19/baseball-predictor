#!/usr/bin/env python3
"""Mutation checks for the per-PA hitter-skill diagnostic."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_pa_counterfactual import (  # noqa: E402
    reject_holdout_dates,
    require_exact_key_set,
)
from src.evaluation.hits_per_pa_diagnostic import (  # noqa: E402
    assign_probability_bands,
    date_block_residual_interval,
    per_pa_metrics,
    sturges_quantile_edges,
    validate_trials,
)


def fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            (1, 10, "2026-03-25", 1, 4, 1, 0.20),
            (2, 11, "2026-03-26", 2, 5, 2, 0.30),
            (3, 12, "2026-03-27", 3, 3, 0, 0.10),
            (4, 13, "2026-03-28", 4, 4, 2, 0.40),
            (5, 14, "2026-03-29", 5, 4, 1, 0.25),
            (6, 15, "2026-03-30", 6, 5, 1, 0.15),
            (7, 16, "2026-03-31", 7, 4, 2, 0.35),
            (8, 17, "2026-04-01", 8, 3, 1, 0.22),
        ],
        columns=[
            "mlb_game_pk", "player_id", "official_game_date",
            "official_lineup_slot", "official_pa", "official_hits",
            "inferred_per_pa_hit_probability",
        ],
    )


def main() -> int:
    base = validate_trials(fixture())
    metrics = per_pa_metrics(base)
    assert metrics["official_pa"] == 32 and metrics["official_hits"] == 10
    print("[OK] exact aggregate per-PA calibration metrics are produced")

    changed_truth = base.copy()
    changed_truth.loc[0, "official_hits"] = 2
    assert per_pa_metrics(changed_truth)["predicted_minus_observed_hit_rate"] != metrics[
        "predicted_minus_observed_hit_rate"
    ]
    print("[OK] MUTATION official hits move the residual metric")

    changed_q = base.copy()
    changed_q.loc[0, "inferred_per_pa_hit_probability"] += 0.05
    assert per_pa_metrics(changed_q)["predicted_minus_observed_hit_rate"] != metrics[
        "predicted_minus_observed_hit_rate"
    ]
    print("[OK] MUTATION inferred q moves the residual metric")

    edges = sturges_quantile_edges(base.inferred_per_pa_hit_probability)
    labels = assign_probability_bands(base.inferred_per_pa_hit_probability, edges)
    outcome_mutation = base.copy()
    outcome_mutation["official_hits"] = 0
    mutated_edges = sturges_quantile_edges(
        outcome_mutation.inferred_per_pa_hit_probability
    )
    assert np.array_equal(edges, mutated_edges)
    assert np.array_equal(
        labels,
        assign_probability_bands(outcome_mutation.inferred_per_pa_hit_probability, mutated_edges),
    )
    print("[OK] selector probability bands are outcome-blind")

    caught = False
    duplicate = pd.concat([base, base.iloc[[0]]], ignore_index=True)
    try:
        validate_trials(duplicate)
    except ValueError as exc:
        caught = "duplicate" in str(exc)
    assert caught
    print("[OK] MUTATION duplicate player-game hard-fails")

    expected = set(map(tuple, base[["mlb_game_pk", "player_id"]].to_numpy()))
    observed = set(map(tuple, base.iloc[:-1][["mlb_game_pk", "player_id"]].to_numpy()))
    caught = False
    try:
        require_exact_key_set(observed, expected, "per-PA fixture")
    except ValueError as exc:
        caught = "key mismatch" in str(exc)
    assert caught
    print("[OK] MUTATION dropped player-game fails exact identity")

    reject_holdout_dates(base.official_game_date.tolist(), "2026-05-01")
    caught = False
    try:
        reject_holdout_dates([*base.official_game_date.tolist(), "2026-05-01"], "2026-05-01")
    except ValueError as exc:
        caught = "holdout" in str(exc)
    assert caught
    print("[OK] MUTATION May injection hard-fails")

    declared = [*base.official_game_date.tolist(), "2026-04-02"]
    with_zero = date_block_residual_interval(
        base, declared_dates=declared, bootstrap=5000, seed=17
    )
    without_zero = date_block_residual_interval(
        base, declared_dates=declared[:-1], bootstrap=5000, seed=17
    )
    assert (with_zero.lower, with_zero.upper) != (without_zero.lower, without_zero.upper)
    print("[OK] MUTATION dropping a zero-exposure date changes bootstrap evidence")
    print("8/8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
