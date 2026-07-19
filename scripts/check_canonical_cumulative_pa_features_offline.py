#!/usr/bin/env python3
"""Chronology, taxonomy, and v1-parity tests for cumulative PA features."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.canonical_cumulative_pa_features import (  # noqa: E402
    SCHEMA_VERSION,
    cumulative_profiles,
    expected_cache_groups,
)
from src.features.canonical_pa_features import PROFILE_FIELDS, canonical_profile  # noqa: E402


RAW_COLUMNS = [
    "game_date", "batter", "pitcher", "events", "description", "type",
    "zone", "launch_speed", "launch_angle", "launch_speed_angle",
    "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
    "estimated_slg_using_speedangle",
]


def row(**updates: object) -> dict[str, object]:
    base: dict[str, object] = {
        "game_date": "2024-06-14", "batter": 10, "pitcher": 20,
        "events": None, "description": "called_strike", "type": "S",
        "zone": 5, "launch_speed": None, "launch_angle": None,
        "launch_speed_angle": None, "estimated_ba_using_speedangle": None,
        "estimated_woba_using_speedangle": None,
        "estimated_slg_using_speedangle": None,
    }
    base.update(updates)
    return base


def expect_failure(fn: object) -> bool:
    try:
        fn()  # type: ignore[operator]
    except ValueError:
        return True
    return False


def main() -> int:
    frame = pd.DataFrame([
        row(game_date="2024-04-30", events="single", description="hit_into_play", type="X", launch_speed=100.0, launch_angle=28.0, launch_speed_angle=6, estimated_ba_using_speedangle=0.7, estimated_woba_using_speedangle=0.8, estimated_slg_using_speedangle=1.4),
        row(events=None, description="foul", type="S", zone=12),
        row(events="strikeout", description="swinging_strike", type="S", zone=12),
        row(game_date="2024-06-15", events="home_run", description="hit_into_play", type="X", launch_speed=110.0, launch_angle=25.0, launch_speed_angle=6),
        row(batter=11, events="home_run", description="hit_into_play", type="X"),
        row(events="truncated_pa"),
        row(events="double_play", description="hit_into_play", type="X"),
        row(events="sac_fly_double_play", description="hit_into_play", type="X"),
    ], columns=RAW_COLUMNS)
    expanding = cumulative_profiles(
        frame,
        target_dates=["2024-06-14", "2024-06-15"],
        start_inclusive="2024-04-01",
        entity_column="batter",
        entity_id=10,
        prefix="history",
    )
    fixed = canonical_profile(
        frame,
        target_date="2024-06-15",
        entity_column="batter",
        entity_id=10,
        prefix="fixed",
    )
    parity = cumulative_profiles(
        frame,
        target_dates=["2024-06-15"],
        start_inclusive="2024-04-30",
        entity_column="batter",
        entity_id=10,
        prefix="parity",
    )[0]
    checks = [
        ("schema bound", expanding[0]["schema_version"] == SCHEMA_VERSION),
        ("expanding chronology differs", expanding[0]["history_pa"] == 1 and expanding[1]["history_pa"] == 4),
        ("target date excluded", expanding[1]["history_home_run_rate"] == 0.0),
        ("declared lower bound retained", expanding[1]["window_start_inclusive"] == "2024-04-01"),
        ("identity isolated", expanding[1]["history_bip"] == 3),
        ("truncated PA excluded", expanding[1]["history_pa"] == 4),
        ("unknown event rejected", expect_failure(lambda: cumulative_profiles(pd.concat([frame, pd.DataFrame([row(events="new_provider_event")])], ignore_index=True), target_dates=["2024-06-15"], start_inclusive="2024-04-01", entity_column="batter", entity_id=10, prefix="history"))),
        ("same lower/upper window equals immutable v1", all(
            parity[f"parity_{name}"] == fixed[f"fixed_{name}"]
            or (pd.isna(parity[f"parity_{name}"]) and pd.isna(fixed[f"fixed_{name}"]))
            for name in PROFILE_FIELDS
        )),
        ("duplicate targets rejected", expect_failure(lambda: cumulative_profiles(frame, target_dates=["2024-06-15", "2024-06-15"], start_inclusive="2024-04-01", entity_column="batter", entity_id=10, prefix="history"))),
        ("nonpositive window rejected", expect_failure(lambda: cumulative_profiles(frame, target_dates=["2024-06-15"], start_inclusive="2024-06-15", entity_column="batter", entity_id=10, prefix="history"))),
        ("invalid identity column rejected", expect_failure(lambda: cumulative_profiles(frame, target_dates=["2024-06-15"], start_inclusive="2024-04-01", entity_column="fielder", entity_id=10, prefix="history"))),
        ("missing source column rejected", expect_failure(lambda: cumulative_profiles(frame.drop(columns=["zone"]), target_dates=["2024-06-15"], start_inclusive="2024-04-01", entity_column="batter", entity_id=10, prefix="history"))),
        ("source universe includes prior active years", expected_cache_groups(pd.DataFrame({"season": [2023, 2024], "player_id": [10, 11]}), allowed_years=[2023, 2024]) == [(2023, 10), (2023, 11), (2024, 11)]),
        ("future source year rejected", expect_failure(lambda: expected_cache_groups(pd.DataFrame({"season": [2025], "player_id": [10]}), allowed_years=[2023, 2024]))),
        ("source-universe schema mutation rejected", expect_failure(lambda: expected_cache_groups(pd.DataFrame({"season": [2024], "player_id": [10], "game_pk": [1]}), allowed_years=[2023, 2024]))),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"canonical cumulative feature checks failed: {failed}")
    print(f"CANONICAL CUMULATIVE FEATURES VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
