from __future__ import annotations

import json
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import pytest

import src.evaluation.prospective_batter_schedule_denominator as denominator_module

from src.evaluation.prospective_batter_opportunity import (
    OPPORTUNITY_FIELDS,
    RawOpportunityResponse,
    SCHEDULE_FIELDS,
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
from src.evaluation.prospective_batter_opportunity_history import (
    ProspectiveOpportunityHistoryLedger,
)
from src.evaluation.prospective_batter_schedule_denominator import (
    CAPTURED,
    MAX_EARLY_SECONDS,
    MISSED,
    SOURCE_ERROR,
    ScheduleDenominatorError,
    ScheduleDenominatorLedger,
    build_denominator_target,
    parse_prior_schedule_denominator,
    permitted_prior_segments,
    run_target_tick,
)
from src.evaluation.shadow_capture_plan import (
    CaptureTarget,
    ShadowCapturePlan,
    ShadowCapturePlanError,
)


TEAM_ID = 147
OTHER_TEAM_ID = 121
TARGET_DATE = "2026-07-30"
HORIZON = "2026-07-30T16:00:00Z"


def _plan() -> tuple[ShadowCapturePlan, CaptureTarget]:
    target = CaptureTarget(
        mlb_game_pk=2001,
        official_game_date=TARGET_DATE,
        official_start_time_utc="2026-07-30T20:00:00Z",
        entry_target_at_utc=HORIZON,
        entry_hours=4,
    )
    plan = ShadowCapturePlan(
        official_game_date=TARGET_DATE,
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot_sha256="b" * 64,
        targets=(target,),
    )
    return plan, target


def _authorities(
    tmp_path: Path, *, epoch: str = "2026-07-27"
) -> tuple[
    ProjectedLineupRosterLedger,
    ProspectiveOpportunityHistoryLedger,
    dict,
]:
    plan, target = _plan()
    runtime_raw = denominator_module.RUNTIME_PATH.read_bytes()
    runtime = json.loads(runtime_raw)
    roster_binding = runtime["source_roster_evidence"]

    def captured_roster(
        *, source_plan: ShadowCapturePlan, source_target: CaptureTarget, root: Path
    ) -> ProjectedLineupRosterLedger:
        source_date = source_target.official_game_date
        committed = f"{source_date}T15:59:30Z"
        roster_raw = json.dumps(
            {
                "copyright": "x", "link": "/api", "roster": players,
                "rosterType": "active", "teamId": TEAM_ID,
            },
            sort_keys=True,
        ).encode()
        roster_record = parse_active_roster_receipt(
            response=RawOfficialRosterResponse(roster_raw, committed),
            requested_date=source_date,
            team_id=TEAM_ID,
            target_horizon_utc=source_target.entry_target_at_utc,
        )
        ledger = ProjectedLineupRosterLedger(
            root,
            plan=source_plan,
            contract_sha256=roster_binding["contract_sha256"],
            collector_code_sha256=roster_binding["collector_code_sha256"],
        )
        schedule_raw = json.dumps(
            {
                "dates": [{"games": [{
                    "gamePk": source_target.mlb_game_pk,
                    "officialDate": source_date,
                    "gameDate": source_target.official_start_time_utc,
                    "teams": {
                        "away": {"team": {"id": TEAM_ID, "name": "Away"}},
                        "home": {"team": {"id": OTHER_TEAM_ID, "name": "Home"}},
                    },
                }]}]
            },
            sort_keys=True,
        ).encode()
        ledger.append_capture(
            target=source_target,
            side="away",
            team_id=TEAM_ID,
            roster_record=roster_record,
            schedule_raw=schedule_raw,
            schedule_received_at_utc=committed,
            roster_raw=roster_raw,
            committed_utc=committed,
        )
        return ledger

    players = [
        {
            "jerseyNumber": str(player_id),
            "parentTeamId": TEAM_ID,
            "person": {
                "fullName": f"Player {player_id}",
                "id": player_id,
                "link": f"/api/v1/people/{player_id}",
            },
            "position": {
                "abbreviation": "SS", "code": "6", "name": "Shortstop",
                "type": "Infielder",
            },
            "status": {"code": "A", "description": "Active"},
        }
        for player_id in range(1, 10)
    ]
    roster = captured_roster(
        source_plan=plan,
        source_target=target,
        root=tmp_path / "roster" / TARGET_DATE / plan.plan_sha256,
    )
    scope_unsigned = {
        "schema_version": "prospective-batter-opportunity-evidence-scope-v1",
        "created_at_utc": "2026-07-26T12:00:00.000000Z",
        "collection_epoch_date": epoch,
        "runtime_manifest_sha256": hashlib.sha256(runtime_raw).hexdigest(),
        "contract_sha256": runtime["contract"]["sha256"],
        "collector_code_sha256": denominator_module._history_collector_identity(),
        "source_roster_contract_sha256": roster_binding["contract_sha256"],
        "source_roster_collector_code_sha256": roster_binding["collector_code_sha256"],
        "research_only": True,
        "economic_evidence_eligible": False,
        "historical_backfill_authorized": False,
        "production_probability_consumption_authorized": False,
        "betting_authorized": False,
    }
    scope = {**scope_unsigned, "evidence_scope_sha256": sha256_value(scope_unsigned)}
    history = ProspectiveOpportunityHistoryLedger(
        tmp_path / "history" / TARGET_DATE / plan.plan_sha256,
        collection_epoch_date=epoch,
        contract_sha256=scope["contract_sha256"],
        collector_code_sha256=scope["collector_code_sha256"],
        evidence_scope_sha256=scope["evidence_scope_sha256"],
    )
    if epoch == "2026-07-27":
        prior_target = CaptureTarget(
            mlb_game_pk=1001,
            official_game_date="2026-07-27",
            official_start_time_utc="2026-07-27T20:00:00Z",
            entry_target_at_utc="2026-07-27T16:00:00Z",
            entry_hours=4,
        )
        prior_plan = ShadowCapturePlan(
            official_game_date="2026-07-27",
            entry_hours=4,
            policy_sha256="8" * 64,
            schedule_snapshot_sha256="9" * 64,
            targets=(prior_target,),
        )
        prior_roster = captured_roster(
            source_plan=prior_plan,
            source_target=prior_target,
            root=tmp_path / "prior_roster" / "2026-07-27" / prior_plan.plan_sha256,
        )
        terminal = json.loads(
            (
                prior_roster.root
                / "terminal"
                / (
                    roster_side_target_id(
                        plan=prior_plan, target=prior_target, side="away"
                    )
                    + ".json"
                )
            ).read_text(encoding="utf-8")
        )
        capture_plan = build_history_capture_plan(
            created_at_utc="2026-07-27T16:00:00Z",
            mlb_game_pk=1001,
            official_game_date="2026-07-27",
            official_start_time_utc="2026-07-27T20:00:00Z",
            capture_deadline_utc="2026-07-28T20:00:00Z",
            side="away",
            team_id=TEAM_ID,
            source_t4_plan_sha256=prior_plan.plan_sha256,
            roster_side_target_id=terminal["side_target_id"],
            active_roster_receipt_sha256=sha256_value(terminal["roster"]),
        )
        history.append_plan(
            capture_plan,
            published_at_utc="2026-07-27T16:00:00Z",
            source_roster_ledger=prior_roster,
        )
        history.append_exclusion(
            plan=capture_plan,
            state="source_invalid",
            observed_at_utc="2026-07-27T21:00:00Z",
            detail="receipt failed closed",
            raw_payload=b'{"invalid":true}',
        )
    return roster, history, scope


def _target(tmp_path: Path, *, epoch: str = "2026-07-27") -> dict:
    return _case(tmp_path, epoch=epoch)[0]


def _case(tmp_path: Path, *, epoch: str = "2026-07-27"):
    plan, target = _plan()
    roster, history, scope = _authorities(tmp_path, epoch=epoch)
    denominator_target = build_denominator_target(
        plan=plan,
        target=target,
        side="away",
        source_roster_ledger=roster,
        source_history_ledger=history,
        source_evidence_scope=scope,
    )
    return denominator_target, roster, history, scope


def _url(start: str, end: str, team_id: int = TEAM_ID) -> str:
    return "https://statsapi.mlb.com/api/v1/schedule?" + urlencode(
        {
            "sportId": "1",
            "teamId": str(team_id),
            "startDate": start,
            "endDate": end,
            "fields": SCHEDULE_FIELDS,
        }
    )


def _raw(
    start: str = "2026-07-27",
    end: str = "2026-07-29",
    *,
    team_id: int = TEAM_ID,
    games: tuple[tuple[int, str, bool], ...] = (
        (1001, "2026-07-27", True),
    ),
) -> bytes:
    from datetime import date, timedelta

    rows = []
    cursor = date.fromisoformat(start)
    final = date.fromisoformat(end)
    while cursor <= final:
        day = cursor.isoformat()
        day_games = []
        for game_pk, official_date, coded_final in games:
            if official_date != day:
                continue
            day_games.append(
                {
                    "gamePk": game_pk,
                    "officialDate": official_date,
                    "status": {
                        "abstractGameState": "Final" if coded_final else "Preview",
                        "codedGameState": "F" if coded_final else "S",
                    },
                    "teams": {
                        "away": {"team": {"id": team_id}},
                        "home": {"team": {"id": OTHER_TEAM_ID}},
                    },
                }
            )
        rows.append({"date": day, "games": day_games})
        cursor += timedelta(days=1)
    return json.dumps({"dates": rows}, sort_keys=True).encode("utf-8")


def _response(
    start: str = "2026-07-27",
    end: str = "2026-07-29",
    *,
    received: str = "2026-07-30T15:59:00Z",
    team_id: int = TEAM_ID,
    games: tuple[tuple[int, str, bool], ...] = (
        (1001, "2026-07-27", True),
    ),
) -> RawOpportunityResponse:
    return RawOpportunityResponse(
        _raw(start, end, team_id=team_id, games=games),
        received,
        _url(start, end, team_id),
    )


def _ledger(tmp_path: Path) -> ScheduleDenominatorLedger:
    return ScheduleDenominatorLedger(tmp_path / "ledger")


def _clock(value: str = "2026-07-30T15:59:00Z") -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def _trusted_clock(*values: str):
    queue = iter(values or ("2026-07-30T15:59:00Z", "2026-07-30T15:59:01Z"))
    last = _clock(values[-1] if values else "2026-07-30T15:59:01Z")

    def read() -> datetime:
        nonlocal last
        try:
            last = _clock(next(queue))
        except StopIteration:
            pass
        return last

    return read


def _run(
    tmp_path: Path,
    *,
    ledger: ScheduleDenominatorLedger,
    fetch_schedule,
    trusted_clock,
    epoch: str = "2026-07-27",
    max_early_seconds: int = MAX_EARLY_SECONDS,
) -> str:
    target, roster, history, scope = _case(tmp_path, epoch=epoch)
    return run_target_tick(
        ledger=ledger,
        target=target,
        fetch_schedule=fetch_schedule,
        source_roster_ledger=roster,
        source_history_ledger=history,
        source_evidence_scope=scope,
        trusted_clock=trusted_clock,
        max_early_seconds=max_early_seconds,
    )


def test_tick_publishes_and_replays_exact_surfaces_10_11_12(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    fetch_calls: list[tuple[str, str, int]] = []

    def fetch(start: str, end: str, team_id: int) -> RawOpportunityResponse:
        fetch_calls.append((start, end, team_id))
        return _response(start, end, team_id=team_id)

    state = _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=fetch,
        trusted_clock=_trusted_clock(),
    )

    assert state == CAPTURED
    assert fetch_calls == [("2026-07-27", "2026-07-29", TEAM_ID)]
    assert ledger.verify() == {CAPTURED: 1, SOURCE_ERROR: 0, MISSED: 0}
    terminal_path = next((ledger.root / "targets").glob("*/terminal.json"))
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    assert terminal["surface_10_history_coverage"]["expected_prior_game_pks"] == [1001]
    assert terminal["surface_10_history_coverage"]["complete_coverage"] is False
    assert len(terminal["surface_11_prior_schedule_receipts"]) == 1
    assert len(terminal["surface_12_prior_schedule_raw"]) == 1


def test_restart_is_idempotent_and_never_fetches_again(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    calls = 0

    def fetch(start: str, end: str, team_id: int) -> RawOpportunityResponse:
        nonlocal calls
        calls += 1
        return _response(start, end, team_id=team_id)

    assert _run(
        tmp_path, ledger=ledger, fetch_schedule=fetch, trusted_clock=_trusted_clock()
    ) == CAPTURED
    assert _run(
        tmp_path, ledger=ledger, fetch_schedule=fetch, trusted_clock=_trusted_clock()
    ) == "terminal_existing"
    assert calls == 1


def test_late_tick_is_permanently_missed_without_fetch(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    called = False

    def fetch(start: str, end: str, team_id: int) -> RawOpportunityResponse:
        nonlocal called
        called = True
        raise AssertionError("late tick must not fetch")

    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=fetch,
        trusted_clock=_trusted_clock("2026-07-30T16:00:00.000001Z"),
    ) == MISSED
    assert called is False
    assert ledger.verify()[MISSED] == 1
    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=fetch,
        trusted_clock=_trusted_clock("2026-07-31T16:00:00Z"),
    ) == "terminal_existing"
    assert called is False


def test_early_tick_waits_without_fetch_or_terminal(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    called = False

    def fetch(start: str, end: str, team_id: int) -> RawOpportunityResponse:
        nonlocal called
        called = True
        return _response(start, end, team_id=team_id)

    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=fetch,
        trusted_clock=_trusted_clock("2026-07-30T15:57:59Z"),
    ) == "future"
    assert called is False
    assert ledger.terminal_target_ids() == set()


def test_source_error_is_terminal_and_retains_no_raw(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    calls = 0

    def fetch(start: str, end: str, team_id: int) -> RawOpportunityResponse:
        nonlocal calls
        calls += 1
        raise TimeoutError("synthetic")

    assert _run(
        tmp_path, ledger=ledger, fetch_schedule=fetch, trusted_clock=_trusted_clock()
    ) == SOURCE_ERROR
    assert _run(
        tmp_path, ledger=ledger, fetch_schedule=fetch, trusted_clock=_trusted_clock()
    ) == "terminal_existing"
    assert calls == 1
    assert not list((ledger.root / "targets").glob("*/raw/*.json"))
    assert ledger.verify()[SOURCE_ERROR] == 1


@pytest.mark.parametrize(
    "response",
    [
        _response(received="2026-07-30T15:57:59Z"),
        _response(received="2026-07-30T16:00:00.000001Z"),
        _response(team_id=999),
    ],
)
def test_stale_late_or_wrong_team_response_fails_terminally(
    tmp_path: Path, response: RawOpportunityResponse
) -> None:
    ledger = _ledger(tmp_path)
    state = _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=lambda start, end, team_id: response,
        trusted_clock=_trusted_clock(),
    )
    assert state in {SOURCE_ERROR, MISSED}
    assert ledger.verify()[state] == 1


def test_caller_authored_partition_or_fallback_is_impossible(tmp_path: Path) -> None:
    target, roster, history, scope = _case(tmp_path)
    with pytest.raises(TypeError, match="unexpected keyword"):
        run_target_tick(
            ledger=_ledger(tmp_path),
            target=target,
            fetch_schedule=lambda start, end, team_id: _response(start, end),
            source_roster_ledger=roster,
            source_history_ledger=history,
            source_evidence_scope=scope,
            trusted_clock=_trusted_clock(),
            resolve_history_partition=lambda expected: expected,
        )


def test_omitted_returned_date_is_not_accepted_as_covered() -> None:
    payload = json.loads(_raw())
    payload["dates"] = [row for row in payload["dates"] if row["date"] != "2026-07-28"]
    response = RawOpportunityResponse(
        json.dumps(payload, sort_keys=True).encode(),
        "2026-07-30T15:59:00Z",
        _url("2026-07-27", "2026-07-29"),
    )
    with pytest.raises(ScheduleDenominatorError, match="exactly every requested date"):
        parse_prior_schedule_denominator(
            response=response,
            requested_start_date="2026-07-27",
            requested_end_date="2026-07-29",
            target_official_game_date=TARGET_DATE,
            target_horizon_utc=HORIZON,
            team_id=TEAM_ID,
        )


def test_partial_multisegment_fetch_never_falls_back_or_retains_raw(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    calls = 0

    def fetch(start: str, end: str, team_id: int) -> RawOpportunityResponse:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError("second segment unavailable")
        return _response(start, end, team_id=team_id, games=())

    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=fetch,
        trusted_clock=_trusted_clock(),
        epoch="2026-04-30",
    ) == SOURCE_ERROR
    assert calls == 2
    assert not list((ledger.root / "targets").glob("*/raw/*.json"))


@pytest.mark.parametrize(
    ("received", "committed", "expected_state"),
    [
        (("2026-07-30T15:59:20Z", "2026-07-30T15:59:10Z"),
         "2026-07-30T15:59:20Z", CAPTURED),
        (("2026-07-30T15:59:10Z", "2026-07-30T15:59:21Z"),
         "2026-07-30T15:59:20Z", SOURCE_ERROR),
        (("2026-07-30T15:57:59Z", "2026-07-30T15:59:10Z"),
         "2026-07-30T15:59:20Z", SOURCE_ERROR),
    ],
)
def test_multisegment_first_min_and_max_receipt_chronology(
    tmp_path: Path,
    received: tuple[str, str],
    committed: str,
    expected_state: str,
) -> None:
    calls = 0

    def fetch(start: str, end: str, team_id: int) -> RawOpportunityResponse:
        nonlocal calls
        response = _response(
            start, end, team_id=team_id, games=(), received=received[calls]
        )
        calls += 1
        return response

    assert _run(
        tmp_path,
        ledger=_ledger(tmp_path),
        fetch_schedule=fetch,
        trusted_clock=_trusted_clock("2026-07-30T15:59:00Z", committed),
        epoch="2026-04-30",
    ) == expected_state


def test_forged_rehashed_target_cannot_replace_ledger_authority(tmp_path: Path) -> None:
    target, roster, history, scope = _case(tmp_path)
    forged = dict(target)
    forged["team_id"] = OTHER_TEAM_ID
    forged.pop("denominator_target_id")
    forged["denominator_target_id"] = sha256_value(forged)
    called = False

    def fetch(start: str, end: str, team_id: int) -> RawOpportunityResponse:
        nonlocal called
        called = True
        return _response(start, end, team_id=team_id)

    with pytest.raises(ScheduleDenominatorError, match="authority replay differs"):
        run_target_tick(
            ledger=_ledger(tmp_path),
            target=forged,
            fetch_schedule=fetch,
            source_roster_ledger=roster,
            source_history_ledger=history,
            source_evidence_scope=scope,
            trusted_clock=_trusted_clock(),
        )
    assert called is False


def test_forged_rehashed_target_cannot_replace_game_chronology(tmp_path: Path) -> None:
    target, roster, history, scope = _case(tmp_path)
    forged = dict(target)
    forged["mlb_game_pk"] = target["mlb_game_pk"] + 1
    forged.pop("denominator_target_id")
    forged["denominator_target_id"] = sha256_value(forged)
    called = False

    def fetch(start: str, end: str, team_id: int) -> RawOpportunityResponse:
        nonlocal called
        called = True
        return _response(start, end, team_id=team_id)

    with pytest.raises(ScheduleDenominatorError, match="game chronology differs"):
        run_target_tick(
            ledger=_ledger(tmp_path),
            target=forged,
            fetch_schedule=fetch,
            source_roster_ledger=roster,
            source_history_ledger=history,
            source_evidence_scope=scope,
            trusted_clock=_trusted_clock(),
        )
    assert called is False


def test_concurrent_ticks_have_one_verified_winner(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    target, roster, history, scope = _case(tmp_path)
    barrier = threading.Barrier(2)

    def fetch(start: str, end: str, team_id: int) -> RawOpportunityResponse:
        barrier.wait(timeout=5)
        return _response(start, end, team_id=team_id)

    def tick() -> str:
        return run_target_tick(
            ledger=ledger,
            target=target,
            fetch_schedule=fetch,
            source_roster_ledger=roster,
            source_history_ledger=history,
            source_evidence_scope=scope,
            trusted_clock=_trusted_clock(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in (pool.submit(tick), pool.submit(tick))]
    assert sorted(results) == [CAPTURED, "terminal_existing"]
    assert ledger.verify()[CAPTURED] == 1


def test_concurrent_late_ticks_have_one_verified_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _ledger(tmp_path)
    target, roster, history, scope = _case(tmp_path)
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    calls = 0
    real_terminal_ids = ledger.terminal_target_ids

    def raced_terminal_ids() -> set[str]:
        nonlocal calls
        with lock:
            calls += 1
            number = calls
        if number <= 2:
            barrier.wait(timeout=5)
        return real_terminal_ids()

    monkeypatch.setattr(ledger, "terminal_target_ids", raced_terminal_ids)

    def tick(stamp: str) -> str:
        return run_target_tick(
            ledger=ledger,
            target=target,
            fetch_schedule=lambda *_: pytest.fail("late tick must not fetch"),
            source_roster_ledger=roster,
            source_history_ledger=history,
            source_evidence_scope=scope,
            trusted_clock=_trusted_clock(stamp),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result()
            for future in (
                pool.submit(tick, "2026-07-30T16:00:00.000001Z"),
                pool.submit(tick, "2026-07-30T16:00:00.000002Z"),
            )
        ]
    assert sorted(results) == [MISSED, "terminal_existing"]
    assert ledger.verify()[MISSED] == 1


def test_response_must_precede_fresh_commit_clock(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=lambda start, end, team_id: _response(
            start, end, received="2026-07-30T15:59:30Z"
        ),
        trusted_clock=_trusted_clock(
            "2026-07-30T15:59:00Z", "2026-07-30T15:59:01Z"
        ),
    ) == SOURCE_ERROR


def test_fetch_crossing_horizon_publishes_only_permanent_miss(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=lambda start, end, team_id: _response(start, end),
        trusted_clock=_trusted_clock(
            "2026-07-30T15:59:00Z", "2026-07-30T16:00:00.000001Z"
        ),
    ) == MISSED
    assert not list((ledger.root / "targets").glob("*/raw/*.json"))
    assert ledger.verify()[MISSED] == 1


def test_raw_mutation_and_orphan_are_visible_to_verifier(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=lambda start, end, team_id: _response(start, end),
        trusted_clock=_trusted_clock(),
    ) == CAPTURED
    raw_path = next((ledger.root / "targets").glob("*/raw/*.json"))
    original = raw_path.read_bytes()
    raw_path.write_bytes(original + b" ")
    with pytest.raises(ScheduleDenominatorError, match="raw schedule bytes differ"):
        ledger.verify()
    raw_path.write_bytes(original)
    (raw_path.parent / ("f" * 64 + ".json")).write_bytes(b"{}")
    with pytest.raises(ScheduleDenominatorError, match="target bytes differ"):
        ledger.verify()


def test_nested_raw_alias_is_not_canonical_even_when_rehashed(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=lambda start, end, team_id: _response(start, end),
        trusted_clock=_trusted_clock(),
    ) == CAPTURED
    terminal_path = next((ledger.root / "targets").glob("*/terminal.json"))
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    reference = terminal["surface_12_prior_schedule_raw"][0]
    reference["path"] = reference["path"].replace("/raw/", "/raw/nested/../")
    unsigned = dict(terminal)
    unsigned.pop("terminal_sha256")
    terminal["terminal_sha256"] = sha256_value(unsigned)
    terminal_path.write_text(json.dumps(terminal, sort_keys=True), encoding="utf-8")
    with pytest.raises(ScheduleDenominatorError, match="path binding differs"):
        ledger.verify()


def test_copied_history_alias_is_not_canonical_even_when_rehashed(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=lambda start, end, team_id: _response(start, end),
        trusted_clock=_trusted_clock(),
    ) == CAPTURED
    terminal_path = next((ledger.root / "targets").glob("*/terminal.json"))
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    reference = terminal["history_authority_files"][0]
    reference["source_relative_path"] = f"./{reference['source_relative_path']}"
    reference["path"] = reference["path"].replace(
        "/history_authority/", "/history_authority/./"
    )
    unsigned = dict(terminal)
    unsigned.pop("terminal_sha256")
    terminal["terminal_sha256"] = sha256_value(unsigned)
    terminal_path.write_text(json.dumps(terminal, sort_keys=True), encoding="utf-8")
    with pytest.raises(ScheduleDenominatorError, match="not canonical"):
        ledger.verify()


def test_release_identity_closure_includes_authority_dependencies_and_mutates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    declared = {path.relative_to(denominator_module.ROOT).as_posix() for path in denominator_module.CODE_PATHS}
    assert {
        "src/evaluation/projected_lineup_contract.py",
        "src/evaluation/projected_lineup_official_roster.py",
        "src/evaluation/shared_pa_forward_collector.py",
        "src/evaluation/shared_pa_forward_evidence.py",
        "src/evaluation/prospective_batter_opportunity_history.py",
        "src/evaluation/prospective_batter_opportunity_ledger.py",
        "scripts/run_prospective_batter_opportunity_tick.py",
    } <= declared
    synthetic_root = tmp_path / "release"
    synthetic_root.mkdir()
    contract = synthetic_root / "contract.json"
    dependency = synthetic_root / "dependency.py"
    contract.write_bytes(b"{}")
    dependency.write_bytes(b"first")
    monkeypatch.setattr(denominator_module, "ROOT", synthetic_root)
    monkeypatch.setattr(denominator_module, "CONTRACT_PATH", contract)
    monkeypatch.setattr(denominator_module, "CODE_PATHS", (dependency,))
    first = denominator_module.release_identities()[1]
    dependency.write_bytes(b"second")
    assert denominator_module.release_identities()[1] != first


def test_release_identity_rejects_unresolved_linked_module_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = tmp_path / "module.py"
    actual.write_bytes(b"source")
    linked = tmp_path / "linked-module.py"
    try:
        linked.symlink_to(actual)
    except OSError:
        linked.write_bytes(b"source")
        real_is_reparse = denominator_module._is_reparse
        monkeypatch.setattr(
            denominator_module,
            "_is_reparse",
            lambda path: Path(path) == linked or real_is_reparse(path),
        )
    monkeypatch.setattr(denominator_module, "MODULE_PATH", linked)
    with pytest.raises(ScheduleDenominatorError, match="linked or reparse"):
        denominator_module.release_identities()


def test_duplicate_manifest_key_is_rejected(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    manifest_path = ledger.root / "ledger_manifest.json"
    manifest_path.write_bytes(
        manifest_path.read_bytes().replace(
            b'"schema_version":',
            b'"schema_version":"duplicate","schema_version":',
            1,
        )
    )
    with pytest.raises(ScheduleDenominatorError, match="duplicate JSON key"):
        ledger.verify()


def test_rehashed_outer_safety_flag_is_rejected(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=lambda start, end, team_id: _response(start, end),
        trusted_clock=_trusted_clock(),
    ) == CAPTURED
    terminal_path = next((ledger.root / "targets").glob("*/terminal.json"))
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    terminal["backfill_authorized"] = True
    unsigned = dict(terminal)
    unsigned.pop("terminal_sha256")
    terminal["terminal_sha256"] = sha256_value(unsigned)
    terminal_path.write_text(json.dumps(terminal, sort_keys=True), encoding="utf-8")
    with pytest.raises(ScheduleDenominatorError, match="terminal state differs"):
        ledger.verify()


def test_atomic_writer_cleans_crash_and_verifies_races(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _ledger(tmp_path)
    target, roster, history, scope = _case(tmp_path)
    authority = {
        "source_roster_ledger": roster,
        "source_history_ledger": history,
        "source_evidence_scope": scope,
    }
    real_rename = denominator_module.os.rename

    def crash(source: Path, destination: Path) -> None:
        raise OSError("synthetic crash before atomic exposure")

    monkeypatch.setattr(denominator_module.os, "rename", crash)
    with pytest.raises(OSError, match="synthetic crash"):
        ledger._append_exclusion(
            target=target,
            **authority,
            state=MISSED,
            observed_at_utc="2026-07-30T16:00:00.000001Z",
            detail="late tick",
        )
    assert list((ledger.root / "targets").iterdir()) == []

    monkeypatch.setattr(denominator_module.os, "rename", real_rename)
    assert ledger._append_exclusion(
        target=target,
        **authority,
        state=MISSED,
        observed_at_utc="2026-07-30T16:00:00.000001Z",
        detail="late tick",
    ) is True
    assert ledger._append_exclusion(
        target=target,
        **authority,
        state=MISSED,
        observed_at_utc="2026-07-30T16:00:00.000001Z",
        detail="late tick",
    ) is False
    with pytest.raises(ScheduleDenominatorError, match="target bytes differ"):
        ledger._append_exclusion(
            target=target,
            **authority,
            state=MISSED,
            observed_at_utc="2026-07-30T16:00:00.000001Z",
            detail="conflicting concurrent writer",
        )
    debris = ledger.root / ".staging" / "abrupt-process-debris"
    debris.mkdir(parents=True)
    (debris / "partial").write_bytes(b"not authoritative")
    assert ledger.verify()[MISSED] == 1


def test_target_refuses_unverified_side_or_release_path(tmp_path: Path) -> None:
    plan, target = _plan()
    roster, history, scope = _authorities(tmp_path)
    with pytest.raises(ScheduleDenominatorError, match="captured roster authority"):
        build_denominator_target(
            plan=plan,
            target=target,
            side="home",
            source_roster_ledger=roster,
            source_history_ledger=history,
            source_evidence_scope=scope,
        )
    wrong_history = ProspectiveOpportunityHistoryLedger(
        tmp_path / "wrong-release-path",
        collection_epoch_date="2026-07-27",
        contract_sha256=scope["contract_sha256"],
        collector_code_sha256=scope["collector_code_sha256"],
        evidence_scope_sha256=scope["evidence_scope_sha256"],
    )
    with pytest.raises(ScheduleDenominatorError, match="release path differs"):
        build_denominator_target(
            plan=plan,
            target=target,
            side="away",
            source_roster_ledger=roster,
            source_history_ledger=wrong_history,
            source_evidence_scope=scope,
        )


def test_linked_ledger_root_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(actual, target_is_directory=True)
    except OSError:
        linked.mkdir()
        real_is_reparse = denominator_module._is_reparse
        monkeypatch.setattr(
            denominator_module,
            "_is_reparse",
            lambda path: Path(path) == linked or real_is_reparse(path),
        )
    with pytest.raises(ScheduleDenominatorError, match="linked or reparse"):
        ScheduleDenominatorLedger(linked)


def test_linked_source_raw_is_rejected_before_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _ledger(tmp_path)
    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=lambda start, end, team_id: _response(start, end),
        trusted_clock=_trusted_clock(),
    ) == CAPTURED
    raw_path = next((ledger.root / "targets").glob("*/raw/*.json"))
    original = raw_path.read_bytes()
    outside = tmp_path / "outside.json"
    outside.write_bytes(original)
    raw_path.unlink()
    try:
        raw_path.symlink_to(outside)
    except OSError:
        raw_path.write_bytes(original)
        real_is_reparse = denominator_module._is_reparse
        monkeypatch.setattr(
            denominator_module,
            "_is_reparse",
            lambda path: Path(path) == raw_path or real_is_reparse(path),
        )
    with pytest.raises(ScheduleDenominatorError, match="linked or reparse"):
        ledger.verify()


def test_zero_game_denominator_is_valid_exact_coverage(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    no_games: tuple[tuple[int, str, bool], ...] = ()
    assert _run(
        tmp_path,
        ledger=ledger,
        fetch_schedule=lambda start, end, team_id: _response(
            start, end, games=no_games
        ),
        trusted_clock=_trusted_clock(),
        epoch="2026-07-29",
    ) == CAPTURED
    terminal = json.loads(
        next((ledger.root / "targets").glob("*/terminal.json")).read_text(encoding="utf-8")
    )
    coverage = terminal["surface_10_history_coverage"]
    assert coverage["expected_game_count"] == 0
    assert coverage["complete_coverage"] is True


def test_segments_skip_may_without_constructing_a_may_request() -> None:
    assert permitted_prior_segments(
        collection_epoch_date="2026-04-30",
        target_official_game_date="2026-06-02",
    ) == (("2026-04-30", "2026-04-30"), ("2026-06-01", "2026-06-01"))
    with pytest.raises(ScheduleDenominatorError, match="May 2026"):
        permitted_prior_segments(
            collection_epoch_date="2026-05-01",
            target_official_game_date="2026-06-02",
        )


def test_may_target_is_rejected_before_fetch_construction() -> None:
    with pytest.raises(ShadowCapturePlanError, match="May 2026"):
        CaptureTarget(
            mlb_game_pk=2002,
            official_game_date="2026-05-20",
            official_start_time_utc="2026-05-20T20:00:00Z",
            entry_target_at_utc="2026-05-20T16:00:00Z",
            entry_hours=4,
        )


def test_may_parse_rejection_precedes_poison_response_body_access() -> None:
    class PoisonResponse:
        @property
        def body(self) -> bytes:
            raise AssertionError("May rejection must precede body access")

    with pytest.raises(ScheduleDenominatorError, match="May 2026"):
        parse_prior_schedule_denominator(
            response=PoisonResponse(),  # type: ignore[arg-type]
            requested_start_date="2026-04-30",
            requested_end_date="2026-04-30",
            target_official_game_date="2026-05-01",
            target_horizon_utc="2026-05-01T16:00:00Z",
            team_id=TEAM_ID,
        )

    with pytest.raises(ScheduleDenominatorError, match="May 2026"):
        parse_prior_schedule_denominator(
            response=PoisonResponse(),  # type: ignore[arg-type]
            requested_start_date="2026-04-30",
            requested_end_date="2026-06-01",
            target_official_game_date="2026-06-02",
            target_horizon_utc="2026-06-02T16:00:00Z",
            team_id=TEAM_ID,
        )


def test_locked_early_window_cannot_be_changed(tmp_path: Path) -> None:
    with pytest.raises(ScheduleDenominatorError, match="early window"):
        _run(
            tmp_path,
            ledger=_ledger(tmp_path),
            fetch_schedule=lambda start, end, team_id: _response(start, end),
            trusted_clock=_trusted_clock(),
            max_early_seconds=MAX_EARLY_SECONDS + 1,
        )
