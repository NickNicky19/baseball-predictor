#!/usr/bin/env python3
"""Offline chronology, accounting, missingness, and schema mutations."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.canonical_pa_features import (  # noqa: E402
    CALENDAR_DAYS_IN_WINDOW,
    SCHEMA_VERSION,
    canonical_profile,
    canonical_profiles,
    canonical_profiles_between,
    window_bounds,
)


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
        "launch_speed_angle": None,
        "estimated_ba_using_speedangle": None,
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
    checks: list[tuple[str, bool]] = []
    start, end = window_bounds("2024-06-15")
    checks.append(("exact 46-calendar-day half-open window", CALENDAR_DAYS_IN_WINDOW == 46 and start.isoformat() == "2024-04-30" and end.isoformat() == "2024-06-15"))

    frame = pd.DataFrame([
        # Included lower boundary: single on a measured 100 mph barrel.
        row(game_date="2024-04-30", events="single", description="hit_into_play", type="X", zone=5, launch_speed=100.0, launch_angle=28.0, launch_speed_angle=6, estimated_ba_using_speedangle=0.7, estimated_woba_using_speedangle=0.8, estimated_slg_using_speedangle=1.4),
        # Included terminal strikeout; its whiff is computed from all pitches.
        row(events=None, description="foul", type="S", zone=12),
        row(events="strikeout", description="swinging_strike", type="S", zone=12),
        # Target-date HR must be excluded from every feature.
        row(game_date="2024-06-15", events="home_run", description="hit_into_play", type="X", zone=5, launch_speed=110.0, launch_angle=25.0, launch_speed_angle=6, estimated_ba_using_speedangle=1.0, estimated_woba_using_speedangle=2.0, estimated_slg_using_speedangle=4.0),
        # Another batter and pitcher must not cross identity boundaries.
        row(batter=11, pitcher=21, events="home_run", description="hit_into_play", type="X", zone=5, launch_speed=110.0, launch_angle=25.0, launch_speed_angle=6),
        # Explicit Statcast terminal labels with official accounting semantics.
        row(events="truncated_pa", description="called_strike", type="S", zone=5),
        row(events="double_play", description="hit_into_play", type="X", zone=5),
        row(events="sac_fly_double_play", description="hit_into_play", type="X", zone=5),
    ], columns=RAW_COLUMNS)
    profile = canonical_profile(frame, target_date="2024-06-15", entity_column="batter", entity_id=10, prefix="hitter")
    checks.extend([
        ("schema bound", profile["schema_version"] == SCHEMA_VERSION),
        ("same-day outcome excluded", profile["hitter_pa"] == 4 and profile["hitter_home_run_rate"] == 0.0),
        ("lower boundary included", profile["hitter_single_rate"] == 0.25 and profile["hitter_xba"] == 0.7),
        ("all-pitch discipline", profile["hitter_pitch_count"] == 6 and abs(profile["hitter_whiff_rate"] - (1.0 / 5.0)) < 1e-12),
        ("identity isolated", profile["hitter_bip"] == 3),
        ("no invented missing fallback", profile["hitter_chase_rate"] == 1.0),
        ("truncated PA excluded", profile["hitter_pa"] == 4),
        ("double play is BIP out", profile["hitter_bip_out_rate"] == 0.25),
        ("sac-fly double play is non-AB", profile["hitter_other_non_ab_rate"] == 0.25),
    ])

    empty = canonical_profile(frame.iloc[0:0], target_date="2024-06-15", entity_column="batter", entity_id=10, prefix="hitter")
    multiple = canonical_profiles(
        frame,
        target_dates=["2024-06-14", "2024-06-15"],
        entity_column="batter",
        entity_id=10,
        prefix="hitter",
    )
    expanding = canonical_profiles_between(
        frame,
        target_dates=["2024-06-14", "2024-06-15"],
        start_inclusive="2024-04-01",
        entity_column="batter",
        entity_id=10,
        prefix="history",
    )
    checks.extend([
        ("empty history retained", empty["hitter_pa"] == 0),
        ("empty rates remain missing", empty["hitter_xba"] is None and empty["hitter_k_rate"] is None),
        ("missing raw column rejected", expect_failure(lambda: canonical_profile(frame.drop(columns=["zone"]), target_date="2024-06-15", entity_column="batter", entity_id=10, prefix="hitter"))),
        ("malformed date rejected", expect_failure(lambda: canonical_profile(frame.assign(game_date="not-a-date"), target_date="2024-06-15", entity_column="batter", entity_id=10, prefix="hitter"))),
        ("unknown terminal event rejected", expect_failure(lambda: canonical_profile(pd.concat([frame, pd.DataFrame([row(events="new_provider_event")])], ignore_index=True), target_date="2024-06-15", entity_column="batter", entity_id=10, prefix="hitter"))),
        ("invalid entity rejected", expect_failure(lambda: canonical_profile(frame, target_date="2024-06-15", entity_column="fielder", entity_id=10, prefix="hitter"))),
        ("multi-date chronology differs", multiple[0]["hitter_pa"] == 1 and multiple[1]["hitter_pa"] == 4),
        ("duplicate target dates rejected", expect_failure(lambda: canonical_profiles(frame, target_dates=["2024-06-15", "2024-06-15"], entity_column="batter", entity_id=10, prefix="hitter"))),
        ("expanding history uses declared lower bound", expanding[0]["history_pa"] == 1 and expanding[1]["history_pa"] == 4),
        ("expanding history excludes target date", expanding[1]["history_home_run_rate"] == 0.0),
        ("expanding history records exact bounds", expanding[1]["window_start_inclusive"] == "2024-04-01" and expanding[1]["window_end_exclusive"] == "2024-06-15"),
        ("invalid expanding lower bound rejected", expect_failure(lambda: canonical_profiles_between(frame, target_dates=["2024-06-15"], start_inclusive="2024-06-15", entity_column="batter", entity_id=10, prefix="history"))),
    ])
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"canonical PA feature checks failed: {failed}")
    print(f"CANONICAL PA FEATURES VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
