"""Fail-closed source and lineage contract for hitter Statcast profiles."""

from __future__ import annotations

from datetime import date

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

