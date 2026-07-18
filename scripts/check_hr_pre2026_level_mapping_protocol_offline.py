#!/usr/bin/env python3
"""Mutation checks for the locked HR level-mapping protocol."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_pre2026_level_mapping import (  # noqa: E402
    load_protocol,
    validate_protocol_payload,
)


PROTOCOL = (
    ROOT
    / "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/level_mapping_protocol_v4.json"
)


def must_fail(payload: dict, label: str) -> None:
    try:
        validate_protocol_payload(payload, verify_files=True)
    except ValueError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    original = load_protocol(PROTOCOL, verify_files=True)
    print("[OK] production protocol and every bound input hash validate")

    payload = copy.deepcopy(original)
    payload["inputs"]["pa_distribution"]["sha256"] = "0" * 64
    must_fail(payload, "tampered input hash")

    payload = copy.deepcopy(original)
    payload["betting_authorized"] = True
    must_fail(payload, "betting authorization")

    payload = copy.deepcopy(original)
    payload["may_2026_opened"] = True
    must_fail(payload, "May 2026 opened")

    payload = copy.deepcopy(original)
    payload["chronology"]["confirmation_dates_open_once"][0] = payload["chronology"][
        "calibration_dates"
    ][-1]
    must_fail(payload, "overlapping chronology")

    payload = copy.deepcopy(original)
    payload["mapping"]["candidate_features"].append("out_hr")
    must_fail(payload, "outcome feature")

    payload = copy.deepcopy(original)
    payload["chronology"]["baseline_history_rule"] = "one earlier game"
    must_fail(payload, "weakened input-availability rule")

    payload = copy.deepcopy(original)
    payload["chronology"]["builder_schema"] = "a3.1"
    must_fail(payload, "retired a3.1 builder schema")

    payload = copy.deepcopy(original)
    payload["chronology"]["population"] = "final_occupants"
    must_fail(payload, "retired final-occupant population")

    payload = copy.deepcopy(original)
    payload["reconstruction"]["simulation_seed"] = 18
    must_fail(payload, "wrong simulation seed")

    payload = copy.deepcopy(original)
    payload["success_condition"]["candidate_Brier_lower_than_raw_and_control"] = False
    must_fail(payload, "weakened success condition")

    print("\n11/11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
