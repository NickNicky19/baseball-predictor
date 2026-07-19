#!/usr/bin/env python3
"""Offline checks that prevent legacy GBM MAE evidence from being promoted."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.legacy_gbm_eligibility import (  # noqa: E402
    LegacyGBMEligibilityError,
    audit_legacy_gbm_report,
)


def must_fail(callable_, label: str) -> None:
    try:
        callable_()
    except LegacyGBMEligibilityError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "legacy.json"
        base = {"comparison": {"categories": {"hits": {"baseline_mae": 1.0, "candidate_mae": 0.9}}}}
        path.write_text(json.dumps(base), encoding="utf-8")
        result = audit_legacy_gbm_report(path)
        if result["status"] != "INELIGIBLE_AS_PROBABILITY_OR_MARKET_CHALLENGER":
            raise AssertionError("legacy report was not classified as ineligible")
        promoted = dict(base)
        promoted["production_promoted"] = True
        path.write_text(json.dumps(promoted), encoding="utf-8")
        must_fail(lambda: audit_legacy_gbm_report(path), "production promotion")
        authorized = dict(base)
        authorized["betting_authorized"] = True
        path.write_text(json.dumps(authorized), encoding="utf-8")
        must_fail(lambda: audit_legacy_gbm_report(path), "betting authorization")
        path.write_text(json.dumps({"comparison": {"categories": {}}}), encoding="utf-8")
        must_fail(lambda: audit_legacy_gbm_report(path), "empty category result")
    print("3/3 mutations rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
