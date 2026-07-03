"""
Base classes for the modular feature engineering layer (src/features/ml/).

This is part of Phase 1: Rich Feature Engineering.
Designed to support high-quality, composable features for hitter and pitcher props,
with future compatibility for gradient boosting models (CatBoost / LightGBM).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class FeatureSet:
    """
    Container for computed features for one player/game.
    Easy to convert into pandas DataFrame or model input.
    """
    player_id: int
    game_date: str
    features: dict[str, Any]


class BaseFeatureEngineer(ABC):
    """
    Abstract base class for feature engineers.

    Each subclass handles one logical group of features
    (e.g. Statcast metrics, contextual features, rolling form).
    This promotes modularity and clean separation.
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def compute(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Compute features from input data.

        Args:
            data: Raw input dictionary (can contain Statcast, context, historical stats, etc.)

        Returns:
            Dictionary of feature_name -> value
        """
        pass

    @abstractmethod
    def get_feature_names(self) -> list[str]:
        """Return list of features this engineer produces."""
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"
