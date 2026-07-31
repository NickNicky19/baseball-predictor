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
