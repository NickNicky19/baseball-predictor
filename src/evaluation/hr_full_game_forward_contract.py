"""Fail-closed validation for the preregistered forward-only full-game HR input boundary.

This module deliberately has no collector integration.  It validates the
contract and a proposed completed T-4 record before that record can ever be
used in a later, separate full-game HR experiment.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


class HRFullGameForwardContractError(ValueError):
    """Raised when a contract or proposed forward record is incomplete or unsafe."""


_REQUIRED_T4_GROUPS = {
    "receipt": {
        "collector_instance_id", "receipt_utc", "monotonic_receipt_sequence",
        "raw_source_payload_sha256", "raw_source_schema_version",
        "collector_code_sha256", "runtime_manifest_sha256",
    },
    "game_identity": {
        "mlb_game_pk", "official_game_date", "official_start_utc", "home_team_id",
        "away_team_id", "game_identity_artifact_sha256",
    },
    "player_identity": {"player_id", "player_identity_artifact_sha256", "hard_model_key"},
    "lineup_and_pa_volume": {
        "lineup_state: confirmed|projected|unknown", "lineup_slot_or_null",
        "lineup_source_receipt_utc", "lineup_raw_payload_sha256",
        "pa_distribution_support", "pa_distribution_probability_by_support",
        "pa_distribution_sha256", "expected_pa", "pa_feature_snapshot_sha256",
        "pa_fallback_labels",
    },
    "per_pa_hr_input": {
        "per_pa_hr_probability", "per_pa_hr_feature_snapshot_sha256",
        "per_pa_hr_component_version_sha256", "rate_fallback_labels",
    },
    "prediction_provenance": {
        "full_game_p_over_0_5", "simulation_seed_or_analytic_method", "model_code_sha256",
        "effective_config_sha256", "feature_manifest_sha256", "policy_sha256",
        "prediction_artifact_sha256",
    },
}

_REQUIRED_COMPLETED_RECORD_FIELDS = {
    "collector_instance_id", "receipt_utc", "monotonic_receipt_sequence", "raw_source_payload_sha256",
    "raw_source_schema_version", "collector_code_sha256", "runtime_manifest_sha256", "source_observation_utc",
    "mlb_game_pk", "official_game_date", "official_start_utc", "home_team_id", "away_team_id",
    "game_identity_artifact_sha256", "player_id", "player_identity_artifact_sha256", "hard_model_key",
    "lineup_state", "lineup_slot_or_null", "lineup_source_receipt_utc", "lineup_raw_payload_sha256",
    "pa_distribution_support", "pa_distribution_probability_by_support", "pa_distribution_sha256", "expected_pa",
    "pa_feature_snapshot_sha256", "pa_fallback_labels", "per_pa_hr_probability",
    "per_pa_hr_feature_snapshot_sha256", "per_pa_hr_component_version_sha256", "rate_fallback_labels",
    "full_game_p_over_0_5", "simulation_seed_or_analytic_method", "model_code_sha256",
    "effective_config_sha256", "feature_manifest_sha256", "policy_sha256", "prediction_artifact_sha256",
    "terminal_state",
}


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HRFullGameForwardContractError(f"{label} must be an object")
    return value


def _iso(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise HRFullGameForwardContractError(f"{label} must be an ISO UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HRFullGameForwardContractError(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HRFullGameForwardContractError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _required_names(group: Mapping[str, Any], label: str) -> set[str]:
    fields = group.get(label)
    if not isinstance(fields, list):
        raise HRFullGameForwardContractError(f"required T-4 {label} fields are missing")
    return {str(value) for value in fields}


def validate_contract(payload: Mapping[str, Any]) -> None:
    """Validate the immutable contract shape and its non-regression safeguards."""
    if payload.get("schema_version") != "hr-full-game-forward-input-contract-v1":
        raise HRFullGameForwardContractError("unsupported HR full-game forward contract schema")
    if payload.get("status") != "PREREGISTERED_NOT_DEPLOYED":
        raise HRFullGameForwardContractError("contract must remain preregistered and undeployed")
    if payload.get("production_unchanged") is not True:
        raise HRFullGameForwardContractError("production_unchanged must be true")
    for key in ("betting_authorized", "economic_evidence_eligible"):
        if payload.get(key) is not False:
            raise HRFullGameForwardContractError(f"{key} must be false")
    if payload.get("may_2026_eligible_as_untouched_holdout") is not False:
        raise HRFullGameForwardContractError("May 2026 cannot be a new untouched holdout")
    if payload.get("separate_from_running_v13_operational_smoke") is not True:
        raise HRFullGameForwardContractError("the forward HR contract must stay separate from v13")

    scope = _mapping(payload.get("scope"), "scope")
    if (scope.get("category"), scope.get("line"), scope.get("side"), scope.get("decision_horizon")) != (
        "home_runs", 0.5, "over", "T-4h"
    ):
        raise HRFullGameForwardContractError("HR scope or decision horizon changed")
    excluded = scope.get("excluded_periods")
    if not isinstance(excluded, list) or "2026-05" not in excluded:
        raise HRFullGameForwardContractError("May exclusion is absent")
    if scope.get("historical_backfill_forbidden") is not True:
        raise HRFullGameForwardContractError("historical backfill must remain forbidden")

    required = _mapping(payload.get("required_t4_record"), "required_t4_record")
    for label, expected in _REQUIRED_T4_GROUPS.items():
        if not expected.issubset(_required_names(required, label)):
            raise HRFullGameForwardContractError(f"required T-4 {label} field was removed")

    derivation = _mapping(payload.get("derivation_invariant"), "derivation_invariant")
    if "sum_n" not in str(derivation.get("minimum_required_formula", "")):
        raise HRFullGameForwardContractError("deterministic PA-to-HR derivation was removed")
    if derivation.get("post_hoc_adjustments_forbidden") is not True:
        raise HRFullGameForwardContractError("post-hoc adjustments must remain forbidden")

    settlement = _mapping(payload.get("official_settlement"), "official_settlement")
    if settlement.get("only_after") != "MLB final game status":
        raise HRFullGameForwardContractError("official outcome final-status guard changed")
    forbidden = settlement.get("not_allowed_as_pregame_input")
    if not isinstance(forbidden, list) or not {"official_pa", "official_home_runs", "final_lineup"}.issubset(forbidden):
        raise HRFullGameForwardContractError("postgame leakage guard was weakened")

    terminal = payload.get("terminal_states")
    if not isinstance(terminal, list) or len(terminal) != len(set(terminal)):
        raise HRFullGameForwardContractError("terminal states must be unique")
    if not {"captured_complete", "missed_before_horizon", "raw_schema_changed"}.issubset(terminal):
        raise HRFullGameForwardContractError("required terminal states were removed")
    publication = _mapping(payload.get("publication"), "publication")
    if any(publication.get(key) is not True for key in (
        "append_only_hash_chained_ledger", "atomic_publish_required", "one_terminal_state_per_target", "late_backfill_forbidden"
    )):
        raise HRFullGameForwardContractError("ledger safety invariant was weakened")

    mutations = payload.get("required_mutations")
    if not isinstance(mutations, list) or len(mutations) < 10:
        raise HRFullGameForwardContractError("required mutation coverage was weakened")


def analytic_hr_over_probability(*, support: list[int], probabilities: list[float], per_pa_hr_probability: float) -> float:
    """Derive P(HR >= 1) from the stored pregame PA distribution and per-PA rate."""
    if not support or len(support) != len(probabilities):
        raise HRFullGameForwardContractError("PA support and probabilities must be non-empty and aligned")
    if any(not isinstance(n, int) or n < 0 for n in support):
        raise HRFullGameForwardContractError("PA support must contain non-negative integer counts")
    if any(not isinstance(p, (float, int)) or not math.isfinite(float(p)) or float(p) < 0 for p in probabilities):
        raise HRFullGameForwardContractError("PA probabilities must be finite and non-negative")
    if not math.isclose(sum(float(p) for p in probabilities), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise HRFullGameForwardContractError("PA probabilities must sum exactly to one within tolerance")
    if not isinstance(per_pa_hr_probability, (float, int)) or not 0.0 <= float(per_pa_hr_probability) <= 1.0:
        raise HRFullGameForwardContractError("per-PA HR probability must be in [0, 1]")
    q = float(per_pa_hr_probability)
    return sum(float(weight) * (1.0 - (1.0 - q) ** n) for n, weight in zip(support, probabilities))


def validate_completed_t4_record(record: Mapping[str, Any]) -> None:
    """Validate a complete T-4 HR record without scoring it or reading any outcome."""
    absent = _REQUIRED_COMPLETED_RECORD_FIELDS - set(record)
    if absent:
        raise HRFullGameForwardContractError(f"complete T-4 record missing fields: {sorted(absent)}")
    if record.get("terminal_state") != "captured_complete":
        raise HRFullGameForwardContractError("only captured_complete records can be evaluated")
    if record.get("lineup_state") not in {"confirmed", "projected"}:
        raise HRFullGameForwardContractError("unknown lineup cannot become a complete HR record")
    if not isinstance(record.get("mlb_game_pk"), int) or not isinstance(record.get("player_id"), int):
        raise HRFullGameForwardContractError("game and player identities must be hard integer IDs")
    start = _iso(record["official_start_utc"], "official_start_utc")
    target = start - timedelta(hours=4)
    receipt = _iso(record["receipt_utc"], "receipt_utc")
    observed = _iso(record["source_observation_utc"], "source_observation_utc")
    lineup_receipt = _iso(record["lineup_source_receipt_utc"], "lineup_source_receipt_utc")
    if any(value > target for value in (receipt, observed, lineup_receipt)):
        raise HRFullGameForwardContractError("T-4 record contains post-horizon information")
    support = record["pa_distribution_support"]
    probabilities = record["pa_distribution_probability_by_support"]
    if not isinstance(support, list) or not isinstance(probabilities, list):
        raise HRFullGameForwardContractError("PA distribution must be serialized as lists")
    derived = analytic_hr_over_probability(
        support=support,
        probabilities=probabilities,
        per_pa_hr_probability=record["per_pa_hr_probability"],
    )
    if not isinstance(record.get("full_game_p_over_0_5"), (int, float)) or not math.isclose(
        float(record["full_game_p_over_0_5"]), derived, rel_tol=0.0, abs_tol=1e-12
    ):
        raise HRFullGameForwardContractError("stored HR tail cannot be recomputed from stored T-4 inputs")
    expected_pa = sum(n * float(weight) for n, weight in zip(support, probabilities))
    if not math.isclose(float(record["expected_pa"]), expected_pa, rel_tol=0.0, abs_tol=1e-12):
        raise HRFullGameForwardContractError("stored expected PA differs from its stored distribution")
    forbidden = {"official_pa", "official_home_runs", "final_lineup", "vendor_result"} & set(record)
    if forbidden:
        raise HRFullGameForwardContractError(f"pregame record contains forbidden postgame fields: {sorted(forbidden)}")
