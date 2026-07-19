#!/usr/bin/env python3
"""Mutation tests for canonical feature-artifact adjudication."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.canonical_pa_features import PROFILE_FIELDS  # noqa: E402
from scripts.validate_shared_pa_canonical_hitter_artifact import validate_frame  # noqa: E402


def expect_failure(fn: object) -> bool:
    try:
        fn()  # type: ignore[operator]
    except ValueError:
        return True
    return False


def fixture() -> tuple[pd.DataFrame, pd.DataFrame, dict, dict]:
    row = {
        "season": 2023, "game_date": "2023-03-30", "game_pk": 1,
        "player_id": 10, "lineup_slot": 1, "bats": "R",
        "opp_sp_throws": "L", "opp_sp_source": "probable", "is_home": 1,
        "venue": "Test Park",
    }
    for name in PROFILE_FIELDS:
        key = f"hitter_{name}"
        row[key] = 1 if name in {"pitch_count", "pa", "bip"} else 0.0
    row["hitter_single_rate"] = 1.0
    second = dict(row)
    second.update({"season": 2024, "game_date": "2024-09-30", "game_pk": 2})
    frame = pd.DataFrame([row, second])
    targets = frame[["game_pk", "player_id"]].copy()
    missing = {f"hitter_{name}": float(frame[f"hitter_{name}"].isna().mean()) for name in PROFILE_FIELDS}
    manifest = {"artifact": {"rows": 2, "feature_missing_rate": missing}}
    contract = {
        "classifier_feature_groups": {
            "canonical_hitter_46d": [f"hitter_{name}" for name in PROFILE_FIELDS],
            "raw_pregame_context": ["bats", "opp_sp_throws", "is_home", "venue"],
        },
        "quarantined_from_classifier": ["lineup_slot", "opp_sp_k9"],
    }
    return frame, targets, manifest, contract


def main() -> int:
    frame, targets, manifest, contract = fixture()
    checks = [("valid fixture", validate_frame(frame, targets, manifest, contract)["rows"] == 2)]
    duplicate = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    duplicate_manifest = copy.deepcopy(manifest)
    duplicate_manifest["artifact"]["rows"] = 3
    checks.extend([
        ("duplicate identity rejected", expect_failure(lambda: validate_frame(duplicate, targets, duplicate_manifest, contract))),
        ("confirmation season rejected", expect_failure(lambda: validate_frame(frame.assign(season=2025), targets, manifest, contract))),
        ("outcome column rejected", expect_failure(lambda: validate_frame(frame.assign(out_hits=1), targets, manifest, contract))),
        ("rate range rejected", expect_failure(lambda: validate_frame(frame.assign(hitter_k_rate=1.1), targets, manifest, contract))),
        ("count relation rejected", expect_failure(lambda: validate_frame(frame.assign(hitter_pa=2), targets, manifest, contract))),
        ("postgame pitcher rejected", expect_failure(lambda: validate_frame(frame.assign(opp_sp_source="actual_starter"), targets, manifest, contract))),
        ("identity coverage rejected", expect_failure(lambda: validate_frame(frame.assign(player_id=11), targets, manifest, contract))),
        ("missingness mutation rejected", expect_failure(lambda: validate_frame(frame.assign(hitter_xba=None), targets, manifest, contract))),
    ])
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"canonical artifact validator checks failed: {failed}")
    print(f"CANONICAL ARTIFACT VALIDATOR VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
