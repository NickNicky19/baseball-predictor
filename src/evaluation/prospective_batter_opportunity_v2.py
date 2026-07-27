"""Chronology-complete wrapper for hash-preserved opportunity v1 evidence."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

from src.evaluation.prospective_batter_opportunity import ProspectiveBatterOpportunityError, validate_snapshot


class ProspectiveBatterOpportunityV2Error(ProspectiveBatterOpportunityError):
    pass


def _date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ProspectiveBatterOpportunityV2Error(f"{label} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ProspectiveBatterOpportunityV2Error(f"{label} must be YYYY-MM-DD") from exc
    if value != parsed.isoformat():
        raise ProspectiveBatterOpportunityV2Error(f"{label} must be canonical YYYY-MM-DD")
    if parsed.year == 2026 and parsed.month == 5:
        raise ProspectiveBatterOpportunityV2Error("May 2026 is sealed")
    return parsed


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProspectiveBatterOpportunityV2Error(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProspectiveBatterOpportunityV2Error(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveBatterOpportunityV2Error(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_snapshot_v2(record: Mapping[str, Any]) -> None:
    validate_snapshot(record)
    target = _date(record.get("official_game_date"), "official_game_date")
    if _utc(record.get("assembled_at_utc"), "assembled_at_utc") > _utc(record.get("target_horizon_utc"), "target_horizon_utc"):
        raise ProspectiveBatterOpportunityV2Error("opportunity snapshot was assembled after T-minus-4")
    coverage = record.get("history_coverage")
    if not isinstance(coverage, Mapping):
        raise ProspectiveBatterOpportunityV2Error("history coverage is missing")
    prior_dates = coverage.get("captured_prior_game_dates", [])
    if not isinstance(prior_dates, list):
        raise ProspectiveBatterOpportunityV2Error("captured prior game dates are malformed")
    if any(_date(value, "captured prior game date") >= target for value in prior_dates):
        raise ProspectiveBatterOpportunityV2Error("same-day or future opportunity history is forbidden")
