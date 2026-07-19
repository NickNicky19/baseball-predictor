"""Fail closed on legacy point-prediction GBM evidence lacking probability provenance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


class LegacyGBMEligibilityError(ValueError):
    """Raised when legacy GBM evidence is misrepresented as a valid challenger."""


_REQUIRED_FOR_PROBABILITY_CANDIDATE = {
    "source_commit",
    "training_data_sha256",
    "feature_schema_sha256",
    "model_artifact_sha256",
    "probability_contract_sha256",
    "calibration_artifact_sha256",
    "chronological_protocol_sha256",
    "market_identity_contract_sha256",
    "official_outcome_contract_sha256",
}


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LegacyGBMEligibilityError(f"{label} must be an object")
    return value


def audit_legacy_gbm_report(path: str | Path) -> dict[str, Any]:
    """Classify an old point-MAE report without scoring or promoting anything."""
    source = Path(path).resolve()
    try:
        report = _mapping(json.loads(source.read_text(encoding="utf-8")), "legacy GBM report")
    except (OSError, json.JSONDecodeError) as exc:
        raise LegacyGBMEligibilityError("legacy GBM report is unreadable") from exc
    comparison = _mapping(report.get("comparison"), "legacy GBM comparison")
    categories = _mapping(comparison.get("categories"), "legacy GBM categories")
    if not categories:
        raise LegacyGBMEligibilityError("legacy GBM report has no category results")
    missing = sorted(_REQUIRED_FOR_PROBABILITY_CANDIDATE - set(report))
    disqualifiers = [
        "point-MAE report does not prove calibrated line-specific probabilities",
        "no hard game/player/category/line/side market identity contract is bound",
        "no official-outcome settlement contract is bound",
        "no model, feature, or training-data artifact hash is bound",
        "no chronological probability-calibration or market-economics gate is reported",
    ]
    if report.get("betting_authorized") is True or report.get("production_promoted") is True:
        raise LegacyGBMEligibilityError("legacy GBM report cannot authorize betting or production promotion")
    return {
        "schema_version": "legacy-gbm-probability-eligibility-audit-v1",
        "status": "INELIGIBLE_AS_PROBABILITY_OR_MARKET_CHALLENGER",
        "report_sha256": sha256_file(source),
        "reported_categories": sorted(str(key) for key in categories),
        "missing_required_probability_bindings": missing,
        "disqualifiers": disqualifiers,
        "production_changed": False,
        "betting_authorized": False,
    }
