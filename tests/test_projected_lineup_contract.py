from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from src.evaluation.projected_lineup_contract import (
    ProjectedLineupContractError,
    load_contract,
    quarantine_record,
    sha256_value,
    validate_projection,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _record() -> dict:
    lineup_a = [{"player_id": value, "slot": value} for value in range(1, 10)]
    lineup_b = [{"player_id": value + 1, "slot": value} for value in range(1, 10)]
    receipt = {
        "source_kind": "internal_historical_lineup_feature_store",
        "source_record_id": "immutable-fixture",
        "received_at_utc": "2026-07-24T15:00:00Z",
        "payload_sha256": _sha("payload"),
        "input_surface_sha256": _sha("surface"),
    }
    record = {
        "schema_version": "projected-lineup-projection-v1",
        "terminal_state": "projected_complete",
        "research_only": True,
        "betting_authorized": False,
        "official_game_date": "2026-07-24",
        "mlb_game_pk": 123,
        "team_id": 147,
        "target_horizon_utc": "2026-07-24T16:00:00Z",
        "projection_receipt_utc": "2026-07-24T15:00:00Z",
        "input_receipts": {"active_roster": receipt, "historical_lineup_features": receipt},
        "active_roster_player_ids": list(range(1, 12)),
        "fitted_candidate_id": "internal_lineup_projector_candidate_v1",
        "fitted_artifact_sha256": _sha("fitted"),
        "model_code_sha256": _sha("code"),
        "scenarios": [{"probability": 0.6, "lineup": lineup_a}, {"probability": 0.4, "lineup": lineup_b}],
    }
    record["projection_content_sha256"] = sha256_value(record)
    return record


def test_valid_joint_distribution_derives_marginals_instead_of_accepting_them():
    result = validate_projection(_record(), load_contract(ROOT / "config/projected_lineup_contract_v1.json"))
    assert result["start_probability"]["1"] == pytest.approx(0.6)
    assert result["start_probability"]["10"] == pytest.approx(0.4)
    assert result["slot_probability"]["2:1"] == pytest.approx(0.4)


@pytest.mark.parametrize("mutation", ["late_input", "duplicate_player", "outside_roster", "bad_mass"])
def test_mutations_fail_closed(mutation: str):
    record = _record()
    if mutation == "late_input":
        record["input_receipts"]["active_roster"]["received_at_utc"] = "2026-07-24T16:00:01Z"
    elif mutation == "duplicate_player":
        record["scenarios"][0]["lineup"][8]["player_id"] = 1
    elif mutation == "outside_roster":
        record["scenarios"][0]["lineup"][0]["player_id"] = 99
    else:
        record["scenarios"][1]["probability"] = 0.5
    with pytest.raises(ProjectedLineupContractError):
        validate_projection(record, load_contract(ROOT / "config/projected_lineup_contract_v1.json"))


def test_mutating_a_hash_bound_projection_after_its_receipt_fails_closed():
    record = _record()
    record["scenarios"][0]["probability"] = 0.5
    record["scenarios"][1]["probability"] = 0.5
    with pytest.raises(ProjectedLineupContractError, match="content hash"):
        validate_projection(record, load_contract(ROOT / "config/projected_lineup_contract_v1.json"))


def test_target_may_is_rejected_and_never_quarantined_into_an_artifact():
    record = _record()
    record["official_game_date"] = "2026-05-14"
    with pytest.raises(ProjectedLineupContractError):
        validate_projection(record, load_contract(ROOT / "config/projected_lineup_contract_v1.json"))
    with pytest.raises(ProjectedLineupContractError):
        quarantine_record(
            official_game_date="2026-05-14", mlb_game_pk=123, team_id=147,
            target_horizon_utc="2026-05-14T16:00:00Z", observed_at_utc="2026-05-14T15:00:00Z", reason="test",
        )


def test_late_or_bad_projection_becomes_terminal_without_fallback_probability():
    terminal = quarantine_record(
        official_game_date="2026-07-24", mlb_game_pk=123, team_id=147,
        target_horizon_utc="2026-07-24T16:00:00Z", observed_at_utc="2026-07-24T16:00:01Z", reason="source unavailable",
    )
    assert terminal["terminal_state"] == "projected_unavailable"
    assert "scenarios" not in terminal
