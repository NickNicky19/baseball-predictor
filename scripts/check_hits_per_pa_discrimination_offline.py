#!/usr/bin/env python3
"""Mutation checks for selector-fitted per-PA discrimination controls."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_per_pa_diagnostic import (  # noqa: E402
    add_selector_baseline_probabilities,
    fit_selector_baselines,
    paired_date_block_score_interval,
)
from scripts.check_hits_per_pa_skill_offline import fixture  # noqa: E402


def main() -> int:
    rows = fixture()
    selector_dates = rows.official_game_date.iloc[:4].tolist()
    confirmation_dates = rows.official_game_date.iloc[4:].tolist()
    # Ensure every lineup slot exists in selector without inventing another
    # baseline rule; this is a boundary fixture, not a statistical sample.
    expanded = pd.concat(
        [rows, *[rows.iloc[[i % 4]].assign(
            mlb_game_pk=100 + slot,
            player_id=200 + slot,
            official_lineup_slot=slot,
        ) for i, slot in enumerate(range(5, 10))]],
        ignore_index=True,
    )
    selector_dates = sorted(expanded.official_game_date.unique())[:4]
    confirmation_dates = sorted(expanded.official_game_date.unique())[4:]
    global_rate, slot_rates = fit_selector_baselines(
        expanded,
        selector_dates=selector_dates,
        confirmation_dates=confirmation_dates,
    )
    scored = add_selector_baseline_probabilities(expanded, global_rate, slot_rates)
    assert 0.0 <= global_rate <= 1.0 and set(slot_rates) == set(range(1, 10))
    print("[OK] selector-only global and slot baselines fit all nine slots")

    changed_confirmation = expanded.copy()
    changed_confirmation.loc[
        changed_confirmation.official_game_date.isin(confirmation_dates), "official_hits"
    ] = 0
    mutated_global, mutated_slots = fit_selector_baselines(
        changed_confirmation,
        selector_dates=selector_dates,
        confirmation_dates=confirmation_dates,
    )
    assert mutated_global == global_rate and mutated_slots == slot_rates
    print("[OK] MUTATION confirmation outcomes cannot move selector baselines")

    confirmation = scored[scored.official_game_date.isin(confirmation_dates)].copy()
    confirmation["candidate_equal_baseline"] = confirmation.selector_global_probability
    zero = paired_date_block_score_interval(
        confirmation,
        candidate_column="candidate_equal_baseline",
        baseline_column="selector_global_probability",
        metric="brier",
        declared_dates=confirmation_dates,
        bootstrap=2000,
        seed=17,
    )
    assert zero.lower == zero.upper == 0.0
    print("[OK] candidate equal to baseline yields exactly zero paired difference")

    changed_scored = add_selector_baseline_probabilities(
        changed_confirmation, global_rate, slot_rates
    )
    changed_confirmation_scored = changed_scored[
        changed_scored.official_game_date.isin(confirmation_dates)
    ]
    base_interval = paired_date_block_score_interval(
        confirmation,
        candidate_column="inferred_per_pa_hit_probability",
        baseline_column="selector_global_probability",
        metric="log_loss",
        declared_dates=confirmation_dates,
        bootstrap=2000,
        seed=17,
    )
    changed_interval = paired_date_block_score_interval(
        changed_confirmation_scored,
        candidate_column="inferred_per_pa_hit_probability",
        baseline_column="selector_global_probability",
        metric="log_loss",
        declared_dates=confirmation_dates,
        bootstrap=2000,
        seed=17,
    )
    assert (base_interval.lower, base_interval.upper) != (
        changed_interval.lower,
        changed_interval.upper,
    )
    print("[OK] MUTATION confirmation truth moves paired score evidence")
    print("4/4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
