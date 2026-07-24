#!/usr/bin/env python3
"""Run one separate, future-derived AWS projected-lineup label tick."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.projected_lineup_label_ledger import (
    ProjectedLineupLabelLedger,
    ProjectedLineupLabelLedgerError,
    roster_manifest_sha256,
)
from src.evaluation.projected_lineup_label_runner import run_tick
from src.evaluation.projected_lineup_roster_ledger import ProjectedLineupRosterLedger
from src.evaluation.shadow_capture_plan import ShadowCapturePlan


class AWSProjectedLineupLabelError(ValueError):
    pass


_CODE_PATHS = (
    "scripts/run_aws_projected_lineup_label_tick.py",
    "src/data/mlb_api.py",
    "src/evaluation/projected_lineup_contract.py",
    "src/evaluation/projected_lineup_roster_ledger.py",
    "src/evaluation/projected_lineup_label_ledger.py",
    "src/evaluation/projected_lineup_label_runner.py",
    "src/evaluation/shadow_capture_plan.py",
)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collector_code_sha256() -> str:
    payload = {
        path: _file_hash(ROOT / path) for path in _CODE_PATHS
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_runtime(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AWSProjectedLineupLabelError("runtime is not valid JSON") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "projected-lineup-label-runtime-v1"
        or value.get("scope") != "future_only_lineup_label_evidence"
    ):
        raise AWSProjectedLineupLabelError("runtime identity changed")
    expected_scheduler = {
        "tick_minutes": 15,
        "request_timeout_seconds": 20,
    }
    if value.get("scheduler") != expected_scheduler:
        raise AWSProjectedLineupLabelError("runtime scheduler changed")
    expected_invariants = {
        "read_existing_pitcher_plan_only": True,
        "read_existing_roster_receipts_only": True,
        "separate_ledger_root": True,
        "retain_raw_responses": True,
        "pregame_probability_inputs_forbidden": True,
        "prices_forbidden": True,
        "late_backfill_forbidden": True,
        "may_2026_forbidden": True,
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
    }
    if (
        value.get("invariants") != expected_invariants
        or value.get("collector_instance_id") != "aws-primary-projected-lineup-label-v1"
    ):
        raise AWSProjectedLineupLabelError("runtime safety invariants changed")
    contract = value.get("contract")
    if (
        not isinstance(contract, dict)
        or set(contract) != {"path", "sha256"}
        or contract["path"] != "config/projected_lineup_contract_v1.json"
        or not isinstance(contract["sha256"], str)
        or len(contract["sha256"]) != 64
    ):
        raise AWSProjectedLineupLabelError("runtime contract binding changed")
    return value, hashlib.sha256(raw).hexdigest()


def _request(url: str, timeout: int) -> bytes:
    with urlopen(
        Request(
            url,
            headers={"User-Agent": "baseball-predictor-projected-lineup-label/1.0"},
        ),
        timeout=timeout,
    ) as response:  # nosec B310 locked official MLB URL
        if response.status != 200:
            raise AWSProjectedLineupLabelError(
                f"official MLB source returned HTTP {response.status}"
            )
        return response.read()


def _plan(path: Path) -> ShadowCapturePlan:
    try:
        return ShadowCapturePlan.from_mapping(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise AWSProjectedLineupLabelError("immutable pitcher plan is unreadable") from exc


def _eligible_plan_paths(plan_dir: Path, current_official_date: str) -> list[Path]:
    plans: list[Path] = []
    for path in sorted(plan_dir.glob("*.plan.json")):
        date_prefix = path.name.split(".plan.json", 1)[0]
        if date_prefix.startswith("2026-05-"):
            continue
        if date_prefix <= current_official_date:
            plans.append(path)
    return plans


def run_all(
    *,
    plan_dir: Path,
    roster_ledger_root: Path,
    label_ledger_root: Path,
    runtime_path: Path,
    now: datetime | None = None,
) -> dict:
    runtime, runtime_sha = load_runtime(runtime_path)
    contract_path = ROOT / runtime["contract"]["path"]
    if _file_hash(contract_path) != runtime["contract"]["sha256"]:
        raise AWSProjectedLineupLabelError("projected-lineup contract hash changed")
    current = now or datetime.now(timezone.utc)
    official_date = current.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    if official_date.startswith("2026-05-"):
        return {
            "schema_version": "aws-projected-lineup-label-tick-v1",
            "collector_state": "sealed_may_no_access",
            "plans": [],
            "research_only": True,
            "betting_authorized": False,
            "production_changed": False,
        }
    timeout = runtime["scheduler"]["request_timeout_seconds"]
    results = []
    for plan_path in _eligible_plan_paths(plan_dir, official_date):
        plan = _plan(plan_path)
        roster_path = roster_ledger_root / plan.official_game_date / plan.plan_sha256
        if not roster_path.is_dir():
            results.append(
                {
                    "plan": plan_path.name,
                    "plan_sha256": plan.plan_sha256,
                    "collector_state": "awaiting_roster_ledger",
                }
            )
            continue
        roster_ledger = ProjectedLineupRosterLedger(
            roster_path,
            plan=plan,
            contract_sha256=runtime["contract"]["sha256"],
            collector_code_sha256=collector_code_sha256(),
        )
        label_ledger = ProjectedLineupLabelLedger(
            label_ledger_root / plan.official_game_date / plan.plan_sha256,
            plan=plan,
            contract_sha256=runtime["contract"]["sha256"],
            collector_code_sha256=collector_code_sha256(),
            roster_manifest_sha256=roster_manifest_sha256(roster_path),
        )
        tick = run_tick(
            plan=plan,
            roster_ledger=roster_ledger,
            label_ledger=label_ledger,
            fetch_completed_feed=lambda game_pk: _request(
                runtime["sources"]["completed_feed"]["base_url_template"].format(
                    game_pk=game_pk
                ),
                timeout,
            ),
            now=current,
        )
        results.append({"plan": plan_path.name, "plan_sha256": plan.plan_sha256, **tick})
    return {
        "schema_version": "aws-projected-lineup-label-tick-v1",
        "collector_state": "processed",
        "runtime_manifest_sha256": runtime_sha,
        "collector_code_sha256": collector_code_sha256(),
        "plans": results,
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--roster-ledger-root", type=Path, required=True)
    parser.add_argument("--label-ledger-root", type=Path, required=True)
    parser.add_argument(
        "--runtime",
        type=Path,
        default=ROOT / "config/projected_lineup_label_runtime_v1.json",
    )
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                run_all(
                    plan_dir=args.plan_dir,
                    roster_ledger_root=args.roster_ledger_root,
                    label_ledger_root=args.label_ledger_root,
                    runtime_path=args.runtime,
                ),
                sort_keys=True,
            )
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        AWSProjectedLineupLabelError,
        ProjectedLineupLabelLedgerError,
    ) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
