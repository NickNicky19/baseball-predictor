from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import run_slate

from src.evaluation.shared_pa_forward_evidence import (
    derive_market_distributions,
    pa_distribution_sha256,
    sha256_value,
)
from src.prediction.integrated_shared_pa_candidate import (
    CandidateEvidenceError,
    MODEL_ID,
    load_candidate_archive,
)


HEX = "a" * 64
PER_PA = {
    "strikeout": 0.22,
    "walk": 0.09,
    "single": 0.14,
    "double": 0.05,
    "triple": 0.005,
    "home_run": 0.035,
    "bip_out": 0.42,
    "other_non_ab": 0.04,
}
SUPPORT = [0, 3, 4, 5]
MASS = [0.12, 0.08, 0.56, 0.24]


def _record() -> dict:
    unsigned = {
        "schema_version": "shared-pa-source-bound-opportunity-player-v3",
        "candidate_id": MODEL_ID,
        "official_game_date": "2026-09-17",
        "official_start_utc": "2026-09-17T23:10:00Z",
        "target_horizon_utc": "2026-09-17T19:10:00Z",
        "prediction_generated_at_utc": "2026-09-17T19:00:00Z",
        "mlb_game_pk": 123456,
        "team_id": 117,
        "side": "home",
        "player_id": 660271,
        "source_manifest_sha256": HEX,
        "runtime_release_receipt_sha256": "b" * 64,
        "evidence_envelope_sha256": "c" * 64,
        "candidate_protocol_sha256": "d" * 64,
        "stats_cutoff_date": "2026-09-16",
        "stats_raw_sha256s": ["e" * 64],
        "stats_transport_receipt_sha256s": ["f" * 64],
        "source_authority_state": "QUALIFIED_2023_OFFICIAL_SOURCE_RELEASE",
        "pa_volume_artifact_sha256": "1" * 64,
        "pa_volume_source_manifest_sha256": "2" * 64,
        "projected_start_probability": 0.88,
        "projected_slot_probability_unconditional": {
            str(slot): 0.88 if slot == 2 else 0.0 for slot in range(1, 10)
        },
        "projected_slot_probability_given_start": {
            str(slot): 1.0 if slot == 2 else 0.0 for slot in range(1, 10)
        },
        "candidate_pa_support": SUPPORT,
        "candidate_pa_mass": MASS,
        "candidate_pa_distribution_sha256": pa_distribution_sha256(support=SUPPORT, mass=MASS),
        "per_pa_probability": PER_PA,
        "candidate_market_distributions": derive_market_distributions(
            per_pa_probability=PER_PA, support=SUPPORT, mass=MASS
        ),
    }
    return {**unsigned, "candidate_record_sha256": sha256_value(unsigned)}


def _bundle() -> dict:
    unsigned = {
        "official_game_date": "2026-09-17",
        "mlb_game_pk": 123456,
        "team_id": 117,
        "side": "home",
        "candidate_records": [_record()],
        "abstentions": [{"player_id": 1, "reason_code": "stats_unavailable"}],
    }
    return {**unsigned, "side_bundle_sha256": sha256_value(unsigned)}


def _v4_bundle() -> dict:
    record = _record()
    record.pop("candidate_record_sha256")
    record.update({
        "schema_version": "shared-pa-source-bound-opportunity-player-v4",
        "component_revision": "hierarchical_opportunity_v1",
        "hierarchical_candidate_protocol_sha256": "9" * 64,
        "hierarchical_development_config_sha256": "adde18b9a3d562d0935837e2e72ca30e8b65ad59beaa68890f17768f63fc2a2d",
        "hierarchical_evaluation_file_sha256": "935c05ca447787a59150352a25d3a801c32f53c54952b27541b5c390c26d1c57",
        "hierarchical_global_slot_weight": 0.2,
        "lineup_state": "projected_probability_distribution",
        "baseline_pa_support": SUPPORT,
        "baseline_pa_mass": MASS,
        "baseline_pa_distribution_sha256": pa_distribution_sha256(
            support=SUPPORT, mass=MASS
        ),
        "baseline_market_distributions": derive_market_distributions(
            per_pa_probability=PER_PA, support=SUPPORT, mass=MASS
        ),
    })
    record["candidate_record_sha256"] = sha256_value(record)
    unsigned = {
        "schema_version": "shared-pa-source-bound-opportunity-side-bundle-v4",
        "candidate_id": MODEL_ID,
        "lineup_state": "projected_probability_distribution",
        "upstream_v2_side_bundle_sha256": "7" * 64,
        "producer_code_sha256": "8" * 64,
        "candidate_protocol_sha256": "9" * 64,
        "source_manifest_sha256": HEX,
        "runtime_release_receipt_sha256": "b" * 64,
        "source_release_commit": "6" * 40,
        "official_game_date": "2026-09-17",
        "mlb_game_pk": 123456,
        "team_id": 117,
        "side": "home",
        "candidate_records": [record],
        "abstentions": [],
    }
    return {**unsigned, "side_bundle_sha256": sha256_value(unsigned)}


