from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.evaluation.projected_lineup_contract import sha256_value
from src.evaluation.projected_lineup_label_ledger import (
    ProjectedLineupLabelLedger,
    ProjectedLineupLabelLedgerError,
    roster_manifest_sha256,
)
from src.evaluation.projected_lineup_label_runner import run_tick
from src.evaluation.projected_lineup_roster_ledger import ProjectedLineupRosterLedger
from src.evaluation.projected_lineup_roster_runner import run_tick as run_roster_tick
from src.evaluation.shadow_capture_plan import plan_from_schedule
from src.evaluation.shared_pa_forward_collector import RawPregameResponse
from src.evaluation.projected_lineup_official_roster import RawOfficialRosterResponse


START = datetime(2026, 7, 30, 20, tzinfo=timezone.utc)
HORIZON = START - timedelta(hours=4)


def _plan():
    return plan_from_schedule(
        official_game_date="2026-07-30",
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot=[
            {
                "gamePk": 123,
                "officialDate": "2026-07-30",
                "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "teams": {"home": {"team": {"name": "Home"}}, "away": {"team": {"name": "Away"}}},
            }
        ],
    )


def _schedule() -> RawPregameResponse:
    raw = {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 123,
                        "officialDate": "2026-07-30",
                        "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "teams": {
                            "home": {"team": {"id": 111, "name": "Home"}},
                            "away": {"team": {"id": 112, "name": "Away"}},
                        },
                    }
                ]
            }
        ]
    }
    return RawPregameResponse(json.dumps(raw).encode(), HORIZON.isoformat())


def _roster(team_id: int) -> RawOfficialRosterResponse:
    entries = [
        {
            "jerseyNumber": str(value),
            "parentTeamId": team_id,
            "person": {"fullName": f"P{value}", "id": team_id * 100 + value, "link": "x"},
            "position": {
                "abbreviation": "SS",
                "code": "6",
                "name": "Shortstop",
                "type": "Infielder",
            },
            "status": {"code": "A", "description": "Active"},
        }
        for value in range(1, 10)
    ]
    payload = {
        "copyright": "x",
        "link": "x",
        "roster": entries,
        "rosterType": "active",
        "teamId": team_id,
    }
    return RawOfficialRosterResponse(json.dumps(payload).encode(), HORIZON.isoformat())


def _roster_ledger(tmp_path, plan):
    ledger = ProjectedLineupRosterLedger(
        tmp_path / "roster",
        plan=plan,
        contract_sha256="b" * 64,
        collector_code_sha256="c" * 64,
    )
    run_roster_tick(
        plan=plan,
        ledger=ledger,
        max_early_seconds=120,
        fetch_schedule=lambda _: _schedule(),
        fetch_roster=lambda team, _: _roster(team),
        now=HORIZON,
    )
    return ledger


def _label_ledger(tmp_path, plan, roster_ledger):
    return ProjectedLineupLabelLedger(
        tmp_path / "labels",
        plan=plan,
        contract_sha256="b" * 64,
        collector_code_sha256="d" * 64,
        roster_manifest_sha256=roster_manifest_sha256(roster_ledger.root),
    )


def _completed_feed(*, away_ids: list[int], home_ids: list[int], coded_state: str = "F") -> bytes:
    players = {}
    for side, ids in (("away", away_ids), ("home", home_ids)):
        side_players = {}
        for slot, player_id in enumerate(ids, start=1):
            side_players[f"ID{player_id}"] = {"battingOrder": f"{slot}00"}
        players[side] = {"players": side_players}
    payload = {
        "gameData": {"status": {"codedGameState": coded_state}},
        "liveData": {"boxscore": {"teams": players}},
    }
    return json.dumps(payload).encode()


def test_final_lineup_labels_bind_to_prior_roster_receipts(tmp_path):
    plan = _plan()
    roster_ledger = _roster_ledger(tmp_path, plan)
    label_ledger = _label_ledger(tmp_path, plan, roster_ledger)
    result = run_tick(
        plan=plan,
        roster_ledger=roster_ledger,
        label_ledger=label_ledger,
        fetch_completed_feed=lambda _: _completed_feed(
            away_ids=[11201, 11202, 11203, 11204, 11205, 11206, 11207, 11208, 11209],
            home_ids=[11101, 11102, 11103, 11104, 11105, 11106, 11107, 11108, 11109],
        ),
        now=START + timedelta(hours=4),
    )
    assert result["captured_final_lineup_label"] == 2
    assert label_ledger.verify(roster_ledger=roster_ledger, require_complete_coverage=True)["missing"] == 0


