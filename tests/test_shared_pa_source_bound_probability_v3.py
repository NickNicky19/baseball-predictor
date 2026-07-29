from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.evaluation.pa_volume_source_truth_v2 import (
    build_pa_volume_artifact,
    canonical_json_bytes,
)
import src.evaluation.shared_pa_source_bound_probability_v3 as subject
from src.evaluation.shared_pa_pa_volume_source_authority_v1 import (
    PAVolumeSourceAuthorityError,
    VerifiedPAVolumeSourceAuthority,
)


def rows() -> list[dict[str, object]]:
    return [
        {
            "game_pk": 1,
            "official_date": "2023-03-30",
            "side": side,
            "team_id": team_id,
            "player_id": base + slot,
            "lineup_slot": slot,
            "out_pa": 5 if slot == 1 else 4,
        }
        for side, team_id, base in (("away", 10, 100), ("home", 20, 200))
        for slot in range(1, 10)
    ]


def probability() -> dict[str, float]:
    return {
        "strikeout": 0.20,
        "walk": 0.10,
        "single": 0.15,
        "double": 0.05,
        "triple": 0.01,
        "home_run": 0.04,
        "bip_out": 0.43,
        "other_non_ab": 0.02,
    }


def marginals() -> dict[str, dict[str, float]]:
    return {
        "start_probability": {"101": 0.75},
        "slot_probability": {
            **{"101:1": 0.75},
            **{f"101:{slot}": 0.0 for slot in range(2, 10)},
        },
    }


def authority(tmp_path: Path) -> VerifiedPAVolumeSourceAuthority:
    artifact_path = tmp_path / "pa.json"
    artifact_path.write_bytes(
        canonical_json_bytes(
            build_pa_volume_artifact(
                rows=rows(),
                source_release_manifest_sha256="a" * 64,
                source_release_external_verification_sha256="b" * 64,
                dependency_lock_sha256="c" * 64,
                builder_source_sha256="d" * 64,
            )
        )
    )
    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    return VerifiedPAVolumeSourceAuthority(
        authority_id="synthetic-qualified-authority",
        authority_manifest_path=tmp_path / "authority.json",
        authority_manifest_sha256="e" * 64,
        external_receipt_path=tmp_path / "receipt.json",
        external_receipt_sha256="f" * 64,
        source_release_manifest_path=tmp_path / "source.json",
        source_release_manifest_sha256="a" * 64,
        source_release_external_verification_path=tmp_path / "verification.json",
        source_release_external_verification_sha256="b" * 64,
        rebuild_manifest_path=tmp_path / "rebuild.json",
        rebuild_manifest_sha256="1" * 64,
        pa_volume_artifact_path=artifact_path,
        pa_volume_artifact_sha256=digest,
        dependency_lock_path=tmp_path / "requirements.lock",
        dependency_lock_sha256="c" * 64,
        qualified_at_utc="2026-07-29T10:00:00Z",
        observed_at_utc="2026-07-29T10:01:00Z",
    )


def test_invalid_authority_prevents_every_market_probability_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def fail(*args, **kwargs):
        raise PAVolumeSourceAuthorityError("synthetic authority failure")

    def markets(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(subject, "invoke_after_pa_volume_source_authority", fail)
    monkeypatch.setattr(subject, "derive_market_distributions", markets)
    with pytest.raises(PAVolumeSourceAuthorityError, match="synthetic"):
        subject.derive_source_bound_projected_markets_v3(
            player_id=101,
            per_pa_probability=probability(),
            projection_marginals=marginals(),
            authority_arguments={},
        )
    assert calls == []


def test_valid_authority_numerically_reaches_hits_hr_and_total_bases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verified = authority(tmp_path)

    def invoke(parent, **kwargs):
        return parent(verified)

    monkeypatch.setattr(subject, "invoke_after_pa_volume_source_authority", invoke)
    result = subject.derive_source_bound_projected_markets_v3(
        player_id=101,
        per_pa_probability=probability(),
        projection_marginals=marginals(),
        authority_arguments={},
    )
    markets = result["candidate_market_distributions"]
    assert set(markets) == {
        "hits_pmf",
        "home_runs_pmf",
        "total_bases_pmf",
        "tails",
    }
    assert "hits_over_0.5" in markets["tails"]
    assert "home_runs_over_0.5" in markets["tails"]
    assert "total_bases_over_0.5" in markets["tails"]
    assert result["candidate_pa_support"] == [0, 4, 5]
    assert result["candidate_pa_mass"] == [0.25, 0.0, 0.75]
    assert result["pa_volume_artifact_sha256"] == verified.pa_volume_artifact_sha256
