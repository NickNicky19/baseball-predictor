"""Regression and mutation tests for the opportunity-only shared-PA candidate."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from src.evaluation.projected_lineup_contract import load_contract, sha256_value
from src.evaluation.shared_pa_batter_skill import (
    BatterSkillSnapshotError,
    build_batter_skill_snapshot,
    validate_batter_skill_snapshot,
)
from src.evaluation.shared_pa_forward_collector import RawPregameResponse
from src.evaluation.shared_pa_forward_evidence import load_forward_contract
from src.evaluation.shared_pa_projected_opportunity_candidate import (
    ProjectedOpportunityCandidateError,
    build_projected_opportunity_candidate,
    load_projected_opportunity_protocol,
    validate_projected_opportunity_candidate,
)
from tests.test_shared_pa_forward_evidence import _snapshot


ROOT = Path(__file__).resolve().parents[1]


def _projection(snapshot: dict, *, include_player: bool = True) -> dict:
    player_id = snapshot["player_id"]
    other_ids = list(range(700001, 700011))
    first_ids = ([player_id] + other_ids[:8]) if include_player else other_ids[:9]
    second_ids = other_ids[:9]
    scenarios = [
        {
            "probability": 0.75,
            "lineup": [
                {"player_id": value, "slot": slot}
                for slot, value in enumerate(first_ids, start=1)
            ],
        },
        {
            "probability": 0.25,
            "lineup": [
                {"player_id": value, "slot": slot}
                for slot, value in enumerate(second_ids, start=1)
            ],
        },
    ]
    if not include_player:
        scenarios = [{"probability": 1.0, "lineup": scenarios[0]["lineup"]}]
    roster = sorted({entry["player_id"] for scenario in scenarios for entry in scenario["lineup"]})
    if not include_player:
        # The player is legitimately active but receives no mass in any
        # projected lineup scenario.  This isolates the model's explicit
        # projection-support abstention from the separate roster-identity gate.
        roster.append(player_id)
        roster.sort()
    receipt = {
        "source_kind": "official_mlb_active_roster_t4",
        "source_record_id": "synthetic-roster",
        "received_at_utc": snapshot["target_horizon_utc"],
        "payload_sha256": "8" * 64,
        "input_surface_sha256": "9" * 64,
    }
    feature_receipt = {
        "source_kind": "internal_historical_lineup_feature_store",
        "source_record_id": "synthetic-history",
        "received_at_utc": snapshot["target_horizon_utc"],
        "payload_sha256": "a" * 64,
        "input_surface_sha256": "b" * 64,
    }
    record = {
        "schema_version": "projected-lineup-projection-v1",
        "terminal_state": "projected_complete",
        "research_only": True,
        "betting_authorized": False,
        "official_game_date": snapshot["official_game_date"],
        "mlb_game_pk": snapshot["mlb_game_pk"],
        "team_id": snapshot["home_team_id"],
        "target_horizon_utc": snapshot["target_horizon_utc"],
        "projection_receipt_utc": snapshot["target_horizon_utc"],
        "input_receipts": {"active_roster": receipt, "historical_lineup_features": feature_receipt},
        "active_roster_player_ids": roster,
        "fitted_candidate_id": "projected_lineup_empirical_joint_v1",
        "fitted_artifact_sha256": "c" * 64,
        "model_code_sha256": hashlib.sha256(
            (ROOT / "src/evaluation/projected_lineup_empirical_joint.py").read_bytes()
        ).hexdigest(),
        "scenarios": scenarios,
    }
    record["projection_content_sha256"] = sha256_value(record)
    return record


def _stats_response(snapshot: dict) -> RawPregameResponse:
    payload = {
        "people": [{
            "id": snapshot["player_id"],
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
        }]
    }
    return RawPregameResponse(
        body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        received_at_utc=snapshot["target_horizon_utc"],
    )


def _inputs() -> tuple[dict, dict, dict, dict, object]:
    source_snapshot = _snapshot()
    projection = _projection(source_snapshot)
    lineup_contract = load_contract(ROOT / "config/projected_lineup_contract_v1.json")
    forward = load_forward_contract(
        root=ROOT,
        contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json",
    )
    skill = build_batter_skill_snapshot(
        projected_lineup_record=projection,
        lineup_contract=lineup_contract,
        player_id=source_snapshot["player_id"],
        side=source_snapshot["side"],
        stats_response=_stats_response(source_snapshot),
        loaded_forward_contract=forward,
        collector_instance_id="synthetic-opportunity-skill",
        collector_code_sha256="d" * 64,
        runtime_manifest_sha256="e" * 64,
    )
    protocol = load_projected_opportunity_protocol(
        root=ROOT,
        path=ROOT / "config/shared_pa_projected_opportunity_forward_v1.json",
    )
    return skill, projection, lineup_contract, forward, protocol


def _build() -> tuple[dict, dict, dict, dict, dict, object]:
    skill, projection, lineup_contract, forward, protocol = _inputs()
    record = build_projected_opportunity_candidate(
        batter_skill_snapshot=skill,
        projected_lineup_record=projection,
        lineup_contract=lineup_contract,
        loaded_forward_contract=forward,
        candidate_protocol=protocol,
        research_epoch_utc="2026-07-27T00:00:00Z",
        prediction_generated_at_utc=skill["target_horizon_utc"],
    )
    return record, skill, projection, lineup_contract, forward, protocol


def test_opportunity_candidate_changes_only_pa_mixture_and_is_reserved_window_smoke() -> None:
    record, skill, _, _, _, _ = _build()
    assert record["projected_start_probability"] == pytest.approx(0.75)
    assert record["candidate_pa_mass"][record["candidate_pa_support"].index(0)] == pytest.approx(
        0.25
    )
    assert record["per_pa_probability"] == skill["per_pa_probability"]
    assert record["evidence_class"] == "nonqualifying_reserved_window_operational_smoke"
    assert record["confirmation_eligible"] is False
    assert record["research_only"] is True
    assert record["betting_authorized"] is False


def test_all_three_markets_derive_from_one_candidate_pa_distribution() -> None:
    record, _, _, _, _, _ = _build()
    markets = record["candidate_market_distributions"]
    assert set(markets) == {"hits_pmf", "home_runs_pmf", "total_bases_pmf", "tails"}
    assert set(record["candidate_minus_baseline_tail_probability"]) == set(markets["tails"])
    for pmf in (markets["hits_pmf"], markets["home_runs_pmf"], markets["total_bases_pmf"]):
        assert sum(pmf) == pytest.approx(1.0, abs=1e-11)


def test_hash_consistent_candidate_probability_mutation_fails_semantic_replay() -> None:
    record, skill, projection, lineup_contract, forward, protocol = _build()
    attacked = copy.deepcopy(record)
    attacked["candidate_market_distributions"]["tails"]["home_runs_over_0.5"] += 0.01
    unsigned = dict(attacked)
    unsigned.pop("candidate_record_sha256")
    attacked["candidate_record_sha256"] = sha256_value(unsigned)
    with pytest.raises(ProjectedOpportunityCandidateError, match="independent probability replay"):
        validate_projected_opportunity_candidate(
            record=attacked,
            batter_skill_snapshot=skill,
            projected_lineup_record=projection,
            lineup_contract=lineup_contract,
            loaded_forward_contract=forward,
            candidate_protocol=protocol,
        )


def test_player_outside_projected_support_abstains_instead_of_receiving_zero_probability() -> None:
    skill, original_projection, lineup_contract, forward, protocol = _inputs()
    source_snapshot = _snapshot()
    projection = _projection(source_snapshot, include_player=False)
    assert projection["projection_content_sha256"] != original_projection["projection_content_sha256"]
    skill = copy.deepcopy(skill)
    skill["projected_lineup_content_sha256"] = projection["projection_content_sha256"]
    unsigned_skill = dict(skill)
    unsigned_skill.pop("batter_skill_snapshot_sha256")
    skill["batter_skill_snapshot_sha256"] = sha256_value(unsigned_skill)
    with pytest.raises(ProjectedOpportunityCandidateError, match="absent from projected-lineup support"):
        build_projected_opportunity_candidate(
            batter_skill_snapshot=skill,
            projected_lineup_record=projection,
            lineup_contract=lineup_contract,
            loaded_forward_contract=forward,
            candidate_protocol=protocol,
            research_epoch_utc="2026-07-27T00:00:00Z",
            prediction_generated_at_utc=skill["target_horizon_utc"],
        )


def test_rehashed_projection_with_unbound_executing_code_fails() -> None:
    skill, projection, lineup_contract, forward, protocol = _inputs()
    projection["model_code_sha256"] = "d" * 64
    unsigned = dict(projection)
    unsigned.pop("projection_content_sha256")
    projection["projection_content_sha256"] = sha256_value(unsigned)
    skill = copy.deepcopy(skill)
    skill["projected_lineup_content_sha256"] = projection["projection_content_sha256"]
    unsigned_skill = dict(skill)
    unsigned_skill.pop("batter_skill_snapshot_sha256")
    skill["batter_skill_snapshot_sha256"] = sha256_value(unsigned_skill)
    with pytest.raises(ProjectedOpportunityCandidateError, match="executing code hash"):
        build_projected_opportunity_candidate(
            batter_skill_snapshot=skill,
            projected_lineup_record=projection,
            lineup_contract=lineup_contract,
            loaded_forward_contract=forward,
            candidate_protocol=protocol,
            research_epoch_utc="2026-07-27T00:00:00Z",
            prediction_generated_at_utc=skill["target_horizon_utc"],
        )


def test_candidate_cannot_be_constructed_before_its_published_epoch() -> None:
    skill, projection, lineup_contract, forward, protocol = _inputs()
    with pytest.raises(ProjectedOpportunityCandidateError, match="out of order"):
        build_projected_opportunity_candidate(
            batter_skill_snapshot=skill,
            projected_lineup_record=projection,
            lineup_contract=lineup_contract,
            loaded_forward_contract=forward,
            candidate_protocol=protocol,
            research_epoch_utc="2026-07-30T00:00:01Z",
            prediction_generated_at_utc=skill["target_horizon_utc"],
        )


def test_prediction_generation_after_t4_cannot_be_backfilled() -> None:
    skill, projection, lineup_contract, forward, protocol = _inputs()
    with pytest.raises(ProjectedOpportunityCandidateError, match="out of order"):
        build_projected_opportunity_candidate(
            batter_skill_snapshot=skill,
            projected_lineup_record=projection,
            lineup_contract=lineup_contract,
            loaded_forward_contract=forward,
            candidate_protocol=protocol,
            research_epoch_utc="2026-07-27T00:00:00Z",
            prediction_generated_at_utc="2026-07-29T20:00:00.000001Z",
        )


def test_batter_skill_is_built_without_an_official_target_lineup() -> None:
    skill, projection, _, _, _ = _inputs()
    assert "raw_lineup_payload_sha256" not in skill
    assert skill["projected_lineup_content_sha256"] == projection["projection_content_sha256"]
    assert skill["stats_pa"] == 100
    assert sum(skill["per_pa_probability"].values()) == pytest.approx(1.0)


def test_rehashed_batter_skill_probability_mutation_fails_replay() -> None:
    skill, _, _, forward, _ = _inputs()
    attacked = copy.deepcopy(skill)
    attacked["per_pa_probability"]["home_run"] += 0.01
    unsigned = dict(attacked)
    unsigned.pop("batter_skill_snapshot_sha256")
    attacked["batter_skill_snapshot_sha256"] = sha256_value(unsigned)
    with pytest.raises(BatterSkillSnapshotError, match="probability differs"):
        validate_batter_skill_snapshot(attacked, loaded_forward_contract=forward)


def test_post_horizon_stats_cannot_build_batter_skill() -> None:
    source_snapshot = _snapshot()
    projection = _projection(source_snapshot)
    lineup_contract = load_contract(ROOT / "config/projected_lineup_contract_v1.json")
    forward = load_forward_contract(
        root=ROOT,
        contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json",
    )
    response = _stats_response(source_snapshot)
    late = RawPregameResponse(
        body=response.body,
        received_at_utc="2026-07-29T20:00:00.000001Z",
    )
    with pytest.raises(BatterSkillSnapshotError, match="after T-4"):
        build_batter_skill_snapshot(
            projected_lineup_record=projection,
            lineup_contract=lineup_contract,
            player_id=source_snapshot["player_id"],
            side=source_snapshot["side"],
            stats_response=late,
            loaded_forward_contract=forward,
            collector_instance_id="synthetic-opportunity-skill",
            collector_code_sha256="d" * 64,
            runtime_manifest_sha256="e" * 64,
        )


def test_protocol_binding_fails_closed_when_a_bound_source_hash_is_mutated(tmp_path: Path) -> None:
    config = copy.deepcopy(
        load_projected_opportunity_protocol(
            root=ROOT,
            path=ROOT / "config/shared_pa_projected_opportunity_forward_v1.json",
        ).value
    )
    config["immutable_bindings"][0]["sha256"] = "0" * 64
    for binding in config["immutable_bindings"]:
        source = ROOT / binding["path"]
        target = tmp_path / binding["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    attacked = tmp_path / "config/shared_pa_projected_opportunity_forward_v1.json"
    attacked.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ProjectedOpportunityCandidateError, match="immutable binding differs"):
        load_projected_opportunity_protocol(root=tmp_path, path=attacked)
