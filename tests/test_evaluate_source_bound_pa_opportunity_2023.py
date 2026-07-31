from __future__ import annotations

from scripts.evaluate_source_bound_pa_opportunity_2023 import distribution, score


def test_slot_conditioning_improves_separated_synthetic_opportunity() -> None:
    training = [
        *[{"out_pa": 5, "lineup_slot": 1} for _ in range(10)],
        *[{"out_pa": 3, "lineup_slot": 9} for _ in range(10)],
    ]
    validation = [{"out_pa": 5, "lineup_slot": 1}, {"out_pa": 3, "lineup_slot": 9}]
    pooled = distribution(training)
    by_slot = {
        slot: distribution(row for row in training if row["lineup_slot"] == slot)
        for slot in (1, 9)
    }
    pooled_score = score(validation, lambda row: pooled)
    slot_score = score(validation, lambda row: by_slot[row["lineup_slot"]])
    assert slot_score["multiclass_brier"] < pooled_score["multiclass_brier"]
    assert slot_score["multiclass_log_loss"] < pooled_score["multiclass_log_loss"]
    assert slot_score["expected_pa_mae"] < pooled_score["expected_pa_mae"]
