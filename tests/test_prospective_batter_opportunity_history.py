from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlencode

import pytest

from src.evaluation.prospective_batter_opportunity import (
    OPPORTUNITY_FIELDS,
    RawOpportunityResponse,
    build_history_capture_plan,
)
from src.evaluation.projected_lineup_contract import sha256_value
from src.evaluation.projected_lineup_official_roster import (
    RawOfficialRosterResponse,
    parse_active_roster_receipt,
)
from src.evaluation.projected_lineup_roster_ledger import (
    ProjectedLineupRosterLedger,
    roster_side_target_id,
)
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.prospective_batter_opportunity_history import (
    ProspectiveOpportunityHistoryError,
    ProspectiveOpportunityHistoryLedger,
    run_history_tick,
)


GAME_PK = 1001
TEAM_ID = 147
GAME_DATE = "2026-07-27"


def _url() -> str:
    return f"https://statsapi.mlb.com/api/v1.1/game/{GAME_PK}/feed/live?" + urlencode(
        {"fields": OPPORTUNITY_FIELDS}
    )


def _source_roster_ledger(tmp_path) -> ProjectedLineupRosterLedger:
    target = CaptureTarget(
        mlb_game_pk=GAME_PK,
        official_game_date=GAME_DATE,
        official_start_time_utc="2026-07-27T18:00:00Z",
        entry_target_at_utc="2026-07-27T14:00:00Z",
        entry_hours=4,
    )
    source_plan = ShadowCapturePlan(
        official_game_date=GAME_DATE,
        entry_hours=4,
        policy_sha256="1" * 64,
        schedule_snapshot_sha256="2" * 64,
        targets=(target,),
    )
    roster_entries = [
        {
            "jerseyNumber": str(player_id),
            "parentTeamId": TEAM_ID,
            "person": {
                "fullName": f"Player {player_id}",
                "id": player_id,
                "link": f"/api/v1/people/{player_id}",
            },
            "position": {
                "abbreviation": "SS",
                "code": "6",
                "name": "Shortstop",
                "type": "Infielder",
            },
            "status": {"code": "A", "description": "Active"},
        }
        for player_id in range(1, 10)
    ]
    roster_raw = json.dumps(
        {
            "copyright": "x",
            "link": "/api",
            "roster": roster_entries,
            "rosterType": "active",
            "teamId": TEAM_ID,
        },
        sort_keys=True,
    ).encode()
    roster_record = parse_active_roster_receipt(
        response=RawOfficialRosterResponse(roster_raw, "2026-07-27T13:59:30Z"),
        requested_date=GAME_DATE,
        team_id=TEAM_ID,
        target_horizon_utc=target.entry_target_at_utc,
    )
    ledger = ProjectedLineupRosterLedger(
        tmp_path / "source_roster",
        plan=source_plan,
        contract_sha256="3" * 64,
        collector_code_sha256="4" * 64,
    )
    ledger.append_capture(
        target=target,
        side="away",
        team_id=TEAM_ID,
        roster_record=roster_record,
        schedule_raw=json.dumps(
            {
                "dates": [
                    {
                        "games": [
                            {
                                "gamePk": GAME_PK,
                                "officialDate": GAME_DATE,
                                "gameDate": "2026-07-27T18:00:00Z",
                                "teams": {
                                    "away": {"team": {"id": TEAM_ID, "name": "Away"}},
                                    "home": {"team": {"id": 121, "name": "Home"}},
                                },
                            }
                        ]
                    }
                ]
            },
            sort_keys=True,
        ).encode(),
        schedule_received_at_utc="2026-07-27T13:59:00Z",
        roster_raw=roster_raw,
        committed_utc="2026-07-27T13:59:30Z",
    )
    return ledger


def _plan(source_roster_ledger: ProjectedLineupRosterLedger) -> dict:
    source_plan = source_roster_ledger.plan
    target = source_plan.targets[0]
    terminal_path = source_roster_ledger.root / "terminal" / (
        roster_side_target_id(plan=source_plan, target=target, side="away") + ".json"
    )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    return build_history_capture_plan(
        created_at_utc="2026-07-27T14:00:00Z",
        mlb_game_pk=GAME_PK,
        official_game_date=GAME_DATE,
        official_start_time_utc="2026-07-27T18:00:00Z",
        capture_deadline_utc="2026-07-27T23:59:59Z",
        side="away",
        team_id=TEAM_ID,
        source_t4_plan_sha256=source_plan.plan_sha256,
        roster_side_target_id=terminal["side_target_id"],
        active_roster_receipt_sha256=sha256_value(terminal["roster"]),
    )


