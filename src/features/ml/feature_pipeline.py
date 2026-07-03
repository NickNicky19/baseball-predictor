"""
Feature Pipeline - combines multiple feature engineers.

This is the main interface for generating rich feature sets.
It will support both the current system and future ML models.
"""

from __future__ import annotations

from typing import Any

from src.features.ml.base import BaseFeatureEngineer


class FeaturePipeline:
    """
    Combines multiple BaseFeatureEngineer classes into one clean interface.
    """

    def __init__(self, engineers: list[BaseFeatureEngineer] | None = None):
        self.engineers = engineers or []

    def add_engineer(self, engineer: BaseFeatureEngineer) -> None:
        self.engineers.append(engineer)

    def compute(self, data: dict[str, Any]) -> dict[str, Any]:
        """Run all engineers and merge results."""
        all_features: dict[str, Any] = {}
        for engineer in self.engineers:
            try:
                features = engineer.compute(data)
                all_features.update(features)
            except Exception as e:
                print(f"[FeaturePipeline] Warning: {engineer.name} failed → {e}")
        return all_features

    def get_all_feature_names(self) -> list[str]:
        names: list[str] = []
        for engineer in self.engineers:
            names.extend(engineer.get_feature_names())
        return list(dict.fromkeys(names))  # remove duplicates, preserve order

    def __repr__(self) -> str:
        return f"FeaturePipeline(engineers={[e.name for e in self.engineers]})"
