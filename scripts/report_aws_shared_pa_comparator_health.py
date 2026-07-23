#!/usr/bin/env python3
"""Publish an immutable, outcome-free health snapshot for comparator capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shadow_capture_plan import ShadowCapturePlan  # noqa: E402
from src.evaluation.shared_pa_comparator_runtime import canonical_bytes, horizon_batch_id, publish_once, read_object_bytes  # noqa: E402


def build_report(*, plan_dir: Path, evidence_root: Path, assessed_at: datetime) -> dict:
    current = assessed_at.astimezone(timezone.utc)
    day = current.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    if day.startswith("2026-05-"):
        return {
            "schema_version": "shared-pa-comparator-health-v1",
            "official_game_date": day,
            "assessed_at_utc": current.isoformat().replace("+00:00", "Z"),
            "state": "sealed_may_no_access",
            "report_written": False,
            "research_only": True,
            "betting_authorized": False,
        }
    plan = ShadowCapturePlan.from_mapping(read_object_bytes(
        (plan_dir / f"{day}.plan.json").read_bytes(), "health pitcher plan"
    ))
    root = evidence_root / "pregame" / day / plan.plan_sha256
    records = evidence_root / "records" / day
    target_rows = []
    overdue_missing = 0
    for target in plan.targets:
        horizon = datetime.fromisoformat(target.entry_target_at_utc.replace("Z", "+00:00"))
        terminal = records / target.target_id / "terminal.json"
        prediction_batch = root / "prediction_batches" / horizon_batch_id(plan, target)
        prediction_available = (prediction_batch / "manifest.json").is_file()
        market_dir = root / "markets" / target.target_id
        row = {
            "target_id": target.target_id,
            "target_horizon_utc": target.entry_target_at_utc,
            "prediction_batch_available": prediction_available,
            "market_capture_available": (market_dir / "capture" / "manifest.json").is_file(),
            "market_capture_terminal_missed": (market_dir / "terminal_missed.json").is_file(),
            "comparator_terminal_available": terminal.is_file(),
        }
        if current > horizon + timedelta(minutes=5) and not terminal.is_file():
            overdue_missing += 1
        target_rows.append(row)
    credential_configured = bool(os.environ.get("ODDS_API_KEY", "").strip())
    state = "healthy" if overdue_missing == 0 and credential_configured else "attention_required"
    return {
        "schema_version": "shared-pa-comparator-health-v1",
        "official_game_date": day,
        "assessed_at_utc": current.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "plan_sha256": plan.plan_sha256,
        "expected_targets": len(plan.targets),
        "overdue_missing_terminal_targets": overdue_missing,
        "provider_credential_configured": credential_configured,
        "state": state,
        "targets": target_rows,
        "outcomes_or_settlement_accessed": False,
        "research_only": True,
        "betting_authorized": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_report(
            plan_dir=args.plan_dir, evidence_root=args.evidence_root,
            assessed_at=datetime.now(timezone.utc),
        )
        if report.get("report_written") is False:
            print(json.dumps(report, sort_keys=True))
            return 0
        payload = canonical_bytes(report)
        digest = hashlib.sha256(payload).hexdigest()
        path = args.report_dir / str(report["official_game_date"]) / f"{digest}.json"
        publish_once(path, payload)
        print(json.dumps({**report, "report_sha256": digest}, sort_keys=True))
        return 0 if report["state"] == "healthy" else 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
