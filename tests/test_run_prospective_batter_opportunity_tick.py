from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import pytest

from scripts.run_prospective_batter_opportunity_tick import (
    ProspectiveBatterOpportunityRuntimeError,
    _exit_code_for_result,
    _history_ledgers,
    build_evidence_scope,
    collector_code_sha256,
    load_runtime,
    run_all as _runtime_run_all,
)
from src.evaluation.projected_lineup_official_roster import (
    RawOfficialRosterResponse,
    parse_active_roster_receipt,
)
from src.evaluation.projected_lineup_roster_ledger import (
    ProjectedLineupRosterLedger,
    ProjectedLineupRosterLedgerError,
)
from src.evaluation.prospective_batter_opportunity import OPPORTUNITY_FIELDS, RawOpportunityResponse
from src.evaluation.prospective_batter_opportunity_history import (
    ProspectiveOpportunityHistoryError,
    ProspectiveOpportunityHistoryLedger,
)
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "config/prospective_batter_opportunity_runtime_v1.json"
GAME_DATE = "2026-07-27"
GAME_PK = 1001
AWAY_TEAM_ID = 147
HOME_TEAM_ID = 121


def run_all(**kwargs):
    scope_path = kwargs["evidence_scope_path"]
    kwargs["expected_evidence_scope_file_sha256"] = hashlib.sha256(scope_path.read_bytes()).hexdigest()
    return _runtime_run_all(**kwargs)


def _setup_roster_evidence(
    tmp_path: Path,
    *,
    game_date: str = GAME_DATE,
    game_pk: int = GAME_PK,
    official_start_time_utc: str = "2026-07-27T18:00:00Z",
    entry_target_at_utc: str = "2026-07-27T14:00:00Z",
) -> tuple[Path, Path, Path, ShadowCapturePlan]:
    target = CaptureTarget(
        mlb_game_pk=game_pk,
        official_game_date=game_date,
        official_start_time_utc=official_start_time_utc,
        entry_target_at_utc=entry_target_at_utc,
        entry_hours=4,
    )
    plan = ShadowCapturePlan(
        official_game_date=game_date,
        entry_hours=4,
        policy_sha256="1" * 64,
        schedule_snapshot_sha256="2" * 64,
        targets=(target,),
    )
    plan_dir = tmp_path / "plans"
    plan.write(plan_dir / f"{game_date}.plan.json")
    roster_root = tmp_path / "rosters"
    source_root = roster_root / game_date / plan.plan_sha256
    source_binding = json.loads(RUNTIME.read_text(encoding="utf-8"))["source_roster_evidence"]
    ledger = ProjectedLineupRosterLedger(
        source_root,
        plan=plan,
        contract_sha256=source_binding["contract_sha256"],
        collector_code_sha256=source_binding["collector_code_sha256"],
    )
    roster_raw = json.dumps(
        {
            "copyright": "source fixture",
            "link": "/api/v1/teams/147/roster",
            "roster": [
                {
                    "jerseyNumber": str(player_id),
                    "parentTeamId": AWAY_TEAM_ID,
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
            ],
            "rosterType": "active",
            "teamId": AWAY_TEAM_ID,
        },
        sort_keys=True,
    ).encode()
    roster_record = parse_active_roster_receipt(
        response=RawOfficialRosterResponse(roster_raw, entry_target_at_utc),
        requested_date=game_date,
        team_id=AWAY_TEAM_ID,
        target_horizon_utc=target.entry_target_at_utc,
    )
    ledger.append_capture(
        target=target,
        side="away",
        team_id=AWAY_TEAM_ID,
        roster_record=roster_record,
        schedule_raw=json.dumps(
            {
                "dates": [
                    {
                        "games": [
                            {
                                "gamePk": game_pk,
                                "officialDate": game_date,
                                "gameDate": official_start_time_utc,
                                "teams": {
                                    "away": {"team": {"id": AWAY_TEAM_ID, "name": "Away"}},
                                    "home": {"team": {"id": HOME_TEAM_ID, "name": "Home"}},
                                },
                            }
                        ]
                    }
                ]
            },
            sort_keys=True,
        ).encode(),
        schedule_received_at_utc=entry_target_at_utc,
        roster_raw=roster_raw,
        committed_utc=entry_target_at_utc,
    )
    return plan_dir, roster_root, tmp_path / "history", plan


def _scope_path(
    tmp_path: Path,
    *,
    collection_epoch_date: str = GAME_DATE,
    created_at_utc: str = "2026-07-27T13:00:00Z",
) -> Path:
    runtime, runtime_sha = load_runtime(RUNTIME)
    source = runtime["source_roster_evidence"]
    scope = build_evidence_scope(
        created_at_utc=created_at_utc,
        collection_epoch_date=collection_epoch_date,
        runtime_manifest_sha256=runtime_sha,
        contract_sha256=runtime["contract"]["sha256"],
        collector_code_sha256_value=collector_code_sha256(),
        source_roster_contract_sha256=source["contract_sha256"],
        source_roster_collector_code_sha256=source["collector_code_sha256"],
    )
    path = tmp_path / "evidence_scope.json"
    payload = json.dumps(scope, sort_keys=True, separators=(",", ":")) + "\n"
    if path.exists():
        assert path.read_text(encoding="utf-8") == payload
    else:
        path.write_text(payload, encoding="utf-8")
    return path


def _final_response(received_at: str = "2026-07-27T23:00:00Z") -> RawOpportunityResponse:
    def players(offset: int) -> dict[str, object]:
        return {
            f"ID{offset + slot}": {
                "person": {"id": offset + slot},
                "battingOrder": f"{slot}00",
                "stats": {"batting": {"plateAppearances": 4}},
            }
            for slot in range(1, 10)
        }

    body = json.dumps(
        {
            "gamePk": GAME_PK,
            "gameData": {
                "datetime": {"officialDate": GAME_DATE},
                "status": {"abstractGameState": "Final", "codedGameState": "F"},
            },
            "liveData": {
                "boxscore": {
                    "teams": {
                        "away": {"team": {"id": AWAY_TEAM_ID}, "players": players(0)},
                        "home": {"team": {"id": HOME_TEAM_ID}, "players": players(100)},
                    }
                }
            },
        },
        sort_keys=True,
    ).encode()
    url = f"https://statsapi.mlb.com/api/v1.1/game/{GAME_PK}/feed/live?" + urlencode(
        {"fields": OPPORTUNITY_FIELDS}
    )
    return RawOpportunityResponse(body, received_at, url)


def _run(tmp_path: Path, *, now: datetime, fetch_final=None) -> tuple[dict, Path, ShadowCapturePlan]:
    plan_dir, roster_root, history_root, plan = _setup_roster_evidence(tmp_path)
    result = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=now,
        fetch_final=fetch_final,
    )
    return result, history_root, plan


