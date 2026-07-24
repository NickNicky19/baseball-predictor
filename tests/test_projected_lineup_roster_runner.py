from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.evaluation.projected_lineup_official_roster import RawOfficialRosterResponse
from src.evaluation.projected_lineup_roster_ledger import ProjectedLineupRosterLedger, ProjectedLineupRosterLedgerError
from src.evaluation.projected_lineup_roster_runner import run_tick
from src.evaluation.projected_lineup_contract import sha256_value
from src.evaluation.shadow_capture_plan import plan_from_schedule
from src.evaluation.shared_pa_forward_collector import RawPregameResponse


START = datetime(2026, 7, 30, 20, tzinfo=timezone.utc); HORIZON = START - timedelta(hours=4)

def _plan():
    return plan_from_schedule(official_game_date="2026-07-30", entry_hours=4, policy_sha256="a" * 64, schedule_snapshot=[{"gamePk": 123, "officialDate": "2026-07-30", "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"), "teams": {"home": {"team": {"name": "Home"}}, "away": {"team": {"name": "Away"}}}}])

def _schedule() -> RawPregameResponse:
    raw = {"dates": [{"games": [{"gamePk": 123, "officialDate": "2026-07-30", "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"), "teams": {"home": {"team": {"id": 111, "name": "Home"}}, "away": {"team": {"id": 112, "name": "Away"}}}}]}]}
    return RawPregameResponse(json.dumps(raw).encode(), HORIZON.isoformat())

def _roster(team_id: int, received: datetime = HORIZON) -> RawOfficialRosterResponse:
    entries = [{"jerseyNumber": str(value), "parentTeamId": team_id, "person": {"fullName": f"P{value}", "id": team_id * 100 + value, "link": "x"}, "position": {"abbreviation": "SS", "code": "6", "name": "Shortstop", "type": "Infielder"}, "status": {"code": "A", "description": "Active"}} for value in range(1, 10)]
    return RawOfficialRosterResponse(json.dumps({"copyright": "x", "link": "x", "roster": entries, "rosterType": "active", "teamId": team_id}).encode(), received.isoformat())

def _ledger(tmp_path, plan):
    return ProjectedLineupRosterLedger(tmp_path, plan=plan, contract_sha256="b" * 64, collector_code_sha256="c" * 64)

def test_full_t4_roster_coverage_is_immutable_and_non_economic(tmp_path):
    plan = _plan(); ledger = _ledger(tmp_path, plan)
    result = run_tick(plan=plan, ledger=ledger, max_early_seconds=120, fetch_schedule=lambda _: _schedule(), fetch_roster=lambda team, _: _roster(team), now=HORIZON)
    assert result["captured_active_roster"] == 2
    assert ledger.verify(require_complete_coverage=True)["missing"] == 0

def test_late_roster_is_missed_not_backfilled(tmp_path):
    plan = _plan(); ledger = _ledger(tmp_path, plan)
    result = run_tick(plan=plan, ledger=ledger, max_early_seconds=120, fetch_schedule=lambda _: _schedule(), fetch_roster=lambda team, _: _roster(team, HORIZON + timedelta(seconds=1)), now=HORIZON)
    assert result["missed_before_horizon"] == 2

def test_tampered_raw_receipt_fails_verification(tmp_path):
    plan = _plan(); ledger = _ledger(tmp_path, plan)
    run_tick(plan=plan, ledger=ledger, max_early_seconds=120, fetch_schedule=lambda _: _schedule(), fetch_roster=lambda team, _: _roster(team), now=HORIZON)
    raw = next((tmp_path / "raw").glob("*.json")); raw.write_bytes(b"tampered")
    with pytest.raises(ProjectedLineupRosterLedgerError, match="raw reference hash"):
        ledger.verify(require_complete_coverage=True)

def test_rehashed_roster_raw_cannot_diverge_from_consumed_identity_record(tmp_path):
    plan = _plan(); ledger = _ledger(tmp_path, plan)
    run_tick(plan=plan, ledger=ledger, max_early_seconds=120, fetch_schedule=lambda _: _schedule(), fetch_roster=lambda team, _: _roster(team), now=HORIZON)
    terminal = next((tmp_path / "terminal").glob("*.json")); entry = json.loads(terminal.read_text())
    roster_path = tmp_path / entry["roster_raw"]["path"]; raw = json.loads(roster_path.read_text()); raw["roster"][0]["position"]["code"] = "4"
    replacement = json.dumps(raw).encode(); replacement_hash = hashlib.sha256(replacement).hexdigest(); replacement_path = tmp_path / "raw" / f"{replacement_hash}.json"; replacement_path.write_bytes(replacement); roster_path.unlink()
    entry["roster_raw"] = {"path": f"raw/{replacement_hash}.json", "sha256": replacement_hash}; entry["entry_sha256"] = sha256_value({key: value for key, value in entry.items() if key != "entry_sha256"}); terminal.write_text(json.dumps(entry))
    with pytest.raises(ProjectedLineupRosterLedgerError, match="differs from consumed"):
        ledger.verify(require_complete_coverage=True)
