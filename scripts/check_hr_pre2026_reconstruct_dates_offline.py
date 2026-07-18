#!/usr/bin/env python3
"""Mutation checks for outcome-blind HR reconstruction date selection."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_hr_pre2026_reconstruct_dates import (  # noqa: E402
    select_evenly_spaced_available_dates,
    select_evenly_spaced_done_dates,
    select_evenly_spaced_reconstructible_dates,
)


def fixture() -> dict:
    return {
        "season": 2025,
        "builder_schema": "a3.1",
        "dates": {
            f"2025-04-{day:02d}": {
                "status": "done", "games": day, "hitter_rows": day * 18,
            }
            for day in range(1, 13)
        },
    }


def fails(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def main() -> int:
    source = fixture()
    selected = select_evenly_spaced_done_dates(source, 6)
    assert selected == sorted(selected) and len(selected) == len(set(selected)) == 6
    print("[OK] exact sorted evenly-spaced universe is deterministic")

    changed_counts = fixture()
    for row in changed_counts["dates"].values():
        row["games"] = 999
        row["hitter_rows"] = 1
    assert select_evenly_spaced_done_dates(changed_counts, 6) == selected
    print("[OK] MUTATION non-status measurements cannot select dates")

    zero_rows = fixture()
    zero_rows["dates"][selected[2]]["hitter_rows"] = 0
    assert select_evenly_spaced_done_dates(zero_rows, 6) != selected
    print("[OK] MUTATION a status-done date with zero hitter rows cannot enter")

    one_empty = fixture()
    one_empty["dates"][selected[2]]["status"] = "empty"
    assert select_evenly_spaced_done_dates(one_empty, 6) != selected
    print("[OK] MUTATION status change moves the declared universe")

    wrong_year = fixture()
    wrong_year["season"] = 2026
    assert fails(lambda: select_evenly_spaced_done_dates(wrong_year, 6))
    print("[OK] MUTATION 2026 manifest cannot enter pre-2026 reconstruction")

    duplicate_pressure = fixture()
    assert fails(lambda: select_evenly_spaced_done_dates(duplicate_pressure, 14))
    print("[OK] MUTATION requested count cannot exceed observed done dates")

    assert fails(lambda: select_evenly_spaced_done_dates(fixture(), 5))
    print("[OK] MUTATION odd count cannot blur calibration/confirmation split")

    reconstructible = select_evenly_spaced_reconstructible_dates(fixture(), 6)
    assert "2025-04-01" not in reconstructible
    assert reconstructible[0] == "2025-04-02"
    print("[OK] first no-history date is excluded before even spacing")

    only_one = fixture()
    only_one["dates"] = {"2025-04-01": only_one["dates"]["2025-04-01"]}
    assert fails(lambda: select_evenly_spaced_reconstructible_dates(only_one, 4))
    print("[OK] MUTATION no prior completed date cannot masquerade as reconstructible")

    availability = {
        "schema_version": "hr-pre2026-input-availability-v1",
        "status": "OUTCOME_BLIND_MODEL_INPUT_AVAILABILITY",
        "uses_outcomes_for_availability": False,
        "may_2026_opened": False,
        "available_dates": [f"2025-04-{day:02d}" for day in range(2, 13)],
        "dates": {
            date: {
                "runner_requirement_satisfied": date != "2025-04-01",
                "all_fallback": date == "2025-04-01",
                "active_player_rows": row["hitter_rows"],
            }
            for date, row in fixture()["dates"].items()
        },
    }
    selected_available = select_evenly_spaced_available_dates(fixture(), availability, 4)
    assert len(selected_available) == 4 and "2025-04-01" not in selected_available
    print("[OK] v3 uses only per-date model-input availability evidence")

    mutated = dict(availability)
    mutated["uses_outcomes_for_availability"] = True
    assert fails(lambda: select_evenly_spaced_available_dates(fixture(), mutated, 4))
    print("[OK] MUTATION outcome-selected availability hard-fails")

    inconsistent = dict(availability)
    inconsistent["available_dates"] = ["2025-04-01"] + availability["available_dates"]
    assert fails(lambda: select_evenly_spaced_available_dates(fixture(), inconsistent, 4))
    print("[OK] MUTATION summary/per-date disagreement hard-fails")
    print("12/12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
