#!/usr/bin/env python3
"""Publish one immutable, future-only AWS probable-starter receipt plan.

The plan is created from a raw official MLB schedule response before any of
its targets are due.  It is an operational expectation record, not a model,
prediction, lineup, price, selection, outcome, or settlement artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_forward_pitcher_context_collector import fetcher, load_runtime
from src.evaluation.forward_pitcher_context import games_from_raw_schedule_response
from src.evaluation.shadow_capture_plan import (
    ShadowCapturePlan,
    ShadowCapturePlanError,
    assert_capture_date_permitted,
    canonical_schedule_records,
    plan_from_schedule,
)


class AWSReceiptPlanError(ValueError):
    """A plan cannot prove its schedule-source and time boundaries."""


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _atomic_publish_once(path: Path, payload: bytes) -> bool:
    """Publish exact bytes once; a different retry is a contract failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise AWSReceiptPlanError(f"immutable plan artifact already differs: {path.name}")
        return False
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        # A plan service is lock-protected.  This final existence check still
        # prevents a second publisher from silently replacing its evidence.
        if path.exists():
            if path.read_bytes() != payload:
                raise AWSReceiptPlanError(f"concurrent plan publication differs: {path.name}")
            return False
        os.replace(temporary, path)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _assert_not_conflicting(path: Path, payload: bytes) -> None:
    if path.exists() and path.read_bytes() != payload:
        raise AWSReceiptPlanError(f"immutable plan artifact already differs: {path.name}")


def build_plan(*, official_date: str, plan_dir: Path, receipt_dir: Path, runtime_path: Path, now: datetime | None = None) -> dict[str, Any]:
    date_value = assert_capture_date_permitted(official_date)
    runtime, runtime_sha = load_runtime(runtime_path)
    received = fetcher(runtime)(date_value)
    try:
        raw = json.loads(received.body.decode("utf-8"))
        games = games_from_raw_schedule_response(raw, allow_empty_date=True)
        records = canonical_schedule_records(games)
        plan = plan_from_schedule(
            official_game_date=date_value,
            entry_hours=runtime["scheduler"]["entry_hours"],
            policy_sha256=runtime_sha,
            schedule_snapshot=records,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ShadowCapturePlanError, ValueError) as exc:
        raise AWSReceiptPlanError("official schedule response cannot create a receipt plan") from exc
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise AWSReceiptPlanError("plan clock must include timezone")
    received_at = datetime.fromisoformat(received.received_at_utc.replace("Z", "+00:00"))
    due = [
        target
        for target in plan.targets
        if datetime.fromisoformat(target.entry_target_at_utc.replace("Z", "+00:00"))
        <= max(current.astimezone(timezone.utc), received_at)
    ]
    if due:
        raise AWSReceiptPlanError("refusing to publish a plan after any T-4 target is due")
    raw_sha = hashlib.sha256(received.body).hexdigest()
    plan_bytes = _canonical_bytes(plan.to_dict())
    receipt = {
        "schema_version": "aws-pitcher-receipt-plan-receipt-v1",
        "official_game_date": date_value,
        "plan_sha256": plan.plan_sha256,
        "runtime_sha256": runtime_sha,
        "source_name": runtime["source"]["name"],
        "source_payload_sha256": raw_sha,
        "received_at_utc": received.received_at_utc,
        "targets": len(plan.targets),
        "research_only": True,
        "betting_authorized": False,
        "model_or_market_accessed": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(_canonical_bytes(receipt)).hexdigest()
    raw_path = receipt_dir / "raw" / f"{date_value}.{raw_sha}.json"
    receipt_path = receipt_dir / "plans" / f"{date_value}.{plan.plan_sha256}.json"
    plan_path = plan_dir / f"{date_value}.plan.json"
    # Validate all immutability boundaries before publishing any part of a
    # plan.  A conflicting retry must not leave a fresh raw/receipt side file.
    _assert_not_conflicting(raw_path, received.body)
    _assert_not_conflicting(receipt_path, _canonical_bytes(receipt))
    _assert_not_conflicting(plan_path, plan_bytes)
    raw_new = _atomic_publish_once(raw_path, received.body)
    receipt_new = _atomic_publish_once(receipt_path, _canonical_bytes(receipt))
    plan_new = _atomic_publish_once(plan_path, plan_bytes)
    return {
        "official_game_date": date_value,
        "plan_path": str(plan_path),
        "plan_sha256": plan.plan_sha256,
        "targets": len(plan.targets),
        "source_payload_sha256": raw_sha,
        "published": {"raw": raw_new, "receipt": receipt_new, "plan": plan_new},
        "research_only": True,
        "betting_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Official MLB date, never May 2026")
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=ROOT / "config/forward_pitcher_context_runtime_v1.json")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build_plan(
            official_date=args.date,
            plan_dir=args.plan_dir,
            receipt_dir=args.receipt_dir,
            runtime_path=args.runtime,
        ), sort_keys=True))
    except (OSError, ValueError, RuntimeError, AWSReceiptPlanError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
