"""Strict source-truth parsing for MLB pitching fields.

MLB represents innings pitched as completed innings plus outs in the current
inning: ``"5.2"`` means 17 outs, not 5.2 decimal innings.  This module is the
single parsing boundary shared by the live and point-in-time paths.

The functions deliberately raise on missing or malformed required fields.
Callers must not turn malformed source data into plausible-looking zeros.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional


class PitchingSourceValidationError(ValueError):
    """A required pitching source field is missing or semantically invalid."""


_MLB_INNINGS_RE = re.compile(r"^(0|[1-9][0-9]*)(?:\.([012]))?$")
_NONNEGATIVE_INTEGER_RE = re.compile(r"^(0|[1-9][0-9]*)$")


def parse_mlb_innings_to_outs(value: Any, *, field: str = "inningsPitched") -> int:
    """Return exact completed outs from canonical MLB innings notation.

    Accepted examples are ``"0.0"``, ``"5.2"``, ``"12"`` and non-negative
    integer values.  Fractional Python floats are rejected because treating
    5.2 as decimal innings is the historical defect this boundary prevents.
    """

    if isinstance(value, bool) or value is None:
        raise PitchingSourceValidationError(f"{field} is required")
    if isinstance(value, int):
        if value < 0:
            raise PitchingSourceValidationError(f"{field} must be non-negative")
        return value * 3
    if not isinstance(value, str):
        raise PitchingSourceValidationError(
            f"{field} must use canonical MLB innings notation"
        )

    match = _MLB_INNINGS_RE.fullmatch(value)
    if match is None:
        raise PitchingSourceValidationError(
            f"{field} is not canonical MLB innings notation: {value!r}"
        )
    whole = int(match.group(1))
    partial_outs = int(match.group(2) or "0")
    return whole * 3 + partial_outs


def innings_from_outs(outs: int) -> float:
    """Convert a validated exact-out count to the decimal value models consume."""

    if isinstance(outs, bool) or not isinstance(outs, int) or outs < 0:
        raise PitchingSourceValidationError("outs must be a non-negative integer")
    return outs / 3.0


def parse_nonnegative_int(value: Any, *, field: str) -> int:
    """Parse a required non-negative integer without silent coercion."""

    if isinstance(value, bool) or value is None:
        raise PitchingSourceValidationError(f"{field} is required")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and _NONNEGATIVE_INTEGER_RE.fullmatch(value):
        parsed = int(value)
    else:
        raise PitchingSourceValidationError(
            f"{field} must be a canonical non-negative integer"
        )
    if parsed < 0:
        raise PitchingSourceValidationError(f"{field} must be non-negative")
    return parsed


def parse_game_start_indicator(value: Any, *, field: str = "gamesStarted") -> int:
    """Parse the 0/1 start indicator carried by a single-game pitching split."""

    parsed = parse_nonnegative_int(value, field=field)
    if parsed not in (0, 1):
        raise PitchingSourceValidationError(f"{field} must be 0 or 1 for a game log")
    return parsed


def parse_optional_nonnegative_rate(value: Any, *, field: str) -> Optional[float]:
    """Parse a source rate, preserving the distinction between missing and zero."""

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise PitchingSourceValidationError(f"{field} must be a finite rate")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise PitchingSourceValidationError(f"{field} must be a finite rate") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise PitchingSourceValidationError(
            f"{field} must be finite and non-negative"
        )
    return parsed


def validate_rate_consistency(
    rate: Optional[float],
    *,
    count: int,
    outs: int,
    field: str,
) -> None:
    """Fail when a supplied per-nine rate contradicts its exact denominator.

    MLB's per-nine fields are normally rounded to two decimals.  A tolerance
    just above one hundredth admits that display rounding while rejecting
    material contradictions, including a supplied zero with a positive count.
    Missing rates are handled by truthful derivation at the caller.
    """

    if rate is None:
        return
    if outs <= 0:
        raise PitchingSourceValidationError(
            f"{field} cannot be supplied without a positive innings denominator"
        )
    expected = count * 27.0 / outs
    if abs(rate - expected) > 0.011:
        raise PitchingSourceValidationError(
            f"{field}={rate} contradicts count={count} and outs={outs} "
            f"(expected approximately {expected:.4f})"
        )


def first_observed_rate(
    *values: Optional[float],
    default: float,
) -> float:
    """Return the first present rate; zero is present and must be preserved."""

    for value in values:
        if value is not None:
            return value
    return default


def frozen_legacy_rate_fallback(
    *values: Optional[float],
    default: float,
) -> float:
    """Reproduce the frozen model's historical truthiness fallback exactly.

    This function intentionally treats zero like missing. It exists only to
    prevent a source-integrity repair from silently changing frozen production
    probabilities. Candidate-ready code must use :func:`first_observed_rate`
    after passing ``validate_candidate_ready_pitching_snapshot``.
    """

    for value in values:
        if value:
            return value
    return default


def validate_candidate_ready_pitching_snapshot(snapshot: Any, *, label: str) -> None:
    """Require a fully source-validated snapshot before candidate consumption."""

    outs = getattr(snapshot, "outs_recorded", None)
    games_started = getattr(snapshot, "games_started", None)
    games = getattr(snapshot, "games", None)
    if outs is None or games_started is None or games is None:
        raise PitchingSourceValidationError(
            f"{label} is not candidate-ready: outs/games_started/games are required"
        )
    if isinstance(outs, bool) or not isinstance(outs, int) or outs <= 0:
        raise PitchingSourceValidationError(
            f"{label} is not candidate-ready: outs_recorded must be positive"
        )
    if (
        isinstance(games_started, bool)
        or not isinstance(games_started, int)
        or games_started < 0
        or isinstance(games, bool)
        or not isinstance(games, int)
        or games <= 0
        or games_started > games
    ):
        raise PitchingSourceValidationError(
            f"{label} is not candidate-ready: invalid start/appearance identity"
        )
    innings = getattr(snapshot, "innings_pitched", None)
    if innings is None or abs(float(innings) - innings_from_outs(outs)) > 1e-12:
        raise PitchingSourceValidationError(
            f"{label} is not candidate-ready: innings do not match exact outs"
        )
    for field in ("k_per_9", "bb_per_9", "hr_per_9"):
        if getattr(snapshot, field, None) is None:
            raise PitchingSourceValidationError(
                f"{label} is not candidate-ready: {field} is missing"
            )
