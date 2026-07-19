#!/usr/bin/env python3
"""Verify v5 consolidation keeps the pitcher lifecycle non-economic and bound."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(payload: dict) -> None:
    if payload.get("status") != "ACTIVE_RESEARCH_FOUNDATION_CONSOLIDATED_NOT_BETTABLE":
        raise ValueError("status changed")
    if payload.get("betting_authorized") or payload.get("production_changed"):
        raise ValueError("scope changed")
    prior = payload["previous_consolidation"]
    if digest(ROOT / prior["path"]) != prior["sha256"]:
        raise ValueError("prior consolidation hash drift")
    prior_payload = json.loads((ROOT / prior["path"]).read_text(encoding="utf-8"))
    if prior_payload.get("protected_invariants", {}).get("may_2026_scope_incident_preserved_in_previous_status") is not True:
        raise ValueError("May incident preservation erased")
    lifecycle = payload["prospective_pitcher_context_lifecycle"]
    if lifecycle.get("terminal_states") != ["captured", "source_error", "missed"]:
        raise ValueError("terminal lifecycle changed")
    if lifecycle.get("live_observations") != 0 or lifecycle.get("economic_evidence_eligible") or lifecycle.get("betting_authorized"):
        raise ValueError("empty lifecycle was promoted")
    for key in ("ledger_source", "mutation_test"):
        record = lifecycle[key]
        if digest(ROOT / record["path"]) != record["sha256"]:
            raise ValueError(f"lifecycle hash drift: {key}")
    if lifecycle["mutation_test"].get("checks") != 6:
        raise ValueError("mutation evidence changed")
    if "future regular-season slates" not in payload.get("highest_value_next_action", {}).get("action", ""):
        raise ValueError("future-only action boundary missing")


def main() -> int:
    payload = json.loads((ROOT / "reports/multi_market_probability_foundation_status_v5.json").read_text(encoding="utf-8"))
    checks = []
    try:
        validate(payload); checks.append(("bound status", True))
    except ValueError:
        checks.append(("bound status", False))
    for label, mutate in (
        ("authorization mutation", lambda p: p.update(betting_authorized=True)),
        ("production mutation", lambda p: p.update(production_changed=True)),
        ("invented evidence", lambda p: p["prospective_pitcher_context_lifecycle"].update(live_observations=1)),
        ("economic promotion", lambda p: p["prospective_pitcher_context_lifecycle"].update(economic_evidence_eligible=True)),
        ("hash tamper", lambda p: p["prospective_pitcher_context_lifecycle"]["ledger_source"].update(sha256="0" * 64)),
    ):
        changed = copy.deepcopy(payload); mutate(changed)
        try:
            validate(changed)
        except ValueError:
            checks.append((label, True))
        else:
            checks.append((label, False))
    if not all(passed for _, passed in checks):
        raise SystemExit(f"foundation v5 checks failed: {checks}")
    print(f"MULTI-MARKET FOUNDATION V5 VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
