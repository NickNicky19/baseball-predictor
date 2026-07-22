#!/usr/bin/env python3
"""Verify and publish immutable non-economic health for shared PA evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_aws_shared_pa_forward_tick import collector_code_sha256, load_runtime  # noqa: E402
from src.evaluation.shadow_capture_plan import ShadowCapturePlan  # noqa: E402
from src.evaluation.shared_pa_forward_evidence import load_forward_contract  # noqa: E402
from src.evaluation.shared_pa_forward_ledger import SharedPAForwardLedger  # noqa: E402
from src.evaluation.shared_pa_forward_ledger import side_target_id  # noqa: E402


def _atomic_publish_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("immutable health report path already differs")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_plan(path: Path) -> ShadowCapturePlan:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("plan root must be an object")
    return ShadowCapturePlan.from_mapping(value)


def build_report(
    *, plan_dir: Path, ledger_root: Path, runtime_path: Path, assessed_at: datetime
) -> dict[str, Any]:
    runtime, runtime_sha = load_runtime(runtime_path)
    loaded = load_forward_contract(
        root=ROOT,
        contract_path=ROOT / runtime["contract"]["path"],
    )
    current = assessed_at.astimezone(timezone.utc)
    official_date = current.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    if official_date.startswith("2026-05-"):
        report = {
            "schema_version": "aws-shared-pa-forward-health-v1",
            "assessed_at_utc": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "runtime_manifest_sha256": runtime_sha,
            "contract_sha256": loaded["contract_sha256"],
            "plans": [],
            "state": "sealed_may_no_access",
            "research_only": True,
            "betting_authorized": False,
            "production_changed": False,
            "lineup_unavailable_is_coverage_not_model_evidence": True,
        }
        report["report_sha256"] = hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return report
    plans = []
    alert = False
    path = plan_dir / f"{official_date}.plan.json"
    if path.is_file():
        plan = _load_plan(path)
        if plan.official_game_date != official_date or plan.entry_hours != 4:
            raise ValueError("current pitcher plan date or horizon differs")
        plan_ledger_root = ledger_root / plan.official_game_date / plan.plan_sha256
        if (plan_ledger_root / "ledger_manifest.json").is_file():
            ledger = SharedPAForwardLedger(
                plan_ledger_root,
                plan=plan,
                contract_sha256=loaded["contract_sha256"],
                runtime_manifest_sha256=runtime_sha,
                collector_code_sha256=collector_code_sha256(),
            )
            verification = ledger.verify(require_complete_coverage=False)
        else:
            verification = {
                "captured_complete": 0,
                "lineup_unavailable": 0,
                "lineup_malformed": 0,
                "source_error": 0,
                "missed_before_horizon": 0,
                "game_identity_ambiguous": 0,
                "raw_schema_changed": 0,
                "expected": 2 * len(plan.targets),
                "terminal": 0,
                "missing": 2 * len(plan.targets),
            }
        due_sides = 0
        for target in plan.targets:
            horizon = datetime.fromisoformat(target.entry_target_at_utc.replace("Z", "+00:00"))
            if horizon <= current:
                due_sides += 2
        terminal_ids = ledger.terminal_side_ids() if (plan_ledger_root / "ledger_manifest.json").is_file() else set()
        due_ids = {
            side_target_id(plan=plan, target=target, side=side)
            for target in plan.targets
            if datetime.fromisoformat(target.entry_target_at_utc.replace("Z", "+00:00")) <= current
            for side in ("away", "home")
        }
        missing_due = len(due_ids - terminal_ids)
        hard_failures = sum(int(verification[state]) for state in (
            "source_error", "missed_before_horizon", "lineup_malformed",
            "game_identity_ambiguous", "raw_schema_changed",
        ))
        plan_alert = missing_due > 0 or hard_failures > 0
        alert = alert or plan_alert
        plans.append({
            "plan": path.name,
            "plan_sha256": plan.plan_sha256,
            "official_game_date": plan.official_game_date,
            "due_sides": due_sides,
            "missing_due_sides": missing_due,
            "alert": plan_alert,
            "verification": verification,
        })
    report = {
        "schema_version": "aws-shared-pa-forward-health-v1",
        "assessed_at_utc": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_manifest_sha256": runtime_sha,
        "contract_sha256": loaded["contract_sha256"],
        "plans": plans,
        "state": "alert" if alert else ("awaiting_published_plan" if not plans else "healthy"),
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
        "lineup_unavailable_is_coverage_not_model_evidence": True,
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--ledger-root", type=Path, required=True)
    parser.add_argument("--health-root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=ROOT / "config/shared_pa_forward_runtime_v1.json")
    args = parser.parse_args(argv)
    try:
        report = build_report(
            plan_dir=args.plan_dir,
            ledger_root=args.ledger_root,
            runtime_path=args.runtime,
            assessed_at=datetime.now(timezone.utc),
        )
        payload = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode("utf-8")
        stamp = report["assessed_at_utc"].replace(":", "").replace("-", "")
        if report["state"] != "sealed_may_no_access":
            _atomic_publish_once(
                args.health_root / f"{stamp}.{report['report_sha256']}.json", payload
            )
        print(json.dumps(report, sort_keys=True))
        return 2 if report["state"] == "alert" else 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