def _history_ledger(history_root: Path, plan: ShadowCapturePlan) -> ProspectiveOpportunityHistoryLedger:
    return ProspectiveOpportunityHistoryLedger(
        history_root / GAME_DATE / plan.plan_sha256,
        collection_epoch_date=GAME_DATE,
        contract_sha256=json.loads(RUNTIME.read_text(encoding="utf-8"))["contract"]["sha256"],
        collector_code_sha256=collector_code_sha256(),
        evidence_scope_sha256=json.loads(
            _scope_path(history_root.parent).read_text(encoding="utf-8")
        )["evidence_scope_sha256"],
    )


def test_runtime_identity_is_exact_and_contract_bound(tmp_path):
    runtime, digest = load_runtime(RUNTIME)
    assert digest == hashlib.sha256(RUNTIME.read_bytes()).hexdigest()
    contract_path = ROOT / runtime["contract"]["path"]
    assert hashlib.sha256(contract_path.read_bytes()).hexdigest() == runtime["contract"]["sha256"]
    mutated = json.loads(RUNTIME.read_text(encoding="utf-8"))
    mutated["invariants"]["late_backfill_forbidden"] = False
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ProspectiveBatterOpportunityRuntimeError, match="safety identity"):
        load_runtime(path)


def test_scope_file_replacement_fails_against_deployment_bound_hash(tmp_path):
    scope_path = _scope_path(tmp_path)
    expected = hashlib.sha256(scope_path.read_bytes()).hexdigest()
    value = json.loads(scope_path.read_text(encoding="utf-8"))
    scope_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ProspectiveBatterOpportunityRuntimeError, match="scope file hash differs"):
        _runtime_run_all(
            plan_dir=tmp_path / "plans",
            roster_ledger_root=tmp_path / "rosters",
            history_ledger_root=tmp_path / "history",
            runtime_path=RUNTIME,
            evidence_scope_path=scope_path,
            expected_evidence_scope_file_sha256=expected,
            now=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
            fetch_final=lambda row: _final_response(),
        )


