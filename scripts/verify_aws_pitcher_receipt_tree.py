#!/usr/bin/env python3
"""Verify one copied AWS T-4 pitcher-receipt tree without network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.report_aws_pitcher_receipt_health import build_report
from scripts.run_forward_pitcher_context_collector import load_runtime
from src.evaluation.forward_pitcher_context import games_from_raw_schedule_response
from src.evaluation.shadow_capture_plan import (
    ShadowCapturePlan,
    assert_capture_date_permitted,
    canonical_schedule_records,
    plan_from_schedule,
)


class AWSReceiptVerificationError(ValueError):
    """The copied receipt tree cannot prove its source-to-ledger chain."""


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AWSReceiptVerificationError(f"cannot load {label}") from exc
    if not isinstance(value, dict):
        raise AWSReceiptVerificationError(f"{label} must be an object")
    return value


def verify_tree(
    *,
    evidence_root: Path,
    official_date: str,
    runtime_path: Path,
    assessed_at: datetime | None = None,
) -> dict[str, Any]:
    date_value = assert_capture_date_permitted(official_date)
    runtime, runtime_sha = load_runtime(runtime_path)
    plan_path = evidence_root / "plans" / f"{date_value}.plan.json"
    plan = ShadowCapturePlan.from_mapping(_object(plan_path, "receipt plan"))
    if plan.official_game_date != date_value:
        raise AWSReceiptVerificationError("plan date differs from requested verification date")

    receipt_path = (
        evidence_root
        / "plan-receipts"
        / "plans"
        / f"{date_value}.{plan.plan_sha256}.json"
    )
    receipt = _object(receipt_path, "plan source receipt")
    receipt_hash = str(receipt.get("receipt_sha256", ""))
    unsigned_receipt = dict(receipt)
    unsigned_receipt.pop("receipt_sha256", None)
    if hashlib.sha256(_canonical_bytes(unsigned_receipt)).hexdigest() != receipt_hash:
        raise AWSReceiptVerificationError("plan source receipt hash differs")
    expected_receipt = {
        "schema_version": "aws-pitcher-receipt-plan-receipt-v1",
        "official_game_date": date_value,
        "plan_sha256": plan.plan_sha256,
        "runtime_sha256": runtime_sha,
        "source_name": runtime["source"]["name"],
        "targets": len(plan.targets),
        "research_only": True,
        "betting_authorized": False,
        "model_or_market_accessed": False,
    }
    for key, expected in expected_receipt.items():
        if receipt.get(key) != expected:
            raise AWSReceiptVerificationError(f"plan source receipt {key} differs")

    source_sha = str(receipt.get("source_payload_sha256", ""))
    raw_path = evidence_root / "plan-receipts" / "raw" / f"{date_value}.{source_sha}.json"
    try:
        raw_bytes = raw_path.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AWSReceiptVerificationError("cannot load retained plan source payload") from exc
    if hashlib.sha256(raw_bytes).hexdigest() != source_sha:
        raise AWSReceiptVerificationError("retained plan source payload hash differs")
    games = games_from_raw_schedule_response(raw, allow_empty_date=True)
    replay = plan_from_schedule(
        official_game_date=date_value,
        entry_hours=runtime["scheduler"]["entry_hours"],
        policy_sha256=runtime_sha,
        schedule_snapshot=canonical_schedule_records(games),
    )
    if replay.to_dict() != plan.to_dict():
        raise AWSReceiptVerificationError("retained official source does not reproduce the plan")

    received_at = datetime.fromisoformat(str(receipt.get("received_at_utc", "")).replace("Z", "+00:00"))
    if received_at.tzinfo is None:
        raise AWSReceiptVerificationError("plan source receipt time lacks timezone")
    if any(
        received_at >= datetime.fromisoformat(target.entry_target_at_utc.replace("Z", "+00:00"))
        for target in plan.targets
    ):
        raise AWSReceiptVerificationError("plan source was received after a T-4 target was due")

    assessment = assessed_at or datetime.now(timezone.utc)
    health = build_report(
        plan_dir=evidence_root / "plans",
        ledger_root=evidence_root / "ledgers",
        runtime_path=runtime_path,
        assessed_at=assessment,
    )
    if health["plan_count"] != 1 or health["plans"][0].get("plan_sha256") != plan.plan_sha256:
        raise AWSReceiptVerificationError("health assessment did not bind exactly one expected plan")
    if health["status"] != "healthy":
        raise AWSReceiptVerificationError("receipt health is alert")
    result = {
        "schema_version": "aws-pitcher-receipt-independent-verification-v1",
        "official_game_date": date_value,
        "plan_sha256": plan.plan_sha256,
        "runtime_sha256": runtime_sha,
        "source_payload_sha256": source_sha,
        "targets": len(plan.targets),
        "health": health,
        "research_only": True,
        "betting_authorized": False,
        "model_or_market_accessed": False,
    }
    result["verification_sha256"] = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--runtime", type=Path, default=ROOT / "config/forward_pitcher_context_runtime_v1.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_tree(
            evidence_root=args.evidence_root,
            official_date=args.date,
            runtime_path=args.runtime,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(_canonical_bytes(result))
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, RuntimeError, AWSReceiptVerificationError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
