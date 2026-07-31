from __future__ import annotations

import copy
from pathlib import Path

import pytest

import src.evaluation.shared_pa_source_bound_candidate_v3 as subject
from src.evaluation.shared_pa_forward_evidence import PA_OUTCOMES, sha256_value


def envelope() -> dict:
    unsigned = {
        "schema_version": "shared-pa-projected-opportunity-evidence-envelope-v2",
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "official_game_date": "2026-09-17",
        "official_start_utc": "2026-09-17T23:10:00Z",
        "target_horizon_utc": "2026-09-17T19:10:00Z",
        "prediction_generated_at_utc": "2026-09-17T19:00:00Z",
        "mlb_game_pk": 123,
        "team_id": 117,
        "side": "home",
        "player_id": 660271,
        "stats_cutoff_date": "2026-09-16",
        "stats_raw_sha256s": ["a" * 64],
        "stats_transport_receipt_sha256s": ["b" * 64],
        "stats_counts": {
            "strikeout": 20,
            "walk": 10,
            "single": 15,
            "double": 5,
            "triple": 1,
            "home_run": 4,
            "bip_out": 43,
            "other_non_ab": 2,
        },
    }
    return {**unsigned, "evidence_envelope_sha256": sha256_value(unsigned)}


def derived() -> dict:
    per_pa = {outcome: 1.0 / len(PA_OUTCOMES) for outcome in PA_OUTCOMES}
    return {
        "pa_volume_source_release_manifest_sha256": "c" * 64,
        "pa_volume_artifact_sha256": "d" * 64,
        "pa_volume_source_authority_id": "qualified-source",
        "pa_volume_source_authority_manifest_sha256": "e" * 64,
        "pa_volume_source_authority_receipt_sha256": "f" * 64,
        "projected_start_probability": 0.8,
        "projected_slot_probability": {
            str(slot): 0.8 if slot == 1 else 0.0 for slot in range(1, 10)
        },
        "candidate_pa_support": [0, 4],
        "candidate_pa_mass": [0.2, 0.8],
        "candidate_pa_distribution_sha256": "1" * 64,
        "candidate_market_distributions": {"tails": {}},
        "baseline_market_distributions": {"tails": {}},
        "per_pa": per_pa,
    }


def test_real_bridge_emits_source_bound_integrated_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = Path(__file__).resolve().parents[1]
    seen: dict = {}
    result = derived()

    def derive(**kwargs):
        seen.update(kwargs)
        return result

    monkeypatch.setattr(subject, "derive_source_bound_projected_markets_v3", derive)
    record = subject.build_source_bound_candidate_record_v3(
        root=repository,
        evidence_envelope=envelope(),
        projection_marginals={"start_probability": {}, "slot_probability": {}},
        authority_arguments={"authority_root": tmp_path},
        candidate_protocol=subject.load_candidate_protocol_v3(
            repository / "config/shared_pa_candidate_v1_protocol.json"
        ),
        source_manifest_sha256="3" * 64,
        runtime_release_receipt_sha256="4" * 64,
    )
    assert seen["authority_arguments"] == {"authority_root": tmp_path}
    assert record["schema_version"] == subject.SCHEMA_VERSION
    assert record["source_authority_state"] == "QUALIFIED_2023_OFFICIAL_SOURCE_RELEASE"
    assert record["projected_slot_probability_unconditional"]["1"] == 0.8
    assert record["projected_slot_probability_given_start"]["1"] == 1.0
    assert record["candidate_record_sha256"] == sha256_value(
        {key: value for key, value in record.items() if key != "candidate_record_sha256"}
    )


def test_bridge_rejects_may_before_probability_call(monkeypatch: pytest.MonkeyPatch) -> None:
    value = envelope()
    unsigned = dict(value)
    unsigned.pop("evidence_envelope_sha256")
    unsigned["official_game_date"] = "2026-05-10"
    value = {**unsigned, "evidence_envelope_sha256": sha256_value(unsigned)}
    called = []
    monkeypatch.setattr(
        subject,
        "derive_source_bound_projected_markets_v3",
        lambda **kwargs: called.append(kwargs),
    )
    with pytest.raises(subject.SourceBoundCandidateV3Error, match="May 2026 is sealed"):
        subject.build_source_bound_candidate_record_v3(
            root=Path(__file__).resolve().parents[1],
            evidence_envelope=value,
            projection_marginals={},
            authority_arguments={},
            candidate_protocol=subject.load_candidate_protocol_v3(
                Path(__file__).resolve().parents[1] / "config/shared_pa_candidate_v1_protocol.json"
            ),
            source_manifest_sha256="3" * 64,
            runtime_release_receipt_sha256="4" * 64,
        )
    assert called == []
