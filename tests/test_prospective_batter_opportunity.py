from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

from src.evaluation.projected_lineup_official_roster import (
    RawOfficialRosterResponse,
    parse_active_roster_receipt,
)
from src.evaluation.projected_lineup_contract import sha256_value
from src.evaluation.prospective_batter_opportunity import (
    ProspectiveBatterOpportunityError,
    OPPORTUNITY_FIELDS,
    RawOpportunityResponse,
    SCHEDULE_FIELDS,
    build_history_capture_plan,
    build_history_coverage,
    build_pregame_opportunity_snapshot,
    parse_opportunity_history,
    parse_prior_schedule_denominator,
    sanitize_opportunity_transport,
    validate_snapshot,
)


REAL_SOURCE_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "prospective_batter_opportunity"
    / "official_mlb_717545_sanitized_v1.json"
)
from src.evaluation.prospective_batter_opportunity_ledger import (
    ProspectiveBatterOpportunityLedger,
    ProspectiveBatterOpportunityLedgerError,
)
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan


TEAM_ID = 147
TARGET_DATE = "2026-07-30"
HORIZON = "2026-07-30T16:00:00Z"


def _opportunity_url(game_pk: int) -> str:
    return f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live?{urlencode({'fields': OPPORTUNITY_FIELDS})}"


def _history_plan(
    game_pk: int,
    official_date: str,
    *,
    side: str = "away",
    team_id: int = TEAM_ID,
) -> dict:
    return build_history_capture_plan(
        created_at_utc=f"{official_date}T12:00:00Z",
        mlb_game_pk=game_pk,
        official_game_date=official_date,
        official_start_time_utc=f"{official_date}T18:00:00Z",
        capture_deadline_utc=f"{official_date}T23:59:59Z",
        side=side,
        team_id=team_id,
        source_t4_plan_sha256="a" * 64,
        roster_side_target_id="b" * 64,
        active_roster_receipt_sha256="c" * 64,
    )


def _schedule_url(start_date: str = "2026-07-27", end_date: str = "2026-07-29") -> str:
    return "https://statsapi.mlb.com/api/v1/schedule?" + urlencode(
        {
            "sportId": "1",
            "teamId": str(TEAM_ID),
            "startDate": start_date,
            "endDate": end_date,
            "fields": SCHEDULE_FIELDS,
        }
    )


def _roster_raw() -> bytes:
    roster = []
    for player_id in range(1, 13):
        roster.append(
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
        )
    return json.dumps(
        {
            "copyright": "x",
            "link": "/api",
            "roster": roster,
            "rosterType": "active",
            "teamId": TEAM_ID,
        },
        sort_keys=True,
    ).encode()


def _roster_receipt(raw: bytes | None = None) -> dict:
    body = raw or _roster_raw()
    return parse_active_roster_receipt(
        response=RawOfficialRosterResponse(body, "2026-07-30T15:58:00Z"),
        requested_date=TARGET_DATE,
        team_id=TEAM_ID,
        target_horizon_utc=HORIZON,
    )


def _opportunity_raw(game_pk: int, official_date: str, *, pa_offset: int = 0) -> bytes:
    away_players = {}
    for slot in range(1, 10):
        away_players[f"ID{slot}"] = {
            "person": {"id": slot},
            "battingOrder": f"{slot}00",
            "stats": {"batting": {"plateAppearances": 3 + ((slot + pa_offset) % 3)}},
        }
    away_players["ID10"] = {
        "person": {"id": 10},
        "battingOrder": "901",
        "stats": {"batting": {"plateAppearances": 1}},
    }
    home_players = {
        f"ID{100 + slot}": {
            "person": {"id": 100 + slot},
            "battingOrder": f"{slot}00",
            "stats": {"batting": {"plateAppearances": 4}},
        }
        for slot in range(1, 10)
    }
    payload = {
        "gamePk": game_pk,
        "gameData": {
            "datetime": {"officialDate": official_date},
            "status": {"abstractGameState": "Final", "codedGameState": "F"},
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {"team": {"id": TEAM_ID}, "players": away_players},
                    "home": {"team": {"id": 121}, "players": home_players},
                }
            }
        },
    }
    return json.dumps(payload, sort_keys=True).encode()