def test_upstream_roster_release_identity_mismatch_fails_before_history_publication(tmp_path):
    plan_dir, roster_root, history_root, plan = _setup_roster_evidence(tmp_path)
    manifest_path = roster_root / GAME_DATE / plan.plan_sha256 / "ledger_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["collector_code_sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ProspectiveBatterOpportunityRuntimeError, match="source roster ledger release identity"):
        run_all(
            plan_dir=plan_dir,
            roster_ledger_root=roster_root,
            history_ledger_root=history_root,
            runtime_path=RUNTIME,
            evidence_scope_path=_scope_path(tmp_path),
            now=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
            fetch_final=lambda row: _final_response(),
        )
    assert not history_root.exists()


def test_rehashed_upstream_schedule_identity_mutation_fails_before_history_publication(tmp_path):
    plan_dir, roster_root, history_root, plan = _setup_roster_evidence(tmp_path)
    source_root = roster_root / GAME_DATE / plan.plan_sha256
    terminal_path = next((source_root / "terminal").glob("*.json"))
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    schedule_path = source_root / terminal["schedule_raw"]["path"]
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    schedule["dates"][0]["games"][0]["teams"]["away"]["team"]["id"] = 999
    mutated = json.dumps(schedule, sort_keys=True).encode()
    schedule_path.write_bytes(mutated)
    terminal["schedule_raw"]["sha256"] = hashlib.sha256(mutated).hexdigest()
    from src.evaluation.projected_lineup_contract import sha256_value

    unsigned = dict(terminal)
    unsigned.pop("entry_sha256")
    terminal["entry_sha256"] = sha256_value(unsigned)
    terminal_path.write_text(json.dumps(terminal), encoding="utf-8")
    with pytest.raises(ProjectedLineupRosterLedgerError, match="schedule raw team identity differs"):
        run_all(
            plan_dir=plan_dir,
            roster_ledger_root=roster_root,
            history_ledger_root=history_root,
            runtime_path=RUNTIME,
            evidence_scope_path=_scope_path(tmp_path),
            now=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
            fetch_final=lambda row: _final_response(),
        )
    assert not history_root.exists()


def test_prestart_tick_plans_then_future_tick_captures_across_date_rollover(tmp_path):
    plan_dir, roster_root, history_root, plan = _setup_roster_evidence(tmp_path)
    fetch_calls: list[str] = []
    before = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("future plan fetched")),
    )
    assert before["planning"]["created"] == 1
    assert before["collection"]["future"] == 1
    captured = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: fetch_calls.append(row["capture_plan_sha256"]) or _final_response(
            "2026-07-28T15:00:00Z"
        ),
    )
    assert captured["official_date"] == "2026-07-28"
    assert captured["collection"]["captured"] == 1
    assert len(fetch_calls) == 1
    verified = _history_ledger(history_root, plan).verify(require_all_terminal=True)
    assert verified["captured"] == 1
    assert verified["missed_before_plan"] == 0


def test_after_start_without_plan_is_immutable_terminal_missingness(tmp_path):
    fetch_calls: list[str] = []
    result, history_root, plan = _run(
        tmp_path,
        now=datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: fetch_calls.append("called") or _final_response(),
    )
    assert result["planning"]["missed_before_plan"] == 1
    assert result["planning"]["created"] == 0
    assert fetch_calls == []
    ledger = _history_ledger(history_root, plan)
    assert ledger.verify()["missed_before_plan"] == 1
    assert ledger.plans() == []
    repeated = run_all(
        plan_dir=tmp_path / "plans",
        roster_ledger_root=tmp_path / "rosters",
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 27, 18, 1, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("terminal miss fetched")),
    )
    assert repeated["planning"]["already_terminal_missed"] == 1
    assert repeated["planning"]["created"] == 0


def test_planning_miss_mutation_is_detected(tmp_path):
    _, history_root, plan = _run(
        tmp_path,
        now=datetime(2026, 7, 27, 18, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: _final_response(),
    )
    path = next((history_root / GAME_DATE / plan.plan_sha256 / "planning_terminal").glob("*.json"))
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["active_roster_terminal"]["team_id"] += 1
    unsigned = dict(entry)
    unsigned.pop("entry_sha256")
    from src.evaluation.projected_lineup_contract import sha256_value

    entry["entry_sha256"] = sha256_value(unsigned)
    path.write_text(json.dumps(entry), encoding="utf-8")
    with pytest.raises(ProspectiveOpportunityHistoryError, match="terminal hash differs"):
        _history_ledger(history_root, plan).verify()


def test_elapsed_capture_deadline_is_terminal_and_never_fetches(tmp_path):
    plan_dir, roster_root, history_root, plan = _setup_roster_evidence(tmp_path)
    run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("future plan fetched")),
    )
    result = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 28, 18, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("expired plan fetched")),
    )
    assert result["collection"]["deadline_missing"] == 1
    assert _history_ledger(history_root, plan).verify(require_all_terminal=True)["deadline_missing"] == 1


