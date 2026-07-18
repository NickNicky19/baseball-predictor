#!/usr/bin/env python3
"""Mutation checks for the locked a3.2 migration protocol."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_pre2026_a3_2_migration import load_protocol, validate_protocol  # noqa: E402


PROTOCOL = ROOT / "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/migration_protocol.json"


def must_fail(payload: dict, label: str) -> None:
    try:
        validate_protocol(payload, verify_files=True)
    except ValueError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    original = load_protocol(PROTOCOL, verify_files=True)
    print("[OK] production migration protocol and every source hash validate")
    changes = [
        ("source hash", lambda p: p["inputs"]["training_builder_source"].update(sha256="0" * 64)),
        ("dual-team canary", lambda p: p["known_failure_addressed"].update(game_pk=1)),
        ("Spring Training admitted", lambda p: p["game_contract"].update(allowed_game_types=["R", "S"])),
        ("final lineup population", lambda p: p["game_contract"].update(hitter_population="final battingOrder occupants")),
        ("old builder schema", lambda p: p.update(builder_schema="a3.1")),
        ("PA fit uses 2025", lambda p: p["derived_evidence_chronology"].update(pa_fit_seasons=[2023, 2024, 2025])),
        ("confirmation used for selection", lambda p: p["derived_evidence_chronology"].update(signal_selection_seasons=[2024, 2025])),
        ("May opened", lambda p: p.update(may_2026_opened=True)),
        ("betting authorized", lambda p: p.update(betting_authorized=True)),
        ("weakened success condition", lambda p: p["required_success_conditions"].update(unique_non_null_game_player_identity=False)),
    ]
    for label, mutate in changes:
        payload = copy.deepcopy(original)
        mutate(payload)
        must_fail(payload, label)
    print("11/11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