def _history(game_pk: int, official_date: str, *, pa_offset: int = 0) -> tuple[dict, bytes]:
    raw = _opportunity_raw(game_pk, official_date, pa_offset=pa_offset)
    record = parse_opportunity_history(
        response=RawOpportunityResponse(raw, f"{official_date}T23:59:00Z", _opportunity_url(game_pk)),
        expected_game_pk=game_pk,
        expected_official_date=official_date,
        side="away",
        expected_team_id=TEAM_ID,
        capture_plan=_history_plan(game_pk, official_date),
    )
    return record, raw


def _schedule_raw(
    *,
    start_date: str = "2026-07-27",
    end_date: str = "2026-07-29",
    games: tuple[tuple[int, str, bool], ...] = (
        (1001, "2026-07-27", True),
        (1002, "2026-07-28", True),
    ),
) -> bytes:
    rows = []
    cursor = start_date
    while cursor <= end_date:
        date_games = []
        for game_pk, official_date, is_final in games:
            if official_date != cursor:
                continue
            date_games.append(
                {
                    "gamePk": game_pk,
                    "officialDate": official_date,
                    "status": {
                        "abstractGameState": "Final" if is_final else "Preview",
                        "codedGameState": "F" if is_final else "S",
                    },
                    "teams": {
                        "away": {"team": {"id": TEAM_ID}},
                        "home": {"team": {"id": 121}},
                    },
                }
            )
        rows.append({"date": cursor, "games": date_games})
        year, month, day = (int(value) for value in cursor.split("-"))
        from datetime import date, timedelta

        cursor = (date(year, month, day) + timedelta(days=1)).isoformat()
    return json.dumps({"dates": rows}, sort_keys=True).encode()


def _schedule_receipt(raw: bytes | None = None, *, received_at: str = "2026-07-30T15:59:00Z") -> dict:
    body = raw or _schedule_raw()
    return parse_prior_schedule_denominator(
        response=RawOpportunityResponse(body, received_at, _schedule_url()),
        requested_start_date="2026-07-27",
        requested_end_date="2026-07-29",
        target_official_game_date=TARGET_DATE,
        target_horizon_utc=HORIZON,
        team_id=TEAM_ID,
    )


def _snapshot_materials(*, complete: bool = True) -> dict:
    roster_raw = _roster_raw()
    first, first_raw = _history(1001, "2026-07-27")
    records = [first]
    raws = {first["source_payload_sha256"]: first_raw}
    captured = [1001]
    missing = [] if complete else [1002]
    if complete:
        second, second_raw = _history(1002, "2026-07-28", pa_offset=1)
        records.append(second)
        raws[second["source_payload_sha256"]] = second_raw
        captured.append(1002)
    schedule_raw = _schedule_raw()
    schedule_receipt = _schedule_receipt(schedule_raw)
    coverage = build_history_coverage(
        collection_epoch_date="2026-07-27",
        target_official_game_date=TARGET_DATE,
        team_id=TEAM_ID,
        schedule_receipts=[schedule_receipt],
        captured_prior_game_pks=captured,
        terminal_missing_prior_game_pks=missing,
    )
    snapshot = build_pregame_opportunity_snapshot(
        official_game_date=TARGET_DATE,
        mlb_game_pk=2001,
        side="away",
        team_id=TEAM_ID,
        target_horizon_utc=HORIZON,
        assembled_at_utc="2026-07-30T15:59:00Z",
        active_roster_receipt=_roster_receipt(roster_raw),
        active_roster_raw=roster_raw,
        history_records=records,
        history_raw_by_sha256=raws,
        history_coverage=coverage,
        schedule_raw_by_sha256={schedule_receipt["source_payload_sha256"]: schedule_raw},
    )
    return {
        "snapshot": snapshot,
        "roster_raw": roster_raw,
        "roster_receipt": _roster_receipt(roster_raw),
        "history_records": records,
        "history_raw": raws,
        "schedule_raw": {schedule_receipt["source_payload_sha256"]: schedule_raw},
    }


def _snapshot(*, complete: bool = True) -> dict:
    return _snapshot_materials(complete=complete)["snapshot"]


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


