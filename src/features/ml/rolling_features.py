"""
Rolling and recent form feature engineering.

Captures recent performance trends using rolling windows.
Recent form is one of the strongest short-term signals for props.
"""

from __future__ import annotations

from typing import Any

from src.features.ml.base import BaseFeatureEngineer


class RollingFeatureEngineer(BaseFeatureEngineer):
    """
    Generates rolling Statcast and rate features + form signals.
    """

    def __init__(self):
        super().__init__(name="rolling")

    def compute(self, data: dict[str, Any]) -> dict[str, Any]:
        features: dict[str, Any] = {}

        # Rolling Statcast metrics
        features["roll5_xwoba"] = data.get("roll5_xwoba")
        features["roll10_xwoba"] = data.get("roll10_xwoba")
        features["roll15_xwoba"] = data.get("roll15_xwoba")
        features["roll30_xwoba"] = data.get("roll30_xwoba")

        features["roll5_xslg"] = data.get("roll5_xslg")
        features["roll10_xslg"] = data.get("roll10_xslg")

        features["roll5_barrel_rate"] = data.get("roll5_barrel_rate")
        features["roll10_hard_hit_rate"] = data.get("roll10_hard_hit_rate")

        features["roll5_avg_exit_velocity"] = data.get("roll5_avg_exit_velocity")
        features["roll10_avg_exit_velocity"] = data.get("roll10_avg_exit_velocity")

        # Recent rate stats
        features["roll15_k_rate"] = data.get("roll15_k_rate")
        features["roll15_bb_rate"] = data.get("roll15_bb_rate")
        features["roll15_babip"] = data.get("roll15_babip")

        # Form deviation (hot/cold signals)
        features["xwoba_vs_season"] = data.get("xwoba_vs_season")
        features["barrel_vs_season"] = data.get("barrel_vs_season")

        # Sample size for recent windows
        features["recent_pa_15"] = data.get("recent_pa_15", 0)
        features["recent_pa_30"] = data.get("recent_pa_30", 0)

        return features

    def get_feature_names(self) -> list[str]:
        return [
            "roll5_xwoba", "roll10_xwoba", "roll15_xwoba", "roll30_xwoba",
            "roll5_xslg", "roll10_xslg",
            "roll5_barrel_rate", "roll10_hard_hit_rate",
            "roll5_avg_exit_velocity", "roll10_avg_exit_velocity",
            "roll15_k_rate", "roll15_bb_rate", "roll15_babip",
            "xwoba_vs_season", "barrel_vs_season",
            "recent_pa_15", "recent_pa_30"
        ]