def _body(*, final: bool = True, outcome_field: bool = False, malformed: bool = False) -> bytes:
    away = {
        f"ID{slot}": {
            "person": {"id": slot},
            "battingOrder": f"{slot}00",
            "stats": {"batting": {"plateAppearances": 4}},
        }
        for slot in range(1, 10)
    }
    home = {
        f"ID{100 + slot}": {
            "person": {"id": 100 + slot},
            "battingOrder": f"{slot}00",
            "stats": {"batting": {"plateAppearances": 4}},
        }
        for slot in range(1, 10)
    }
    if outcome_field:
        away["ID1"]["stats"]["batting"]["hits"] = 2
    if malformed:
        away["ID1"]["stats"]["batting"]["plateAppearances"] = "four"
    payload = {
        "gamePk": GAME_PK,
        "gameData": {
            "datetime": {"officialDate": GAME_DATE},
            "status": {
                "abstractGameState": "Final" if final else "Live",
                "codedGameState": "F" if final else "I",
            },
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {"team": {"id": TEAM_ID}, "players": away},
                    "home": {"team": {"id": 121}, "players": home},
                }
            }
        },
    }
    return json.dumps(payload, sort_keys=True).encode()


def _response(*, final: bool = True, outcome_field: bool = False, malformed: bool = False, received: str = "2026-07-27T23:00:00Z") -> RawOpportunityResponse:
    return RawOpportunityResponse(
        _body(final=final, outcome_field=outcome_field, malformed=malformed),
        received,
        _url(),
    )


def _ledger(tmp_path) -> ProspectiveOpportunityHistoryLedger:
    source_roster_ledger = _source_roster_ledger(tmp_path)
    ledger = ProspectiveOpportunityHistoryLedger(
        tmp_path,
        collection_epoch_date=GAME_DATE,
        contract_sha256="d" * 64,
        collector_code_sha256="e" * 64,
        evidence_scope_sha256="f" * 64,
    )
    ledger.append_plan(
        _plan(source_roster_ledger),
        published_at_utc="2026-07-27T14:00:00Z",
        source_roster_ledger=source_roster_ledger,
    )
    return ledger


def test_future_plan_captures_once_and_replays(tmp_path):
    ledger = _ledger(tmp_path)
    before = run_history_tick(
        ledger=ledger,
        fetch_final=lambda plan: _response(),
        now=datetime(2026, 7, 27, 17, 59, tzinfo=timezone.utc),
    )
    assert before["future"] == 1
    captured = run_history_tick(
        ledger=ledger,
        fetch_final=lambda plan: _response(),
        now=datetime(2026, 7, 27, 23, 0, tzinfo=timezone.utc),
    )
    assert captured["captured"] == 1
    assert ledger.verify(require_all_terminal=True) == {
        "captured": 1,
        "deadline_missing": 0,
        "source_invalid": 0,
        "planned": 1,
        "terminal": 1,
        "missing": 0,
        "missed_before_plan": 0,
    }
    records, raw = ledger.captured_materials()
    assert len(records) == 1
    assert json.loads(raw[records[0]["source_payload_sha256"]]) == json.loads(_body())
    assert run_history_tick(
        ledger=ledger,
        fetch_final=lambda plan: (_ for _ in ()).throw(AssertionError("terminal plan refetched")),
        now=datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc),
    )["captured"] == 0


def test_nonfinal_is_retryable_but_deadline_is_terminal_and_never_backfilled(tmp_path):
    ledger = _ledger(tmp_path)
    pending = run_history_tick(
        ledger=ledger,
        fetch_final=lambda plan: _response(final=False, received="2026-07-27T22:00:00Z"),
        now=datetime(2026, 7, 27, 22, 0, tzinfo=timezone.utc),
    )
    assert pending["pending"] == 1
    assert ledger.terminal_plan_ids() == set()
    missed = run_history_tick(
        ledger=ledger,
        fetch_final=lambda plan: _response(),
        now=datetime(2026, 7, 28, 0, 0, tzinfo=timezone.utc),
    )
    assert missed["deadline_missing"] == 1
    assert ledger.verify(require_all_terminal=True)["deadline_missing"] == 1
    assert run_history_tick(
        ledger=ledger,
        fetch_final=lambda plan: (_ for _ in ()).throw(AssertionError("missed plan backfilled")),
        now=datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc),
    )["captured"] == 0


