"""Fail-closed checker for the consolidated multi-market probability foundation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


class MultiMarketFoundationStatusError(ValueError):
    """Raised when a consolidated evidence record drifts or overclaims."""


_EXPECTED_ARTIFACT_STATUS = {
    "batter_flat_cumulative_rejection": "CERTIFIED_REJECTION_DIAGNOSIS_REPRODUCED",
    "batter_hierarchical_rejection": "HIERARCHICAL_SELECTION_REJECTION_CERTIFIED",
    "open_2026_eb_benchmark": "CERTIFIED_DIAGNOSTIC_ONE_NEXT_INTERVENTION_MAXIMUM",
    "hits_1_5_market_gate": "CERTIFIED_REJECTED_ON_OPEN_MARKET_GRADEABLE_GATE",
    "hr_open_probability_and_economic_gate": "CERTIFIED_PROBABILITY_READY_HISTORICAL_ECONOMICS_BLOCKED",
    "hr_per_pa_2025_confirmation": "CERTIFIED_PER_PA_HR_COMPONENT_CONFIRMED_FULL_GAME_AND_ECONOMICS_BLOCKED",
    "other_market_readiness": "OUTCOME_BLIND_READINESS_COMPLETE_RESEARCH_ONLY",
    "pitcher_k_rejection": "CERTIFIED_CONFIRMATION_REJECTION",
    "hr_full_game_forward_input_contract_certificate": "PREREGISTRATION_AND_OFFLINE_MUTATIONS_CERTIFIED",
    "v13_operational_failure": "MATERIAL_OPERATIONAL_FAILURE_NO_RETRY",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MultiMarketFoundationStatusError(f"{label} must be an object")
    return value


def resolve_evidence_path(relative_path: str, roots: Sequence[Path]) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() and candidate.is_file():
        return candidate
    for root in roots:
        resolved = root / candidate
        if resolved.is_file():
            return resolved
    raise MultiMarketFoundationStatusError(f"evidence artifact cannot be found: {relative_path}")


def validate_status(payload: Mapping[str, Any], *, evidence_roots: Sequence[Path]) -> None:
    if payload.get("schema_version") != "multi-market-probability-foundation-status-v1":
        raise MultiMarketFoundationStatusError("unsupported foundation status schema")
    if payload.get("status") != "ACTIVE_RESEARCH_FOUNDATION_CONSOLIDATED_NOT_BETTABLE":
        raise MultiMarketFoundationStatusError("foundation status cannot be promoted or relabeled")
    if payload.get("betting_authorized") is not False or payload.get("production_changed") is not False:
        raise MultiMarketFoundationStatusError("foundation cannot authorize betting or change production")
    if payload.get("economic_evidence_eligible") is not False:
        raise MultiMarketFoundationStatusError("foundation cannot declare economic evidence eligible")
    if payload.get("may_2026_opened") is not True:
        raise MultiMarketFoundationStatusError("the recorded May scope incident must not be erased")
    incident = _mapping(payload.get("may_2026_scope_incident"), "May scope incident")
    if incident.get("occurred") is not True or incident.get("model_fit_or_selection_used_may") is not False or incident.get("outcome_or_economic_metric_used_may") is not False:
        raise MultiMarketFoundationStatusError("May scope incident is incomplete or overclaimed")
    if "must not be represented as an untouched holdout" not in str(incident.get("future_rule", "")):
        raise MultiMarketFoundationStatusError("May future-holdout prohibition is absent")

    evidence = _mapping(payload.get("evidence"), "evidence")
    for key, expected_status in _EXPECTED_ARTIFACT_STATUS.items():
        descriptor = _mapping(evidence.get(key), f"evidence.{key}")
        path = resolve_evidence_path(str(descriptor.get("path", "")), evidence_roots)
        if _sha256(path) != str(descriptor.get("sha256", "")).lower():
            raise MultiMarketFoundationStatusError(f"evidence hash drift: {key}")
        source = _mapping(json.loads(path.read_text(encoding="utf-8")), key)
        if source.get("status") != expected_status:
            raise MultiMarketFoundationStatusError(f"evidence status drift: {key}")
    # These two are a config contract and a readiness snapshot respectively.
    for key in ("hr_full_game_forward_input_contract", "per_market_readiness"):
        descriptor = _mapping(evidence.get(key), f"evidence.{key}")
        path = resolve_evidence_path(str(descriptor.get("path", "")), evidence_roots)
        if _sha256(path) != str(descriptor.get("sha256", "")).lower():
            raise MultiMarketFoundationStatusError(f"evidence hash drift: {key}")

    markets = _mapping(payload.get("market_status"), "market_status")
    expected_states = {
        "hits": "NO_SURVIVING_PRODUCTION_CANDIDATE",
        "home_runs_over_0_5": "PER_PA_COMPONENT_CONFIRMED_FULL_GAME_AND_ECONOMICS_BLOCKED",
        "total_bases": "READINESS_ONLY",
        "rbi": "NOT_READY",
        "hits_runs_rbi": "NOT_READY",
        "pitcher_strikeouts": "FITTED_CHALLENGER_REJECTED",
    }
    for market, expected in expected_states.items():
        entry = _mapping(markets.get(market), f"market_status.{market}")
        if entry.get("current_state") != expected:
            raise MultiMarketFoundationStatusError(f"market state drift or promotion: {market}")
    protected = _mapping(payload.get("protected_invariants"), "protected invariants")
    if any(protected.get(key) is not True for key in (
        "v13_operational_smoke_unchanged_and_non_economic",
        "may_2026_scope_incident_preserved_and_not_reused",
        "immutable_production_baselines_retained",
        "no_betting_authorization",
    )):
        raise MultiMarketFoundationStatusError("protected invariant weakened")
    operational = _mapping(payload.get("operational_prerequisite_before_any_future_collection"), "operational prerequisite")
    if operational.get("not_a_model_or_betting_change") is not True:
        raise MultiMarketFoundationStatusError("operational repair was misrepresented as a model or betting change")
    if operational.get("evidence") != "reports/v13_operational_smoke_failure_2026-07-19.json":
        raise MultiMarketFoundationStatusError("operational failure evidence binding changed")
