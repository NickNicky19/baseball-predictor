"""Source-bound market derivation using the qualified hierarchical PA component."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.pa_volume_hierarchical_shrinkage_v1 import shrink_slot_distributions
from src.evaluation.pa_volume_source_truth_v2 import validate_pa_volume_artifact
from src.evaluation.shared_pa_forward_evidence import (
    ForwardPAVolumeArtifact,
    derive_market_distributions,
    pa_distribution_sha256,
)
from src.evaluation.shared_pa_pa_volume_source_authority_v1 import (
    VerifiedPAVolumeSourceAuthority,
    invoke_after_pa_volume_source_authority,
)
from src.evaluation.shared_pa_projected_opportunity_candidate import mix_projected_pa_distribution


class HierarchicalProbabilityV4Error(ValueError):
    """The locked hierarchical component cannot be replayed exactly."""


SCHEMA_VERSION = "shared-pa-source-bound-market-distributions-v4"
CONFIG_RELATIVE = "config/shared_pa_hierarchical_opportunity_2023_v1.json"
REPORT_RELATIVE = "reports/shared_pa_hierarchical_opportunity_2023_v1.json"
CONFIG_SHA256 = "adde18b9a3d562d0935837e2e72ca30e8b65ad59beaa68890f17768f63fc2a2d"
REPORT_FILE_SHA256 = "935c05ca447787a59150352a25d3a801c32f53c54952b27541b5c390c26d1c57"
SELECTED_WEIGHT = 0.2


def _load_json(path: Path, expected_sha256: str, label: str) -> Mapping[str, Any]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise HierarchicalProbabilityV4Error(f"{label} bytes differ")
    value = json.loads(raw)
    if not isinstance(value, Mapping):
        raise HierarchicalProbabilityV4Error(f"{label} root is malformed")
    return value


def _load_component(root: Path) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    config = _load_json(root / CONFIG_RELATIVE, CONFIG_SHA256, "hierarchical config")
    report = _load_json(root / REPORT_RELATIVE, REPORT_FILE_SHA256, "hierarchical report")
    if (
        config.get("status") != "LOCKED_BEFORE_RESULTS"
        or config.get("protected_boundaries", {}).get("may_2026_access_allowed") is not False
        or report.get("config_sha256") != CONFIG_SHA256
        or report.get("component_result") != "HIERARCHICAL_COMPONENT_PASSED_PREDECLARED_GATE"
        or report.get("market_superiority_claimed") is not False
        or {row.get("selected_weight") for row in report.get("fold_results", [])} != {SELECTED_WEIGHT}
        or any(
            report.get("paired_official_date_bootstrap_95", {}).get(metric, {}).get("upper", 1.0) >= 0.0
            for metric in ("multiclass_brier", "multiclass_log_loss", "expected_pa_mae")
        )
    ):
        raise HierarchicalProbabilityV4Error("hierarchical qualification evidence differs")
    return config, report


def _load_hierarchical_pa_volume(
    authority: VerifiedPAVolumeSourceAuthority, root: Path,
) -> ForwardPAVolumeArtifact:
    _load_component(root)
    value = json.loads(authority.pa_volume_artifact_path.read_bytes())
    validate_pa_volume_artifact(value)
    pooled = {int(state): float(probability) for state, probability in value["pooled"].items()}
    by_slot = shrink_slot_distributions(
        pooled=pooled,
        by_slot=value["by_lineup_slot"],
        weight=SELECTED_WEIGHT,
    )
    return ForwardPAVolumeArtifact(
        candidate_id="pa_volume_2023_hierarchical_source_bound_v1",
        pooled=pooled,
        by_lineup_slot=by_slot,
    )


def derive_hierarchical_projected_markets_v4(
    *, root: Path, player_id: int, per_pa_probability: Mapping[str, Any],
    projection_marginals: Mapping[str, Any], authority_arguments: Mapping[str, Any],
) -> dict[str, Any]:
    def build(authority: VerifiedPAVolumeSourceAuthority) -> dict[str, Any]:
        pa_volume = _load_hierarchical_pa_volume(authority, root)
        support, mass, start_probability, slot_probability = mix_projected_pa_distribution(
            player_id=player_id,
            projection_marginals=projection_marginals,
            pa_volume=pa_volume,
        )
        baseline_support = sorted(pa_volume.pooled)
        baseline_mass = [pa_volume.pooled[state] for state in baseline_support]
        return {
            "schema_version": SCHEMA_VERSION,
            "research_only": True,
            "betting_authorized": False,
            "promotion_eligible": False,
            "player_id": player_id,
            **authority.binding(),
            "hierarchical_development_config_sha256": CONFIG_SHA256,
            "hierarchical_evaluation_file_sha256": REPORT_FILE_SHA256,
            "hierarchical_global_slot_weight": SELECTED_WEIGHT,
            "projected_start_probability": start_probability,
            "projected_slot_probability": slot_probability,
            "candidate_pa_support": support,
            "candidate_pa_mass": mass,
            "candidate_pa_distribution_sha256": pa_distribution_sha256(support=support, mass=mass),
            "baseline_market_distributions": derive_market_distributions(
                per_pa_probability=per_pa_probability,
                support=baseline_support,
                mass=baseline_mass,
            ),
            "candidate_market_distributions": derive_market_distributions(
                per_pa_probability=per_pa_probability, support=support, mass=mass
            ),
        }

    return invoke_after_pa_volume_source_authority(build, **dict(authority_arguments))
