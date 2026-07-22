"""Append-only and mutation proof for the shared PA game-side ledger."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.evaluation.shadow_capture_plan import plan_from_schedule
from src.evaluation.shared_pa_forward_collector import (
    RawPregameResponse,
    build_projected_player_snapshot,
)
from src.evaluation.shared_pa_forward_evidence import (
    load_forward_contract,
    sha256_value,
    snapshot_sha256,
)
from src.evaluation.shared_pa_forward_ledger import (
    SharedPAForwardLedger,
    SharedPAForwardLedgerError,
)


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


def _lineup_raw() -> bytes:
    return json.dumps({
        "dates": [{"games": [{
            "gamePk": 123456,
            "officialDate": "2026-07-29",
            "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "teams": {
                "home": {"team": {"id": 111, "name": "Home"}},
                "away": {"team": {"id": 112, "name": "Away"}},
            },
            "lineups": {
                "homePlayers": [{"id": value} for value in range(201, 210)],
                "awayPlayers": [{"id": value} for value in range(101, 110)],
            },
        }]}],
    }, sort_keys=True).encode("utf-8")


def _stats(player_id: int) -> RawPregameResponse:
    raw = json.dumps({
        "people": [{
            "id": player_id,
            "stats": [{
                "group": {"displayName": "hitting"},
                "type": {"displayName": "season"},
                "splits": [{"stat": {
                    "plateAppearances": 100,
                    "atBats": 88,
                    "hits": 28,
                    "doubles": 5,
                    "triples": 1,
                    "homeRuns": 6,
                    "baseOnBalls": 10,
                    "strikeOuts": 20,
                }}],
            }],
        }],
    }, sort_keys=True).encode("utf-8")
    return RawPregameResponse(body=raw, received_at_utc=HORIZON.isoformat())


def _players(plan, side: str = "home"):
    loaded = load_forward_contract(
        root=ROOT,
        contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json",
    )
    lineup = RawPregameResponse(body=_lineup_raw(), received_at_utc=HORIZON.isoformat())
    identifiers = range(201, 210) if side == "home" else range(101, 110)
    players = []
    raw = {}
    for slot, player_id in enumerate(identifiers, start=1):
        stats = _stats(player_id)
        raw[stats.sha256] = stats.body
        players.append(build_projected_player_snapshot(
            plan=plan,
            target=plan.targets[0],
            side=side,
            source_slot=slot,
            player_id=player_id,
            home_team_id=111,
            away_team_id=112,
            lineup_response=lineup,
            stats_response=stats,
            loaded_contract=loaded,
            collector_instance_id="synthetic",
            collector_code_sha256="b" * 64,
            runtime_manifest_sha256="c" * 64,
        ))
    return loaded, lineup, players, raw


def test_complete_and_excluded_sides_verify_as_full_coverage(tmp_path: Path) -> None:
    plan = _plan()
    loaded, lineup, players, stats_raw = _players(plan)
    ledger = SharedPAForwardLedger(
        tmp_path,
        plan=plan,
        contract_sha256=loaded["contract_sha256"],
        runtime_manifest_sha256="c" * 64,
        collector_code_sha256="b" * 64,
    )
    assert ledger.append_complete(
        target=plan.targets[0],
        side="home",
        players=players,
        lineup_raw=lineup.body,
        stats_raw_by_sha256=stats_raw,
        committed_utc=HORIZON.isoformat(),
    )
    assert ledger.append_exclusion(
        target=plan.targets[0],
        side="away",
        state="lineup_unavailable",
        observed_at_utc=HORIZON.isoformat(),
        detail="official projected lineup absent at horizon",
        raw_payload=lineup.body,
    )
    report = ledger.verify(require_complete_coverage=True)
    assert report["captured_complete"] == 1
    assert report["lineup_unavailable"] == 1
    assert report["missing"] == 0


def test_duplicate_terminal_side_cannot_be_overwritten(tmp_path: Path) -> None:
    plan = _plan()
    loaded, lineup, players, stats_raw = _players(plan)
    ledger = SharedPAForwardLedger(
        tmp_path, plan=plan, contract_sha256=loaded["contract_sha256"], runtime_manifest_sha256="c" * 64,
        collector_code_sha256="b" * 64,
    )
    ledger.append_complete(
        target=plan.targets[0], side="home", players=players, lineup_raw=lineup.body,
        stats_raw_by_sha256=stats_raw, committed_utc=HORIZON.isoformat(),
    )
    with pytest.raises(SharedPAForwardLedgerError, match="already differs"):
        ledger.append_exclusion(
            target=plan.targets[0], side="home", state="source_error",
            observed_at_utc=HORIZON.isoformat(), detail="conflicting retry",
        )


def test_player_mutation_breaks_entry_verification(tmp_path: Path) -> None:
    plan = _plan()
    loaded, lineup, players, stats_raw = _players(plan)
    ledger = SharedPAForwardLedger(
        tmp_path, plan=plan, contract_sha256=loaded["contract_sha256"], runtime_manifest_sha256="c" * 64,
        collector_code_sha256="b" * 64,
    )
    ledger.append_complete(
        target=plan.targets[0], side="home", players=players, lineup_raw=lineup.body,
        stats_raw_by_sha256=stats_raw, committed_utc=HORIZON.isoformat(),
    )
    terminal = next((tmp_path / "terminal").glob("*.json"))
    payload = json.loads(terminal.read_text(encoding="utf-8"))
    payload["players"][0]["market_distributions"]["tails"]["home_runs_over_0.5"] += 0.01
    terminal.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SharedPAForwardLedgerError, match="entry hash"):
        ledger.verify()


def test_orphan_raw_payload_is_terminal_health_failure(tmp_path: Path) -> None:
    plan = _plan()
    loaded, _, _, _ = _players(plan)
    ledger = SharedPAForwardLedger(
        tmp_path, plan=plan, contract_sha256=loaded["contract_sha256"], runtime_manifest_sha256="c" * 64,
        collector_code_sha256="b" * 64,
    )
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / ("0" * 64 + ".json")).write_bytes(b"orphan")
    with pytest.raises(SharedPAForwardLedgerError, match="orphaned"):
        ledger.verify()


def test_outcome_bearing_raw_payload_is_refused_before_write(tmp_path: Path) -> None:
    plan = _plan()
    loaded, lineup, players, stats_raw = _players(plan)
    payload = json.loads(lineup.body)
    payload["dates"][0]["games"][0]["status"] = {"codedGameState": "F"}
    unsafe = json.dumps(payload, sort_keys=True).encode("utf-8")
    forged_players = copy.deepcopy(players)
    for player in forged_players:
        player["raw_lineup_payload_sha256"] = hashlib.sha256(unsafe).hexdigest()
        player["snapshot_sha256"] = snapshot_sha256(player)
    ledger = SharedPAForwardLedger(
        tmp_path, plan=plan, contract_sha256=loaded["contract_sha256"], runtime_manifest_sha256="c" * 64,
        collector_code_sha256="b" * 64,
    )
    with pytest.raises(SharedPAForwardLedgerError, match="safe input surface"):
        ledger.append_complete(
            target=plan.targets[0], side="home", players=forged_players, lineup_raw=unsafe,
            stats_raw_by_sha256=stats_raw, committed_utc=HORIZON.isoformat(),
        )
    assert not (tmp_path / "raw").exists()


def test_stats_payload_must_reproduce_consumed_counts_even_after_rehash(tmp_path: Path) -> None:
    plan = _plan()
    loaded, lineup, players, stats_raw = _players(plan)
    forged_players = copy.deepcopy(players)
    first = forged_players[0]
    old_hash = first["raw_stats_payload_sha256"]
    payload = json.loads(stats_raw[old_hash])
    payload["people"][0]["stats"][0]["splits"][0]["stat"]["hits"] += 1
    forged_raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    forged_hash = hashlib.sha256(forged_raw).hexdigest()
    first["raw_stats_payload_sha256"] = forged_hash
    first["feature_snapshot_sha256"] = sha256_value({
        "counts": first["stats_counts"],
        "league_prior_probability": first["league_prior_probability"],
        "prior_strength_pa": first["prior_strength_pa"],
        "raw_stats_payload_sha256": forged_hash,
        "stats_receipt_utc": first["stats_receipt_utc"],
    })
    first["snapshot_sha256"] = snapshot_sha256(first)
    forged_stats = dict(stats_raw)
    del forged_stats[old_hash]
    forged_stats[forged_hash] = forged_raw
    ledger = SharedPAForwardLedger(
        tmp_path, plan=plan, contract_sha256=loaded["contract_sha256"],
        runtime_manifest_sha256="c" * 64, collector_code_sha256="b" * 64,
    )
    with pytest.raises(SharedPAForwardLedgerError, match="counts differ"):
        ledger.append_complete(
            target=plan.targets[0], side="home", players=forged_players,
            lineup_raw=lineup.body, stats_raw_by_sha256=forged_stats,
            committed_utc=HORIZON.isoformat(),
        )
    assert not (tmp_path / "raw").exists()
