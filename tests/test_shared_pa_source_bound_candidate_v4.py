from __future__ import annotations

from pathlib import Path

import pytest

import src.evaluation.shared_pa_source_bound_candidate_v4 as subject
from src.evaluation.shared_pa_forward_evidence import sha256_value
from src.evaluation.shared_pa_source_bound_candidate_v3 import load_candidate_protocol_v3


ROOT = Path(__file__).resolve().parents[1]


def test_v4_replaces_only_opportunity_math_and_binds_qualification(monkeypatch: pytest.MonkeyPatch):
    parent_unsigned = {
        "schema_version": "shared-pa-source-bound-opportunity-player-v3",
        "candidate_id": "shared_pa_candidate_v1",
        "player_id": 101,
        "per_pa_probability": {
            "strikeout": 0.2, "walk": 0.1, "single": 0.15, "double": 0.05,
            "triple": 0.01, "home_run": 0.04, "bip_out": 0.43, "other_non_ab": 0.02,
        },
        "candidate_pa_support": [0, 4],
        "candidate_pa_mass": [0.2, 0.8],
        "candidate_pa_distribution_sha256": "1" * 64,
        "candidate_market_distributions": {"old": True},
        "baseline_market_distributions": {"old": True},
    }
    parent = {**parent_unsigned, "candidate_record_sha256": sha256_value(parent_unsigned)}
    monkeypatch.setattr(subject, "build_source_bound_candidate_record_v3", lambda **kwargs: parent)
    monkeypatch.setattr(subject, "derive_hierarchical_projected_markets_v4", lambda **kwargs: {
        "candidate_pa_support": list(range(8)),
        "candidate_pa_mass": [0.2, 0.0, 0.0, 0.0, 0.8, 0.0, 0.0, 0.0],
        "candidate_pa_distribution_sha256": "2" * 64,
        "candidate_market_distributions": {"new": True},
        "baseline_pa_support": [0, 4],
        "baseline_pa_mass": [0.2, 0.8],
        "baseline_pa_distribution_sha256": "5" * 64,
        "baseline_market_distributions": {"pooled": True},
    })
    record = subject.build_source_bound_candidate_record_v4(
        root=ROOT,
        evidence_envelope={},
        projection_marginals={},
        authority_arguments={},
        parent_protocol=load_candidate_protocol_v3(ROOT / "config/shared_pa_candidate_v1_protocol.json"),
        hierarchical_protocol=subject.load_candidate_protocol_v4(
            ROOT / "config/shared_pa_candidate_v1_hierarchical_protocol.json"
        ),
        source_manifest_sha256="3" * 64,
        runtime_release_receipt_sha256="4" * 64,
    )
    assert record["schema_version"] == subject.SCHEMA_VERSION
    assert record["candidate_market_distributions"] == {"new": True}
    assert record["hierarchical_global_slot_weight"] == 0.2
    assert record["lineup_state"] == "projected_probability_distribution"
    assert record["baseline_pa_support"] == [0, 4]
    unsigned = dict(record)
    unsigned.pop("candidate_record_sha256")
    assert record["candidate_record_sha256"] == sha256_value(unsigned)
