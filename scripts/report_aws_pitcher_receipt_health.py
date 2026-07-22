#!/usr/bin/env python3
"""Write a hash-addressed, non-economic health report for AWS receipts."""
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

from scripts.run_forward_pitcher_context_collector import load_runtime
from src.evaluation.forward_pitcher_context_ledger import ForwardPitcherContextLedger, ForwardPitcherContextLedgerError
from src.evaluation.shadow_capture_plan import ShadowCapturePlan, ShadowCapturePlanError


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _load_plan(path: Path) -> ShadowCapturePlan:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return ShadowCapturePlan.from_mapping(row)
    except (OSError, json.JSONDecodeError, ShadowCapturePlanError) as exc:
        raise ValueError(f"invalid receipt plan {path.name}") from exc


def _publish_once(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("health-report hash path has conflicting bytes")
        return path
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        if path.exists() and path.read_bytes() != payload:
            raise ValueError("concurrent health report differs")
        if not path.exists():
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def build_report(*, plan_dir: Path, ledger_root: Path, runtime_path: Path, assessed_at: datetime | None = None) -> dict[str, Any]:
    runtime, runtime_sha = load_runtime(runtime_path)
    now = assessed_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("health assessment clock needs timezone")
    assessed = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows: list[dict[str, Any]] = []
    for plan_path in sorted(plan_dir.glob("*.plan.json")) if plan_dir.is_dir() else []:
        try:
            plan = _load_plan(plan_path)
            verdict = {
                "complete_due_targets": True, "state_counts": {"captured": 0, "source_error": 0, "missed": 0},
                "missing_due_target_ids": [], "due_targets": 0, "terminal_targets": 0,
                "candidate_input_eligible_captured": 0,
                "candidate_input_ineligible_captured": 0,
            }
            if plan.targets:
                ledger = ForwardPitcherContextLedger(
                    ledger_root / plan.official_game_date / plan.plan_sha256,
                    plan,
                    runtime_sha,
                )
                verdict = ledger.verify(assessed_at_utc=assessed)
            states = verdict["state_counts"]
            if not verdict["complete_due_targets"]:
                status = "missing_terminal_receipts"
            elif states["source_error"] or states["missed"]:
                status = "terminal_collection_failure"
            else:
                status = "healthy"
            rows.append({"plan": plan_path.name, "plan_sha256": plan.plan_sha256, "targets": len(plan.targets), "status": status, "ledger": verdict})
        except (OSError, ValueError, ForwardPitcherContextLedgerError) as exc:
            rows.append({"plan": plan_path.name, "status": "invalid", "detail": type(exc).__name__})
    report = {
        "schema_version": "aws-pitcher-receipt-health-v1",
        "assessed_at_utc": assessed,
        "runtime_sha256": runtime_sha,
        "plan_count": len(rows),
        "plans": rows,
        "status": "healthy" if rows and all(row["status"] == "healthy" for row in rows) else "alert",
        "research_only": True,
        "betting_authorized": False,
        "model_or_market_accessed": False,
    }
    report["report_sha256"] = hashlib.sha256(_canonical_bytes(report)).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--ledger-root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=ROOT / "config/forward_pitcher_context_runtime_v1.json")
    args = parser.parse_args(argv)
    try:
        report = build_report(plan_dir=args.plan_dir, ledger_root=args.ledger_root, runtime_path=args.runtime)
        stamp = report["assessed_at_utc"].replace(":", "").replace("-", "")
        path = _publish_once(args.report_dir / f"{stamp}.{report['report_sha256']}.json", _canonical_bytes(report))
        print(json.dumps({"health_report_path": str(path), **report}, sort_keys=True))
        if report["status"] != "healthy":
            print("[FAIL] pitcher receipt health is alert; immutable report retained", file=sys.stderr)
            return 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
