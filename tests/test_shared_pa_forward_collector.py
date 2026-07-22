"""Source-parser regression and mutation tests for shared PA forward capture."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.evaluation.shadow_capture_plan import plan_from_schedule
from src.evaluation.shared_pa_forward_collector import (
    RawPregameResponse,
    SharedPAForwardCollectorError,
    build_projected_player_snapshot,
    pa_counts_from_hitting_stats,
    projected_lineups_from_schedule,
)
from src.evaluation.shared_pa_forward_evidence import load_forward_contract


ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 7, 30, tzinfo=timezone.utc)
HORIZON = START - timedelta(hours=4)


def _plan():
    schedule = [{
        "gamePk": 123456,
        "officialDate": "2026-07-29",
        "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "teams": {
            "home": {"team": {"name": "Home"}},
            "away": {"team": {"name": "Away"}},
        },
    }]
    return plan_from_schedule(
        official_game_date="2026-07-29",
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot=schedule,
    )


def _lineup_response(*, home: list[int] | None = None, away: list[int] | None = None) -> RawPregameResponse:
    home = home if home is not None else list(range(201, 210))
    away = away if away is not None else list(range(101, 110))
    payload = {
        "dates": [{
            "games": [{
                "gamePk": 123456,
                "officialDate": "2026-07-29",
                "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "teams": {
                    "home": {"team": {"id": 111, "name": "Home"}},
                    "away": {"team": {"id": 112, "name": "Away"}},
                },
                "lineups": {
                    "homePlayers": [{"id": value} for value in home],
                    "awayPlayers": [{"id": value} for value in away],
                },
            }]
        }]
    }
    return RawPregameResponse(
        body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        received_at_utc=HORIZON.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _stats_response(player_id: int = 201, *, stat: dict | None = None) -> RawPregameResponse:
    stat = stat or {
        "plateAppearances": 100,
        "atBats": 88,
        "hits": 28,
        "doubles": 5,
        "triples": 1,
        "homeRuns": 6,
        "baseOnBalls": 10,
        "strikeOuts": 20,
    }
    payload = {
        "people": [{
            "id": player_id,
            "stats": [{
                "group": {"displayName": "hitting"},
                "type": {"displayName": "season"},
                "splits": [{"stat": stat}],
            }],
        }]
    }
    return RawPregameResponse(
        body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        received_at_utc=HORIZON.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def test_schedule_lineups_and_stats_build_valid_snapshot() -> None:
    plan = _plan()
    target = plan.targets[0]
    lineups = projected_lineups_from_schedule(
        response=_lineup_response(), plan=plan, target=target
    )
    assert lineups["status"] == "captured_complete"
    assert lineups["home"] == list(range(201, 210))
    loaded = load_forward_contract(
        root=ROOT,
        contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json",
    )
    record = build_projected_player_snapshot(
        plan=plan,
        target=target,
        side="home",
        source_slot=1,
        player_id=201,
        home_team_id=111,
        away_team_id=112,
        lineup_response=_lineup_response(),
        stats_response=_stats_response(),
        loaded_contract=loaded,
        collector_instance_id="synthetic",
        collector_code_sha256="b" * 64,
        runtime_manifest_sha256="c" * 64,
    )
    assert record["stats_pa"] == 100
    assert record["stats_counts"] == {
        "strikeout": 20,
        "walk": 10,
        "single": 16,
        "double": 5,
        "triple": 1,
        "home_run": 6,
        "bip_out": 40,
        "other_non_ab": 2,
    }
    assert record["pitcher_block_status"] == "excluded_batter_only"


@pytest.mark.parametrize(
    "stat",
    [
        {"plateAppearances": 10, "atBats": 9, "hits": 5, "doubles": 4, "triples": 1, "homeRuns": 1, "baseOnBalls": 1, "strikeOuts": 2},
        {"plateAppearances": 10, "atBats": 4, "hits": 3, "doubles": 0, "triples": 0, "homeRuns": 0, "baseOnBalls": 1, "strikeOuts": 3},
        {"plateAppearances": 10, "atBats": 11, "hits": 3, "doubles": 0, "triples": 0, "homeRuns": 0, "baseOnBalls": 1, "strikeOuts": 3},
    ],
)
def test_impossible_count_denominators_fail_closed(stat: dict) -> None:
    with pytest.raises(SharedPAForwardCollectorError, match="truthful PA partition"):
        pa_counts_from_hitting_stats(response=_stats_response(stat=stat), player_id=201)


def test_stats_player_identity_mutation_fails() -> None:
    with pytest.raises(SharedPAForwardCollectorError, match="different player"):
        pa_counts_from_hitting_stats(response=_stats_response(player_id=999), player_id=201)


def test_duplicate_or_partial_lineup_fails_closed() -> None:
    plan = _plan()
    target = plan.targets[0]
    with pytest.raises(SharedPAForwardCollectorError, match="duplicate"):
        projected_lineups_from_schedule(
            response=_lineup_response(home=[201] * 9), plan=plan, target=target
        )
    with pytest.raises(SharedPAForwardCollectorError, match="partial"):
        projected_lineups_from_schedule(
            response=_lineup_response(home=[201, 202]), plan=plan, target=target
        )


def test_late_lineup_response_cannot_be_backfilled() -> None:
    plan = _plan()
    target = plan.targets[0]
    response = _lineup_response()
    late = RawPregameResponse(body=response.body, received_at_utc=START.isoformat())
    with pytest.raises(SharedPAForwardCollectorError, match="after T-minus-4"):
        projected_lineups_from_schedule(response=late, plan=plan, target=target)


def test_subsecond_post_horizon_receipt_cannot_be_truncated_into_eligibility() -> None:
    plan = _plan()
    target = plan.targets[0]
    response = _lineup_response()
    late = RawPregameResponse(
        body=response.body,
        received_at_utc=(HORIZON + timedelta(microseconds=1)).isoformat(),
    )
    assert late.received_at_utc.endswith(".000001Z")
    with pytest.raises(SharedPAForwardCollectorError, match="after T-minus-4"):
        projected_lineups_from_schedule(response=late, plan=plan, target=target)


def test_missing_lineup_is_explicit_not_an_empty_success() -> None:
    plan = _plan()
    target = plan.targets[0]
    response = _lineup_response()
    payload = json.loads(response.body)
    payload["dates"][0]["games"][0].pop("lineups")
    missing = RawPregameResponse(
        body=json.dumps(payload).encode("utf-8"),
        received_at_utc=response.received_at_utc,
    )
    parsed = projected_lineups_from_schedule(response=missing, plan=plan, target=target)
    assert parsed["status"] == "lineup_unavailable"
    assert parsed["home"] is None and parsed["away"] is None


@pytest.mark.parametrize(
    ("fixture", "mutation"),
    [
        ("lineup", lambda payload: payload["dates"][0]["games"][0].update({"status": {"codedGameState": "F"}})),
        ("stats", lambda payload: payload["people"][0]["stats"][0]["splits"][0].update({"date": "2026-07-29"})),
    ],
)
def test_unapproved_source_fields_fail_before_retention(fixture: str, mutation) -> None:
    response = _lineup_response() if fixture == "lineup" else _stats_response()
    payload = json.loads(response.body)
    mutation(payload)
    unsafe = RawPregameResponse(
        body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        received_at_utc=response.received_at_utc,
    )
    if fixture == "lineup":
        plan = _plan()
        with pytest.raises(SharedPAForwardCollectorError, match="unapproved"):
            projected_lineups_from_schedule(response=unsafe, plan=plan, target=plan.targets[0])
    else:
        with pytest.raises(SharedPAForwardCollectorError, match="unapproved"):
            pa_counts_from_hitting_stats(response=unsafe, player_id=201)
