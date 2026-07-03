# src/features/rich_feature_enricher.py

from src.features.ml.feature_pipeline import FeaturePipeline
from src.features.ml.statcast_features import StatcastFeatureEngineer
from src.features.ml.context_features import ContextFeatureEngineer
from src.features.ml.rolling_features import RollingFeatureEngineer
from src.models.dataclasses import StatcastProfile


class RichFeatureEnricher:
    """
    Enriches existing player data + StatcastProfile with rich features
    from the new modular feature layer (src/features/ml/).
    
    This is additive — it does not modify your current StatcastProfile.
    """

    def __init__(self):
        self.pipeline = FeaturePipeline([
            StatcastFeatureEngineer(),
            ContextFeatureEngineer(),
            RollingFeatureEngineer()
        ])

    def enrich(self, data: dict, profile: StatcastProfile) -> dict:
        """
        Returns a dictionary of rich features.
        You can later decide to merge this into StatcastProfile or keep it separate.
        """
        input_data = {
            **data,
            "xwoba": profile.xwoba,
            "xslg": profile.xslg,
            "barrel_rate": profile.barrel_rate,
            "hard_hit_rate": profile.hard_hit_rate,
            "avg_exit_velocity": profile.avg_exit_velocity,
            "avg_launch_angle": profile.avg_launch_angle,
            # Add more fields from the profile as needed
        }

        rich_features = self.pipeline.compute(input_data)
        return rich_features
