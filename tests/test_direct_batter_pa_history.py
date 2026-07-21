from __future__ import annotations

import pandas as pd
import pytest

from src.features.direct_batter_pa_history import history_features, prepare_raw


def rows() -> pd.DataFrame:
    base = {
        "game_type": "R", "batter": 7, "description": "hit_into_play", "type": "X",
        "zone": 5, "pitch_type": "FF", "release_speed": 95, "pfx_x": 1, "pfx_z": 2,
        "plate_x": 0, "plate_z": 2.5, "launch_speed": 100, "launch_angle": 20,
        "launch_speed_angle": 6,
    }
    return pd.DataFrame([
        {**base, "game_date": "2023-04-01", "events": "single"},
        {**base, "game_date": "2023-04-02", "events": "truncated_pa"},
        {**base, "game_date": "2023-04-03", "events": "home_run", "game_type": "S"},
        {**base, "game_date": "2023-04-04", "events": "double"},
    ])


def test_strict_prior_regular_only_and_non_pa_excluded() -> None:
    prepared = prepare_raw(rows(), player_id=7)
    out = history_features(prepared, player_id=7, target_date="2023-04-04")
    assert out["history_pa"] == 1
    assert out["history_single_count"] == 1
    assert out["history_home_run_count"] == 0
    assert out["history_double_count"] == 0
    assert out["max_source_date"] == "2023-04-02"


def test_same_day_mutation_changes_nothing() -> None:
    prepared = prepare_raw(rows(), player_id=7)
    out = history_features(prepared, player_id=7, target_date="2023-04-04")
    assert out["history_double_count"] == 0


def test_unknown_terminal_event_fails_closed() -> None:
    frame = rows()
    frame.loc[0, "events"] = "future_schema_event"
    with pytest.raises(ValueError, match="unmapped terminal"):
        history_features(prepare_raw(frame, player_id=7), player_id=7, target_date="2023-04-04")


def test_identity_and_year_fail_closed() -> None:
    with pytest.raises(ValueError, match="identity mismatch"):
        prepare_raw(rows(), player_id=8)
    with pytest.raises(ValueError, match="only 2023 and 2024"):
        history_features(prepare_raw(rows(), player_id=7), player_id=7, target_date="2025-04-04")
