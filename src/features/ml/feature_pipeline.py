"""
Feature Pipeline - combines multiple feature engineers.

This is the main interface for generating rich feature sets.
It will support both the current system and future ML models.
"""

from __future__ import annotations

from typing import Any

from src.features.ml.base import BaseFeatureEngineer


class FeaturePipelineError(RuntimeError):
    """Raised when a feature block cannot be produced truthfully."""


class FeaturePipeline:
    """
    Combines multiple BaseFeatureEngineer classes into one clean interface.
    """

    def __init__(self, engineers: list[BaseFeatureEngineer] | None = None):
        self.engineers = engineers or []

    def add_engineer(self, engineer: BaseFeatureEngineer) -> None:
        self.engineers.append(engineer)

    def compute(self, data: dict[str, Any]) -> dict[str, Any]:
        """Run all engineers and merge results, failing closed on any defect.

        Returning partial features makes downstream fallback indistinguishable
        from legitimate missingness. Duplicate ownership is also rejected so
        update order can never decide which value reaches a probability.
        """
        all_features: dict[str, Any] = {}
        for engineer in self.engineers:
            try:
                features = engineer.compute(data)
            except Exception as e:
                raise FeaturePipelineError(
                    f"feature engineer {engineer.name!r} failed; no partial "
                    "feature set was returned"
                ) from e
            if not isinstance(features, dict):
                raise FeaturePipelineError(
                    f"feature engineer {engineer.name!r} returned "
                    f"{type(features).__name__}, expected dict"
                )
            overlap = sorted(set(all_features).intersection(features))
            if overlap:
                raise FeaturePipelineError(
                    f"duplicate feature ownership from {engineer.name!r}: {overlap}"
                )
            all_features.update(features)
        return all_features

    def get_all_feature_names(self) -> list[str]:
        names: list[str] = []
        for engineer in self.engineers:
            names.extend(engineer.get_feature_names())
        return list(dict.fromkeys(names))  # remove duplicates, preserve order

    def __repr__(self) -> str:
        return f"FeaturePipeline(engineers={[e.name for e in self.engineers]})"
