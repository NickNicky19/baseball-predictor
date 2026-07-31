from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import src.evaluation.shared_pa_hierarchical_probability_v4 as subject
from src.evaluation.pa_volume_source_truth_v2 import build_pa_volume_artifact, canonical_json_bytes
from src.evaluation.shared_pa_pa_volume_source_authority_v1 import VerifiedPAVolumeSourceAuthority
from src.evaluation.shared_pa_source_bound_candidate_v4 import load_candidate_protocol_v4


ROOT = Path(__file__).resolve().parents[1]


def test_locked_component_and_protocol_replay_exact_bytes():
    config, report = subject._load_component(ROOT)
    assert config["status"] == "LOCKED_BEFORE_RESULTS"
    assert report["component_gate_passed"] is True
    assert all(
        report["paired_official_date_bootstrap_95"][metric]["upper"] < 0.0
        for metric in ("multiclass_brier", "multiclass_log_loss", "expected_pa_mae")
    )
    protocol = load_candidate_protocol_v4(
        ROOT / "config/shared_pa_candidate_v1_hierarchical_protocol.json"
    )
    assert protocol.value["selected_global_slot_weight"] == 0.2


def test_component_rejects_changed_qualification_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = tmp_path / "config.json"
    report = tmp_path / "report.json"
    config.write_text("{}", encoding="utf-8")
    report.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(subject, "CONFIG_RELATIVE", config.name)
    monkeypatch.setattr(subject, "REPORT_RELATIVE", report.name)
    with pytest.raises(subject.HierarchicalProbabilityV4Error, match="bytes differ"):
        subject._load_component(tmp_path)


def test_hierarchical_component_numerically_reaches_market_distributions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    rows = [
        {
            "game_pk": 1, "official_date": "2023-03-30", "side": side,
            "team_id": team_id, "player_id": base + slot, "lineup_slot": slot,
            "out_pa": 5 if slot == 1 else 4,
        }
        for side, team_id, base in (("away", 10, 100), ("home", 20, 200))
        for slot in range(1, 10)
    ]
    artifact_path = tmp_path / "pa.json"
    artifact_path.write_bytes(canonical_json_bytes(build_pa_volume_artifact(
        rows=rows,
        source_release_manifest_sha256="a" * 64,
        source_release_external_verification_sha256="b" * 64,
        dependency_lock_sha256="c" * 64,
        builder_source_sha256="d" * 64,
    )))
    verified = VerifiedPAVolumeSourceAuthority(
        authority_id="synthetic", authority_manifest_path=tmp_path / "authority.json",
        authority_manifest_sha256="e" * 64, external_receipt_path=tmp_path / "receipt.json",
        external_receipt_sha256="f" * 64, source_release_manifest_path=tmp_path / "source.json",
        source_release_manifest_sha256="a" * 64,
        source_release_external_verification_path=tmp_path / "verification.json",
        source_release_external_verification_sha256="b" * 64,
        rebuild_manifest_path=tmp_path / "rebuild.json", rebuild_manifest_sha256="1" * 64,
        pa_volume_artifact_path=artifact_path,
        pa_volume_artifact_sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        dependency_lock_path=tmp_path / "requirements.lock", dependency_lock_sha256="c" * 64,
        qualified_at_utc="2026-07-29T10:00:00Z", observed_at_utc="2026-07-29T10:01:00Z",
    )
    monkeypatch.setattr(
        subject, "invoke_after_pa_volume_source_authority",
        lambda callback, **kwargs: callback(verified),
    )
    per_pa = {
        "strikeout": 0.20, "walk": 0.10, "single": 0.15, "double": 0.05,
        "triple": 0.01, "home_run": 0.04, "bip_out": 0.43, "other_non_ab": 0.02,
    }
    result = subject.derive_hierarchical_projected_markets_v4(
        root=ROOT,
        player_id=101,
        per_pa_probability=per_pa,
        projection_marginals={
            "start_probability": {"101": 0.75},
            "slot_probability": {
                **{"101:1": 0.75},
                **{f"101:{slot}": 0.0 for slot in range(2, 10)},
            },
        },
        authority_arguments={},
    )
    assert result["hierarchical_global_slot_weight"] == 0.2
    assert result["candidate_pa_support"] == list(range(8))
    assert result["baseline_pa_support"]
    assert result["baseline_pa_distribution_sha256"]
    assert set(result["candidate_market_distributions"]) == {
        "hits_pmf", "home_runs_pmf", "total_bases_pmf", "tails"
    }
