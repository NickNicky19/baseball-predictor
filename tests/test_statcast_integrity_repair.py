from __future__ import annotations

import pandas as pd
import pytest

from src.data.historical_backfill_contract import (
    BackfillContractError,
    assert_not_may_2026,
    assert_permissible_artifact_class,
    project_2026_point_in_time_inputs,
)
from src.data.savant import SavantClient
from src.data.statcast_integrity import (
    StatcastIntegrityError,
    derive_batted_ball_evidence,
    validate_profile_and_rich_features,
    validate_rate_pair,
)
from src.models.dataclasses import StatcastProfile


def _kwan_shaped_raw() -> pd.DataFrame:
    # The sparse aggregate columns reproduce the old failure: mean(barrel)=.5
    # and mean(hard_hit)=.094. Raw bucket/EV evidence proves 1/4 and 2/4.
    return pd.DataFrame(
        {
            "batter": [680757] * 4,
            "player_name": ["Steven Kwan"] * 4,
            "events": ["single", "field_out", "double", "field_out"],
            "game_date": ["2026-07-20"] * 4,
            "type": ["X"] * 4,
            "launch_speed": [100.0, 96.0, 92.0, 88.0],
            "launch_speed_angle": [6, 2, 1, 1],
            "barrel": [1.0, 0.0, None, None],
            "hard_hit": [0.094] * 4,
            "estimated_woba_using_speedangle": [0.4, 0.2, 0.3, 0.1],
            "estimated_ba_using_speedangle": [0.5, 0.1, 0.2, 0.05],
            "estimated_slg_using_speedangle": [0.8, 0.2, 0.4, 0.1],
        }
    )


def test_source_repair_uses_one_count_bearing_denominator() -> None:
    evidence = derive_batted_ball_evidence(_kwan_shaped_raw())
    assert evidence is not None
    assert evidence.measured_batted_balls == 4
    assert evidence.barrel_count == 1
    assert evidence.hard_hit_count == 2
    assert evidence.barrel_rate == 0.25
    assert evidence.hard_hit_rate == 0.5

    profile = SavantClient(min_pa=1).build_hitter_profiles_from_statcast(
        _kwan_shaped_raw()
    )[680757]
    assert profile.barrel_rate == 0.25
    assert profile.hard_hit_rate == 0.5


def test_mutation_barrel_below_hard_hit_velocity_fails_closed() -> None:
    mutated = _kwan_shaped_raw()
    mutated.loc[2, "launch_speed_angle"] = 6
    with pytest.raises(StatcastIntegrityError, match="hard-hit threshold"):
        derive_batted_ball_evidence(mutated)


def test_foul_contact_is_not_a_batted_ball_denominator_mutation() -> None:
    frame = _kwan_shaped_raw()
    foul = frame.iloc[[0]].copy()
    foul["type"] = "S"
    foul["launch_speed"] = 110.0
    foul["launch_speed_angle"] = pd.NA
    evidence = derive_batted_ball_evidence(pd.concat([frame, foul], ignore_index=True))
    assert evidence is not None
    assert evidence.measured_batted_balls == 4
    assert evidence.barrel_count == 1
    assert evidence.hard_hit_count == 2


def test_kwan_archived_rate_pair_fails_at_every_boundary() -> None:
    with pytest.raises(StatcastIntegrityError, match="exceeds"):
        validate_rate_pair(0.50, 0.094, context="incident")

    profile = StatcastProfile(
        player_id=680757,
        player_name="Steven Kwan",
        sample_pa=299,
        barrel_rate=0.50,
        hard_hit_rate=0.094,
    )
    with pytest.raises(StatcastIntegrityError, match="exceeds"):
        validate_profile_and_rich_features(profile, {}, context="consumer")


def test_mutation_rich_override_cannot_reverse_valid_profile() -> None:
    profile = StatcastProfile(
        player_id=1,
        player_name="Valid",
        sample_pa=50,
        barrel_rate=0.10,
        hard_hit_rate=0.40,
    )
    validate_profile_and_rich_features(profile, {}, context="valid")
    with pytest.raises(StatcastIntegrityError, match="effective_rich_features"):
        validate_profile_and_rich_features(
            profile,
            {"barrel_rate": 0.50, "hard_hit_rate": 0.20},
            context="mutation",
        )


def test_may_2026_is_not_addressable() -> None:
    with pytest.raises(BackfillContractError, match="sealed"):
        assert_not_may_2026("2026-05-15", context="target")
    assert assert_not_may_2026("2026-04-30", context="target").isoformat() == "2026-04-30"
    assert assert_not_may_2026("2026-06-01", context="target").isoformat() == "2026-06-01"


def test_2026_projection_discards_outcome_fields_without_reading_values() -> None:
    raw = _kwan_shaped_raw()
    raw["home_runs"] = [999, 999, 999, 999]
    raw["result"] = ["forbidden"] * 4
    projected = project_2026_point_in_time_inputs(raw)
    assert "home_runs" not in projected
    assert "result" not in projected
    assert "events" not in projected
    assert "player_name" not in projected
    assert {"game_date", "batter", "launch_speed", "launch_speed_angle"}.issubset(
        projected.columns
    )


def test_mutation_2026_projection_rejects_any_may_row() -> None:
    raw = _kwan_shaped_raw()
    raw.loc[3, "game_date"] = "2026-05-31"
    with pytest.raises(BackfillContractError, match="sealed May"):
        project_2026_point_in_time_inputs(raw)


def test_prospective_evidence_classes_cannot_be_backfilled() -> None:
    with pytest.raises(BackfillContractError, match="cannot be backfilled"):
        assert_permissible_artifact_class("t4_starter_receipt")
    assert_permissible_artifact_class("historical_statcast_input")