def test_network_failure_remains_retryable_then_valid_source_captures(tmp_path):
    plan_dir, roster_root, history_root, plan = _setup_roster_evidence(tmp_path)
    run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("future plan fetched")),
    )
    pending = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 27, 22, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(OSError("synthetic network loss")),
    )
    assert pending["collection"]["pending"] == 1
    assert _history_ledger(history_root, plan).verify()["missing"] == 1
    captured = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 27, 23, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: _final_response(),
    )
    assert captured["collection"]["captured"] == 1
    assert _history_ledger(history_root, plan).verify(require_all_terminal=True)["captured"] == 1


def test_source_invalid_is_hash_size_terminal_without_raw_retention(tmp_path):
    plan_dir, roster_root, history_root, plan = _setup_roster_evidence(tmp_path)
    run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("future plan fetched")),
    )
    valid = _final_response()
    body = json.loads(valid.body)
    body["liveData"]["boxscore"]["teams"]["away"]["players"]["ID1"]["stats"]["batting"][
        "plateAppearances"
    ] = "four"
    rejected = json.dumps(body, sort_keys=True).encode()
    result = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 27, 23, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: RawOpportunityResponse(
            rejected,
            valid.received_at_utc,
            valid.request_url,
        ),
    )
    assert result["collection"]["source_invalid"] == 1
    ledger_root = history_root / GAME_DATE / plan.plan_sha256
    terminal = json.loads(next((ledger_root / "terminal").glob("*.json")).read_text(encoding="utf-8"))
    assert terminal["raw"] is None
    assert terminal["transport_payload_sha256"] == hashlib.sha256(rejected).hexdigest()
    assert terminal["transport_payload_size"] == len(rejected)
    assert not (ledger_root / "raw").exists()
    assert _history_ledger(history_root, plan).verify(require_all_terminal=True)["source_invalid"] == 1


def test_may_tick_returns_before_opening_or_creating_any_evidence_path(tmp_path):
    plan_dir = tmp_path / "never-open-plan"
    roster_root = tmp_path / "never-open-roster"
    history_root = tmp_path / "never-open-history"
    result = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(
            tmp_path,
            collection_epoch_date="2026-04-01",
            created_at_utc="2026-03-31T12:00:00Z",
        ),
        now=datetime(2026, 5, 12, 16, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("May source fetched")),
    )
    assert result["collector_state"] == "sealed_may_no_access"
    assert not plan_dir.exists()
    assert not roster_root.exists()
    assert not history_root.exists()


def test_history_scan_never_touches_a_may_path(tmp_path, monkeypatch):
    root = tmp_path / "history"
    root.mkdir()
    original_is_dir = Path.is_dir

    def guarded_is_dir(path: Path) -> bool:
        if path.name.startswith("2026-05-"):
            raise AssertionError("May path was inspected")
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", guarded_is_dir)
    assert _history_ledgers(
        root,
        collection_epoch=datetime(2026, 4, 30, tzinfo=timezone.utc).date(),
        through_date=datetime(2026, 6, 2, tzinfo=timezone.utc).date(),
        expected_contract_sha256="d" * 64,
        expected_collector_code_sha256="e" * 64,
        expected_evidence_scope_sha256="f" * 64,
    ) == []


def test_runtime_has_no_prediction_price_or_outcome_scoring_authority():
    runtime, _ = load_runtime(RUNTIME)
    assert runtime["invariants"]["probability_generation_forbidden"] is True
    assert runtime["invariants"]["outcome_scoring_forbidden"] is True
    assert runtime["invariants"]["prices_and_economic_evidence_forbidden"] is True
    assert runtime["invariants"]["betting_authorized"] is False


def test_first_run_next_day_records_receipt_proven_miss_once_without_fetch(tmp_path):
    plan_dir, roster_root, history_root, plan = _setup_roster_evidence(tmp_path)
    fetch_calls: list[str] = []
    first = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: fetch_calls.append("called") or _final_response(),
    )
    assert first["planning"]["missed_before_plan"] == 1
    assert first["planning"]["created"] == 0
    assert fetch_calls == []
    second = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 28, 12, 1, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("missed side fetched")),
    )
    assert second["planning"]["already_terminal_missed"] == 1
    assert _history_ledger(history_root, plan).verify()["missed_before_plan"] == 1


