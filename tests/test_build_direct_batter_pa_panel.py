from __future__ import annotations

import pandas as pd
import pytest

from scripts.build_direct_batter_pa_panel import apply_raw_outcome_truth, outcome_counts, raw_terminal_outcome_counts
from scripts.validate_direct_batter_pa_panel import validate_batted_ball_lineage


def test_official_outcome_decomposition() -> None:
    frame = pd.DataFrame([{
        "out_pa": 6, "out_ab": 5, "out_hits": 3, "out_doubles": 1,
        "out_triples": 0, "out_hr": 1, "out_bb": 1, "out_k": 1,
    }])
    out = outcome_counts(frame).iloc[0]
    assert out.to_dict() == {
        "strikeout": 1, "walk": 1, "single": 1, "double": 1,
        "triple": 0, "home_run": 1, "bip_out": 1, "other_non_ab": 0,
    }


def test_negative_outcome_decomposition_fails() -> None:
    frame = pd.DataFrame([{
        "out_pa": 1, "out_ab": 1, "out_hits": 0, "out_doubles": 1,
        "out_triples": 0, "out_hr": 0, "out_bb": 0, "out_k": 0,
    }])
    with pytest.raises(ValueError, match="negative"):
        outcome_counts(frame)


def test_raw_truth_repairs_old_zero_denominator_and_consumer_reads_it() -> None:
    official = pd.DataFrame([{"game_pk": 746942, "player_id": 643376, "season": 2024, "game_date": "2024-06-26", "lineup_slot": 7,
        "out_pa": 0, "out_ab": 0, "out_hits": 0, "out_doubles": 0, "out_triples": 0, "out_hr": 0, "out_bb": 0, "out_k": 0,
        **{f"target_{name}": 0 for name in ("strikeout", "walk", "single", "double", "triple", "home_run", "bip_out", "other_non_ab")}}])
    raw = pd.DataFrame([
        {"game_pk": 746942, "batter": 643376, "game_type": "R", "events": event, "at_bat_number": number, "pitch_number": 1}
        for number, event in enumerate(["single", "strikeout", "field_out", "field_out"], 1)
    ])
    counts = raw_terminal_outcome_counts(raw, player_id=643376)
    repaired, mismatches = apply_raw_outcome_truth(official, counts)
    assert mismatches == 1
    assert repaired.loc[0, "out_pa"] == 4
    assert repaired.loc[0, "out_hits"] == 1
    assert outcome_counts(repaired).iloc[0].to_dict() == {"strikeout": 1, "walk": 0, "single": 1, "double": 0, "triple": 0, "home_run": 0, "bip_out": 2, "other_non_ab": 0}


def test_duplicate_terminal_pa_mutation_fails_closed() -> None:
    raw = pd.DataFrame([
        {"game_pk": 1, "batter": 7, "game_type": "R", "events": "single", "at_bat_number": 1, "pitch_number": pitch}
        for pitch in (3, 4)
    ])
    with pytest.raises(ValueError, match="duplicated"):
        raw_terminal_outcome_counts(raw, player_id=7)


def test_serialized_batted_ball_lineage_accepts_shared_denominator() -> None:
    validate_batted_ball_lineage(pd.DataFrame([{
        "history_batted_ball_denominator": 100,
        "history_barrel_count": 8,
        "history_hard_hit_count": 42,
        "history_barrel_rate": 0.08,
        "history_hard_hit_rate": 0.42,
    }]))


@pytest.mark.parametrize("mutation, match", [
    ({"history_barrel_count": 50}, "count ordering"),
    ({"history_barrel_rate": 0.50}, "barrel count/rate"),
    ({"history_hard_hit_rate": None}, "partial"),
    ({"history_hard_hit_rate": "bad"}, "nonnumeric"),
])
def test_serialized_batted_ball_lineage_mutations_fail_closed(mutation, match) -> None:
    row = {
        "history_batted_ball_denominator": 100,
        "history_barrel_count": 8,
        "history_hard_hit_count": 42,
        "history_barrel_rate": 0.08,
        "history_hard_hit_rate": 0.42,
    }
    row.update(mutation)
    with pytest.raises(ValueError, match=match):
        validate_batted_ball_lineage(pd.DataFrame([row]))