def test_candidate_is_exact_receipt_bound_and_market_coherent(tmp_path: Path) -> None:
    source = tmp_path / "side.json"
    source.write_text(json.dumps(_bundle()), encoding="utf-8")

    archive = load_candidate_archive(source, expected_date="2026-09-17")
    replay = load_candidate_archive(source, expected_date="2026-09-17")

    assert archive["model_id"] == MODEL_ID
    assert replay == archive
    assert archive["coverage"] == {"predicted_players": 1, "abstained_players": 1}
    player = archive["predictions"][0]
    assert set(player["markets"]) == {
        "hits", "home_runs", "total_bases", "hitter_strikeouts", "hitter_walks"
    }
    for market in player["markets"].values():
        assert sum(market["pmf"]) == pytest.approx(1.0)
        assert market["mean"] >= 0.0
    assert player["markets"]["home_runs"]["threshold_probabilities"]["over_0.5"] == pytest.approx(
        _record()["candidate_market_distributions"]["tails"]["home_runs_over_0.5"]
    )
    assert archive["feature_schema"]["excluded"] == [
        "mutable_savant_override", "direct_bvp", "unreceipted_pitcher_matchup"
    ]


def test_hierarchical_v4_record_is_consumed_but_bad_provenance_fails(tmp_path: Path) -> None:
    bundle = _v4_bundle()
    source = tmp_path / "v4.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")
    archive = load_candidate_archive(source, expected_date="2026-09-17")
    assert archive["predictions"][0]["opportunity_mean_pa"] == pytest.approx(
        sum(pa * mass for pa, mass in zip(SUPPORT, MASS))
    )
    assert set(archive["predictions"][0]["baselines"]) == {
        "league_rate_2023_same_pa_volume",
        "time_safe_player_empirical_bayes_with_pooled_projected_pa_volume",
        "frozen_production_simulator",
        "valid_market_implied_probability",
    }
    assert archive["predictions"][0]["input_health"]["full_raw_receipt_replay"] == (
        "verified_by_source_bound_runner_v4"
    )

    record = bundle["candidate_records"][0]
    record["hierarchical_global_slot_weight"] = 0.25
    unsigned_record = dict(record)
    unsigned_record.pop("candidate_record_sha256")
    record["candidate_record_sha256"] = sha256_value(unsigned_record)
    unsigned_bundle = dict(bundle)
    unsigned_bundle.pop("side_bundle_sha256")
    bundle["side_bundle_sha256"] = sha256_value(unsigned_bundle)
    source.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(CandidateEvidenceError, match="hierarchical opportunity provenance"):
        load_candidate_archive(source, expected_date="2026-09-17")


def test_hierarchical_v4_requires_retained_evidence_producer_binding(tmp_path: Path) -> None:
    bundle = _v4_bundle()
    bundle.pop("producer_code_sha256")
    bundle.pop("side_bundle_sha256")
    bundle["side_bundle_sha256"] = sha256_value(bundle)
    source = tmp_path / "unbound-v4.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(CandidateEvidenceError, match="producer binding"):
        load_candidate_archive(source, expected_date="2026-09-17")


def test_hash_bound_official_confirmed_state_requires_exact_start_and_slot(tmp_path: Path) -> None:
    bundle = _v4_bundle()
    bundle["lineup_state"] = "official_confirmed"
    record = bundle["candidate_records"][0]
    record["lineup_state"] = "official_confirmed"
    record["projected_start_probability"] = 1.0
    record["projected_slot_probability_unconditional"] = {
        str(slot): 1.0 if slot == 2 else 0.0 for slot in range(1, 10)
    }
    record["projected_slot_probability_given_start"] = dict(
        record["projected_slot_probability_unconditional"]
    )
    record.pop("candidate_record_sha256")
    record["candidate_record_sha256"] = sha256_value(record)
    bundle.pop("side_bundle_sha256")
    bundle["side_bundle_sha256"] = sha256_value(bundle)
    source = tmp_path / "confirmed.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")
    archive = load_candidate_archive(source, expected_date="2026-09-17")
    assert archive["predictions"][0]["input_health"]["lineup_state"] == "official_confirmed"

    record = bundle["candidate_records"][0]
    record["projected_start_probability"] = 0.99
    record["projected_slot_probability_unconditional"]["2"] = 0.99
    record.pop("candidate_record_sha256")
    record["candidate_record_sha256"] = sha256_value(record)
    bundle.pop("side_bundle_sha256")
    bundle["side_bundle_sha256"] = sha256_value(bundle)
    source.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(CandidateEvidenceError, match="start probability one"):
        load_candidate_archive(source, expected_date="2026-09-17")



