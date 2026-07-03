"""
src/features/ml/ - New modular feature engineering layer

This package focuses on rich, high-signal features (especially Statcast)
for hitter and pitcher props. It is designed to support the transition
toward gradient boosting models while remaining modular and backtestable.
"""

from src.features.ml.base import BaseFeatureEngineer, FeatureSet
from src.features.ml.statcast_features import StatcastFeatureEngineer
from src.features.ml.context_features import ContextFeatureEngineer
from src.features.ml.rolling_features import RollingFeatureEngineer
from src.features.ml.feature_pipeline import FeaturePipeline

__all__ = [
    "BaseFeatureEngineer",
    "FeatureSet",
    "StatcastFeatureEngineer",
    "ContextFeatureEngineer",
    "RollingFeatureEngineer",
    "FeaturePipeline",
]
