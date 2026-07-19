#!/usr/bin/env python3
"""Offline mutations for the future HR expected-target coverage boundary."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_full_game_forward_target_plan import (  # noqa: E402
    HRFullGameForwardTargetPlanError,
    load_target_plan,
    verify_terminal_coverage,
)

CONTRACT = ROOT / "config/hr_full_game_forward_target_plan_contract_v1.json"
BASE = ROOT / "config/hr_full_game_forward_input_contract_v1.json"


def must_fail(callable_, label: str) -> None:
    try:
        callable_()
    except HRFullGameForwardTargetPlanError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def plan() -> dict:
    start = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    target = start - timedelta(hours=4)
    row = {
        "mlb_game_pk": 123,
        "player_id": 456,
        "official_start_utc": start.isoformat(),
        "target_horizon_utc": target.isoformat(),
        "source_observation_utc": target.isoformat(),
        "hard_model_key": "123:456:home_runs:0.5:over",
    }
    return {
        "schema_version": "hr-full-game-forward-target-plan-v1",
        "plan_id": "synthetic",
        "plan_receipt_utc": target.isoformat(),
        "base_contract_sha256": hashlib.sha256(BASE.read_bytes()).hexdigest(),
        "collector_code_sha256": "a" * 64,
        "runtime_manifest_sha256": "b" * 64,
        "targets": [row],
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "plan.json"
        value = plan()
        path.write_text(json.dumps(value), encoding="utf-8")
        keys = load_target_plan(plan_path=path, target_plan_contract_path=CONTRACT)
        if keys != {"123:456:home_runs:0.5:over"}:
            raise AssertionError("valid plan did not load")
        mutations = [
            ("post-horizon receipt", lambda p: p.update(plan_receipt_utc=p["targets"][0]["official_start_utc"])),
            ("post-horizon source", lambda p: p["targets"][0].update(source_observation_utc=p["targets"][0]["official_start_utc"])),
            ("duplicate target", lambda p: p["targets"].append(copy.deepcopy(p["targets"][0]))),
            ("identity mismatch", lambda p: p["targets"][0].update(hard_model_key="wrong")),
            ("wrong horizon", lambda p: p["targets"][0].update(target_horizon_utc=p["targets"][0]["official_start_utc"])),
            ("base hash drift", lambda p: p.update(base_contract_sha256="0" * 64)),
        ]
        for label, mutate in mutations:
            candidate = copy.deepcopy(value)
            mutate(candidate)
            path.write_text(json.dumps(candidate), encoding="utf-8")
            must_fail(lambda: load_target_plan(plan_path=path, target_plan_contract_path=CONTRACT), label)
        path.write_text(json.dumps(value), encoding="utf-8")
        ledger = Path(temporary) / "ledger"
        ledger.mkdir()
        (ledger / "terminal_index.json").write_text(json.dumps({"targets": {next(iter(keys)): {}}}), encoding="utf-8")
        if verify_terminal_coverage(target_keys=keys, ledger_root=ledger) != 1:
            raise AssertionError("complete target coverage failed")
        (ledger / "terminal_index.json").write_text(json.dumps({"targets": {}}), encoding="utf-8")
        must_fail(lambda: verify_terminal_coverage(target_keys=keys, ledger_root=ledger), "missing planned terminal")
        (ledger / "terminal_index.json").write_text(json.dumps({"targets": {"extra": {}}}), encoding="utf-8")
        must_fail(lambda: verify_terminal_coverage(target_keys=keys, ledger_root=ledger), "unplanned terminal")
    print("8/8 mutations rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