def test_plan_cannot_be_added_after_first_pitch_or_backdated(tmp_path):
    source_roster_ledger = _source_roster_ledger(tmp_path)
    ledger = ProspectiveOpportunityHistoryLedger(
        tmp_path,
        collection_epoch_date=GAME_DATE,
        contract_sha256="d" * 64,
        collector_code_sha256="e" * 64,
        evidence_scope_sha256="f" * 64,
    )
    with pytest.raises(ProspectiveOpportunityHistoryError, match="publication time"):
        ledger.append_plan(
            _plan(source_roster_ledger),
            published_at_utc="2026-07-27T14:00:01Z",
            source_roster_ledger=source_roster_ledger,
        )
    late = dict(_plan(source_roster_ledger))
    late["created_at_utc"] = "2026-07-27T18:00:00.000000Z"
    with pytest.raises(ProspectiveOpportunityHistoryError, match="invalid"):
        ledger.append_plan(
            late,
            published_at_utc="2026-07-27T18:00:00Z",
            source_roster_ledger=source_roster_ledger,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "source_plan",
        "receipt",
        "side_target",
        "raw",
        "wrong_game",
        "wrong_team",
        "wrong_date",
        "wrong_horizon",
        "nonterminal",
        "wrong_commit_time",
        "created_before_commit",
    ],
)
def test_plan_requires_replayed_t4_roster_proof(tmp_path, mutation: str):
    source_roster_ledger = _source_roster_ledger(tmp_path)
    plan = _plan(source_roster_ledger)
    if mutation == "source_plan":
        plan = build_history_capture_plan(
            created_at_utc=plan["created_at_utc"],
            mlb_game_pk=plan["mlb_game_pk"],
            official_game_date=plan["official_game_date"],
            official_start_time_utc=plan["official_start_time_utc"],
            capture_deadline_utc=plan["capture_deadline_utc"],
            side=plan["side"],
            team_id=plan["team_id"],
            source_t4_plan_sha256="f" * 64,
            roster_side_target_id=plan["roster_side_target_id"],
            active_roster_receipt_sha256=plan["active_roster_receipt_sha256"],
        )
    elif mutation == "receipt":
        plan = build_history_capture_plan(
            created_at_utc=plan["created_at_utc"],
            mlb_game_pk=plan["mlb_game_pk"],
            official_game_date=plan["official_game_date"],
            official_start_time_utc=plan["official_start_time_utc"],
            capture_deadline_utc=plan["capture_deadline_utc"],
            side=plan["side"],
            team_id=plan["team_id"],
            source_t4_plan_sha256=plan["source_t4_plan_sha256"],
            roster_side_target_id=plan["roster_side_target_id"],
            active_roster_receipt_sha256="f" * 64,
        )
    elif mutation == "side_target":
        plan = build_history_capture_plan(
            created_at_utc=plan["created_at_utc"],
            mlb_game_pk=plan["mlb_game_pk"],
            official_game_date=plan["official_game_date"],
            official_start_time_utc=plan["official_start_time_utc"],
            capture_deadline_utc=plan["capture_deadline_utc"],
            side=plan["side"],
            team_id=plan["team_id"],
            source_t4_plan_sha256=plan["source_t4_plan_sha256"],
            roster_side_target_id="f" * 64,
            active_roster_receipt_sha256=plan["active_roster_receipt_sha256"],
        )
    elif mutation == "raw":
        terminal = next((source_roster_ledger.root / "terminal").glob("*.json"))
        entry = json.loads(terminal.read_text(encoding="utf-8"))
        raw_path = source_roster_ledger.root / entry["roster_raw"]["path"]
        raw_path.write_bytes(raw_path.read_bytes() + b" ")
    elif mutation == "created_before_commit":
        plan = build_history_capture_plan(
            created_at_utc="2026-07-27T13:59:59Z",
            mlb_game_pk=plan["mlb_game_pk"],
            official_game_date=plan["official_game_date"],
            official_start_time_utc=plan["official_start_time_utc"],
            capture_deadline_utc=plan["capture_deadline_utc"],
            side=plan["side"],
            team_id=plan["team_id"],
            source_t4_plan_sha256=plan["source_t4_plan_sha256"],
            roster_side_target_id=plan["roster_side_target_id"],
            active_roster_receipt_sha256=plan["active_roster_receipt_sha256"],
        )
    else:
        terminal_path = next((source_roster_ledger.root / "terminal").glob("*.json"))
        entry = json.loads(terminal_path.read_text(encoding="utf-8"))
        if mutation == "wrong_game":
            entry["mlb_game_pk"] += 1
        elif mutation == "wrong_team":
            entry["team_id"] += 1
        elif mutation == "wrong_date":
            entry["official_game_date"] = "2026-07-26"
        elif mutation == "wrong_horizon":
            entry["roster"]["target_horizon_utc"] = "2026-07-27T14:00:01.000000Z"
        elif mutation == "nonterminal":
            entry["terminal_state"] = "source_error"
        elif mutation == "wrong_commit_time":
            entry["committed_utc"] = "2026-07-27T13:58:00Z"
        unsigned_entry = dict(entry)
        unsigned_entry.pop("entry_sha256")
        entry["entry_sha256"] = sha256_value(unsigned_entry)
        terminal_path.write_text(json.dumps(entry), encoding="utf-8")
    ledger = ProspectiveOpportunityHistoryLedger(
        tmp_path / "history",
        collection_epoch_date=GAME_DATE,
        contract_sha256="d" * 64,
        collector_code_sha256="e" * 64,
        evidence_scope_sha256="f" * 64,
    )
    with pytest.raises(ProspectiveOpportunityHistoryError):
        ledger.append_plan(
            plan,
            published_at_utc="2026-07-27T14:00:00Z",
            source_roster_ledger=source_roster_ledger,
        )
    assert not (tmp_path / "history" / "plans").exists()


