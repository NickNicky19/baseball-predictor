from __future__ import annotations

from datetime import date

import pandas as pd

from scripts.build_repaired_batted_ball_features import (
    build_target_features,
    target_dates,
)


def test_feature_builder_is_strict_prior_and_count_consistent() -> None:
    source = pd.DataFrame(
        {
            "_game_date": [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)],
            "game_date": ["2026-06-01", "2026-06-02", "2026-06-03"],
            "batter": [1, 1, 1],
            "player_name": ["One"] * 3,
            "type": ["X", "X", "X"],
            "launch_speed": [100.0, 96.0, 110.0],
            "launch_angle": [25.0, 10.0, 30.0],
            "launch_speed_angle": [6, 2, 6],
            "estimated_woba_using_speedangle": [0.8, 0.2, 1.0],
            "estimated_ba_using_speedangle": [0.7, 0.1, 0.9],
            "estimated_slg_using_speedangle": [1.5, 0.2, 2.0],
        }
    )
    result = build_target_features(source, target_date=date(2026, 6, 3))
    row = result.iloc[0]
    assert row["max_source_date"] == "2026-06-02"
    assert row["batted_ball_denominator"] == 2
    assert row["barrel_count"] == 1
    assert row["hard_hit_count"] == 2
    assert row["barrel_rate"] == 0.5
    assert row["hard_hit_rate"] == 1.0


def test_target_calendar_has_no_may() -> None:
    dates = target_dates(date(2026, 4, 30), date(2026, 6, 2))
    assert dates == [date(2026, 4, 30), date(2026, 6, 1), date(2026, 6, 2)]


def test_empty_history_retains_explicit_feature_schema() -> None:
    source = pd.DataFrame(
        columns=[
            "_game_date", "game_date", "batter", "type", "launch_speed",
            "launch_angle", "launch_speed_angle", "estimated_woba_using_speedangle",
            "estimated_ba_using_speedangle", "estimated_slg_using_speedangle",
        ]
    )
    result = build_target_features(source, target_date=date(2026, 3, 25))
    assert result.empty
    assert "player_id" in result.columns
    assert "batted_ball_denominator" in result.columns