def test_unfinished_games_remain_pending_without_terminal_backfill(tmp_path):
    plan = _plan()
    roster_ledger = _roster_ledger(tmp_path, plan)
    label_ledger = _label_ledger(tmp_path, plan, roster_ledger)
    result = run_tick(
        plan=plan,
        roster_ledger=roster_ledger,
        label_ledger=label_ledger,
        fetch_completed_feed=lambda _: _completed_feed(
            away_ids=[11201, 11202, 11203, 11204, 11205, 11206, 11207, 11208, 11209],
            home_ids=[11101, 11102, 11103, 11104, 11105, 11106, 11107, 11108, 11109],
            coded_state="I",
        ),
        now=START + timedelta(minutes=30),
    )
    assert result["awaiting_game_completion"] == 2
    assert label_ledger.verify(roster_ledger=roster_ledger, require_complete_coverage=False)["terminal"] == 0


def test_lineup_player_outside_t4_roster_is_quarantined(tmp_path):
    plan = _plan()
    roster_ledger = _roster_ledger(tmp_path, plan)
    label_ledger = _label_ledger(tmp_path, plan, roster_ledger)
    result = run_tick(
        plan=plan,
        roster_ledger=roster_ledger,
        label_ledger=label_ledger,
        fetch_completed_feed=lambda _: _completed_feed(
            away_ids=[11201, 11202, 11203, 11204, 11205, 11206, 11207, 11208, 99999],
            home_ids=[11101, 11102, 11103, 11104, 11105, 11106, 11107, 11108, 11109],
        ),
        now=START + timedelta(hours=4),
    )
    assert result["roster_label_mismatch"] == 1
    assert result["captured_final_lineup_label"] == 1


def test_tampered_label_feed_raw_fails_verification(tmp_path):
    plan = _plan()
    roster_ledger = _roster_ledger(tmp_path, plan)
    label_ledger = _label_ledger(tmp_path, plan, roster_ledger)
    run_tick(
        plan=plan,
        roster_ledger=roster_ledger,
        label_ledger=label_ledger,
        fetch_completed_feed=lambda _: _completed_feed(
            away_ids=[11201, 11202, 11203, 11204, 11205, 11206, 11207, 11208, 11209],
            home_ids=[11101, 11102, 11103, 11104, 11105, 11106, 11107, 11108, 11109],
        ),
        now=START + timedelta(hours=4),
    )
    raw = next((label_ledger.root / "raw").glob("*.json"))
    raw.write_bytes(b"tampered")
    with pytest.raises(ProjectedLineupLabelLedgerError, match="final feed raw reference hash differs"):
        label_ledger.verify(roster_ledger=roster_ledger, require_complete_coverage=True)


def test_rebound_to_other_roster_terminal_fails_verification(tmp_path):
    plan = _plan()
    roster_ledger = _roster_ledger(tmp_path, plan)
    label_ledger = _label_ledger(tmp_path, plan, roster_ledger)
    run_tick(
        plan=plan,
        roster_ledger=roster_ledger,
        label_ledger=label_ledger,
        fetch_completed_feed=lambda _: _completed_feed(
            away_ids=[11201, 11202, 11203, 11204, 11205, 11206, 11207, 11208, 11209],
            home_ids=[11101, 11102, 11103, 11104, 11105, 11106, 11107, 11108, 11109],
        ),
        now=START + timedelta(hours=4),
    )
    terminals = sorted((label_ledger.root / "terminal").glob("*.json"))
    entry = json.loads(terminals[0].read_text(encoding="utf-8"))
    other = json.loads(terminals[1].read_text(encoding="utf-8"))
    entry["roster_terminal"] = other["roster_terminal"]
    entry["entry_sha256"] = sha256_value({key: value for key, value in entry.items() if key != "entry_sha256"})
    terminals[0].write_text(json.dumps(entry), encoding="utf-8")
    with pytest.raises(ProjectedLineupLabelLedgerError, match="outside the receipted active roster"):
        label_ledger.verify(roster_ledger=roster_ledger, require_complete_coverage=True)
