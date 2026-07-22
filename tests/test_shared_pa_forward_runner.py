"""End-to-end offline tick and terminal-coverage tests for shared PA capture."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.evaluation.shadow_capture_plan import plan_from_schedule
from src.evaluation.shared_pa_forward_collector import RawPregameResponse
from src.evaluation.shared_pa_forward_evidence import load_forward_contract
from src.evaluation.shared_pa_forward_ledger import SharedPAForwardLedger
from src.evaluation.shared_pa_forward_runner import StatsBatch, run_tick


ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 7, 30, tzinfo=timezone.utc)
HORIZON = START - timedelta(hours=4)


def _plan():
    return plan_from_schedule(
        official_game_date="2026-07-29",
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot=[{
            "gamePk": 123456,
            "officialDate": "2026-07-29",
            "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "teams": {
                "home": {"team": {"name": "Home"}},
                "away": {"team": {"name": "Away"}},
            },
        }],
    )


def _lineup(*, away: bool = True) -> RawPregameResponse:
    lineups = {"homePlayers": [{"id": value} for value in range(201, 210)]}
    if away:
        lineups["awayPlayers"] = [{"id": value} for value in range(101, 110)]
    body = json.dumps({
        "dates": [{"games": [{
            "gamePk": 123456,
            "officialDate": "2026-07-29",
            "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "teams": {
                "home": {"team": {"id": 111, "name": "Home"}},
                "away": {"team": {"id": 112, "name": "Away"}},
            },
            "lineups": lineups,
        }]}],
    }, sort_keys=True).encode("utf-8")
    return RawPregameResponse(body=body, received_at_utc=HORIZON.isoformat())


def _stats(player_id: int) -> RawPregameResponse:
    body = json.dumps({
        "people": [{
            "id": player_id,
            "stats": [{
                "group": {"displayName": "hitting"},
                "type": {"displayName": "season"},
                "splits": [{"stat": {
                    "plateAppearances": 100, "atBats": 88, "hits": 28,
                    "doubles": 5, "triples": 1, "homeRuns": 6,
                    "baseOnBalls": 10, "strikeOuts": 20,
                }}],
            }],
        }],
    }, sort_keys=True).encode("utf-8")
    return RawPregameResponse(body=body, received_at_utc=HORIZON.isoformat())


def _loaded():
    return load_forward_contract(
        root=ROOT,
        contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json",
    )


def _ledger(tmp_path: Path, plan):
    loaded = _loaded()
    return loaded, SharedPAForwardLedger(
        tmp_path,
        plan=plan,
        contract_sha256=loaded["contract_sha256"],
        runtime_manifest_sha256="c" * 64,
        collector_code_sha256="b" * 64,
    )


def test_valid_tick_captures_both_game_sides(tmp_path: Path) -> None:
    plan = _plan()
    loaded, ledger = _ledger(tmp_path, plan)
    result = run_tick(
        plan=plan,
        ledger=ledger,
        loaded_contract=loaded,
        max_early_seconds=120,
        fetch_lineup_schedule=lambda _: _lineup(),
        fetch_stats_batch=lambda players, _: StatsBatch(
            responses={player: _stats(player) for player in players}, errors={}
        ),
        collector_instance_id="synthetic",
        collector_code_sha256="b" * 64,
        runtime_manifest_sha256="c" * 64,
        now=HORIZON - timedelta(seconds=30),
        completed_clock=lambda: HORIZON,
    )
    assert result["captured_complete"] == 2
    report = ledger.verify(require_complete_coverage=True)
    assert report["captured_complete"] == 2
    assert report["terminal"] == 2


def test_missing_side_lineup_is_permanent_explicit_terminal(tmp_path: Path) -> None:
    plan = _plan()
    loaded, ledger = _ledger(tmp_path, plan)
    result = run_tick(
        plan=plan,
        ledger=ledger,
        loaded_contract=loaded,
        max_early_seconds=120,
        fetch_lineup_schedule=lambda _: _lineup(away=False),
        fetch_stats_batch=lambda players, _: StatsBatch(
            responses={player: _stats(player) for player in players}, errors={}
        ),
        collector_instance_id="synthetic",
        collector_code_sha256="b" * 64,
        runtime_manifest_sha256="c" * 64,
        now=HORIZON,
        completed_clock=lambda: HORIZON,
    )
    assert result["captured_complete"] == 1
    assert result["lineup_unavailable"] == 1
    assert ledger.verify(require_complete_coverage=True)["missing"] == 0


def test_partial_stats_batch_is_source_error_not_partial_prediction(tmp_path: Path) -> None:
    plan = _plan()
    loaded, ledger = _ledger(tmp_path, plan)

    def batch(players: list[int], _: int) -> StatsBatch:
        if players[0] == 101:
            return StatsBatch(
                responses={player: _stats(player) for player in players[1:]},
                errors={players[0]: "official_stats_http_error"},
            )
        return StatsBatch(responses={player: _stats(player) for player in players}, errors={})

    result = run_tick(
        plan=plan,
        ledger=ledger,
        loaded_contract=loaded,
        max_early_seconds=120,
        fetch_lineup_schedule=lambda _: _lineup(),
        fetch_stats_batch=batch,
        collector_instance_id="synthetic",
        collector_code_sha256="b" * 64,
        runtime_manifest_sha256="c" * 64,
        now=HORIZON,
        completed_clock=lambda: HORIZON,
    )
    assert result["source_error"] == 1
    assert result["captured_complete"] == 1
    report = ledger.verify(require_complete_coverage=True)
    assert report["source_error"] == 1


def test_late_tick_records_missed_without_calling_sources(tmp_path: Path) -> None:
    plan = _plan()
    loaded, ledger = _ledger(tmp_path, plan)

    def forbidden(*_):
        raise AssertionError("late tick must not fetch")

    result = run_tick(
        plan=plan,
        ledger=ledger,
        loaded_contract=loaded,
        max_early_seconds=120,
        fetch_lineup_schedule=forbidden,
        fetch_stats_batch=forbidden,
        collector_instance_id="synthetic",
        collector_code_sha256="b" * 64,
        runtime_manifest_sha256="c" * 64,
        now=HORIZON + timedelta(seconds=1),
    )
    assert result["missed_before_horizon"] == 2
    report = ledger.verify(require_complete_coverage=True)
    assert report["missed_before_horizon"] == 2
