"""Upgrade a validated v3 candidate record to hierarchical opportunity v4."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.shared_pa_forward_evidence import sha256_value
from src.evaluation.shared_pa_hierarchical_probability_v4 import (
    CONFIG_SHA256,
    REPORT_FILE_SHA256,
    SELECTED_WEIGHT,
    derive_hierarchical_projected_markets_v4,
)
from src.evaluation.shared_pa_source_bound_candidate_v3 import (
    CandidateProtocolV3,
    build_source_bound_candidate_record_v3,
)


class SourceBoundCandidateV4Error(ValueError):
    """The hierarchical protocol or upgraded record is invalid."""


SCHEMA_VERSION = "shared-pa-source-bound-opportunity-player-v4"
PROTOCOL_SCHEMA = "shared-pa-candidate-hierarchical-protocol-v1"
PARENT_PROTOCOL_SHA256 = "ca8a419b0ec5c610da1bf9c1efb20ec62580ec02187a634dab3f2b25b33b671b"


@dataclass(frozen=True)
class CandidateProtocolV4:
    value: Mapping[str, Any]
    sha256: str
    path: Path


def load_candidate_protocol_v4(path: Path) -> CandidateProtocolV4:
    raw = path.read_bytes()
    value = json.loads(raw)
    protected = value.get("protected_boundaries", {}) if isinstance(value, Mapping) else {}
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != PROTOCOL_SCHEMA
        or value.get("candidate_id") != "shared_pa_candidate_v1"
        or value.get("component_revision") != "hierarchical_opportunity_v1"
        or value.get("status") != "RESEARCH_ONLY_EXACT_RELEASE_PENDING"
        or value.get("parent_protocol_sha256") != PARENT_PROTOCOL_SHA256
        or value.get("hierarchical_development_config_sha256") != CONFIG_SHA256
        or value.get("hierarchical_evaluation_file_sha256") != REPORT_FILE_SHA256
        or value.get("selected_global_slot_weight") != SELECTED_WEIGHT
        or any(item is not False for item in protected.values())
    ):
        raise SourceBoundCandidateV4Error("hierarchical candidate protocol changed")
    return CandidateProtocolV4(value=value, sha256=hashlib.sha256(raw).hexdigest(), path=path.resolve())


def build_source_bound_candidate_record_v4(
    *, root: Path, evidence_envelope: Mapping[str, Any],
    projection_marginals: Mapping[str, Any], authority_arguments: Mapping[str, Any],
    parent_protocol: CandidateProtocolV3, hierarchical_protocol: CandidateProtocolV4,
    source_manifest_sha256: str, runtime_release_receipt_sha256: str,
) -> dict[str, Any]:
    replayed = load_candidate_protocol_v4(hierarchical_protocol.path)
    if replayed.sha256 != hierarchical_protocol.sha256 or replayed.value != hierarchical_protocol.value:
        raise SourceBoundCandidateV4Error("hierarchical protocol retained-byte replay differs")
    parent = build_source_bound_candidate_record_v3(
        root=root,
        evidence_envelope=evidence_envelope,
        projection_marginals=projection_marginals,
        authority_arguments=authority_arguments,
        candidate_protocol=parent_protocol,
        source_manifest_sha256=source_manifest_sha256,
        runtime_release_receipt_sha256=runtime_release_receipt_sha256,
    )
    derived = derive_hierarchical_projected_markets_v4(
        root=root,
        player_id=int(parent["player_id"]),
        per_pa_probability=parent["per_pa_probability"],
        projection_marginals=projection_marginals,
        authority_arguments=authority_arguments,
    )
    lineup_state = evidence_envelope.get(
        "lineup_state", "projected_probability_distribution"
    )
    if lineup_state not in {
        "projected_probability_distribution", "official_confirmed"
    }:
        raise SourceBoundCandidateV4Error("lineup evidence state is invalid")
    unsigned = dict(parent)
    unsigned.pop("candidate_record_sha256")
    unsigned.update({
        "schema_version": SCHEMA_VERSION,
        "component_revision": "hierarchical_opportunity_v1",
        "hierarchical_candidate_protocol_sha256": hierarchical_protocol.sha256,
        "hierarchical_development_config_sha256": CONFIG_SHA256,
        "hierarchical_evaluation_file_sha256": REPORT_FILE_SHA256,
        "hierarchical_global_slot_weight": SELECTED_WEIGHT,
        "lineup_state": lineup_state,
        "candidate_pa_support": derived["candidate_pa_support"],
        "candidate_pa_mass": derived["candidate_pa_mass"],
        "candidate_pa_distribution_sha256": derived["candidate_pa_distribution_sha256"],
        "candidate_market_distributions": derived["candidate_market_distributions"],
        "baseline_pa_support": derived["baseline_pa_support"],
        "baseline_pa_mass": derived["baseline_pa_mass"],
        "baseline_pa_distribution_sha256": derived[
            "baseline_pa_distribution_sha256"
        ],
        "baseline_market_distributions": derived["baseline_market_distributions"],
    })
    return {**unsigned, "candidate_record_sha256": sha256_value(unsigned)}
