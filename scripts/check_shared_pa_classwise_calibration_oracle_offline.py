#!/usr/bin/env python3
"""Boundary mutations for the non-deployable calibration oracle screen."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_shared_pa_classwise_calibration_oracle import audit  # noqa: E402


REPORT = ROOT.parent / "data/analysis/shared_pa_regular_season_v1/selection/selection_report.json"


def fails(payload: dict) -> bool:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "selection_report.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            audit(path)
        except ValueError:
            return True
    return False


def main() -> int:
    original = json.loads(REPORT.read_text(encoding="utf-8"))
    valid = audit(REPORT)
    checks = [
        ("valid oracle screen", valid["status"] == "ORACLE_FAMILY_NOT_ELIGIBLE_FOR_CHALLENGER"),
        ("selection promotion rejected", fails(copy.deepcopy(original) | {"status": "SELECTION_PASSED_CANDIDATE_FROZEN"})),
        ("confirmation access rejected", fails(copy.deepcopy(original) | {"confirmation_2025_opened": True})),
        ("May access rejected", fails(copy.deepcopy(original) | {"may_2026_opened": True})),
    ]
    hash_mutation = copy.deepcopy(original)
    hash_mutation["oof_artifact"]["sha256"] = "0" * 64
    checks.append(("OOF hash tamper rejected", fails(hash_mutation)))
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"classwise oracle checks failed: {failed}")
    print(f"CLASSWISE CALIBRATION ORACLE VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
