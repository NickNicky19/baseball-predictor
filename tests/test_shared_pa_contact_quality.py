from __future__ import annotations

import copy

import pandas as pd
import pytest

from src.evaluation.shared_pa_contact_quality import (
    FEATURE_COLUMNS,
    contact_quality_features,
    prepare_contact_source,
    validate_contact_feature_row,
)


def _rows() -> pd.DataFrame:
    base = {
        "game_pk": 1,
        "batter": 10,
        "game_type": "R",
        "events": "field_out",
        "type": "X",
        "pitch_number": 1,
    }
    values = [
        ("2023-04-01", 1, 100.0, 20.0, 6, "fly_ball"),
        ("2023-04-02", 2, 96.0, 5.0, 4, "ground_ball"),
        ("2023-04-03", 3, 90.0, 15.0, 3, "line_drive"),
        ("2023-04-04", 4, 80.0, 55.0, 1, "popup"),
        ("2023-04-05", 5, None, None, None, None),
    ]
    rows = []
    for date, at_bat, ev, la, speed_angle, bb_type in values:
        row = copy.deepcopy(base)
        row.update(
            {
                "game_date": date,
                "at_bat_number": at_bat,
                "launch_speed": ev,
                "launch_angle": la,
                "launch_speed_angle": speed_angle,
                "bb_type": bb_type,
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def test_contact_features_have_truthful_counts_denominators_and_ev50() -> None:
    prepared = prepare_contact_source(_rows(), player_id=10)
    result = contact_quality_features(prepared, player_id=10, target_date="2023-04-06")
    assert set(result) == set(FEATURE_COLUMNS)
    assert result["history_contact_bip"] == 5
    assert result["history_contact_ev_denominator"] == 4
    assert result["history_contact_ev_missing_count"] == 1
    assert result["history_contact_ev50_count"] == 2
    assert result["history_contact_ev50_mean"] == pytest.approx(98.0)
    assert result["history_contact_joint_denominator"] == 4
    assert result["history_contact_hard_hit_count_joint"] == 2
    assert result["history_contact_sweet_spot_count_joint"] == 2
    assert result["history_contact_hard_hit_sweet_spot_count_joint"] == 1
    assert result["history_contact_barrel_count_classified"] == 1
    assert result["history_contact_classified_hard_hit_count"] == 2
    assert result["history_contact_fly_ball_count"] == 1
    assert result["history_contact_hard_hit_fly_ball_count"] == 1
    validate_contact_feature_row(result)


def test_target_date_is_strictly_excluded() -> None:
    prepared = prepare_contact_source(_rows(), player_id=10)
    result = contact_quality_features(prepared, player_id=10, target_date="2023-04-05")
    assert result["history_contact_bip"] == 4
    assert result["history_contact_max_source_date"] == "2023-04-04"


def test_invalid_source_mutations_fail_closed() -> None:
    frame = _rows()
    frame.loc[0, "launch_speed"] = 90.0
    with pytest.raises(ValueError, match="barrel classification"):
        prepare_contact_source(frame, player_id=10)
    frame = _rows()
    frame.loc[0, "launch_speed"] = None
    with pytest.raises(ValueError, match="without joint"):
        prepare_contact_source(frame, player_id=10)
    frame = _rows()
    frame.loc[0, "bb_type"] = "mystery"
    with pytest.raises(ValueError, match="bb_type is unknown"):
        prepare_contact_source(frame, player_id=10)
    frame = _rows()
    frame.loc[1, "at_bat_number"] = 1
    with pytest.raises(ValueError, match="identity is duplicated"):
        prepare_contact_source(frame, player_id=10)


def test_rate_or_missingness_mutation_fails_closed() -> None:
    result = contact_quality_features(
        prepare_contact_source(_rows(), player_id=10),
        player_id=10,
        target_date="2023-04-06",
    )
    mutated = dict(result)
    mutated["history_contact_hard_hit_rate_joint"] = 0.99
    with pytest.raises(ValueError, match="count/rate mismatch"):
        validate_contact_feature_row(mutated)
    mutated = dict(result)
    mutated["history_contact_joint_missing_count"] += 1
    with pytest.raises(ValueError, match="missingness mismatch"):
        validate_contact_feature_row(mutated)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("history_contact_ev50_count", 1, "EV50 support"),
        ("history_contact_ground_count_joint", 0, "launch-angle partition"),
        ("history_contact_fly_ball_count", 0, "batted-ball-type partition"),
        ("history_contact_speed_angle_denominator", 5, "classified denominator"),
        ("history_contact_hard_hit_fly_ball_count", 2, "exceeds type population"),
        ("history_contact_barrel_count_classified", 3, "barrel count exceeds"),
        ("history_contact_bip", 5.5, "nonnegative integer"),
        ("history_contact_max_source_date", "04/05/2023", "malformed"),
    ],
)
def test_structural_mutations_fail_closed(field: str, value: object, message: str) -> None:
    result = contact_quality_features(
        prepare_contact_source(_rows(), player_id=10),
        player_id=10,
        target_date="2023-04-06",
    )
    result[field] = value
    if field == "history_contact_speed_angle_denominator":
        result["history_contact_speed_angle_missing_count"] = 0
    elif field == "history_contact_ground_count_joint":
        result["history_contact_ground_rate_joint"] = 0.0
    elif field == "history_contact_fly_ball_count":
        result["history_contact_fly_ball_rate"] = 0.0
    with pytest.raises(ValueError, match=message):
        validate_contact_feature_row(result)
