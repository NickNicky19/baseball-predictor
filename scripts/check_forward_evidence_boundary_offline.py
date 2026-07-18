#!/usr/bin/env python3
"""Mutation checks for the locked prospective first-economic-look boundary."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.forward_evidence_boundary import (  # noqa: E402
    ForwardEvidenceBoundaryError,
    load_forward_evidence_boundary,
    validate_boundary_mapping,
)


PASS = 0
FAIL = 0


def check(value: bool, label: str) -> None:
    global PASS, FAIL
    if value:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def rejects(payload: dict) -> bool:
    try:
        validate_boundary_mapping(payload, root=ROOT)
    except (OSError, ValueError, ForwardEvidenceBoundaryError):
        return True
    return False


def main() -> int:
    path = ROOT / "config/forward_shadow_evidence_boundary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    boundary = load_forward_evidence_boundary(path, root=ROOT)
    check(
        boundary.minimum_complete_official_date_blocks == 56
        and boundary.capture_lower_bound == 0.10
        and boundary.roi_lower_bound == 0.0
        and len(boundary.sha256) == 64,
        "37 March-April plus 19 June open dates lock a hash-bound 56-date first look",
    )

    changed = copy.deepcopy(payload)
    changed["first_economic_look_boundary"]["minimum_complete_official_date_blocks"] = 55
    check(rejects(changed), "MUTATION a smaller first-look boundary hard-fails")

    changed = copy.deepcopy(payload)
    changed["open_data_derivation"]["may_2026_used"] = True
    check(rejects(changed), "MUTATION sealed May cannot derive the boundary")

    changed = copy.deepcopy(payload)
    changed["first_economic_look_boundary"]["operational_smoke_counts"] = True
    check(rejects(changed), "MUTATION operational smoke cannot enter economic evidence")

    changed = copy.deepcopy(payload)
    changed["locked_first_look_evaluation"]["capture_lower_95_bound_strictly_greater_than"] = 0.09
    check(rejects(changed), "MUTATION the +0.10 capture lower-bound gate cannot be lowered")

    changed = copy.deepcopy(payload)
    changed["locked_first_look_evaluation"]["net_roi_lower_95_bound_strictly_greater_than"] = -0.01
    check(rejects(changed), "MUTATION the positive net-ROI lower-bound gate cannot be lowered")

    changed = copy.deepcopy(payload)
    changed["scope"]["execution_products_evaluated_separately"] = ["prizepicks"]
    check(rejects(changed), "MUTATION execution products cannot be pooled or silently removed")

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
