"""
Base classes and utilities for the feature engineering layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FeatureBundle:
    """Container for a player's features at a point in time."""
    player_id: str
    game_date: str
    features: dict[str, float | int | str | None]


class BaseFeatureEngineer:
    """Base class for all feature engineers."""

    def __init__(self, name: str):
        self.name = name

    def compute(self, data: dict) -> dict[str, float | int | str | None]:
        """Compute features. Override this method in subclasses."""
        raise NotImplementedError

    def get_feature_names(self) -> list[str]:
        """Return list of feature names this engineer produces."""
        raise NotImplementedError