def test_history_parser_retains_only_opportunity_fields_and_starter_identity():
    record, _ = _history(1001, "2026-07-27")
    assert record["source_kind"] == "official_mlb_final_opportunity_only_v1"
    assert len([row for row in record["players"] if row["is_starter"]]) == 9
    assert not any(key in json.dumps(record) for key in ("homeRuns", "hits", "totalBases", "rbi"))


def test_transport_sanitizer_discards_real_endpoint_extras_before_persistence():
    payload = json.loads(_opportunity_raw(1001, "2026-07-27"))
    player = payload["liveData"]["boxscore"]["teams"]["away"]["players"]["ID1"]
    player.update(
        {
            "allPositions": [{"abbreviation": "RF"}],
            "gameStatus": {"isCurrentBatter": False},
            "seasonStats": {"batting": {"hits": 99, "homeRuns": 20}},
        }
    )
    transport = json.dumps(payload, sort_keys=True).encode()
    sanitized = sanitize_opportunity_transport(
        response=RawOpportunityResponse(
            transport,
            "2026-07-27T23:59:00Z",
            _opportunity_url(1001),
        ),
        expected_game_pk=1001,
    )
    assert sanitized.body != transport
    assert b"seasonStats" not in sanitized.body
    assert b"hits" not in sanitized.body
    assert sanitized.transport_payload_sha256 != hashlib.sha256(sanitized.body).hexdigest()
    record = parse_opportunity_history(
        response=sanitized,
        expected_game_pk=1001,
        expected_official_date="2026-07-27",
        side="away",
        expected_team_id=TEAM_ID,
        capture_plan=_history_plan(1001, "2026-07-27"),
    )
    assert record["transport_payload_retained"] is False
    assert record["transport_payload_size"] == len(transport)


