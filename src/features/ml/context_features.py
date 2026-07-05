"""
Contextual and situational feature engineering.

Includes platoon, park, weather, leverage, lineup position, and basic
BvP/recent-form signals.

FIXED (this revision): derived flags (is_high_leverage, is_top_of_order,
is_middle_order, is_home) crashed with TypeError when their source value was
present-but-None; FeaturePipeline's try/except then silently discarded the
ENTIRE context feature set. All derivations are now None-safe.
"""

from __future__ import annotations

from typing import Any, Optional

from src.features.ml.base import BaseFeatureEngineer


def _flag(value: Optional[Any], predicate) -> Optional[int]:
    """Apply predicate to a value, returning None when the value is unknown."""
    if value is None:
        return None
    try:
        return int(bool(predicate(value)))
    except (TypeError, ValueError):
        return None


class ContextFeatureEngineer(BaseFeatureEngineer):
    """Generates contextual features for hitter props."""

    def __init__(self):
        super().__init__(name="context")

    def compute(self, data: dict[str, Any]) -> dict[str, Any]:
        features: dict[str, Any] = {}

        # Platoon / Handedness
        platoon = data.get("platoon_advantage")
        features["platoon_advantage"] = platoon if platoon is not None else 0.0
        features["is_platoon_matchup"] = _flag(platoon, lambda v: v != 0)

        # Park Factors
        features["park_hits_factor"] = data.get("park_hits_factor", 1.0)
        features["park_hr_factor"] = data.get("park_hr_factor", 1.0)
        features["park_runs_factor"] = data.get("park_runs_factor", 1.0)

        # Weather
        features["weather_temp"] = data.get("weather_temp")
        features["weather_wind_speed"] = data.get("weather_wind_speed")
        features["weather_wind_direction"] = data.get("weather_wind_direction")
        features["weather_is_dome"] = data.get("weather_is_dome")
        features["weather_hr_factor"] = data.get("weather_hr_factor", 1.0)

        # Count & Leverage
        features["balls"] = data.get("balls")
        features["strikes"] = data.get("strikes")
        leverage = data.get("leverage_index")
        features["leverage_index"] = leverage
        features["is_high_leverage"] = _flag(leverage, lambda v: v > 1.5)

        # Lineup Position
        slot = data.get("lineup_slot")
        features["lineup_slot"] = slot
        features["is_top_of_order"] = _flag(slot, lambda v: v <= 3)
        features["is_middle_order"] = _flag(slot, lambda v: 4 <= v <= 6)

        # Home/Away
        features["is_home"] = _flag(data.get("is_home"), bool)

        # Umpire
        features["umpire_k_bias"] = data.get("umpire_k_bias")

        # Basic BvP / Form signals
        features["bvp_ops_factor"] = data.get("bvp_ops_factor", 1.0)
        features["bvp_hr_factor"] = data.get("bvp_hr_factor", 1.0)
        features["recent_form_mult"] = data.get("recent_form_mult", 1.0)

        return features

    def get_feature_names(self) -> list[str]:
        return [
            "platoon_advantage", "is_platoon_matchup",
            "park_hits_factor", "park_hr_factor", "park_runs_factor",
            "weather_temp", "weather_wind_speed", "weather_wind_direction",
            "weather_is_dome", "weather_hr_factor",
            "balls", "strikes", "leverage_index", "is_high_leverage",
            "lineup_slot", "is_top_of_order", "is_middle_order",
            "is_home", "umpire_k_bias",
            "bvp_ops_factor", "bvp_hr_factor", "recent_form_mult",
        ]

