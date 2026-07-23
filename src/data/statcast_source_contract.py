"""Fail-closed source and lineage contract for hitter Statcast profiles."""

from __future__ import annotations

from datetime import date
import hashlib
import json
import math
from numbers import Integral, Real
import re

import pandas as pd

from src.models.dataclasses import StatcastProfile


class StatcastSourceError(RuntimeError):
    """Base class for unavailable or untruthful Statcast source evidence."""


class StatcastSourceUnavailableError(StatcastSourceError):
    """The declared Statcast source could not supply a usable response."""


class StatcastSourceSchemaError(StatcastSourceError):
    """The source responded, but its payload cannot satisfy the input contract."""


ALLOWED_SOURCE_KINDS = frozenset(
    {
        "untracked_legacy",
        "pybaseball_statcast",
        "savant_pitch_csv",
        "savant_player_csv",
        "league_baseline",
    }
)

ALLOWED_SOURCE_STATUSES = frozenset(
    {
        "untracked_legacy",
        "observed_complete",
        "observed_with_field_fallback",
        "league_fallback_no_player_profile",
    }
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def statcast_frame_content_sha256(frame: pd.DataFrame) -> str:
    """Hash exact tabular source content with stable row/column ordering."""
    if frame.columns.has_duplicates:
        raise StatcastSourceSchemaError(
            "Statcast source cannot be hashed with duplicate columns"
        )
    if any(not isinstance(column, str) for column in frame.columns):
        raise StatcastSourceSchemaError(
            "Statcast source cannot be hashed with non-string columns"
        )
    columns = sorted(frame.columns)
    encoded_rows: list[str] = []
    for values in frame.loc[:, columns].itertuples(index=False, name=None):
        row = {
            column: _canonical_source_cell(value)
            for column, value in zip(columns, values)
        }
        encoded_rows.append(
            json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
    payload = json.dumps(
        {"columns": columns, "rows": sorted(encoded_rows)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _canonical_source_cell(value):
    if value is None or value is pd.NA:
        return {"type": "null"}
    try:
        if bool(pd.isna(value)):
            return {"type": "null"}
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, Integral):
        return {"type": "int", "value": int(value)}
    if isinstance(value, Real):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise StatcastSourceSchemaError(
                "Statcast source contains a non-finite numeric cell"
            )
        return {"type": "float", "value": format(parsed, ".17g")}
    if isinstance(value, (pd.Timestamp, date)):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, str):
        return {"type": "str", "value": value}
    raise StatcastSourceSchemaError(
        f"Statcast source contains unsupported cell type {type(value).__name__}"
    )


def validate_statcast_source_lineage(
    profile: StatcastProfile,
    *,
    context: str,
) -> None:
    """Reject contradictory provenance before persistence or consumption."""

    if profile.source_kind not in ALLOWED_SOURCE_KINDS:
        raise StatcastSourceSchemaError(
            f"{context}: unsupported source_kind={profile.source_kind!r}"
        )
    if profile.source_status not in ALLOWED_SOURCE_STATUSES:
        raise StatcastSourceSchemaError(
            f"{context}: unsupported source_status={profile.source_status!r}"
        )

    fallback_fields = tuple(str(field) for field in profile.fallback_fields)
    if len(set(fallback_fields)) != len(fallback_fields):
        raise StatcastSourceSchemaError(
            f"{context}: fallback_fields contains duplicates"
        )
    if tuple(sorted(fallback_fields)) != fallback_fields:
        raise StatcastSourceSchemaError(
            f"{context}: fallback_fields must be sorted for stable identity"
        )

    if profile.source_status == "observed_complete" and fallback_fields:
        raise StatcastSourceSchemaError(
            f"{context}: observed_complete cannot contain fallback_fields"
        )
    if profile.source_status == "observed_with_field_fallback" and not fallback_fields:
        raise StatcastSourceSchemaError(
            f"{context}: observed_with_field_fallback requires fallback_fields"
        )
    if profile.source_status == "league_fallback_no_player_profile":
        if profile.source_kind != "league_baseline":
            raise StatcastSourceSchemaError(
                f"{context}: player fallback must use source_kind='league_baseline'"
            )
        if profile.sample_pa != 0 or not fallback_fields:
            raise StatcastSourceSchemaError(
                f"{context}: player fallback requires sample_pa=0 and fallback_fields"
            )

    observed = profile.source_status.startswith("observed_")
    if observed:
        if profile.source_kind not in {
            "pybaseball_statcast",
            "savant_pitch_csv",
            "savant_player_csv",
        }:
            raise StatcastSourceSchemaError(
                f"{context}: observed status has non-observed source_kind"
            )
        if profile.source_row_count is None or profile.source_row_count <= 0:
            raise StatcastSourceSchemaError(
                f"{context}: observed profile requires positive source_row_count"
            )

    if profile.source_row_count is not None and profile.source_row_count < 0:
        raise StatcastSourceSchemaError(
            f"{context}: source_row_count cannot be negative"
        )
    if profile.source_window_end is not None:
        try:
            date.fromisoformat(profile.source_window_end)
        except (TypeError, ValueError) as exc:
            raise StatcastSourceSchemaError(
                f"{context}: source_window_end must be an ISO date"
            ) from exc

    source_bound = profile.source_kind != "untracked_legacy"
    digest = profile.source_content_sha256
    if source_bound and (not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None):
        raise StatcastSourceSchemaError(
            f"{context}: source_content_sha256 is missing or invalid"
        )
    if not source_bound and digest is not None:
        raise StatcastSourceSchemaError(
            f"{context}: untracked legacy profile cannot claim a source hash"
        )
