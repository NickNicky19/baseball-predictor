"""One validated effective PA context shared by explicit and sampled paths."""
from __future__ import annotations

from dataclasses import dataclass
import math

from src.models.dataclasses import LeagueBaselines, PlayerFeatureBundle


class SimulationInputContractError(ValueError):
    """A bundle cannot truthfully produce one shared simulation context."""


@dataclass(frozen=True)
class EffectivePAContext:
    pitcher_k_pct: float
    pitcher_bb_pct: float
    pitcher_hr_per_9: float | None
    park_hr_factor: float
    park_hits_factor: float
    weather_hr_factor: float
    umpire_k_bias: float
    handedness_advantage: float
    recent_form_mult: float
    bvp_ops_factor: float
    bvp_hr_factor: float


def effective_pa_context(
    bundle: PlayerFeatureBundle, league: LeagueBaselines
) -> EffectivePAContext:
    """Resolve the exact legacy-comparator context once for both consumers."""
    pitcher_k = _finite(league.k_pct, "league pitcher K%")
    pitcher_bb = _finite(league.bb_pct, "league pitcher BB%")
    pitcher_hr_per_9 = None
    pitcher = bundle.pitcher_statcast
    if pitcher is not None:
        if pitcher.k_rate is not None:
            pitcher_k = 100.0 * _fraction(pitcher.k_rate, "pitcher k_rate")
        if pitcher.bb_rate is not None:
            pitcher_bb = 100.0 * _fraction(pitcher.bb_rate, "pitcher bb_rate")
        if pitcher.hr_per_9 is not None:
            pitcher_hr_per_9 = _nonnegative(
                pitcher.hr_per_9, "pitcher hr_per_9"
            )

    weather_hr = _positive(
        bundle.metadata.get("weather_hr_factor", 1.0), "weather_hr_factor"
    )
    umpire_k_bias = _finite(
        bundle.umpire.k_bias if bundle.umpire else 0.0, "umpire_k_bias"
    )
    handedness = _finite(
        bundle.matchup.platoon_advantage, "handedness_advantage"
    )

    # These are the pre-existing frozen-comparator bounds. Centralization fixes
    # representation drift; it neither endorses nor retunes the hand-specified
    # effects, which remain excluded from the fitted batter-only challenger.
    bvp_ops = _bounded(bundle.matchup.bvp_ops_factor, 0.80, 1.25, "bvp_ops_factor")
    bvp_hr = _bounded(bundle.matchup.bvp_hr_factor, 0.70, 1.40, "bvp_hr_factor")
    recent_form = _bounded(
        bundle.matchup.recent_form_multiplier,
        0.85,
        1.18,
        "recent_form_multiplier",
    )

    return EffectivePAContext(
        pitcher_k_pct=pitcher_k,
        pitcher_bb_pct=pitcher_bb,
        pitcher_hr_per_9=pitcher_hr_per_9,
        park_hr_factor=_positive(bundle.park.hr_factor, "park_hr_factor"),
        park_hits_factor=_positive(bundle.park.hits_factor, "park_hits_factor"),
        weather_hr_factor=weather_hr,
        umpire_k_bias=umpire_k_bias,
        handedness_advantage=handedness,
        recent_form_mult=recent_form,
        bvp_ops_factor=bvp_ops,
        bvp_hr_factor=bvp_hr,
    )


def _finite(value, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SimulationInputContractError(f"{label} is not numeric") from exc
    if not math.isfinite(parsed):
        raise SimulationInputContractError(f"{label} is non-finite")
    return parsed


def _positive(value, label: str) -> float:
    parsed = _finite(value, label)
    if parsed <= 0.0:
        raise SimulationInputContractError(f"{label} must be positive")
    return parsed


def _nonnegative(value, label: str) -> float:
    parsed = _finite(value, label)
    if parsed < 0.0:
        raise SimulationInputContractError(f"{label} must be nonnegative")
    return parsed


def _fraction(value, label: str) -> float:
    parsed = _finite(value, label)
    if not 0.0 <= parsed <= 1.0:
        raise SimulationInputContractError(f"{label} must be in [0,1]")
    return parsed


def _bounded(value, lower: float, upper: float, label: str) -> float:
    parsed = _finite(value, label)
    if not lower <= parsed <= upper:
        raise SimulationInputContractError(
            f"{label} must be within the frozen comparator bounds "
            f"[{lower},{upper}], got {parsed}"
        )
    return parsed
