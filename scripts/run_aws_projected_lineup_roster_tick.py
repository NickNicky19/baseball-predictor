#!/usr/bin/env python3
"""Run one separate, future-only AWS projected-lineup roster tick."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from src.evaluation.projected_lineup_official_roster import RawOfficialRosterResponse
from src.evaluation.projected_lineup_roster_ledger import ProjectedLineupRosterLedger
from src.evaluation.projected_lineup_roster_runner import run_tick
from src.evaluation.shadow_capture_plan import ShadowCapturePlan
from src.evaluation.shared_pa_forward_collector import RawPregameResponse, validate_schedule_input_surface

class AWSProjectedLineupRosterError(ValueError): pass

_CODE_PATHS = ("scripts/run_aws_projected_lineup_roster_tick.py", "src/evaluation/shadow_capture_plan.py", "src/evaluation/shared_pa_forward_collector.py", "src/evaluation/projected_lineup_contract.py", "src/evaluation/projected_lineup_official_roster.py", "src/evaluation/projected_lineup_roster_ledger.py", "src/evaluation/projected_lineup_roster_runner.py")

def _file_hash(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def collector_code_sha256() -> str: return hashlib.sha256(json.dumps({path: _file_hash(ROOT / path) for path in _CODE_PATHS}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def load_runtime(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    try: value = json.loads(raw)
    except json.JSONDecodeError as exc: raise AWSProjectedLineupRosterError("runtime is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "projected-lineup-roster-runtime-v1" or value.get("scope") != "future_only_lineup_input_evidence":
        raise AWSProjectedLineupRosterError("runtime identity changed")
    if value.get("scheduler") != {"entry_hours": 4, "max_early_seconds": 120, "tick_seconds": 60, "request_timeout_seconds": 20}:
        raise AWSProjectedLineupRosterError("runtime scheduler changed")
    invariants = value.get("invariants")
    expected_invariants = {"read_existing_pitcher_plan_only": True, "separate_ledger_root": True, "retain_raw_responses": True, "outcomes_forbidden": True, "prices_forbidden": True, "pitcher_probability_inputs_forbidden": True, "late_backfill_forbidden": True, "may_2026_forbidden": True, "research_only": True, "betting_authorized": False, "production_changed": False}
    if invariants != expected_invariants or value.get("collector_instance_id") != "aws-primary-projected-lineup-roster-v1":
        raise AWSProjectedLineupRosterError("runtime safety invariants changed")
    contract = value.get("contract")
    if not isinstance(contract, dict) or set(contract) != {"path", "sha256"} or contract["path"] != "config/projected_lineup_contract_v1.json" or not isinstance(contract["sha256"], str) or len(contract["sha256"]) != 64:
        raise AWSProjectedLineupRosterError("runtime contract binding changed")
    return value, hashlib.sha256(raw).hexdigest()

def _request(url: str, timeout: int) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "baseball-predictor-projected-lineup-roster/1.0"}), timeout=timeout) as response:  # nosec B310 locked official MLB URLs
        if response.status != 200: raise AWSProjectedLineupRosterError(f"official MLB source returned HTTP {response.status}")
        return response.read()

def _plan(path: Path) -> ShadowCapturePlan:
    try: return ShadowCapturePlan.from_mapping(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError) as exc: raise AWSProjectedLineupRosterError("immutable pitcher plan is unreadable") from exc

def run_all(*, plan_dir: Path, ledger_root: Path, runtime_path: Path, now: datetime | None = None) -> dict:
    runtime, runtime_sha = load_runtime(runtime_path)
    contract_path = ROOT / runtime["contract"]["path"]
    if _file_hash(contract_path) != runtime["contract"]["sha256"]: raise AWSProjectedLineupRosterError("projected-lineup contract hash changed")
    current = now or datetime.now(timezone.utc); official_date = current.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    if official_date.startswith("2026-05-"):
        return {"schema_version": "aws-projected-lineup-roster-tick-v1", "collector_state": "sealed_may_no_access", "plans": [], "research_only": True, "betting_authorized": False, "production_changed": False}
    plan_path = plan_dir / f"{official_date}.plan.json"
    if not plan_path.is_file():
        return {"schema_version": "aws-projected-lineup-roster-tick-v1", "collector_state": "awaiting_published_plan", "plans": [], "research_only": True, "betting_authorized": False, "production_changed": False}
    plan = _plan(plan_path)
    if plan.official_game_date != official_date or plan.entry_hours != 4: raise AWSProjectedLineupRosterError("plan date or horizon differs")
    timeout = runtime["scheduler"]["request_timeout_seconds"]
    def schedule(date: str) -> RawPregameResponse:
        source = runtime["sources"]["schedule"]
        raw = _request(f"{source['base_url']}?" + urlencode({"sportId": source["sport_id"], "date": date, "hydrate": source["hydrate"], "fields": source["fields"]}), timeout)
        response = RawPregameResponse(raw, datetime.now(timezone.utc).isoformat())
        validate_schedule_input_surface(response); return response
    def roster(team_id: int, date: str) -> RawOfficialRosterResponse:
        source = runtime["sources"]["active_roster"]
        raw = _request(source["base_url_template"].format(team_id=team_id) + "?" + urlencode({"rosterType": source["roster_type"], "date": date}), timeout)
        return RawOfficialRosterResponse(raw, datetime.now(timezone.utc).isoformat())
    ledger = ProjectedLineupRosterLedger(ledger_root / plan.official_game_date / plan.plan_sha256, plan=plan, contract_sha256=runtime["contract"]["sha256"], collector_code_sha256=collector_code_sha256())
    tick = run_tick(plan=plan, ledger=ledger, max_early_seconds=runtime["scheduler"]["max_early_seconds"], fetch_schedule=schedule, fetch_roster=roster, now=current)
    return {"schema_version": "aws-projected-lineup-roster-tick-v1", "collector_state": "processed", "runtime_manifest_sha256": runtime_sha, "collector_code_sha256": collector_code_sha256(), "plans": [{"plan": plan_path.name, "plan_sha256": plan.plan_sha256, **tick}], "research_only": True, "betting_authorized": False, "production_changed": False}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--plan-dir", type=Path, required=True); parser.add_argument("--ledger-root", type=Path, required=True); parser.add_argument("--runtime", type=Path, default=ROOT / "config/projected_lineup_roster_runtime_v1.json")
    args = parser.parse_args(argv)
    try: print(json.dumps(run_all(plan_dir=args.plan_dir, ledger_root=args.ledger_root, runtime_path=args.runtime), sort_keys=True))
    except (OSError, ValueError, RuntimeError, AWSProjectedLineupRosterError) as exc: print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr); return 2
    return 0
if __name__ == "__main__": raise SystemExit(main())
