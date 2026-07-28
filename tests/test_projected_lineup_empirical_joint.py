from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from src.evaluation.projected_lineup_contract import load_contract, sha256_value, validate_projection
from src.evaluation.projected_lineup_empirical_joint import (
    EmpiricalJointLineupError,
    build_empirical_joint_projection,
)
from src.evaluation.projected_lineup_history import build_feature_store


ROOT = Path(__file__).resolve().parents[1]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _roster(ids=range(1, 12)) -> dict:
    return {
        "source_kind": "official_mlb_active_roster_t4",
        "source_record_id": "mlb-roster-147-2023-07-10",
        "received_at_utc": "2023-07-10T14:00:00Z",
        "payload_sha256": _sha("roster"),
        "input_surface_sha256": _sha("roster-surface"),
        "players": [
            {"player_id": player_id, "position_code": "1", "position_type": "Player"}
            for player_id in ids
        ],
    }


def _game(game_pk: int, game_date: str, ids: range) -> list[dict]:
    return [
        {
            "official_game_date": game_date,
            "mlb_game_pk": game_pk,
            "team_id": 147,
            "player_id": player_id,
            "slot": slot,
        }
        for slot, player_id in enumerate(ids, start=1)
    ]


def _inputs() -> tuple[dict, list[dict], dict]:
    roster = _roster()
    history = (
        _game(101, "2023-07-07", range(1, 10))
        + _game(102, "2023-07-08", range(1, 10))
        + _game(103, "2023-07-09", range(2, 11))
    )
    store = build_feature_store(
        official_game_date="2023-07-10",
        team_id=147,
        active_roster_receipt=roster,
        completed_lineups=history,
    )
    return roster, history, store


def _build(*, roster=None, history=None, store=None) -> dict:
    default_roster, default_history, default_store = _inputs()
    return build_empirical_joint_projection(
        official_game_date="2023-07-10",
        mlb_game_pk=200,
        team_id=147,
        target_horizon_utc="2023-07-10T15:00:00Z",
        projection_receipt_utc="2023-07-10T14:30:00Z",
        active_roster_receipt=roster or default_roster,
        historical_feature_store=store or default_store,
        completed_lineups=history or default_history,
        contract=load_contract(ROOT / "config/projected_lineup_contract_v1.json"),
    )


def test_empirical_joint_frequency_combines_duplicate_lineups_without_coefficients() -> None:
    record = _build()
    assert [scenario["probability"] for scenario in record["scenarios"]] == pytest.approx([2 / 3, 1 / 3])
    derived = validate_projection(
        record, load_contract(ROOT / "config/projected_lineup_contract_v1.json")
    )
    assert derived["start_probability"]["1"] == pytest.approx(2 / 3)
    assert derived["start_probability"]["10"] == pytest.approx(1 / 3)
    assert record["model_code_sha256"] == hashlib.sha256(
        (ROOT / "src/evaluation/projected_lineup_empirical_joint.py").read_bytes()
    ).hexdigest()


def test_no_eligible_joint_lineup_is_terminal_missing_not_a_fallback() -> None:
    roster, history, _ = _inputs()
    replacement = _roster(range(20, 31))
    store = build_feature_store(
        official_game_date="2023-07-10",
        team_id=147,
        active_roster_receipt=replacement,
        completed_lineups=history,
    )
    record = _build(roster=replacement, history=history, store=store)
    assert record["terminal_state"] == "projected_quarantined"
    assert "scenarios" not in record


@pytest.mark.parametrize("mutation", ["history", "roster", "same_day"])
def test_lineage_and_chronology_mutations_fail_closed(mutation: str) -> None:
    roster, history, store = _inputs()
    if mutation == "history":
        history = deepcopy(history)
        history[0]["player_id"] = 11
    elif mutation == "roster":
        roster = deepcopy(roster)
        roster["source_record_id"] = "different"
    else:
        history = deepcopy(history)
        history[0]["official_game_date"] = "2023-07-10"
    with pytest.raises(EmpiricalJointLineupError):
        _build(roster=roster, history=history, store=store)


def test_hash_consistent_but_semantically_mutated_feature_store_fails_replay() -> None:
    roster, history, store = _inputs()
    store = deepcopy(store)
    store["features"][0]["prior_completed_starts"] += 1
    unsigned = dict(store)
    unsigned.pop("feature_store_sha256")
    store["feature_store_sha256"] = sha256_value(unsigned)
    with pytest.raises(EmpiricalJointLineupError, match="semantic replay"):
        _build(roster=roster, history=history, store=store)


def test_late_projection_is_terminal_and_never_backfilled() -> None:
    roster, history, store = _inputs()
    record = build_empirical_joint_projection(
        official_game_date="2023-07-10",
        mlb_game_pk=200,
        team_id=147,
        target_horizon_utc="2023-07-10T15:00:00Z",
        projection_receipt_utc="2023-07-10T15:00:01Z",
        active_roster_receipt=roster,
        historical_feature_store=store,
        completed_lineups=history,
        contract=load_contract(ROOT / "config/projected_lineup_contract_v1.json"),
    )
    assert record["terminal_state"] == "projected_unavailable"
    assert "scenarios" not in record


def test_may_is_rejected_before_projection() -> None:
    roster, history, store = _inputs()
    with pytest.raises(EmpiricalJointLineupError):
        build_empirical_joint_projection(
            official_game_date="2026-05-10",
            mlb_game_pk=200,
            team_id=147,
            target_horizon_utc="2026-05-10T15:00:00Z",
            projection_receipt_utc="2026-05-10T14:00:00Z",
            active_roster_receipt=roster,
            historical_feature_store=store,
            completed_lineups=history,
            contract=load_contract(ROOT / "config/projected_lineup_contract_v1.json"),
        )


def test_noncanonical_date_is_rejected_even_when_projection_is_late() -> None:
    roster, history, store = _inputs()
    with pytest.raises(EmpiricalJointLineupError, match="canonical"):
        build_empirical_joint_projection(
            official_game_date="2023-7-10",
            mlb_game_pk=200,
            team_id=147,
            target_horizon_utc="2023-07-10T15:00:00Z",
            projection_receipt_utc="2023-07-10T15:00:01Z",
            active_roster_receipt=roster,
            historical_feature_store=store,
            completed_lineups=history,
            contract=load_contract(ROOT / "config/projected_lineup_contract_v1.json"),
        )