def test_persisted_pre_2026_real_source_projection_replays_offline():
    fixture = json.loads(REAL_SOURCE_FIXTURE.read_text(encoding="utf-8"))
    payload = json.dumps(
        fixture["sanitized_payload"],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    assert hashlib.sha256(payload).hexdigest() == fixture["sanitized_payload_sha256"]
    assert fixture["classification"] == "PRE_2026_STATIC_SOURCE_SURFACE_AUDIT_NOT_PROSPECTIVE_EVIDENCE"
    for side, team_id in (("away", 147), ("home", 138)):
        plan = build_history_capture_plan(
            created_at_utc="2023-07-01T12:00:00Z",
            mlb_game_pk=717545,
            official_game_date="2023-07-01",
            official_start_time_utc="2023-07-01T17:00:00Z",
            capture_deadline_utc="2023-07-02T17:00:00Z",
            side=side,
            team_id=team_id,
            source_t4_plan_sha256="a" * 64,
            roster_side_target_id=("b" if side == "away" else "c") * 64,
            active_roster_receipt_sha256=("d" if side == "away" else "e") * 64,
        )
        record = parse_opportunity_history(
            response=RawOpportunityResponse(
                payload,
                fixture["source_received_at_utc"],
                fixture["source_request_url"],
                transport_payload_sha256=fixture["transport_payload_sha256"],
                transport_payload_size=fixture["transport_payload_size"],
            ),
            expected_game_pk=717545,
            expected_official_date="2023-07-01",
            side=side,
            expected_team_id=team_id,
            capture_plan=plan,
        )
        assert record["team_id"] == team_id
        assert len([row for row in record["players"] if row["is_starter"]]) == 9
        assert record["transport_payload_retained"] is False
    serialized = json.dumps(fixture["sanitized_payload"], sort_keys=True)
    for forbidden in ("seasonStats", "hits", "homeRuns", "totalBases", "rbi"):
        assert forbidden not in serialized


def test_candidate_report_recomputes_every_declared_file_hash():
    root = Path(__file__).parents[1]
    report = json.loads(
        (root / "reports" / "prospective_batter_opportunity_evidence_v1.json").read_text(
            encoding="utf-8"
        )
    )
    declared: dict[str, str] = {}
    for group in ("code", "configuration", "tests", "source_audit"):
        declared.update(report["hashes"][group])
    declared.update(
        {
            path: digest
            for path, digest in report["hashes"]["data"].items()
            if path.endswith(".json")
        }
    )
    assert declared
    for relative_path, expected_sha256 in sorted(declared.items()):
        candidate = root / relative_path
        assert candidate.is_file(), relative_path
        assert hashlib.sha256(candidate.read_bytes()).hexdigest() == expected_sha256, relative_path


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("outcome_field", "source surface changed"),
        ("opposing_outcome_field", "source surface changed"),
        ("wrong_source", "source identity differs"),
        ("duplicate_query", "request parameters differ"),
        ("bad_http", "not approved JSON"),
        ("wrong_game", "game identity differs"),
        ("wrong_team", "team identity differs"),
        ("not_final", "not coded final"),
        ("duplicate_starter_slot", "duplicate original starter slots"),
        ("may", "May 2026 is sealed"),
    ],
)
def test_history_parser_mutations_fail_closed(mutation: str, match: str):
    raw = json.loads(_opportunity_raw(1001, "2026-07-27"))
    expected_game = 1001
    expected_team = TEAM_ID
    expected_date = "2026-07-27"
    request_url = _opportunity_url(expected_game)
    http_status = 200
    if mutation == "outcome_field":
        raw["liveData"]["boxscore"]["teams"]["away"]["players"]["ID1"]["stats"]["batting"]["hits"] = 2
    elif mutation == "opposing_outcome_field":
        raw["liveData"]["boxscore"]["teams"]["home"]["players"]["ID101"]["stats"]["batting"]["hits"] = 2
    elif mutation == "wrong_source":
        request_url = request_url.replace("statsapi.mlb.com", "example.invalid")
    elif mutation == "duplicate_query":
        request_url += "&fields=duplicate"
    elif mutation == "bad_http":
        http_status = 500
    elif mutation == "wrong_game":
        expected_game = 2002
        request_url = _opportunity_url(expected_game)
    elif mutation == "wrong_team":
        expected_team = 121
    elif mutation == "not_final":
        raw["gameData"]["status"] = {"abstractGameState": "Live", "codedGameState": "I"}
    elif mutation == "duplicate_starter_slot":
        raw["liveData"]["boxscore"]["teams"]["away"]["players"]["ID2"]["battingOrder"] = "100"
    elif mutation == "may":
        raw["gameData"]["datetime"]["officialDate"] = "2026-05-27"
        expected_date = "2026-05-27"
    capture_plan = _history_plan(
        expected_game,
        expected_date if mutation != "may" else "2026-07-27",
        team_id=expected_team,
    )
    with pytest.raises(ProspectiveBatterOpportunityError, match=match):
        parse_opportunity_history(
            response=RawOpportunityResponse(
                json.dumps(raw).encode(),
                "2026-07-27T23:59:00Z",
                request_url,
                http_status,
            ),
            expected_game_pk=expected_game,
            expected_official_date=expected_date,
            side="away",
            expected_team_id=expected_team,
            capture_plan=capture_plan,
        )


def test_history_capture_plan_blocks_backfill_and_plan_mutation():
    with pytest.raises(ProspectiveBatterOpportunityError, match="created before"):
        build_history_capture_plan(
            created_at_utc="2026-07-27T18:00:00Z",
            mlb_game_pk=1001,
            official_game_date="2026-07-27",
            official_start_time_utc="2026-07-27T18:00:00Z",
            capture_deadline_utc="2026-07-27T23:59:59Z",
            side="away",
            team_id=TEAM_ID,
            source_t4_plan_sha256="a" * 64,
            roster_side_target_id="b" * 64,
            active_roster_receipt_sha256="c" * 64,
        )

    plan = _history_plan(1001, "2026-07-27")
    with pytest.raises(ProspectiveBatterOpportunityError, match="outside its predeclared capture window"):
        parse_opportunity_history(
            response=RawOpportunityResponse(
                _opportunity_raw(1001, "2026-07-27"),
                "2026-07-28T00:00:00Z",
                _opportunity_url(1001),
            ),
            expected_game_pk=1001,
            expected_official_date="2026-07-27",
            side="away",
            expected_team_id=TEAM_ID,
            capture_plan=plan,
        )

    mutated = copy.deepcopy(plan)
    mutated["capture_deadline_utc"] = "2026-07-28T01:00:00.000000Z"
    with pytest.raises(ProspectiveBatterOpportunityError, match="identity or hash differs"):
        parse_opportunity_history(
            response=RawOpportunityResponse(
                _opportunity_raw(1001, "2026-07-27"),
                "2026-07-27T23:59:00Z",
                _opportunity_url(1001),
            ),
            expected_game_pk=1001,
            expected_official_date="2026-07-27",
            side="away",
            expected_team_id=TEAM_ID,
            capture_plan=mutated,
        )


