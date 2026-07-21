#!/usr/bin/env python3
"""Fail-closed audit of whether another shared-PA research step is admissible."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""): digest.update(chunk)
    return digest.hexdigest()


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None: raise ValueError("timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def audit_attempt(plan: dict[str, Any], manifest: dict[str, Any], terminal: dict[str, Any], *, as_of: datetime) -> dict[str, Any]:
    targets = plan.get("targets")
    if not isinstance(targets, list) or not targets: raise ValueError("receipt plan has no targets")
    target_ids = [str(row.get("target_id", "")) for row in targets]
    if any(not value for value in target_ids) or len(target_ids) != len(set(target_ids)):
        raise ValueError("receipt plan target identity missing or duplicated")
    if manifest.get("target_ids") != sorted(target_ids):
        raise ValueError("ledger target identities differ from plan")
    records = int(manifest.get("records", -1))
    chain = manifest.get("entry_chain_order")
    terminal_targets = terminal.get("targets")
    if not isinstance(chain, list) or not isinstance(terminal_targets, dict):
        raise ValueError("ledger chain or terminal index malformed")
    if records != len(chain): raise ValueError("ledger record count differs from chain length")
    if set(terminal_targets).difference(target_ids): raise ValueError("terminal index contains an unknown target")
    past = [row for row in targets if parse_utc(str(row["entry_target_at_utc"])) < as_of]
    receipt_complete = records >= len(targets) and len(terminal_targets) == len(targets)
    if receipt_complete and any(target_id not in terminal_targets for target_id in target_ids):
        raise ValueError("receipt-complete claim lacks a terminal target")
    return {"targets": len(targets), "past_targets": len(past), "records": records,
            "terminal_targets": len(terminal_targets), "receipt_complete": receipt_complete,
            "permanently_missed_targets": len(past) if records == 0 else max(0, len(past) - len(terminal_targets))}


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists(): raise FileExistsError(f"refusing to overwrite admissibility audit: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2, allow_nan=False); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def run(*, contract: Path, receipt_root: Path, output: Path) -> dict[str, Any]:
    policy = json.loads(contract.read_text(encoding="utf-8"))
    if policy.get("status") != "READ_ONLY_ADMISSIBILITY_AUDIT_LOCKED": raise ValueError("admissibility contract is not locked")
    protected = policy.get("protected", {})
    if not all(protected.get(key) is True for key in ("may_2026_sealed", "spent_2025_hr_not_reusable", "operational_smoke_non_economic", "collector_runtime_must_not_be_started_or_modified", "historical_starters_must_not_be_backfilled")):
        raise ValueError("admissibility contract weakens a protected boundary")
    rejections = []
    for item in policy["required_rejections"]:
        path = contract.parents[1] / item["path"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != item["status"]: raise ValueError(f"rejection status changed: {item['id']}")
        rejections.append({"id": item["id"], "path": item["path"], "sha256": sha256_file(path), "status": item["status"]})
    as_of = parse_utc(policy["as_of_utc"])
    attempts = []
    locations = {
        "collector_runtime_6383": ("plans/capture_plan_2026-07-20.json", "ledger_2026-07-20/manifest.json", "ledger_2026-07-20/terminal_index.json"),
        "collector_runtime_e02d": ("pitcher_context_plan_2026-07-20.json", "pitcher_context_ledger_2026-07-20/manifest.json", "pitcher_context_ledger_2026-07-20/terminal_index.json"),
    }
    for item in policy["receipt_attempts"]:
        plan_rel, manifest_rel, terminal_rel = locations[item["id"]]
        paths = [receipt_root / plan_rel, receipt_root / manifest_rel, receipt_root / terminal_rel]
        expected = [item["plan_sha256"], item["manifest_sha256"], item["terminal_index_sha256"]]
        for path, digest in zip(paths, expected):
            if sha256_file(path) != digest: raise ValueError(f"receipt artifact hash mismatch: {path}")
        result = audit_attempt(*(json.loads(path.read_text(encoding="utf-8")) for path in paths), as_of=as_of)
        attempts.append({"id": item["id"], **result, "hashes": {"plan": expected[0], "manifest": expected[1], "terminal_index": expected[2]}})
    pitcher_pass = any(item["receipt_complete"] for item in attempts)
    result = {
        "schema_version": "shared-pa-next-action-admissibility-audit-v1",
        "status": "NO_ADMISSIBLE_NEW_CANDIDATE_FRESH_EVIDENCE_REQUIRED" if not pitcher_pass else "PITCHER_RECEIPT_BLOCK_ELIGIBLE_FOR_PREDECLARATION",
        "contract": {"path": str(contract), "sha256": sha256_file(contract)},
        "rejected_families": rejections,
        "receipt_attempts": attempts,
        "pitcher_identity_contract_passed": pitcher_pass,
        "batter_only_next_candidate_permitted_on_spent_outcomes": False,
        "measured_limiter": "All adjudicated batter-only families miss materiality; the only unadjudicated measured input family lacks prospective identity receipts.",
        "highest_value_next_action": "On a future untouched regular-season slate, capture and certify complete T-minus-4 probable-starter receipts under the released lifecycle. Do not backfill July 20.",
        "confirmation_2025_reused": False, "may_2026_opened": False, "operational_smoke_used": False,
        "production_changed": False, "betting_authorized": False,
        "script_sha256": sha256_file(Path(__file__)),
    }
    atomic_json(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path); parser.add_argument("--receipt-root", required=True, type=Path); parser.add_argument("--output", required=True, type=Path)
    result = run(**vars(parser.parse_args())); print(json.dumps({"status": result["status"], "pitcher_identity_contract_passed": result["pitcher_identity_contract_passed"]}, sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
