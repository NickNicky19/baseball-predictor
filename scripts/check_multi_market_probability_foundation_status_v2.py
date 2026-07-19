#!/usr/bin/env python3
"""Verify the append-only v2 multi-market probability-foundation status."""
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


def validate(payload: dict, evidence_root: Path) -> None:
    if payload.get("status") != "ACTIVE_RESEARCH_FOUNDATION_CONSOLIDATED_NOT_BETTABLE":
        raise ValueError("foundation status is invalid")
    if payload.get("betting_authorized") or payload.get("production_changed"):
        raise ValueError("foundation status altered authorization or production")
    if payload.get("may_2026_scope_incident", {}).get("occurred") is not True:
        raise ValueError("May scope incident was erased")
    records = [
        (ROOT / payload["previous_consolidation"]["path"], payload["previous_consolidation"]["sha256"]),
        (ROOT / payload["new_evidence"]["nonregular_statcast_audit"]["path"], payload["new_evidence"]["nonregular_statcast_audit"]["sha256"]),
        (evidence_root / payload["new_evidence"]["regular_season_feature_certificate"]["path"], payload["new_evidence"]["regular_season_feature_certificate"]["sha256"]),
        (evidence_root / payload["new_evidence"]["regular_season_selection"]["report_path"], payload["new_evidence"]["regular_season_selection"]["report_sha256"]),
        (evidence_root / payload["new_evidence"]["regular_season_selection"]["certificate_path"], payload["new_evidence"]["regular_season_selection"]["certificate_sha256"]),
    ]
    for path, expected in records:
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"foundation evidence hash changed: {path}")
    selection = payload["new_evidence"]["regular_season_selection"]
    if selection.get("status") != "CANONICAL_SELECTION_REJECTION_CERTIFIED":
        raise ValueError("regular-season candidate was not certified rejected")
    if selection.get("confirmation_2025_opened") or selection.get("may_2026_opened") or selection.get("production_changed"):
        raise ValueError("rejected candidate crossed a protected boundary")
    guard = payload.get("repetition_guard", {}).get("forbidden_without_new_measured_limiter", [])
    if not guard or payload.get("highest_value_next_action", {}).get("action", "").startswith("Run an outcome-blind") is False:
        raise ValueError("rejection or next-action boundary is missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads((ROOT / "reports/multi_market_probability_foundation_status_v2.json").read_text(encoding="utf-8"))
    checks = []
    try:
        validate(payload, args.evidence_root.resolve())
        checks.append(("hash-bound status", True))
    except ValueError:
        checks.append(("hash-bound status", False))
    for label, mutate in (
        ("authorization mutation", lambda p: p.update(betting_authorized=True)),
        ("production mutation", lambda p: p.update(production_changed=True)),
        ("May incident erasure", lambda p: p["may_2026_scope_incident"].update(occurred=False)),
        ("selection promotion", lambda p: p["new_evidence"]["regular_season_selection"].update(status="CANONICAL_SELECTION_PASS_CERTIFIED")),
        ("confirmation opening", lambda p: p["new_evidence"]["regular_season_selection"].update(confirmation_2025_opened=True)),
        ("hash tamper", lambda p: p["new_evidence"]["nonregular_statcast_audit"].update(sha256="0" * 64)),
    ):
        altered = copy.deepcopy(payload)
        mutate(altered)
        try:
            validate(altered, args.evidence_root.resolve())
        except ValueError:
            checks.append((label, True))
        else:
            checks.append((label, False))
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"foundation v2 checks failed: {failed}")
    print(f"MULTI-MARKET FOUNDATION V2 VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
