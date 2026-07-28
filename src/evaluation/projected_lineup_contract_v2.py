"""Canonical-date wrapper for hash-preserved projected-lineup v1 evidence."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from src.evaluation.projected_lineup_contract import ProjectedLineupContractError, validate_projection


class ProjectedLineupContractV2Error(ProjectedLineupContractError):
    pass


def _canonical_nonsealed_date(value: Any) -> str:
    if not isinstance(value, str):
        raise ProjectedLineupContractV2Error("official_game_date must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ProjectedLineupContractV2Error("official_game_date must be YYYY-MM-DD") from exc
    if value != parsed.isoformat():
        raise ProjectedLineupContractV2Error("official_game_date must be canonical YYYY-MM-DD")
    if parsed.year == 2026 and parsed.month == 5:
        raise ProjectedLineupContractV2Error("May 2026 is sealed")
    return value


def validate_projection_v2(record: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    _canonical_nonsealed_date(record.get("official_game_date"))
    return validate_projection(record, contract)
