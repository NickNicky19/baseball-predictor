"""Fail-closed provenance contract for point-in-time rolling features."""

from __future__ import annotations

from datetime import date
import re
from typing import Any, Mapping


class RollingSourceError(RuntimeError):
    """Base error for unavailable or contradictory rolling feature evidence."""


class RollingSourceUnavailableError(RollingSourceError):
    """The configured rolling source could not supply durable evidence."""


class RollingSourceSchemaError(RollingSourceError):
    """Rolling source evidence exists but violates its schema or chronology."""


ROLLING_LINEAGE_FIELDS = (
    "rolling_source_kind",
    "rolling_source_status",
    "rolling_source_target_date",
    "rolling_source_max_game_date",
    "rolling_source_row_count",
    "rolling_source_content_sha256",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def lineage_from_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if not any(key in payload for key in ROLLING_LINEAGE_FIELDS):
        return None
    return {key: payload.get(key) for key in ROLLING_LINEAGE_FIELDS}


def validate_rolling_lineage(lineage: Mapping[str, Any], *, context: str) -> None:
    missing = [key for key in ROLLING_LINEAGE_FIELDS if key not in lineage]
    if missing:
        raise RollingSourceSchemaError(
            f"{context}: missing rolling lineage fields: {', '.join(missing)}"
        )
    if not str(lineage["rolling_source_kind"] or "").strip():
        raise RollingSourceSchemaError(f"{context}: rolling_source_kind is empty")
    status = lineage["rolling_source_status"]
    if status not in {"observed_strict_prior", "confirmed_empty_history"}:
        raise RollingSourceSchemaError(
            f"{context}: unsupported rolling_source_status={status!r}"
        )
    try:
        target = date.fromisoformat(str(lineage["rolling_source_target_date"]))
    except ValueError as exc:
        raise RollingSourceSchemaError(
            f"{context}: rolling_source_target_date must be an ISO date"
        ) from exc
    row_count = lineage["rolling_source_row_count"]
    if not isinstance(row_count, int) or row_count < 0:
        raise RollingSourceSchemaError(
            f"{context}: rolling_source_row_count must be a nonnegative integer"
        )
    content_hash = str(lineage["rolling_source_content_sha256"] or "")
    if not _SHA256.fullmatch(content_hash):
        raise RollingSourceSchemaError(
            f"{context}: rolling_source_content_sha256 is invalid"
        )
    maximum = lineage["rolling_source_max_game_date"]
    if status == "confirmed_empty_history":
        if row_count != 0 or maximum is not None:
            raise RollingSourceSchemaError(
                f"{context}: empty history requires zero rows and no maximum date"
            )
        return
    if row_count <= 0 or maximum is None:
        raise RollingSourceSchemaError(
            f"{context}: observed history requires rows and a maximum date"
        )
    try:
        maximum_date = date.fromisoformat(str(maximum))
    except ValueError as exc:
        raise RollingSourceSchemaError(
            f"{context}: rolling_source_max_game_date must be an ISO date"
        ) from exc
    if maximum_date >= target:
        raise RollingSourceSchemaError(
            f"{context}: rolling source is not strictly prior to target date"
        )

