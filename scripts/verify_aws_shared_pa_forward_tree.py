#!/usr/bin/env python3
"""Independently verify a copied AWS shared-PA source-to-probability tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_aws_shared_pa_forward_tick import collector_code_sha256, load_runtime  # noqa: E402
from src.evaluation.shadow_capture_plan import ShadowCapturePlan  # noqa: E402
from src.evaluation.shared_pa_forward_evidence import load_forward_contract  # noqa: E402
from src.evaluation.shared_pa_forward_ledger import SharedPAForwardLedger, side_target_id  # noqa: E402


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def verify_tree(*, evidence_root: Path, official_date: str, assessed_at: datetime) -> dict[str, object]:
    if not _DATE.fullmatch(official_date):
        raise ValueError("official date must be YYYY-MM-DD")
    if official_date.startswith("2026-05-"):
        raise ValueError("May 2026 is sealed before evidence access")
    plan_path = evidence_root / "plans" / f"{official_date}.plan.json"
    raw_plan = plan_path.read_bytes()
    value = json.loads(raw_plan)
    if not isinstance(value, dict):
        raise ValueError("copied plan root must be an object")
    plan = ShadowCapturePlan.from_mapping(value)
    if plan.official_game_date != official_date or plan.entry_hours != 4:
        raise ValueError("copied plan date or horizon differs")
    runtime, runtime_sha = load_runtime(ROOT / "config/shared_pa_forward_runtime_v1.json")
    loaded = load_forward_contract(
        root=ROOT, contract_path=ROOT / runtime["contract"]["path"]
    )
    code_sha = collector_code_sha256()
    ledger_root = evidence_root / "shared-pa-forward" / "ledgers" / official_date / plan.plan_sha256
    if not (ledger_root / "ledger_manifest.json").is_file():
        raise ValueError("copied shared PA ledger manifest is missing")
    ledger = SharedPAForwardLedger(
        ledger_root,
        plan=plan,
        contract_sha256=loaded["contract_sha256"],
        runtime_manifest_sha256=runtime_sha,
        collector_code_sha256=code_sha,
    )
    counts = ledger.verify(require_complete_coverage=False)
    current = assessed_at.astimezone(timezone.utc)
    due_ids = {
        side_target_id(plan=plan, target=target, side=side)
        for target in plan.targets
        if datetime.fromisoformat(target.entry_target_at_utc.replace("Z", "+00:00")) <= current
        for side in ("away", "home")
    }
    missing_due = sorted(due_ids - ledger.terminal_side_ids())
    if missing_due:
        raise ValueError("copied shared PA ledger is missing due terminal side records")
    hard_failures = sum(int(counts[state]) for state in (
        "source_error", "missed_before_horizon", "lineup_malformed",
        "game_identity_ambiguous", "raw_schema_changed",
    ))
    if hard_failures:
        raise ValueError("copied shared PA ledger contains a hard integrity failure")
    report: dict[str, object] = {
        "schema_version": "aws-shared-pa-forward-independent-verification-v1",
        "official_game_date": official_date,
        "assessed_at_utc": current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "plan_sha256": plan.plan_sha256,
        "plan_file_sha256": hashlib.sha256(raw_plan).hexdigest(),
        "contract_sha256": loaded["contract_sha256"],
        "runtime_manifest_sha256": runtime_sha,
        "collector_code_sha256": code_sha,
        "verification": counts,
        "due_side_count": len(due_ids),
        "missing_due_side_count": 0,
        "state": "verified",
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
        "replacement_data_fetched": False,
        "outcomes_prices_and_settlement_accessed": False,
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = verify_tree(
            evidence_root=args.evidence_root,
            official_date=args.date,
            assessed_at=datetime.now(timezone.utc),
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, sort_keys=True))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