def test_complete_snapshot_is_hash_bound_but_not_a_probability():
    snapshot = _snapshot()
    validate_snapshot(snapshot)
    assert snapshot["terminal_state"] == "captured_complete"
    assert all(row["fit_eligible"] for row in snapshot["features"])
    assert snapshot["production_probability_consumption_authorized"] is False
    rendered = json.dumps(snapshot).lower()
    assert "start_probability" not in rendered
    assert "pa_probability" not in rendered
    assert "hr_probability" not in rendered


def test_terminal_missing_history_never_enters_denominator_as_zero():
    snapshot = _snapshot(complete=False)
    validate_snapshot(snapshot)
    assert snapshot["terminal_state"] == "captured_with_terminal_history_missingness"
    assert snapshot["history_coverage"]["expected_game_count"] == 2
    assert snapshot["history_coverage"]["captured_game_count"] == 1
    assert all(row["captured_team_games"] == 1 for row in snapshot["features"])
    assert not any(row["fit_eligible"] for row in snapshot["features"])


def test_same_day_history_late_snapshot_raw_mutation_and_hash_mutation_fail_closed():
    roster_raw = _roster_raw()
    same_day, same_raw = _history(1003, TARGET_DATE)
    schedule_raw = _schedule_raw(games=())
    schedule_receipt = _schedule_receipt(schedule_raw)
    coverage = build_history_coverage(
        collection_epoch_date="2026-07-27",
        target_official_game_date=TARGET_DATE,
        team_id=TEAM_ID,
        schedule_receipts=[schedule_receipt],
        captured_prior_game_pks=[],
        terminal_missing_prior_game_pks=[],
    )
    kwargs = {
        "official_game_date": TARGET_DATE,
        "mlb_game_pk": 2001,
        "side": "away",
        "team_id": TEAM_ID,
        "target_horizon_utc": HORIZON,
        "assembled_at_utc": "2026-07-30T15:59:00Z",
        "active_roster_receipt": _roster_receipt(roster_raw),
        "active_roster_raw": roster_raw,
        "history_records": [same_day],
        "history_raw_by_sha256": {same_day["source_payload_sha256"]: same_raw},
        "history_coverage": coverage,
        "schedule_raw_by_sha256": {schedule_receipt["source_payload_sha256"]: schedule_raw},
    }
    with pytest.raises(ProspectiveBatterOpportunityError, match="same-day or future"):
        build_pregame_opportunity_snapshot(**kwargs)
    kwargs["history_records"] = []
    kwargs["history_raw_by_sha256"] = {}
    kwargs["assembled_at_utc"] = "2026-07-30T16:00:00.000001Z"
    with pytest.raises(ProspectiveBatterOpportunityError, match="after T-minus-4"):
        build_pregame_opportunity_snapshot(**kwargs)

    snapshot = _snapshot()
    bad = copy.deepcopy(snapshot)
    bad["features"][0]["prior_starts"] += 1
    with pytest.raises(ProspectiveBatterOpportunityError, match="differs from its hash"):
        validate_snapshot(bad)


