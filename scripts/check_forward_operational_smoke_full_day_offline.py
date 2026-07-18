#!/usr/bin/env python3
"""Mutation checks for the full-day operational-smoke certification guard."""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.certify_forward_operational_smoke_full_day import (  # noqa: E402
    FullDaySmokeError,
    require_full_day_completion,
)


def rejected(plan: SimpleNamespace, report: dict) -> bool:
    try:
        require_full_day_completion(plan, report)
    except FullDaySmokeError:
        return True
    return False


def main() -> int:
    targets = (
        SimpleNamespace(official_start_time_utc="2026-07-18T17:00:00Z"),
        SimpleNamespace(official_start_time_utc="2026-07-18T20:00:00Z"),
    )
    plan = SimpleNamespace(official_game_date="2026-07-18", targets=targets)
    report = {
        "official_game_date": "2026-07-18",
        "assessed_at_utc": "2026-07-18T21:00:00Z",
        "entry_capture": {
            "terminal_receipts_loaded": 2,
            "coverage": {
                "due_targets": 2,
                "future_targets": 0,
                "complete": True,
                "missing_target_ids": [],
                "source_error_targets": 0,
                "observed_targets": 2,
            },
        },
        "lifecycle_counts": {
            "selection_committed_targets": 2,
            "prestart_verified_targets": 2,
            "selected_entries": 3,
        },
        "complete_due_entry_and_prestart_phases": True,
        "settlement_complete": True,
        "unresolved_entries": 0,
        "verified_resolution_artifacts": 3,
        "replacement_odds_fetched": False,
        "betting_authorized": False,
    }

    checks: list[tuple[str, bool]] = []
    checks.append(("full planned day passes", not rejected(plan, report)))

    mutations = {
        "one future target fails": ("entry_capture", "coverage", "future_targets", 1),
        "partial due count fails": ("entry_capture", "coverage", "due_targets", 1),
        "missing terminal receipt fails": ("entry_capture", "terminal_receipts_loaded", 1),
        "source error fails": ("entry_capture", "coverage", "source_error_targets", 1),
        "prestart gap fails": ("lifecycle_counts", "prestart_verified_targets", 1),
        "zero-entry smoke fails": ("lifecycle_counts", "selected_entries", 0),
        "unresolved settlement fails": ("unresolved_entries", 1),
        "resolution mismatch fails": ("verified_resolution_artifacts", 2),
    }
    for label, path in mutations.items():
        changed = copy.deepcopy(report)
        cursor = changed
        for key in path[:-2]:
            cursor = cursor[key]
        cursor[path[-2]] = path[-1]
        checks.append((label, rejected(plan, changed)))

    early = copy.deepcopy(report)
    early["assessed_at_utc"] = "2026-07-18T19:59:59Z"
    checks.append(("assessment before final start fails", rejected(plan, early)))

    for label, passed in checks:
        print(f"[{'OK' if passed else 'FAIL'}] {label}")
    print(f"{sum(passed for _, passed in checks)}/{len(checks)}")
    return 0 if all(passed for _, passed in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
