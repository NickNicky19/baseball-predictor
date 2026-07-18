#!/usr/bin/env python3
"""Mutation checks for the pre-2026 HR batted-ball input contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_pre2026_batted_ball_mapping import (  # noqa: E402
    validate_feature_allowlist,
    validate_historical_input,
    validate_manifest_coverage,
)


def fixture() -> pd.DataFrame:
    rows = []
    for season in (2023, 2024, 2025):
        rows.append(
            {
                "season": season,
                "game_date": f"{season}-04-01",
                "game_pk": season * 10,
                "player_id": 1,
                "out_pa": 4,
                "out_hits": 1,
                "out_hr": 0,
                "roller_schema": "a4.1",
                "roll15_barrel_rate": 0.10,
                "roll15_hardhit_rate": 0.40,
                "roll15_bip": 10,
                "roll30_barrel_rate": 0.09,
                "roll30_hardhit_rate": 0.38,
                "roll30_bip": 20,
            }
        )
    return pd.DataFrame(rows)


def fails(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def manifests() -> dict[int, dict[str, object]]:
    return {
        season: {
            "season": season,
            "builder_schema": "a3.1",
            "dates": {
                f"{season}-04-01": {
                    "status": "done",
                    "games": 1,
                    "hitter_rows": 1,
                    "pitcher_rows": 1,
                }
            },
        }
        for season in (2023, 2024, 2025)
    }


def main() -> int:
    base = fixture()
    assert validate_historical_input(base).summary["rows"] == 3
    print("[OK] valid 2023-2025 fixture passes")

    contaminated = base.copy()
    contaminated.loc[len(contaminated)] = {**base.iloc[-1].to_dict(), "season": 2026, "game_date": "2026-04-01", "game_pk": 20260}
    assert fails(lambda: validate_historical_input(contaminated))
    print("[OK] MUTATION a 2026 row fails the season boundary")

    duplicated = pd.concat([base, base.iloc[[0]]], ignore_index=True)
    assert fails(lambda: validate_historical_input(duplicated))
    print("[OK] MUTATION duplicate player-game identity fails")

    impossible = base.copy()
    impossible.loc[0, "out_hr"] = 2
    impossible.loc[0, "out_hits"] = 1
    assert fails(lambda: validate_historical_input(impossible))
    print("[OK] MUTATION impossible official outcome accounting fails")

    out_of_range = base.copy()
    out_of_range.loc[0, "roll15_barrel_rate"] = 1.2
    assert fails(lambda: validate_historical_input(out_of_range))
    print("[OK] MUTATION out-of-range batted-ball rate fails")

    partial_pair = base.copy()
    partial_pair.loc[0, "roll15_barrel_rate"] = float("nan")
    assert fails(lambda: validate_historical_input(partial_pair))
    print("[OK] MUTATION partial barrel/hard-hit availability fails")

    unavailable_pair = base.copy()
    unavailable_pair.loc[0, ["roll15_barrel_rate", "roll15_hardhit_rate"]] = float("nan")
    summary = validate_historical_input(unavailable_pair).summary
    assert summary["fallback_by_window"]["15"]["quality_pair_unavailable_with_sufficient_bip"] == 1
    print("[OK] paired quality-unavailable fallback is preserved and counted")

    populated_ineligible = base.copy()
    populated_ineligible.loc[0, "roll15_bip"] = 4
    assert fails(lambda: validate_historical_input(populated_ineligible))
    print("[OK] MUTATION populated rate below the BIP guard fails")

    no_history = base.copy()
    no_history.loc[0, ["roll15_bip", "roll15_barrel_rate", "roll15_hardhit_rate"]] = float("nan")
    assert validate_historical_input(no_history).summary["rows"] == 3
    print("[OK] no-history fallback is explicit and preserves the row")

    bad_schema = base.copy()
    bad_schema["roller_schema"] = "mutated"
    assert fails(lambda: validate_historical_input(bad_schema))
    print("[OK] MUTATION roller schema drift fails")

    assert fails(lambda: validate_feature_allowlist(["roll15_barrel_rate", "out_hr"]))
    print("[OK] MUTATION official target cannot enter the feature allowlist")

    wrong_manifest = manifests()
    wrong_manifest[2024]["dates"]["2024-04-01"]["hitter_rows"] = 2  # type: ignore[index]
    assert fails(lambda: validate_manifest_coverage(base, wrong_manifest))
    print("[OK] MUTATION manifest/artifact row mismatch fails")

    missing_done = manifests()
    missing_done[2025]["dates"]["2025-04-01"]["status"] = "empty"  # type: ignore[index]
    assert fails(lambda: validate_manifest_coverage(base, missing_done))
    print("[OK] MUTATION an observed date not declared done fails")

    print("12/12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