def test_coverage_must_be_an_exact_terminal_partition():
    receipt = _schedule_receipt()
    with pytest.raises(ProspectiveBatterOpportunityError, match="do not partition"):
        build_history_coverage(
            collection_epoch_date="2026-07-27",
            target_official_game_date=TARGET_DATE,
            team_id=TEAM_ID,
            schedule_receipts=[receipt],
            captured_prior_game_pks=[1001],
            terminal_missing_prior_game_pks=[],
        )


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("outcome_field", "source surface changed"),
        ("wrong_source", "source identity differs"),
        ("duplicate_query", "request parameters differ"),
        ("stale", "stale at T-minus-4"),
        ("late", "after T-minus-4"),
        ("may", "May 2026 is sealed"),
        ("wrong_team", "team identity is missing or ambiguous"),
    ],
)
def test_schedule_denominator_mutations_fail_closed(mutation: str, match: str):
    raw = json.loads(_schedule_raw())
    received_at = "2026-07-30T15:59:00Z"
    start, end = "2026-07-27", "2026-07-29"
    request_url = _schedule_url(start, end)
    if mutation == "outcome_field":
        raw["dates"][0]["games"][0]["score"] = 5
    elif mutation == "wrong_source":
        request_url = request_url.replace("statsapi.mlb.com", "example.invalid")
    elif mutation == "duplicate_query":
        request_url += "&teamId=147"
    elif mutation == "stale":
        received_at = "2026-07-30T15:57:59Z"
    elif mutation == "late":
        received_at = "2026-07-30T16:00:00.000001Z"
    elif mutation == "may":
        start, end = "2026-05-01", "2026-05-02"
    elif mutation == "wrong_team":
        raw["dates"][0]["games"][0]["teams"]["away"]["team"]["id"] = 999
    with pytest.raises(ProspectiveBatterOpportunityError, match=match):
        parse_prior_schedule_denominator(
            response=RawOpportunityResponse(
                json.dumps(raw).encode(),
                received_at,
                request_url if mutation not in {"may"} else _schedule_url(start, end),
            ),
            requested_start_date=start,
            requested_end_date=end,
            target_official_game_date=TARGET_DATE,
            target_horizon_utc=HORIZON,
            team_id=TEAM_ID,
        )


def test_schedule_receipt_ranges_must_cover_every_permitted_date_without_overlap():
    raw = _schedule_raw()
    receipt = _schedule_receipt(raw)
    with pytest.raises(ProspectiveBatterOpportunityError, match="overlap"):
        build_history_coverage(
            collection_epoch_date="2026-07-27",
            target_official_game_date=TARGET_DATE,
            team_id=TEAM_ID,
            schedule_receipts=[receipt, receipt],
            captured_prior_game_pks=[1001, 1002],
            terminal_missing_prior_game_pks=[],
        )

    short_raw = _schedule_raw(end_date="2026-07-28")
    short_receipt = parse_prior_schedule_denominator(
        response=RawOpportunityResponse(
            short_raw,
            "2026-07-30T15:59:00Z",
            _schedule_url("2026-07-27", "2026-07-28"),
        ),
        requested_start_date="2026-07-27",
        requested_end_date="2026-07-28",
        target_official_game_date=TARGET_DATE,
        target_horizon_utc=HORIZON,
        team_id=TEAM_ID,
    )
    with pytest.raises(ProspectiveBatterOpportunityError, match="every permitted date"):
        build_history_coverage(
            collection_epoch_date="2026-07-27",
            target_official_game_date=TARGET_DATE,
            team_id=TEAM_ID,
            schedule_receipts=[short_receipt],
            captured_prior_game_pks=[1001, 1002],
            terminal_missing_prior_game_pks=[],
        )


def test_schedule_raw_mutation_is_rejected_during_snapshot_replay():
    materials = _snapshot_materials()
    raw_hash, raw = next(iter(materials["schedule_raw"].items()))
    with pytest.raises(ProspectiveBatterOpportunityError, match="raw is missing or differs"):
        build_pregame_opportunity_snapshot(
            official_game_date=TARGET_DATE,
            mlb_game_pk=2001,
            side="away",
            team_id=TEAM_ID,
            target_horizon_utc=HORIZON,
            assembled_at_utc="2026-07-30T15:59:00Z",
            active_roster_receipt=materials["roster_receipt"],
            active_roster_raw=materials["roster_raw"],
            history_records=materials["history_records"],
            history_raw_by_sha256=materials["history_raw"],
            history_coverage=materials["snapshot"]["history_coverage"],
            schedule_raw_by_sha256={raw_hash: raw + b" "},
        )


