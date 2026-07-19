#!/usr/bin/env python3
"""Mutation tests for cumulative-history artifact validation."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.canonical_pa_features import PROFILE_FIELDS  # noqa: E402
from scripts.validate_shared_pa_cumulative_history_artifact import validate_frame  # noqa: E402


def expect_failure(fn: object) -> bool:
    try:
        fn()  # type: ignore[operator]
    except ValueError:
        return True
    return False


def fixture() -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    rows = []
    for game_pk, season, game_date, pa in (
        (1, 2023, "2023-03-30", 0),
        (2, 2024, "2024-09-30", 4),
    ):
        row = {"season": season, "game_date": game_date, "game_pk": game_pk, "player_id": 10}
        for name in PROFILE_FIELDS:
            row[f"history_{name}"] = 0 if name in {"pitch_count", "pa", "bip"} else None
        row["history_pitch_count"] = pa * 4
        row["history_pa"] = pa
        row["history_bip"] = pa
        if pa:
            for name in (
                "k_rate", "bb_rate", "single_rate", "double_rate", "triple_rate",
                "home_run_rate", "bip_out_rate", "other_non_ab_rate",
            ):
                row[f"history_{name}"] = 0.0
            row["history_single_rate"] = 1.0
        rows.append(row)
    frame = pd.DataFrame(rows)
    targets = frame[["game_pk", "player_id"]].copy()
    missing = {f"history_{name}": float(frame[f"history_{name}"].isna().mean()) for name in PROFILE_FIELDS}
    manifest = {"artifact": {"rows": 2, "feature_missing_rate": missing}}
    contract = {"feature_group": [f"history_{name}" for name in PROFILE_FIELDS]}
    return frame, targets, manifest, contract


def main() -> int:
    frame, targets, manifest, contract = fixture()
    checks = [("valid fixture", validate_frame(frame, targets, manifest, contract)["rows"] == 2)]
    duplicate = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    duplicate_manifest = copy.deepcopy(manifest)
    duplicate_manifest["artifact"]["rows"] = 3
    decreased = frame.copy()
    decreased.loc[0, ["history_pitch_count", "history_pa", "history_bip"]] = [20, 5, 5]
    checks.extend([
        ("duplicate identity rejected", expect_failure(lambda: validate_frame(duplicate, targets, duplicate_manifest, contract))),
        ("confirmation season rejected", expect_failure(lambda: validate_frame(frame.assign(season=2025), targets, manifest, contract))),
        ("outcome column rejected", expect_failure(lambda: validate_frame(frame.assign(out_hits=1), targets, manifest, contract))),
        ("rate range rejected", expect_failure(lambda: validate_frame(frame.assign(history_k_rate=1.1), targets, manifest, contract))),
        ("count relation rejected", expect_failure(lambda: validate_frame(frame.assign(history_bip=9), targets, manifest, contract))),
        ("identity coverage rejected", expect_failure(lambda: validate_frame(frame.assign(player_id=11), targets, manifest, contract))),
        ("missingness mutation rejected", expect_failure(lambda: validate_frame(frame.assign(history_xba=0.2), targets, manifest, contract))),
        ("decreasing exposure rejected", expect_failure(lambda: validate_frame(decreased, targets, manifest, contract))),
        ("feature omission rejected", expect_failure(lambda: validate_frame(frame, targets, manifest, {"feature_group": contract["feature_group"][:-1]}))),
    ])
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"cumulative history validator checks failed: {failed}")
    print(f"CUMULATIVE HISTORY VALIDATOR VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
