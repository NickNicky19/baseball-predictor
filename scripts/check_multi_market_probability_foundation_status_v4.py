#!/usr/bin/env python3
"""Verify v4 consolidation and the non-predictive pitcher-input boundary."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(payload: dict) -> None:
    if payload.get("status") != "ACTIVE_RESEARCH_FOUNDATION_CONSOLIDATED_NOT_BETTABLE":
        raise ValueError("invalid foundation status")
    if payload.get("betting_authorized") or payload.get("production_changed"):
        raise ValueError("authorization or production mutation")
    previous = payload["previous_consolidation"]
    if sha256(ROOT / previous["path"]) != previous["sha256"]:
        raise ValueError("previous consolidation hash drift")
    v3 = json.loads((ROOT / previous["path"]).read_text(encoding="utf-8"))
    if v3.get("protected_invariants", {}).get("may_2026_scope_incident_preserved_in_v2") is not True:
        raise ValueError("May scope incident no longer preserved")
    contract = payload["new_prospective_input_contract"]
    if contract.get("live_observations") != 0 or contract.get("candidate_permitted") is not False:
        raise ValueError("uncollected input contract was promoted")
    if contract.get("source") != "official MLB Stats API schedule response":
        raise ValueError("pitcher source changed")
    for key in ("source_code", "publisher", "mutation_test"):
        record = contract[key]
        if sha256(ROOT / record["path"]) != record["sha256"]:
            raise ValueError(f"pitcher contract hash drift: {key}")
    if contract["mutation_test"].get("checks") != 10:
        raise ValueError("mutation test count changed")
    text = payload.get("next_permitted_action", {}).get("action", "")
    if "prospective regular-season T-4" not in text:
        raise ValueError("prospective collection boundary missing")


def main() -> int:
    payload = json.loads((ROOT / "reports/multi_market_probability_foundation_status_v4.json").read_text(encoding="utf-8"))
    checks = []
    try:
        validate(payload); checks.append(("hash-bound status", True))
    except ValueError:
        checks.append(("hash-bound status", False))
    for label, mutate in (
        ("authorization mutation", lambda p: p.update(betting_authorized=True)),
        ("production mutation", lambda p: p.update(production_changed=True)),
        ("premature candidate", lambda p: p["new_prospective_input_contract"].update(candidate_permitted=True)),
        ("invented live evidence", lambda p: p["new_prospective_input_contract"].update(live_observations=1)),
        ("source hash tamper", lambda p: p["new_prospective_input_contract"]["source_code"].update(sha256="0" * 64)),
    ):
        altered = copy.deepcopy(payload); mutate(altered)
        try:
            validate(altered)
        except ValueError:
            checks.append((label, True))
        else:
            checks.append((label, False))
    failed = [label for label, passed in checks if not passed]
    if failed:
        raise SystemExit(f"foundation v4 checks failed: {failed}")
    print(f"MULTI-MARKET FOUNDATION V4 VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
