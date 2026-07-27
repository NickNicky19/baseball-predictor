from __future__ import annotations

import pandas as pd
import pytest

from scripts.build_direct_batter_pa_panel import apply_raw_outcome_truth, outcome_counts, raw_terminal_outcome_counts
from scripts.validate_direct_batter_pa_panel import (
    validate_batted_ball_lineage, validate_feature_denominator_lineage,
)


ZERO_COLUMNS = [
    "season", "game_date", "game_pk", "player_id", "verified_zero_pa",
    "source_kind", "source_sha256",
]


def raw_row(**overrides) -> dict:
    row = {
        "season": 2023, "game_date": "2023-06-26", "game_pk": 746942,
        "batter": 643376, "game_type": "R", "events": "single",
        "at_bat_number": 1, "pitch_number": 1, "description": "hit_into_play",
        "type": "X", "zone": 5, "pitch_type": "FF", "release_speed": 95.0,
        "pfx_x": 0.1, "pfx_z": 1.2, "plate_x": 0.0, "plate_z": 2.5,
        "launch_speed": 100.0, "launch_angle": 20.0, "launch_speed_angle": 6,
    }
    row.update(overrides)
    return row


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
    official = pd.DataFrame([{"game_pk": 746942, "player_id": 643376, "season": 2023, "game_date": "2023-06-26", "lineup_slot": 7,
        "out_pa": 0, "out_ab": 0, "out_hits": 0, "out_doubles": 0, "out_triples": 0, "out_hr": 0, "out_bb": 0, "out_k": 0,
        **{f"target_{name}": 0 for name in ("strikeout", "walk", "single", "double", "triple", "home_run", "bip_out", "other_non_ab")}}])
    raw = pd.DataFrame([
        raw_row(events=event, at_bat_number=number)
        for number, event in enumerate(["single", "strikeout", "field_out", "field_out"], 1)
    ])
    counts = raw_terminal_outcome_counts(raw, player_id=643376)
    repaired, mismatches = apply_raw_outcome_truth(
        official, counts, pd.DataFrame(columns=ZERO_COLUMNS),
    )
    assert mismatches == 1
    assert repaired.loc[0, "out_pa"] == 4
    assert repaired.loc[0, "out_hits"] == 1
    assert outcome_counts(repaired).iloc[0].to_dict() == {"strikeout": 1, "walk": 0, "single": 1, "double": 0, "triple": 0, "home_run": 0, "bip_out": 2, "other_non_ab": 0}


def test_duplicate_terminal_pa_mutation_fails_closed() -> None:
    raw = pd.DataFrame([
        raw_row(game_pk=1, batter=7, events="single", at_bat_number=1, pitch_number=pitch)
        for pitch in (3, 4)
    ])
    with pytest.raises(ValueError, match="duplicated"):
        raw_terminal_outcome_counts(raw, player_id=7)


def test_terminal_event_on_nonfinal_pitch_mutation_fails_closed() -> None:
    raw = pd.DataFrame([
        raw_row(game_pk=1, batter=7, events="single", at_bat_number=1, pitch_number=1),
        raw_row(game_pk=1, batter=7, events=None, at_bat_number=1, pitch_number=2),
    ])
    with pytest.raises(ValueError, match="not the final pitch"):
        raw_terminal_outcome_counts(raw, player_id=7)


