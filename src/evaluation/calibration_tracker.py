"""
Probability calibration tracking — Brier score and reliability bins per category.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.evaluation.backtest_engine import OutcomeRecord
from src.models.dataclasses import PropProjection
from src.models.total_bases_contract import over_threshold_for_half_point_line


@dataclass
class CalibrationBin:
    bin_center: float
    predicted_mean: float
    observed_rate: float
    count: int


@dataclass
class CalibrationReport:
    category: str
    brier_score: float
    expected_calibration_error: float
    bins: list[CalibrationBin] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "brier_score": self.brier_score,
            "expected_calibration_error": self.expected_calibration_error,
            "bins": [
                {
                    "bin_center": b.bin_center,
                    "predicted_mean": b.predicted_mean,
                    "observed_rate": b.observed_rate,
                    "count": b.count,
                }
                for b in self.bins
            ],
        }


class CalibrationTracker:
    """Track over/under probability calibration using simulation tail probabilities."""

    def evaluate_category(
        self,
        projections: list[PropProjection],
        outcomes: list[OutcomeRecord],
        category: str,
        threshold: float,
        n_bins: int = 10,
    ) -> CalibrationReport:
        try:
            event_threshold = over_threshold_for_half_point_line(threshold)
        except ValueError as exc:
            raise ValueError(
                "CalibrationTracker evaluates binary half-point markets only; "
                "integer/push lines need a separate settlement contract"
            ) from exc

        outcome_index = {
            (o.player_id, o.game_date, o.category): o.actual_value for o in outcomes
        }

        probs: list[float] = []
        observed: list[float] = []

        for proj in projections:
            if proj.category != category:
                continue
            key = (proj.player_id, proj.game_date, proj.category)
            actual = outcome_index.get(key)
            if actual is None or proj.simulation is None:
                continue

            model_prob = proj.simulation.p_ge_threshold.get(event_threshold)
            if model_prob is None:
                raise ValueError(
                    f"{category} projection for player {proj.player_id} on "
                    f"{proj.game_date} lacks exact P(actual >= {event_threshold:g}) "
                    f"for market line {threshold}; refusing a point-estimate fallback"
                )

            probs.append(float(model_prob))
            observed.append(1.0 if actual >= event_threshold else 0.0)

        if not probs:
            return CalibrationReport(category=category, brier_score=0.0, expected_calibration_error=0.0)

        prob_arr = np.array(probs)
        obs_arr = np.array(observed)
        brier = float(np.mean((prob_arr - obs_arr) ** 2))

        bins = self._reliability_bins(prob_arr, obs_arr, n_bins)
        ece = self._expected_calibration_error(bins, len(probs))

        return CalibrationReport(
            category=category,
            brier_score=round(brier, 4),
            expected_calibration_error=round(ece, 4),
            bins=bins,
        )

    @staticmethod
    def _reliability_bins(
        probs: np.ndarray,
        observed: np.ndarray,
        n_bins: int,
    ) -> list[CalibrationBin]:
        edges = np.linspace(0, 1, n_bins + 1)
        bins: list[CalibrationBin] = []
        for i in range(n_bins):
            mask = (probs >= edges[i]) & (probs < edges[i + 1])
            if not mask.any():
                continue
            bins.append(
                CalibrationBin(
                    bin_center=round((edges[i] + edges[i + 1]) / 2, 3),
                    predicted_mean=round(float(probs[mask].mean()), 4),
                    observed_rate=round(float(observed[mask].mean()), 4),
                    count=int(mask.sum()),
                )
            )
        return bins

    @staticmethod
    def _expected_calibration_error(bins: list[CalibrationBin], total: int) -> float:
        if total == 0:
            return 0.0
        ece = 0.0
        for b in bins:
            weight = b.count / total
            ece += weight * abs(b.predicted_mean - b.observed_rate)
        return ece