def test_first_run_after_multiple_plan_dates_records_each_receipt_proven_miss(tmp_path):
    plan_dir, roster_root, history_root, _ = _setup_roster_evidence(tmp_path)
    _setup_roster_evidence(
        tmp_path,
        game_date="2026-07-28",
        game_pk=1002,
        official_start_time_utc="2026-07-28T18:00:00Z",
        entry_target_at_utc="2026-07-28T14:00:00Z",
    )
    result = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("late side fetched")),
    )
    assert result["planning"]["source_plans_seen"] == 2
    assert result["planning"]["missed_before_plan"] == 2
    assert result["planning"]["created"] == 0
    assert len(list(history_root.glob("*/**/planning_terminal/*.json"))) == 2


def test_missing_roster_manifest_is_explicit_block_and_can_recover(tmp_path):
    plan_dir, roster_root, history_root, plan = _setup_roster_evidence(tmp_path)
    manifest_path = roster_root / GAME_DATE / plan.plan_sha256 / "ledger_manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_path.unlink()
    blocked = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("unproven side fetched")),
    )
    assert blocked["collector_state"] == "blocked_upstream_roster_evidence"
    assert _exit_code_for_result(blocked) == 2
    assert blocked["planning"]["source_ledgers_missing"] == 1
    assert not history_root.exists()
    manifest_path.write_bytes(manifest_bytes)
    recovered = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 27, 17, 1, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("future plan fetched")),
    )
    assert recovered["planning"]["created"] == 1


def test_unreadable_roster_manifest_fails_closed(tmp_path):
    plan_dir, roster_root, history_root, plan = _setup_roster_evidence(tmp_path)
    manifest_path = roster_root / GAME_DATE / plan.plan_sha256 / "ledger_manifest.json"
    manifest_path.write_bytes(b"not-json")
    with pytest.raises(ProspectiveBatterOpportunityRuntimeError, match="manifest is unreadable"):
        run_all(
            plan_dir=plan_dir,
            roster_ledger_root=roster_root,
            history_ledger_root=history_root,
            runtime_path=RUNTIME,
            evidence_scope_path=_scope_path(tmp_path),
            now=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
            fetch_final=lambda row: _final_response(),
        )


def test_zero_and_partial_roster_coverage_never_report_healthy(tmp_path):
    plan_dir, roster_root, history_root, plan = _setup_roster_evidence(tmp_path)
    partial = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path),
        now=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("future plan fetched")),
    )
    assert partial["collector_state"] == "blocked_upstream_roster_evidence"
    assert partial["planning"]["source_terminal_coverage_missing"] == 1

    plan_dir2, roster_root2, history_root2, plan2 = _setup_roster_evidence(tmp_path / "zero")
    source_root2 = roster_root2 / GAME_DATE / plan2.plan_sha256
    for path in (source_root2 / "terminal").glob("*.json"):
        path.unlink()
    for path in (source_root2 / "raw").glob("*.json"):
        path.unlink()
    zero = run_all(
        plan_dir=plan_dir2,
        roster_ledger_root=roster_root2,
        history_ledger_root=history_root2,
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(tmp_path / "zero"),
        now=datetime(2026, 7, 27, 17, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("unproven side fetched")),
    )
    assert zero["collector_state"] == "blocked_upstream_roster_evidence"
    assert zero["planning"]["source_terminal_coverage_missing"] == 2
    assert zero["planning"]["created"] == 0


def test_plan_scan_skips_may_before_constructing_a_source_path(tmp_path, monkeypatch):
    plan_dir = tmp_path / "plans"
    original_is_file = Path.is_file

    def guarded_is_file(path: Path) -> bool:
        if path.parent == plan_dir and path.name.startswith("2026-05-"):
            raise AssertionError("May plan path was constructed or inspected")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", guarded_is_file)
    result = run_all(
        plan_dir=plan_dir,
        roster_ledger_root=tmp_path / "rosters",
        history_ledger_root=tmp_path / "history",
        runtime_path=RUNTIME,
        evidence_scope_path=_scope_path(
            tmp_path,
            collection_epoch_date="2026-04-30",
            created_at_utc="2026-04-30T12:00:00Z",
        ),
        now=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
        fetch_final=lambda row: (_ for _ in ()).throw(AssertionError("source fetched")),
    )
    assert result["collector_state"] == "processed"