def test_missing_raw_group_requires_explicit_hashed_zero_pa_evidence() -> None:
    official = pd.DataFrame([{
        "season": 2023, "game_date": "2023-06-26", "game_pk": 1, "player_id": 7,
        "lineup_slot": 9, "out_pa": 0, "out_ab": 0, "out_hits": 0,
        "out_doubles": 0, "out_triples": 0, "out_hr": 0, "out_bb": 0, "out_k": 0,
        **{f"target_{name}": 0 for name in (
            "strikeout", "walk", "single", "double", "triple", "home_run",
            "bip_out", "other_non_ab",
        )},
    }])
    raw = pd.DataFrame(columns=[
        "season", "game_date", "game_pk", "player_id",
        *[f"target_{name}" for name in (
            "strikeout", "walk", "single", "double", "triple", "home_run",
            "bip_out", "other_non_ab",
        )],
    ])
    with pytest.raises(ValueError, match="not exactly explained"):
        apply_raw_outcome_truth(official, raw, pd.DataFrame(columns=ZERO_COLUMNS))
    zero = pd.DataFrame([{
        "season": 2023, "game_date": "2023-06-26", "game_pk": 1, "player_id": 7,
        "verified_zero_pa": True, "source_kind": "official_final_boxscore_zero_pa",
        "source_sha256": "a" * 64,
    }])
    repaired, mismatches = apply_raw_outcome_truth(official, raw, zero)
    assert repaired.loc[0, "out_pa"] == 0
    assert mismatches == 0


def test_raw_date_identity_mutation_cannot_join_same_game_and_player() -> None:
    official = pd.DataFrame([{
        "season": 2023, "game_date": "2023-06-26", "game_pk": 1, "player_id": 7,
        "lineup_slot": 1, "out_pa": 1, "out_ab": 1, "out_hits": 1,
        "out_doubles": 0, "out_triples": 0, "out_hr": 0, "out_bb": 0, "out_k": 0,
        **{f"target_{name}": int(name == "single") for name in (
            "strikeout", "walk", "single", "double", "triple", "home_run",
            "bip_out", "other_non_ab",
        )},
    }])
    counts = raw_terminal_outcome_counts(pd.DataFrame([
        raw_row(game_pk=1, batter=7, game_date="2023-06-27"),
    ]), player_id=7)
    with pytest.raises(ValueError, match="target-orphan"):
        apply_raw_outcome_truth(official, counts, pd.DataFrame(columns=ZERO_COLUMNS))


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


def complete_feature_lineage_row() -> dict:
    row = {
        "history_pitch_count": 10, "history_pa": 2, "history_bip": 1,
        "history_description_denominator": 9, "history_description_missing_count": 1,
        "history_swing_count": 4, "history_swing_denominator": 9,
        "history_swing_rate": 4 / 9, "history_whiff_count": 1,
        "history_whiff_denominator": 4, "history_whiff_rate": 0.25,
        "history_chase_count": 1, "history_chase_denominator": 2,
        "history_chase_rate": 0.5, "history_zone_count": 6,
        "history_zone_denominator": 8, "history_zone_missing_count": 2,
        "history_zone_rate": 0.75, "history_pitch_type_denominator": 9,
        "history_pitch_type_missing_count": 1, "history_pitch_type_entropy": 0.5,
    }
    for prefix, population in (
        ("history_pa_age_days", 2), ("history_exit_velocity", 1),
        ("history_launch_angle", 1), ("history_release_speed", 10),
        ("history_pfx_x", 10), ("history_pfx_z", 10),
        ("history_plate_x", 10), ("history_plate_z", 10),
    ):
        row[f"{prefix}_count"] = population
        row[f"{prefix}_missing_count"] = 0
        row[f"{prefix}_mean"] = 1.0
        row[f"{prefix}_sd"] = 0.0
    return row


def test_complete_feature_denominator_lineage_is_accepted() -> None:
    validate_feature_denominator_lineage(pd.DataFrame([complete_feature_lineage_row()]))


@pytest.mark.parametrize("column,value,match", [
    ("history_swing_denominator", 10, "swing"),
    ("history_whiff_denominator", 3, "whiff"),
    ("history_exit_velocity_count", 0, "count and missing"),
    ("history_pitch_type_missing_count", 2, "pitch-type denominator"),
])
def test_feature_denominator_mutations_fail_closed(column, value, match) -> None:
    row = complete_feature_lineage_row()
    row[column] = value
    with pytest.raises(ValueError, match=match):
        validate_feature_denominator_lineage(pd.DataFrame([row]))
