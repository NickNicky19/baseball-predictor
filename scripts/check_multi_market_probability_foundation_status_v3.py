#!/usr/bin/env python3
"""Verify the append-only v3 probability-foundation status and oracle rejection."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(payload: dict) -> None:
    if payload.get("status") != "ACTIVE_RESEARCH_FOUNDATION_CONSOLIDATED_NOT_BETTABLE":
        raise ValueError("foundation status is invalid")
    if payload.get("betting_authorized") or payload.get("production_changed"):
        raise ValueError("foundation status altered authorization or production")
    previous = payload["previous_consolidation"]
    oracle = payload["additional_rejection"]["classwise_calibration_oracle"]
    for record in (previous, oracle):
        path = ROOT / record["path"]
        if not path.is_file() or sha256(path) != record["sha256"]:
            raise ValueError(f"foundation evidence hash changed: {path}")
    if oracle.get("status") != "ORACLE_FAMILY_NOT_ELIGIBLE_FOR_CHALLENGER":
        raise ValueError("oracle rejection was altered")
    oracle_payload = json.loads((ROOT / oracle["path"]).read_text(encoding="utf-8"))
    if oracle_payload.get("status") != "ORACLE_FAMILY_NOT_ELIGIBLE_FOR_CHALLENGER":
        raise ValueError("oracle report status is invalid")
    scope = oracle_payload.get("scope", {})
    if any(scope.get(key) for key in ("confirmation_2025_read", "may_2026_read", "production_changed", "betting_authorized")):
        raise ValueError("oracle crossed a protected boundary")
    decision = oracle_payload.get("decision", {})
    if decision.get("cross_fitted_classwise_calibration_candidate_permitted") is not False:
        raise ValueError("rejected calibration family was permitted")
    v2 = json.loads((ROOT / previous["path"]).read_text(encoding="utf-8"))
    if v2.get("may_2026_scope_incident", {}).get("occurred") is not True:
        raise ValueError("May scope incident was erased from prior status")
    next_action = payload.get("next_permitted_action", {}).get("action", "")
    if "prospective T-4 opposing-pitcher" not in next_action:
        raise ValueError("next-action boundary is missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    payload = json.loads((ROOT / "reports/multi_market_probability_foundation_status_v3.json").read_text(encoding="utf-8"))
    checks = []
    try:
        validate(payload)
        checks.append(("hash-bound status", True))
    except ValueError:
        checks.append(("hash-bound status", False))
    for label, mutate in (
        ("authorization mutation", lambda p: p.update(betting_authorized=True)),
        ("production mutation", lambda p: p.update(production_changed=True)),
        ("oracle promotion", lambda p: p["additional_rejection"]["classwise_calibration_oracle"].update(status="ORACLE_FAMILY_ELIGIBLE")),
        ("candidate permission", lambda p: p["additional_rejection"]["classwise_calibration_oracle"].update(status="ORACLE_FAMILY_ELIGIBLE_FOR_CHALLENGER")),
        ("hash tamper", lambda p: p["additional_rejection"]["classwise_calibration_oracle"].update(sha256="0" * 64)),
    ):
        altered = copy.deepcopy(payload)
        mutate(altered)
        try:
            validate(altered)
        except ValueError:
            checks.append((label, True))
        else:
            checks.append((label, False))
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"foundation v3 checks failed: {failed}")
    print(f"MULTI-MARKET FOUNDATION V3 VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
