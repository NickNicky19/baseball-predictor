"""
Contextual and situational feature engineering.

Includes platoon, park, weather, leverage, lineup position, and basic BvP/recent form signals.
These features add important context beyond raw player ability.
"""

from __future__ import annotations

from typing import Any

from src.features.ml.base import BaseFeatureEngineer


class ContextFeatureEngineer(BaseFeatureEngineer):
    """
    Generates contextual features for hitter props.
    """

    def __init__(self):
        super().__init__(name="context")

    def compute(self, data: dict[str, Any]) -> dict[str, Any]:
        features: dict[str, Any] = {}

        # Platoon / Handedness
        features["platoon_advantage"] = data.get("platoon_advantage", 0.0)
        features["is_platoon_matchup"] = int(data.get("platoon_advantage", 0) != 0)

        # Park Factors
        features["park_hits_factor"] = data.get("park_hits_factor", 1.0)
        features["park_hr_factor"] = data.get("park_hr_factor", 1.0)
        features["park_runs_factor"] = data.get("park_runs_factor", 1.0)

        # Weather
        features["weather_temp"] = data.get("weather_temp")
        features["weather_wind_speed"] = data.get("weather_wind_speed")
        features["weather_wind_direction"] = data.get("weather_wind_direction")
        features["weather_humidity"] = data.get("weather_humidity")
        features["weather_hr_factor"] = data.get("weather_hr_factor", 1.0)

        # Count & Leverage
        features["balls"] = data.get("balls", 0)
        features["strikes"] = data.get("strikes", 0)
        features["leverage_index"] = data.get("leverage_index")
        features["is_high_leverage"] = int(data.get("leverage_index", 1.0) > 1.5)

        # Lineup Position
        features["lineup_slot"] = data.get("lineup_slot")
        features["is_top_of_order"] = int(data.get("lineup_slot", 9) <= 3)
        features["is_middle_order"] = int(4 <= data.get("lineup_slot", 9) <= 6)

        # Home/Away
        features["is_home"] = int(data.get("is_home", False))

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
            "weather_humidity", "weather_hr_factor",
            "balls", "strikes", "leverage_index", "is_high_leverage",
            "lineup_slot", "is_top_of_order", "is_middle_order",
            "is_home",
            "bvp_ops_factor", "bvp_hr_factor", "recent_form_mult"
        ]
