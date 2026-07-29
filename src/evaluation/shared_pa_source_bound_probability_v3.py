"""Source-bound PA opportunity consumption for a future candidate version.

This is the single numerical boundary between a qualified 2023 PA-volume
artifact and the coherent Hits, HR, and Total Bases derivation.  It is not a
complete candidate, prediction runner, promotion, or betting policy.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.pa_volume_source_truth_v2 import (
    CANDIDATE_ID as PA_VOLUME_CANDIDATE_ID,
    validate_pa_volume_artifact,
)
from src.evaluation.shared_pa_forward_evidence import (
    ForwardPAVolumeArtifact,
    derive_market_distributions,
    pa_distribution_sha256,
)
from src.evaluation.shared_pa_pa_volume_source_authority_v1 import (
    VerifiedPAVolumeSourceAuthority,
    invoke_after_pa_volume_source_authority,
)
from src.evaluation.shared_pa_projected_opportunity_candidate import (
    mix_projected_pa_distribution,
)


class SourceBoundProbabilityV3Error(ValueError):
    """A qualified PA artifact cannot be truthfully consumed."""


SCHEMA_VERSION = "shared-pa-source-bound-market-distributions-v3"


def _load_pa_volume(
    authority: VerifiedPAVolumeSourceAuthority,
) -> ForwardPAVolumeArtifact:
    try:
        value = json.loads(authority.pa_volume_artifact_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceBoundProbabilityV3Error(
            "qualified PA-volume artifact is unreadable"
        ) from exc
    if not isinstance(value, Mapping):
        raise SourceBoundProbabilityV3Error(
            "qualified PA-volume artifact root is malformed"
        )
    validate_pa_volume_artifact(value)
    by_slot = value["by_lineup_slot"]
    pooled = value["pooled"]
    return ForwardPAVolumeArtifact(
        candidate_id=PA_VOLUME_CANDIDATE_ID,
        pooled={int(pa): float(probability) for pa, probability in pooled.items()},
        by_lineup_slot={
            slot: {
                int(pa): float(probability)
                for pa, probability in by_slot[str(slot)].items()
            }
            for slot in range(1, 10)
        },
    )


def derive_source_bound_projected_markets_v3(
    *,
    player_id: int,
    per_pa_probability: Mapping[str, Any],
    projection_marginals: Mapping[str, Any],
    authority_arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify source authority before any market probability function runs."""

    def build(
        authority: VerifiedPAVolumeSourceAuthority,
    ) -> dict[str, Any]:
        pa_volume = _load_pa_volume(authority)
        support, mass, start_probability, slot_probability = (
            mix_projected_pa_distribution(
                player_id=player_id,
                projection_marginals=projection_marginals,
                pa_volume=pa_volume,
            )
        )
        candidate = derive_market_distributions(
            per_pa_probability=per_pa_probability,
            support=support,
            mass=mass,
        )
        baseline_support = sorted(pa_volume.pooled)
        baseline_mass = [pa_volume.pooled[state] for state in baseline_support]
        baseline = derive_market_distributions(
            per_pa_probability=per_pa_probability,
            support=baseline_support,
            mass=baseline_mass,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "research_only": True,
            "betting_authorized": False,
            "promotion_eligible": False,
            "player_id": player_id,
            "pa_volume_candidate_id": PA_VOLUME_CANDIDATE_ID,
            **authority.binding(),
            "projected_start_probability": start_probability,
            "projected_slot_probability": slot_probability,
            "candidate_pa_support": support,
            "candidate_pa_mass": mass,
            "candidate_pa_distribution_sha256": pa_distribution_sha256(
                support=support, mass=mass
            ),
            "baseline_market_distributions": baseline,
            "candidate_market_distributions": candidate,
        }

    return invoke_after_pa_volume_source_authority(
        build, **dict(authority_arguments)
    )
