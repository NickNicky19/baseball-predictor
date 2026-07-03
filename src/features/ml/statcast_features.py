"""
Statcast feature engineering for hitter props.

Focuses on high-signal batted ball and expected metrics.
These are among the strongest predictors for Hits, Home Runs, and offensive production.
"""

from __future__ import annotations

from typing import Any

from src.features.ml.base import BaseFeatureEngineer


class StatcastFeatureEngineer(BaseFeatureEngineer):
    """
    Generates rich Statcast-derived features.

    Prioritizes:
    - Exit Velocity, Launch Angle, Barrel %, Hard Hit %
    - Expected metrics (xwOBA, xSLG, xBA)
    - Contact and swing quality metrics
    """

    def __init__(self):
        super().__init__(name="statcast")

    def compute(self, data: dict[str, Any]) -> dict[str, Any]:
        features: dict[str, Any] = {}

        # Batted ball quality (core predictors)
        features["exit_velocity"] = data.get("exit_velocity")
        features["launch_angle"] = data.get("launch_angle")
        features["barrel_rate"] = data.get("barrel_rate")
        features["hard_hit_rate"] = data.get("hard_hit_rate")
        features["sweet_spot_rate"] = data.get("sweet_spot_rate")

        # Expected metrics (very strong)
        features["xwoba"] = data.get("xwoba")
        features["xslg"] = data.get("xslg")
        features["xba"] = data.get("xba")
        features["xoba"] = data.get("xoba")

        # Aggregated quality
        features["avg_exit_velocity"] = data.get("avg_exit_velocity")
        features["avg_launch_angle"] = data.get("avg_launch_angle")

        # Swing & contact profile
        features["chase_rate"] = data.get("chase_rate")
        features["contact_rate"] = data.get("contact_rate")
        features["whiff_rate"] = data.get("whiff_rate")
        features["swing_rate"] = data.get("swing_rate")
        features["zone_rate"] = data.get("zone_rate")

        # Rate stats
        features["k_rate"] = data.get("k_rate")
        features["bb_rate"] = data.get("bb_rate")

        # Reliability
        features["statcast_sample_pa"] = data.get("sample_pa", 0)
        features["has_advanced_data"] = bool(
            data.get("xwoba") is not None or data.get("barrel_rate") is not None
        )

        return features

    def get_feature_names(self) -> list[str]:
        return [
            "exit_velocity", "launch_angle", "barrel_rate", "hard_hit_rate",
            "sweet_spot_rate", "xwoba", "xslg", "xba", "xoba",
            "avg_exit_velocity", "avg_launch_angle",
            "chase_rate", "contact_rate", "whiff_rate", "swing_rate", "zone_rate",
            "k_rate", "bb_rate", "statcast_sample_pa", "has_advanced_data"
        ]
