"""Fail-closed contracts for Statcast batted-ball evidence.

The two public rates in this module deliberately share the same measured-exit-
velocity denominator.  A Statcast barrel (launch_speed_angle == 6) is a subset
of hard-hit batted balls, so a source row or aggregate that violates that set
relationship is not safe to consume as a probability input.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Optional

import pandas as pd

from src.data.rolling_source_contract import validate_rolling_lineage


class StatcastIntegrityError(ValueError):
    """Raised when batted-ball evidence is contradictory or unprovable."""


RICH_STATCAST_PASSTHROUGH_FIELDS = (
    "avg_exit_velocity",
    "avg_launch_angle",
    "barrel_rate",
    "bb_rate",
    "chase_rate",
    "contact_rate",
    "hard_hit_rate",
    "k_rate",
    "sweet_spot_rate",
    "swing_rate",
    "whiff_rate",
    "xba",
    "xslg",
    "xwoba",
    "zone_rate",
)


@dataclass(frozen=True)
class BattedBallEvidence:
    measured_batted_balls: int
    barrel_count: int
    hard_hit_count: int
    barrel_rate: float
    hard_hit_rate: float
    classification: str


def derive_batted_ball_evidence(frame: pd.DataFrame) -> Optional[BattedBallEvidence]:
    """Derive coherent barrel/hard-hit counts over one common population.

    Only Statcast's launch-speed-angle bucket is accepted as the barrel label.
    The old path averaged a sparse ``barrel`` column independently from the
    ``hard_hit`` column, allowing incompatible denominators to reach the model.
    Rows without measured exit velocity are outside both rate denominators.
    """

    if frame.empty or "launch_speed" not in frame.columns:
        return None
    if "launch_speed_angle" not in frame.columns:
        return None

    # Statcast can expose contact speed on foul balls. Barrel and hard-hit
    # percentages are BBE metrics, so their population is balls put in play,
    # represented by pitch ``type == X`` when that source field is available.
    work = frame
    if "type" in frame.columns:
        work = frame.loc[frame["type"].astype("string").eq("X")]
    if work.empty:
        return None

    exit_velocity = pd.to_numeric(work["launch_speed"], errors="coerce")
    speed_angle = pd.to_numeric(work["launch_speed_angle"], errors="coerce")
    measured = exit_velocity.notna() & (exit_velocity > 0.0)
    denominator = int(measured.sum())
    if denominator == 0:
        return None

    barrel_mask = speed_angle.eq(6)
    if bool((barrel_mask & ~measured).any()):
        raise StatcastIntegrityError(
            "Statcast barrel classification exists without measured exit velocity"
        )
    if bool((barrel_mask & (exit_velocity < 95.0)).any()):
        raise StatcastIntegrityError(
            "Statcast barrel classification contradicts the hard-hit threshold"
        )

    barrel_count = int((barrel_mask & measured).sum())
    hard_hit_count = int((measured & exit_velocity.ge(95.0)).sum())
    if barrel_count > hard_hit_count:
        raise StatcastIntegrityError("barrel_count exceeds hard_hit_count")

    return BattedBallEvidence(
        measured_batted_balls=denominator,
        barrel_count=barrel_count,
        hard_hit_count=hard_hit_count,
        barrel_rate=barrel_count / denominator,
        hard_hit_rate=hard_hit_count / denominator,
        classification="statcast_type_x_launch_speed_angle_6_over_measured_ev",
    )


def validate_rate_pair(
    barrel_rate: Any,
    hard_hit_rate: Any,
    *,
    context: str,
    allow_both_missing: bool = True,
) -> None:
    """Reject invalid or internally contradictory aggregate rate pairs."""

    if barrel_rate is None and hard_hit_rate is None and allow_both_missing:
        return
    if barrel_rate is None or hard_hit_rate is None:
        raise StatcastIntegrityError(
            f"{context}: barrel_rate and hard_hit_rate must be present together"
        )
    try:
        barrel = float(barrel_rate)
        hard_hit = float(hard_hit_rate)
    except (TypeError, ValueError) as exc:
        raise StatcastIntegrityError(f"{context}: batted-ball rates are not numeric") from exc
    if not math.isfinite(barrel) or not math.isfinite(hard_hit):
        raise StatcastIntegrityError(f"{context}: batted-ball rates are non-finite")
    if not 0.0 <= barrel <= 1.0 or not 0.0 <= hard_hit <= 1.0:
        raise StatcastIntegrityError(f"{context}: batted-ball rates are outside [0,1]")
    if barrel > hard_hit + 1e-12:
        raise StatcastIntegrityError(
            f"{context}: barrel_rate {barrel:.12g} exceeds hard_hit_rate {hard_hit:.12g}"
        )


def validate_profile_and_rich_features(
    profile: Any,
    rich: Optional[Mapping[str, Any]],
    *,
    context: str,
) -> None:
    """Validate both profile values and the effective rich-feature overrides."""

    validate_rate_pair(
        getattr(profile, "barrel_rate", None),
        getattr(profile, "hard_hit_rate", None),
        context=f"{context}.statcast",
    )
    denominator = getattr(profile, "batted_ball_denominator", None)
    barrel_count = getattr(profile, "barrel_count", None)
    hard_hit_count = getattr(profile, "hard_hit_count", None)
    count_values = (denominator, barrel_count, hard_hit_count)
    if any(value is not None for value in count_values):
        if any(value is None for value in count_values):
            raise StatcastIntegrityError(
                f"{context}.statcast: partial batted-ball count lineage"
            )
        if any(not isinstance(value, int) or value < 0 for value in count_values):
            raise StatcastIntegrityError(
                f"{context}.statcast: invalid batted-ball count lineage"
            )
        if barrel_count > hard_hit_count or hard_hit_count > denominator:
            raise StatcastIntegrityError(
                f"{context}.statcast: impossible batted-ball count ordering"
            )
        if denominator == 0:
            raise StatcastIntegrityError(
                f"{context}.statcast: zero denominator cannot carry rates"
            )
        if not math.isclose(
            float(getattr(profile, "barrel_rate")),
            barrel_count / denominator,
            rel_tol=0.0,
            abs_tol=1e-12,
        ) or not math.isclose(
            float(getattr(profile, "hard_hit_rate")),
            hard_hit_count / denominator,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise StatcastIntegrityError(
                f"{context}.statcast: count/rate serialization mismatch"
            )
    distribution = getattr(profile, "distribution", None)
    if distribution is not None:
        validate_rate_pair(
            getattr(distribution, "barrel_rate", None),
            getattr(distribution, "hard_hit_rate", None),
            context=f"{context}.distribution",
        )
    if rich is None:
        return
    lineage = rich.get("_statcast_lineage")
    source_bound = getattr(profile, "source_status", "untracked_legacy") != "untracked_legacy"
    for field in RICH_STATCAST_PASSTHROUGH_FIELDS:
        if field not in rich or rich.get(field) is None:
            continue
        profile_value = getattr(profile, field, None)
        if profile_value is None:
            raise StatcastIntegrityError(
                f"{context}.effective_rich_features: {field} cannot replace missing source evidence"
            )
        try:
            equal = math.isclose(
                float(rich[field]), float(profile_value), rel_tol=0.0, abs_tol=1e-12
            )
        except (TypeError, ValueError) as exc:
            raise StatcastIntegrityError(
                f"{context}.effective_rich_features: {field} is not numeric"
            ) from exc
        if not equal:
            raise StatcastIntegrityError(
                f"{context}.effective_rich_features: unauthorized {field} override"
            )
        if not source_bound:
            continue
        if not isinstance(lineage, Mapping) or not isinstance(lineage.get(field), Mapping):
            raise StatcastIntegrityError(
                f"{context}.effective_rich_features: missing lineage for {field}"
            )
        entry = lineage[field]
        expected = {
            "source": "statcast_profile",
            "source_kind": getattr(profile, "source_kind", None),
            "source_status": getattr(profile, "source_status", None),
            "source_window_end": getattr(profile, "source_window_end", None),
            "source_row_count": getattr(profile, "source_row_count", None),
        }
        if any(entry.get(key) != value for key, value in expected.items()):
            raise StatcastIntegrityError(
                f"{context}.effective_rich_features: lineage mismatch for {field}"
            )
        if field in {"barrel_rate", "hard_hit_rate"}:
            count_expected = {
                "batted_ball_denominator": denominator,
                "barrel_count": barrel_count,
                "hard_hit_count": hard_hit_count,
                "batted_ball_rate_definition": getattr(
                    profile, "batted_ball_rate_definition", None
                ),
            }
            if any(entry.get(key) != value for key, value in count_expected.items()):
                raise StatcastIntegrityError(
                    f"{context}.effective_rich_features: count lineage mismatch for {field}"
                )
    effective_barrel = rich.get("barrel_rate", getattr(profile, "barrel_rate", None))
    effective_hard_hit = rich.get(
        "hard_hit_rate", getattr(profile, "hard_hit_rate", None)
    )
    validate_rate_pair(
        effective_barrel,
        effective_hard_hit,
        context=f"{context}.effective_rich_features",
    )
    rolling_values_present = any(
        (
            key.startswith("roll") or key.startswith("recent_pa_")
        )
        and not key.startswith("rolling_source_")
        and value not in (None, "", 0, 0.0)
        for key, value in rich.items()
    )
    rolling_lineage = rich.get("_rolling_lineage")
    if source_bound and rolling_values_present:
        if not isinstance(rolling_lineage, Mapping):
            raise StatcastIntegrityError(
                f"{context}.effective_rich_features: missing rolling lineage"
            )
        validate_rolling_lineage(
            rolling_lineage,
            context=f"{context}.effective_rich_features",
        )