def test_ledger_replays_snapshot_and_preserves_missing_side_coverage(tmp_path):
    plan, target = _plan()
    materials = _snapshot_materials()
    ledger = ProspectiveBatterOpportunityLedger(
        tmp_path,
        plan=plan,
        contract_sha256="c" * 64,
        collector_code_sha256="d" * 64,
    )
    assert ledger.append_snapshot(
        target=target,
        side="away",
        team_id=TEAM_ID,
        snapshot=materials["snapshot"],
        active_roster_receipt=materials["roster_receipt"],
        active_roster_raw=materials["roster_raw"],
        history_records=materials["history_records"],
        history_raw_by_sha256=materials["history_raw"],
        schedule_raw_by_sha256=materials["schedule_raw"],
    )
    report = ledger.verify()
    assert report["captured_complete"] == 1
    assert report["terminal"] == 1
    assert report["missing"] == 1
    with pytest.raises(ProspectiveBatterOpportunityLedgerError, match="terminal coverage differs"):
        ledger.verify(require_complete_coverage=True)


def test_ledger_rejects_rehashed_semantic_snapshot_mutation_before_publication(tmp_path):
    plan, target = _plan()
    materials = _snapshot_materials()
    ledger = ProspectiveBatterOpportunityLedger(
        tmp_path,
        plan=plan,
        contract_sha256="c" * 64,
        collector_code_sha256="d" * 64,
    )
    mutated = copy.deepcopy(materials["snapshot"])
    mutated["features"][0]["prior_starts"] += 1
    unsigned = dict(mutated)
    unsigned.pop("snapshot_sha256")
    mutated["snapshot_sha256"] = sha256_value(unsigned)
    with pytest.raises(
        ProspectiveBatterOpportunityLedgerError,
        match="prepublication replay",
    ):
        ledger.append_snapshot(
            target=target,
            side="away",
            team_id=TEAM_ID,
            snapshot=mutated,
            active_roster_receipt=materials["roster_receipt"],
            active_roster_raw=materials["roster_raw"],
            history_records=materials["history_records"],
            history_raw_by_sha256=materials["history_raw"],
            schedule_raw_by_sha256=materials["schedule_raw"],
        )
    assert not (tmp_path / "terminal").exists()
    assert not (tmp_path / "raw").exists()


def test_ledger_raw_mutation_or_orphan_fails_closed(tmp_path):
    plan, target = _plan()
    materials = _snapshot_materials()
    ledger = ProspectiveBatterOpportunityLedger(
        tmp_path,
        plan=plan,
        contract_sha256="c" * 64,
        collector_code_sha256="d" * 64,
    )
    ledger.append_snapshot(
        target=target,
        side="away",
        team_id=TEAM_ID,
        snapshot=materials["snapshot"],
        active_roster_receipt=materials["roster_receipt"],
        active_roster_raw=materials["roster_raw"],
        history_records=materials["history_records"],
        history_raw_by_sha256=materials["history_raw"],
        schedule_raw_by_sha256=materials["schedule_raw"],
    )
    raw_path = next((tmp_path / "raw").glob("*.json"))
    original = raw_path.read_bytes()
    raw_path.write_bytes(original + b" ")
    with pytest.raises(ProspectiveBatterOpportunityLedgerError, match="hash differs"):
        ledger.verify()
    raw_path.write_bytes(original)
    (tmp_path / "raw" / ("e" * 64 + ".json")).write_bytes(b"{}")
    with pytest.raises(ProspectiveBatterOpportunityLedgerError, match="orphaned"):
        ledger.verify()


def test_ledger_rejects_wrong_raw_key_before_publication(tmp_path):
    plan, target = _plan()
    materials = _snapshot_materials()
    ledger = ProspectiveBatterOpportunityLedger(
        tmp_path,
        plan=plan,
        contract_sha256="c" * 64,
        collector_code_sha256="d" * 64,
    )
    wrong = {"f" * 64: next(iter(materials["history_raw"].values()))}
    with pytest.raises(ProspectiveBatterOpportunityLedgerError, match="key differs"):
        ledger.append_snapshot(
            target=target,
            side="away",
            team_id=TEAM_ID,
            snapshot=materials["snapshot"],
            active_roster_receipt=materials["roster_receipt"],
            active_roster_raw=materials["roster_raw"],
            history_records=materials["history_records"],
            history_raw_by_sha256=wrong,
            schedule_raw_by_sha256=materials["schedule_raw"],
        )
