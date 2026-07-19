#!/usr/bin/env python3
"""Offline append-only and provenance mutation tests for the future HR ledger."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_full_game_forward_ledger import (  # noqa: E402
    HRFullGameForwardLedgerError,
    append_terminal_record,
    verify_ledger,
)
from scripts.check_hr_full_game_forward_contract_offline import sample_record  # noqa: E402

CONTRACT = ROOT / "config" / "hr_full_game_forward_input_contract_v1.json"


def must_fail(callable_, label: str) -> None:
    try:
        callable_()
    except HRFullGameForwardLedgerError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "ledger"
        raw = b'{"synthetic":"pregame"}\n'
        record = sample_record()
        import hashlib
        record["raw_source_payload_sha256"] = hashlib.sha256(raw).hexdigest()
        chain = append_terminal_record(root=root, contract_path=CONTRACT, record=record, raw_payload=raw)
        if len(chain) != 64 or verify_ledger(root=root, contract_path=CONTRACT) != 1:
            raise AssertionError("valid ledger did not verify")
        excluded = {
            "mlb_game_pk": 123456,
            "player_id": 654322,
            "hard_model_key": "123456:654322:home_runs:0.5:over",
            "terminal_state": "missed_before_horizon",
        }
        append_terminal_record(root=root, contract_path=CONTRACT, record=excluded, raw_payload=None)
        if verify_ledger(root=root, contract_path=CONTRACT) != 2:
            raise AssertionError("two-entry ledger did not preserve append order")
        print("[OK] atomic complete-record ledger verifies")

        must_fail(lambda: append_terminal_record(root=root, contract_path=CONTRACT, record=record, raw_payload=raw), "duplicate terminal target")
        changed_raw = copy.deepcopy(record)
        changed_raw["player_id"] = 654322
        changed_raw["hard_model_key"] = "123456:654322:home_runs:0.5:over"
        must_fail(lambda: append_terminal_record(root=root, contract_path=CONTRACT, record=changed_raw, raw_payload=b"wrong"), "raw payload hash mismatch")

        orphan = root / "raw" / ("0" * 64 + ".json")
        orphan.write_bytes(b"orphan")
        must_fail(lambda: verify_ledger(root=root, contract_path=CONTRACT), "orphaned raw payload")
        orphan.unlink()
        if verify_ledger(root=root, contract_path=CONTRACT) != 2:
            raise AssertionError("ledger did not recover after test cleanup")

        entry_path = next((root / "records").glob("*.json"))
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
        entry["record"]["full_game_p_over_0_5"] = 0.99
        entry_path.write_text(json.dumps(entry), encoding="utf-8")
        must_fail(lambda: verify_ledger(root=root, contract_path=CONTRACT), "mutated committed probability")
    print("4/4 ledger mutations rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
