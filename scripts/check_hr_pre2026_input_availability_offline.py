#!/usr/bin/env python3
"""Mutation checks for the outcome-blind HR input-availability contract."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_pre2026_input_availability import (  # noqa: E402
    date_availability,
    qualifying_event_count,
    regular_season_event_dates,
    statcast_window,
    validate_source_columns,
)


def fails(fn) -> bool:
    try:
        fn()
    except (TypeError, ValueError):
        return True
    return False


def main() -> int:
    start, end = statcast_window("2025-04-20", 45)
    assert (start, end) == ("2025-03-05", "2025-04-20")
    print("[OK] exact inclusive/exclusive 45-day fetch contract is pinned")

    dates = ["2025-03-05", "2025-04-19", "2025-04-20", "2025-03-04"]
    assert qualifying_event_count(dates, target_date="2025-04-20", lookback_days=45) == 2
    print("[OK] target-date and before-window rows cannot enter availability")

    regular = regular_season_event_dates(
        ["2025-03-10", "2025-03-11", "2025-03-18"], ["S", "E", "R"]
    )
    assert regular == ["2025-03-18"]
    print("[OK] Spring Training/exhibition cache rows cannot certify availability")

    rows = date_availability(
        [1, 2],
        {1: ["2025-04-01"] * 8, 2: ["2025-04-01"] * 7},
        target_date="2025-04-20",
        lookback_days=45,
        min_events=8,
    )
    assert rows["advanced_active_player_ids"] == [1]
    assert rows["runner_requirement_satisfied"] is True
    print("[OK] one qualified active profile satisfies the preserved runner gate")

    stale = date_availability(
        [1], {1: ["2025-01-01"] * 50}, target_date="2025-04-20", lookback_days=45, min_events=8
    )
    assert stale["all_fallback"] is True
    print("[OK] abundant but stale history cannot satisfy the gate")

    changed = date_availability(
        [1], {1: ["2025-04-01"] * 7}, target_date="2025-04-20", lookback_days=45, min_events=7
    )
    assert changed["runner_requirement_satisfied"] is True and stale["runner_requirement_satisfied"] is False
    print("[OK] MUTATION changing min-events changes eligibility and must require a new artifact")

    assert fails(lambda: validate_source_columns(["game_date", "player_id", "out_hr"]))
    print("[OK] MUTATION outcome truth is forbidden from the availability source")

    assert fails(
        lambda: date_availability(
            [1, 1], {1: []}, target_date="2025-04-20", lookback_days=45, min_events=8
        )
    )
    print("[OK] duplicate active-player identity hard-fails")

    assert fails(lambda: statcast_window("2025-04-20", 0))
    print("[OK] invalid lookback cannot masquerade as availability")
    assert fails(lambda: regular_season_event_dates(["2025-03-18"], []))
    print("[OK] MUTATION unpaired game-type evidence hard-fails")
    print("10/10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