def test_stored_t4_roster_proof_mutation_is_rejected_on_replay(tmp_path):
    ledger = _ledger(tmp_path)
    plan_path = next((tmp_path / "plans").glob("*.json"))
    bundle = json.loads(plan_path.read_text(encoding="utf-8"))
    terminal = bundle["active_roster_terminal"]
    terminal["team_id"] += 1
    unsigned = dict(terminal)
    unsigned.pop("entry_sha256")
    terminal["entry_sha256"] = sha256_value(unsigned)
    plan_path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ProspectiveOpportunityHistoryError, match="semantic identity"):
        ledger.plans()


def test_transport_outcome_extras_are_removed_before_capture(tmp_path):
    ledger = _ledger(tmp_path)
    report = run_history_tick(
        ledger=ledger,
        fetch_final=lambda plan: _response(outcome_field=True),
        now=datetime(2026, 7, 27, 23, 0, tzinfo=timezone.utc),
    )
    assert report["captured"] == 1
    assert ledger.verify(require_all_terminal=True)["captured"] == 1
    _, raw = ledger.captured_materials()
    assert all(b"hits" not in payload for payload in raw.values())


def test_malformed_required_opportunity_field_is_terminal_source_invalid(tmp_path):
    ledger = _ledger(tmp_path)
    report = run_history_tick(
        ledger=ledger,
        fetch_final=lambda plan: _response(malformed=True),
        now=datetime(2026, 7, 27, 23, 0, tzinfo=timezone.utc),
    )
    assert report["source_invalid"] == 1
    assert ledger.verify(require_all_terminal=True)["source_invalid"] == 1
    terminal = json.loads(next((tmp_path / "terminal").glob("*.json")).read_text(encoding="utf-8"))
    rejected = _body(malformed=True)
    assert terminal["raw"] is None
    assert terminal["transport_payload_sha256"] == hashlib.sha256(rejected).hexdigest()
    assert terminal["transport_payload_size"] == len(rejected)
    assert not (tmp_path / "raw").exists()


def test_raw_mutation_or_orphan_fails_closed(tmp_path):
    ledger = _ledger(tmp_path)
    run_history_tick(
        ledger=ledger,
        fetch_final=lambda plan: _response(),
        now=datetime(2026, 7, 27, 23, 0, tzinfo=timezone.utc),
    )
    raw_path = next((tmp_path / "raw").glob("*.json"))
    original = raw_path.read_bytes()
    raw_path.write_bytes(original + b" ")
    with pytest.raises(ProspectiveOpportunityHistoryError, match="hash differs"):
        ledger.verify()
    raw_path.write_bytes(original)
    (tmp_path / "raw" / ("f" * 64 + ".json")).write_bytes(b"{}")
    with pytest.raises(ProspectiveOpportunityHistoryError, match="orphaned"):
        ledger.verify()


def test_may_collection_epoch_is_rejected(tmp_path):
    with pytest.raises(ProspectiveOpportunityHistoryError, match="May 2026"):
        ProspectiveOpportunityHistoryLedger(
            tmp_path,
            collection_epoch_date="2026-05-01",
            contract_sha256="d" * 64,
            collector_code_sha256="e" * 64,
            evidence_scope_sha256="f" * 64,
        )
