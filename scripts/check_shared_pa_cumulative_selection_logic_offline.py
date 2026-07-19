#!/usr/bin/env python3
"""Offline mutations for cumulative-selection joins and comparison gates."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_cumulative_selection import (  # noqa: E402
    aligned_v1_probabilities,
    assert_expected_simple,
    clears_all_comparisons,
    feature_columns,
    validate_pa_distribution_payload,
)


def expect_failure(fn: object) -> bool:
    try:
        fn()  # type: ignore[operator]
    except (KeyError, TypeError, ValueError):
        return True
    return False


def comparator(rows: int = 2) -> pd.DataFrame:
    frame = pd.DataFrame({
        "game_pk": [11, 12][:rows],
        "player_id": [101, 102][:rows],
        "game_date": ["2024-04-01", "2024-04-02"][:rows],
        "lineup_slot": [1, 2][:rows],
        "_selection_fold": [0, 0][:rows],
    })
    probability = np.asarray([0.20, 0.08, 0.18, 0.05, 0.01, 0.03, 0.42, 0.03])
    for index, outcome in enumerate(PA_OUTCOMES):
        frame[f"canonical_selected_{outcome}"] = probability[index]
    return frame


def main() -> int:
    protocol = json.loads((ROOT / "config/shared_pa_cumulative_selection_protocol.json").read_text(encoding="utf-8"))
    core = feature_columns(protocol, "canonical_recent_plus_cumulative")
    context = feature_columns(protocol, "canonical_recent_plus_cumulative_context")
    simple_floor = {"multiclass_log_loss": 0.012, "multiclass_brier": 0.004}
    simple_pass = {
        "multiclass_log_loss": {"point": -0.014, "upper": -0.013},
        "multiclass_brier": {"point": -0.006, "upper": -0.005},
    }
    simple_fail = copy.deepcopy(simple_pass)
    simple_fail["multiclass_log_loss"]["upper"] = -0.011
    v1_pass = {
        "multiclass_log_loss": {"point": -0.002, "upper": -0.001},
        "multiclass_brier": {"point": -0.001, "upper": -0.0001},
    }
    v1_fail = copy.deepcopy(v1_pass)
    v1_fail["multiclass_brier"]["upper"] = 0.0001
    expected_simple = {
        "selected": "empirical_bayes_player_rate_pa_200",
        "empirical_bayes_player_rate_pa_200": {
            "scores": protocol["simple_baseline_selection"]["required_exact_scores"],
        },
    }
    validation = comparator()[["game_pk", "player_id", "game_date", "lineup_slot", "_selection_fold"]]
    aligned = aligned_v1_probabilities(validation, comparator())
    reordered = comparator().iloc[::-1].reset_index(drop=True)
    missing = comparator(rows=1)
    wrong_date = comparator()
    wrong_date.loc[0, "game_date"] = "2024-04-03"
    wrong_simple = copy.deepcopy(expected_simple)
    wrong_simple["empirical_bayes_player_rate_pa_200"]["scores"]["multiclass_brier"] += 1e-10
    valid_distribution = {
        "by_lineup_slot": {
            str(slot): {"3": 0.25, "4": 0.75}
            for slot in range(1, 10)
        }
    }
    missing_slot = copy.deepcopy(valid_distribution)
    missing_slot["by_lineup_slot"].pop("9")
    bad_sum = copy.deepcopy(valid_distribution)
    bad_sum["by_lineup_slot"]["9"] = {"3": 0.25, "4": 0.70}
    checks = [
        ("core exact size", len(core) == 46),
        ("context exact addition", set(context) - set(core) == {"bats", "is_home", "venue"}),
        ("valid dual comparison accepted", clears_all_comparisons(simple_intervals=simple_pass, simple_floor=simple_floor, v1_intervals=v1_pass)[0]),
        ("simple materiality failure rejected", not clears_all_comparisons(simple_intervals=simple_fail, simple_floor=simple_floor, v1_intervals=v1_pass)[0]),
        ("v1 superiority failure rejected", not clears_all_comparisons(simple_intervals=simple_pass, simple_floor=simple_floor, v1_intervals=v1_fail)[0]),
        ("exact simple accepted", not expect_failure(lambda: assert_expected_simple(expected_simple, protocol))),
        ("simple score drift rejected", expect_failure(lambda: assert_expected_simple(wrong_simple, protocol))),
        ("v1 aligned exact shape", aligned.shape == (2, len(PA_OUTCOMES))),
        ("v1 left order preserved", np.allclose(aligned_v1_probabilities(validation, reordered), aligned)),
        ("v1 missing coverage rejected", expect_failure(lambda: aligned_v1_probabilities(validation, missing))),
        ("v1 chronology mismatch rejected", expect_failure(lambda: aligned_v1_probabilities(validation, wrong_date))),
        ("PA wrapper extracted", set(validate_pa_distribution_payload(valid_distribution)) == {str(slot) for slot in range(1, 10)}),
        ("PA slot omission rejected", expect_failure(lambda: validate_pa_distribution_payload(missing_slot))),
        ("PA probability sum rejected", expect_failure(lambda: validate_pa_distribution_payload(bad_sum))),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"cumulative selection logic checks failed: {failed}")
    print(f"CUMULATIVE SELECTION LOGIC VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
