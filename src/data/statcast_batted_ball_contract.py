"""Fail-closed integrity contract for Statcast batted-ball rate pairs.

Barrels are a strict subset of hard-hit batted balls: a barrel requires an
exit velocity of at least 98 mph, while hard-hit is at least 95 mph.  Therefore
``barrel_rate > hard_hit_rate`` cannot be repaired by clipping or by replacing
one rate with a league value.  It is a raw-source, unit, denominator, or
identity defect and must be quarantined before any feature or probability path
can consume it.
"""

from __future__ import annotations

import math
from typing import Any


class StatcastBattedBallContractError(ValueError):
    """A batted-ball record is not truthful enough to consume."""


def _rate(value: Any, *, field: str, source: str) -> float | None:
    if value is None:
        return None
    try:
        rate = float(value)
    except (TypeError, ValueError) as exc:
        raise StatcastBattedBallContractError(
            f"{source}.{field} is not a numeric fraction"
        ) from exc
    if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
        raise StatcastBattedBallContractError(
            f"{source}.{field} must be a finite fraction in [0, 1]"
        )
    return rate


def validate_barrel_hard_hit_pair(
    *,
    barrel_rate: Any,
    hard_hit_rate: Any,
    source: str,
    require_pair_when_present: bool = False,
) -> tuple[float | None, float | None]:
    """Validate a same-universe barrel/hard-hit pair without coercion.

    ``require_pair_when_present`` is used at rich-feature consumption, where a
    partial override would otherwise make the missing counterpart invisible.
    Raw profiles may omit both fields; their missingness is dealt with by an
    explicit upstream source/fallback policy, never by this validator.
    """
    barrel = _rate(barrel_rate, field="barrel_rate", source=source)
    hard_hit = _rate(hard_hit_rate, field="hard_hit_rate", source=source)
    if require_pair_when_present and (barrel is None) != (hard_hit is None):
        raise StatcastBattedBallContractError(
            f"{source} supplies only one of barrel_rate and hard_hit_rate"
        )
    if barrel is not None and hard_hit is not None and barrel > hard_hit:
        raise StatcastBattedBallContractError(
            f"{source} has impossible barrel_rate > hard_hit_rate"
        )
    return barrel, hard_hit
