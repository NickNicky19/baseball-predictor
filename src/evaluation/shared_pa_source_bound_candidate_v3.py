"""Build the first runnable source-bound opportunity candidate.

This module bridges a receipt-replayed v2 evidence envelope to the independently
qualified official-2023 PA-volume authority.  The only candidate change from
the surviving EB-200 batter-skill control is the game PA opportunity mixture.
It does not fit a new batter model, use pitcher context, read outcomes, or
authorize betting.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.shared_pa_forward_evidence import (
    CONTROL_CONFIG_SHA256,
    CONTROL_ID,
    PA_OUTCOMES,
    empirical_bayes_pa_probability,
    sha256_value,
)
from src.evaluation.shared_pa_source_bound_probability_v3 import (
    derive_source_bound_projected_markets_v3,
)


class SourceBoundCandidateV3Error(ValueError):
    """Candidate input is incomplete, mutable, or chronologically unsafe."""


SCHEMA_VERSION = "shared-pa-source-bound-opportunity-player-v3"
CANDIDATE_ID = "shared_pa_candidate_v1"
CONTROL_PATH = "config/shared_pa_forward_eb_control_v1.json"
SEALED_FIRST = date(2026, 5, 1)
SEALED_LAST = date(2026, 5, 31)


@dataclass(frozen=True)
class CandidateProtocolV3:
    value: Mapping[str, Any]
    sha256: str
    path: Path


def load_candidate_protocol_v3(path: Path) -> CandidateProtocolV3:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceBoundCandidateV3Error("candidate protocol is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise SourceBoundCandidateV3Error("candidate protocol root must be an object")
    numerical = value.get("numerical_contract")
    protected = value.get("protected_boundaries")
    if (
        value.get("schema_version") != "shared-pa-candidate-protocol-v3"
        or value.get("candidate_id") != CANDIDATE_ID
        or value.get("status") != "RESEARCH_ONLY_EXACT_RELEASE_PENDING"
        or value.get("research_only") is not True
        or value.get("betting_authorized") is not False
        or not isinstance(numerical, Mapping)
        or numerical.get("per_pa_control_id") != CONTROL_ID
        or numerical.get("per_pa_control_config_sha256") != CONTROL_CONFIG_SHA256
        or numerical.get("pa_volume_candidate_id") != "pa_volume_2023_source_bound_v2"
        or numerical.get("only_new_quantity") != "receipt_bound_game_pa_opportunity_mixture"
        or numerical.get("pitcher_features") != "excluded_until_receipt_proven_matchup_block"
        or not isinstance(protected, Mapping)
        or any(protected.get(key) is not False for key in protected)
    ):
        raise SourceBoundCandidateV3Error("candidate protocol contract changed")
    for label in (
        "per_pa_control_config_sha256", "pa_volume_artifact_sha256",
        "source_authority_manifest_sha256", "source_authority_runtime_receipt_file_sha256",
        "projected_lineup_contract_sha256",
    ):
        _sha(numerical.get(label), f"candidate protocol {label}")
    if value.get("markets") != [
        "hits", "home_runs", "total_bases", "hitter_strikeouts", "hitter_walks"
    ] or value.get("qualification_markets") != ["hits", "home_runs", "total_bases"]:
        raise SourceBoundCandidateV3Error("candidate market contract changed")
    return CandidateProtocolV3(
        value=value,
        sha256=hashlib.sha256(raw).hexdigest(),
        path=path.resolve(),
    )


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise SourceBoundCandidateV3Error(f"{label} must be a lowercase SHA-256")
    return value


def _load_control(root: Path) -> tuple[dict[str, float], float]:
    path = root / CONTROL_PATH
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CONTROL_CONFIG_SHA256:
        raise SourceBoundCandidateV3Error("EB-200 control bytes differ")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SourceBoundCandidateV3Error("EB-200 control is invalid JSON") from exc
    if (
        not isinstance(value, Mapping)
        or value.get("candidate_id") != CONTROL_ID
        or value.get("fit_season") != 2023
        or value.get("prior_strength_pa") != 200.0
        or value.get("outcome_order") != list(PA_OUTCOMES)
        or value.get("boundaries", {}).get("may_2026_forbidden") is not True
        or value.get("boundaries", {}).get("batter_only") is not True
    ):
        raise SourceBoundCandidateV3Error("EB-200 control contract changed")
    counts = value.get("league_prior_counts")
    if not isinstance(counts, Mapping) or set(counts) != set(PA_OUTCOMES):
        raise SourceBoundCandidateV3Error("EB-200 league prior is incomplete")
    parsed = {outcome: int(counts[outcome]) for outcome in PA_OUTCOMES}
    if any(count < 0 for count in parsed.values()) or sum(parsed.values()) <= 0:
        raise SourceBoundCandidateV3Error("EB-200 league prior counts are invalid")
    total = float(sum(parsed.values()))
    return ({outcome: parsed[outcome] / total for outcome in PA_OUTCOMES}, 200.0)


def _validate_envelope(value: Mapping[str, Any]) -> None:
    supplied = value.get("evidence_envelope_sha256")
    unsigned = dict(value)
    unsigned.pop("evidence_envelope_sha256", None)
    if supplied != sha256_value(unsigned):
        raise SourceBoundCandidateV3Error("evidence envelope hash differs")
    try:
        target = date.fromisoformat(str(value.get("official_game_date", "")))
    except ValueError as exc:
        raise SourceBoundCandidateV3Error("official game date is invalid") from exc
    if SEALED_FIRST <= target <= SEALED_LAST:
        raise SourceBoundCandidateV3Error("May 2026 is sealed")
    if value.get("research_only") is not True or value.get("betting_authorized") is not False:
        raise SourceBoundCandidateV3Error("evidence envelope research boundary changed")
    counts = value.get("stats_counts")
    if not isinstance(counts, Mapping) or set(counts) != set(PA_OUTCOMES):
        raise SourceBoundCandidateV3Error("evidence envelope PA counts are incomplete")
    for label in ("mlb_game_pk", "team_id", "player_id"):
        if isinstance(value.get(label), bool) or not isinstance(value.get(label), int) or value[label] <= 0:
            raise SourceBoundCandidateV3Error(f"{label} is invalid")
    if value.get("side") not in {"home", "away"}:
        raise SourceBoundCandidateV3Error("side is invalid")


def build_source_bound_candidate_record_v3(
    *,
    root: Path,
    evidence_envelope: Mapping[str, Any],
    projection_marginals: Mapping[str, Any],
    authority_arguments: Mapping[str, Any],
    candidate_protocol: CandidateProtocolV3,
    source_manifest_sha256: str,
    runtime_release_receipt_sha256: str,
) -> dict[str, Any]:
    """Create one replayable integrated candidate record after authority."""
    _validate_envelope(evidence_envelope)
    if not isinstance(candidate_protocol, CandidateProtocolV3):
        raise SourceBoundCandidateV3Error("hash-verified candidate protocol is required")
    replayed = load_candidate_protocol_v3(candidate_protocol.path)
    if replayed.sha256 != candidate_protocol.sha256 or replayed.value != candidate_protocol.value:
        raise SourceBoundCandidateV3Error("candidate protocol retained-byte replay differs")
    league_prior, prior_strength = _load_control(root)
    counts = {outcome: int(evidence_envelope["stats_counts"][outcome]) for outcome in PA_OUTCOMES}
    per_pa = empirical_bayes_pa_probability(
        counts=counts,
        league_prior=league_prior,
        prior_strength_pa=prior_strength,
    )
    derived = derive_source_bound_projected_markets_v3(
        player_id=int(evidence_envelope["player_id"]),
        per_pa_probability=per_pa,
        projection_marginals=projection_marginals,
        authority_arguments=authority_arguments,
    )
    start_probability = float(derived["projected_start_probability"])
    unconditional_slot = {
        str(slot): float(derived["projected_slot_probability"][str(slot)])
        for slot in range(1, 10)
    }
    conditional_slot = {
        str(slot): (
            unconditional_slot[str(slot)] / start_probability
            if start_probability > 0.0
            else 0.0
        )
        for slot in range(1, 10)
    }
    stats_raw = evidence_envelope.get("stats_raw_sha256s")
    stats_receipts = evidence_envelope.get("stats_transport_receipt_sha256s")
    if not isinstance(stats_raw, list) or not stats_raw or not isinstance(stats_receipts, list) or not stats_receipts:
        raise SourceBoundCandidateV3Error("stats receipt lineage is incomplete")
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": CANDIDATE_ID,
        "terminal_state": "candidate_complete",
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "official_game_date": evidence_envelope["official_game_date"],
        "official_start_utc": evidence_envelope["official_start_utc"],
        "target_horizon_utc": evidence_envelope["target_horizon_utc"],
        "prediction_generated_at_utc": evidence_envelope["prediction_generated_at_utc"],
        "mlb_game_pk": evidence_envelope["mlb_game_pk"],
        "team_id": evidence_envelope["team_id"],
        "side": evidence_envelope["side"],
        "player_id": evidence_envelope["player_id"],
        "source_manifest_sha256": _sha(source_manifest_sha256, "source manifest"),
        "runtime_release_receipt_sha256": _sha(runtime_release_receipt_sha256, "runtime release receipt"),
        "evidence_envelope_sha256": evidence_envelope["evidence_envelope_sha256"],
        "candidate_protocol_sha256": candidate_protocol.sha256,
        "stats_cutoff_date": evidence_envelope["stats_cutoff_date"],
        "stats_raw_sha256s": [_sha(value, "stats raw") for value in stats_raw],
        "stats_transport_receipt_sha256s": [_sha(value, "stats receipt") for value in stats_receipts],
        "source_authority_state": "QUALIFIED_2023_OFFICIAL_SOURCE_RELEASE",
        "pa_volume_source_manifest_sha256": derived["pa_volume_source_release_manifest_sha256"],
        "pa_volume_artifact_sha256": derived["pa_volume_artifact_sha256"],
        "pa_volume_source_authority_id": derived["pa_volume_source_authority_id"],
        "pa_volume_source_authority_manifest_sha256": derived["pa_volume_source_authority_manifest_sha256"],
        "pa_volume_source_authority_receipt_sha256": derived["pa_volume_source_authority_receipt_sha256"],
        "per_pa_control_id": CONTROL_ID,
        "per_pa_control_config_sha256": CONTROL_CONFIG_SHA256,
        "projected_start_probability": start_probability,
        "projected_slot_probability_unconditional": unconditional_slot,
        "projected_slot_probability_given_start": conditional_slot,
        "candidate_pa_support": derived["candidate_pa_support"],
        "candidate_pa_mass": derived["candidate_pa_mass"],
        "candidate_pa_distribution_sha256": derived["candidate_pa_distribution_sha256"],
        "per_pa_probability": per_pa,
        "candidate_market_distributions": derived["candidate_market_distributions"],
        "baseline_market_distributions": derived["baseline_market_distributions"],
        "pitcher_block_status": "excluded_batter_only",
        "nonstarter_opportunity_status": "pa_zero_only_v1_no_qualified_pinch_hit_model",
        "policy_status": "research_probability_only",
    }
    return {**unsigned, "candidate_record_sha256": sha256_value(unsigned)}