def test_candidate_rejects_hash_matching_bundle_with_tampered_market(tmp_path: Path) -> None:
    bundle = _bundle()
    bundle["candidate_records"][0]["candidate_market_distributions"]["tails"]["home_runs_over_0.5"] += 0.01
    record = bundle["candidate_records"][0]
    unsigned_record = dict(record)
    unsigned_record.pop("candidate_record_sha256")
    record["candidate_record_sha256"] = sha256_value(unsigned_record)
    unsigned_bundle = dict(bundle)
    unsigned_bundle.pop("side_bundle_sha256")
    bundle["side_bundle_sha256"] = sha256_value(unsigned_bundle)
    source = tmp_path / "tampered.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(CandidateEvidenceError, match="do not replay"):
        load_candidate_archive(source, expected_date="2026-09-17")


def test_candidate_rejects_known_blocked_pa_volume_artifact(tmp_path: Path) -> None:
    bundle = _bundle()
    record = bundle["candidate_records"][0]
    record["pa_volume_artifact_sha256"] = (
        "7ffd6a8fecb1c4f8aed1966c234a61499050346f87f884f731c440daca793c90"
    )
    unsigned_record = dict(record)
    unsigned_record.pop("candidate_record_sha256")
    record["candidate_record_sha256"] = sha256_value(unsigned_record)
    unsigned_bundle = dict(bundle)
    unsigned_bundle.pop("side_bundle_sha256")
    bundle["side_bundle_sha256"] = sha256_value(unsigned_bundle)
    source = tmp_path / "blocked.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(CandidateEvidenceError, match="blocked PA-volume artifact"):
        load_candidate_archive(source, expected_date="2026-09-17")


def test_candidate_rejects_conditional_unconditional_slot_conflation(tmp_path: Path) -> None:
    bundle = _bundle()
    record = bundle["candidate_records"][0]
    record["projected_slot_probability_unconditional"] = {
        str(slot): 1.0 if slot == 2 else 0.0 for slot in range(1, 10)
    }
    unsigned_record = dict(record)
    unsigned_record.pop("candidate_record_sha256")
    record["candidate_record_sha256"] = sha256_value(unsigned_record)
    unsigned_bundle = dict(bundle)
    unsigned_bundle.pop("side_bundle_sha256")
    bundle["side_bundle_sha256"] = sha256_value(unsigned_bundle)
    source = tmp_path / "slot-conflation.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(CandidateEvidenceError, match="slot distribution is invalid"):
        load_candidate_archive(source, expected_date="2026-09-17")


def test_candidate_rejects_may_before_projection(tmp_path: Path) -> None:
    bundle = _bundle()
    record = bundle["candidate_records"][0]
    record["official_game_date"] = "2026-05-17"
    record["stats_cutoff_date"] = "2026-05-16"
    unsigned_record = dict(record)
    unsigned_record.pop("candidate_record_sha256")
    record["candidate_record_sha256"] = sha256_value(unsigned_record)
    bundle["official_game_date"] = "2026-05-17"
    unsigned_bundle = dict(bundle)
    unsigned_bundle.pop("side_bundle_sha256")
    bundle["side_bundle_sha256"] = sha256_value(unsigned_bundle)
    source = tmp_path / "may.json"
    source.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(CandidateEvidenceError, match="May 2026 is sealed"):
        load_candidate_archive(source, expected_date="2026-05-17")


def test_real_runner_writes_model_separated_candidate_archive(tmp_path: Path) -> None:
    source = tmp_path / "side.json"
    source.write_text(json.dumps(_bundle()), encoding="utf-8")
    archive_root = tmp_path / "archives"

    exit_code = run_slate.main([
        "--model", MODEL_ID,
        "--date", "2026-09-17",
        "--candidate-evidence", str(source),
        "--archive-dir", str(archive_root),
    ])

    assert exit_code == run_slate.EXIT_OK
    output = archive_root / MODEL_ID / "predictions_2026-09-17.json"
    assert output.is_file()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["model_id"] == MODEL_ID
    assert payload["betting_authorized"] is False


def test_runner_fails_closed_without_candidate_evidence(tmp_path: Path) -> None:
    assert run_slate.main([
        "--model", MODEL_ID,
        "--date", "2026-09-17",
        "--archive-dir", str(tmp_path),
    ]) == run_slate.EXIT_NO_DATA
    payload = json.loads(
        (tmp_path / MODEL_ID / "predictions_2026-09-17.json").read_text(encoding="utf-8")
    )
    assert payload["predictions"] == []
    assert payload["abstentions"][0]["reason_code"] == "QUALIFIED_OPPORTUNITY_EVIDENCE_UNAVAILABLE"
