"""
Backtest engine for projection and simulation evaluation.

Designed to run independently of data fetching and the GUI. Accepts
predictions plus observed outcomes (or runs PropEngine on feature bundles)
and returns structured error metrics per prop category.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

import numpy as np

from src.models.dataclasses import PropCategory, PropProjection, PlayerFeatureBundle
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from src.prediction.prop_engine import PropEngine

logger = get_logger(__name__)


@dataclass(frozen=True)
class OutcomeRecord:
    """Observed result for a single player-game prop."""

    player_id: int
    player_name: str
    game_date: str
    category: PropCategory
    actual_value: float
    # Optional for generic/live objects only. Historical evaluation refuses a
    # missing value before it can collapse a doubleheader by player/date.
    mlb_game_pk: Optional[int] = None


@dataclass(frozen=True)
class BacktestMetrics:
    """Error summary for one prop category."""

    category: PropCategory
    n_samples: int
    mae: float
    rmse: float
    mean_error: float
    median_error: float
    mean_abs_pct_error: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "n_samples": self.n_samples,
            "mae": self.mae,
            "rmse": self.rmse,
            "mean_error": self.mean_error,
            "median_error": self.median_error,
            "mean_abs_pct_error": self.mean_abs_pct_error,
        }


@dataclass
class BacktestReport:
    """Full backtest output across categories."""

    metrics_by_category: dict[PropCategory, BacktestMetrics] = field(default_factory=dict)
    matched_pairs: int = 0
    unmatched_predictions: int = 0
    unmatched_outcomes: int = 0
    notes: str = ""

    @property
    def weighted_mae(self) -> float:
        if not self.metrics_by_category:
            return 0.0
        total_n = sum(m.n_samples for m in self.metrics_by_category.values())
        if total_n == 0:
            return 0.0
        return sum(m.mae * m.n_samples for m in self.metrics_by_category.values()) / total_n

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched_pairs": self.matched_pairs,
            "unmatched_predictions": self.unmatched_predictions,
            "unmatched_outcomes": self.unmatched_outcomes,
            "weighted_mae": round(self.weighted_mae, 4),
            "metrics": {k: v.to_dict() for k, v in self.metrics_by_category.items()},
            "notes": self.notes,
        }


class BacktestEngine:
    """
    Evaluates prediction quality against observed outcomes.

    All scoring logic is deterministic and side-effect free so it can be
    unit-tested with synthetic PropProjection / OutcomeRecord pairs.
    """

    CATEGORY_WEIGHTS: dict[PropCategory, float] = {
        "hits": 0.30,
        "hrr": 0.45,
        "home_runs": 0.10,
        "fantasy": 0.15,
        "strikeouts": 0.20,
    }
    # Candidate-only categories have their own gates. They may be reported
    # individually, but must never enter an aggregate score by a silent
    # fallback weight that could hide a losing market.
    CANDIDATE_ONLY_CATEGORIES = frozenset({"total_bases"})

    def evaluate_predictions(
        self,
        projections: list[PropProjection],
        outcomes: list[OutcomeRecord],
        categories: Optional[tuple[PropCategory, ...]] = None,
    ) -> BacktestReport:
        """Match predictions to outcomes and compute per-category metrics."""
        self._require_historical_identity(projections, outcomes)
        outcome_index = {
            (o.mlb_game_pk, o.player_id, o.category): o.actual_value for o in outcomes
        }
        pred_index = {
            (p.mlb_game_pk, p.player_id, p.category): p.projected_value for p in projections
        }

        cats = categories or tuple(self._unique_categories(projections, outcomes))
        metrics: dict[PropCategory, BacktestMetrics] = {}
        matched = 0

        for category in cats:
            pairs = self._collect_pairs(category, pred_index, outcome_index)
            matched += len(pairs)
            metrics[category] = self._compute_metrics(category, pairs)

        unmatched_preds = len(pred_index) - matched
        unmatched_outs = len(outcome_index) - matched

        return BacktestReport(
            metrics_by_category=metrics,
            matched_pairs=matched,
            unmatched_predictions=max(0, unmatched_preds),
            unmatched_outcomes=max(0, unmatched_outs),
            notes=f"Evaluated {matched} matched prediction-outcome pairs.",
        )

    def evaluate_bundles(
        self,
        bundles: list[PlayerFeatureBundle],
        outcomes: list[OutcomeRecord],
        prop_engine: PropEngine,
        categories: Optional[tuple[PropCategory, ...]] = None,
    ) -> BacktestReport:
        """Generate projections with PropEngine, then evaluate against outcomes."""
        projections: list[PropProjection] = []
        for bundle in bundles:
            projections.extend(prop_engine.project_hitter(bundle, categories=categories))
        return self.evaluate_predictions(projections, outcomes, categories=categories)

    def evaluate_component(
        self,
        name: str,
        predict_fn: Callable[[PlayerFeatureBundle], list[PropProjection]],
        bundles: list[PlayerFeatureBundle],
        outcomes: list[OutcomeRecord],
    ) -> BacktestReport:
        """
        Backtest an arbitrary prediction component in isolation.

        predict_fn should accept a PlayerFeatureBundle and return projections.
        """
        projections: list[PropProjection] = []
        for bundle in bundles:
            projections.extend(predict_fn(bundle))
        report = self.evaluate_predictions(projections, outcomes)
        report.notes = f"Component '{name}': {report.notes}"
        return report

    def combined_score(self, report: BacktestReport) -> float:
        """Weighted MAE across categories (lower is better)."""
        candidate_only = sorted(
            set(report.metrics_by_category) & self.CANDIDATE_ONLY_CATEGORIES
        )
        if candidate_only:
            raise ValueError(
                "Aggregate BacktestEngine score cannot include unpromoted candidate "
                f"market(s) {candidate_only}; report their gate separately."
            )
        score = 0.0
        weight_sum = 0.0
        for category, metrics in report.metrics_by_category.items():
            w = self.CATEGORY_WEIGHTS.get(category, 0.10)
            if metrics.n_samples > 0:
                score += metrics.mae * w
                weight_sum += w
        return score / weight_sum if weight_sum else 0.0

    def compare_reports(self, baseline: BacktestReport, candidate: BacktestReport) -> dict[str, Any]:
        """Compare two backtest reports; positive improvement = lower MAE."""
        comparison: dict[str, Any] = {"categories": {}, "overall_improved": False}
        for category in set(baseline.metrics_by_category) | set(candidate.metrics_by_category):
            b = baseline.metrics_by_category.get(category)
            c = candidate.metrics_by_category.get(category)
            if not b or not c:
                continue
            comparison["categories"][category] = {
                "baseline_mae": b.mae,
                "candidate_mae": c.mae,
                "mae_delta": round(c.mae - b.mae, 4),
                "bias_delta": round(c.mean_error - b.mean_error, 4),
            }
        comparison["baseline_score"] = round(self.combined_score(baseline), 4)
        comparison["candidate_score"] = round(self.combined_score(candidate), 4)
        comparison["overall_improved"] = comparison["candidate_score"] < comparison["baseline_score"]
        return comparison

    @staticmethod
    def _collect_pairs(
        category: PropCategory,
        pred_index: dict[tuple[int, int, PropCategory], float],
        outcome_index: dict[tuple[int, int, PropCategory], float],
    ) -> list[tuple[float, float]]:
        pairs: list[tuple[float, float]] = []
        for key, predicted in pred_index.items():
            if key[2] != category:
                continue
            actual = outcome_index.get(key)
            if actual is not None:
                pairs.append((predicted, actual))
        return pairs

    @staticmethod
    def _require_historical_identity(
        projections: list[PropProjection], outcomes: list[OutcomeRecord]
    ) -> None:
        """Keep generic dataclasses backward-compatible, never historical joins."""
        missing_predictions = [p for p in projections if p.mlb_game_pk is None]
        missing_outcomes = [o for o in outcomes if o.mlb_game_pk is None]
        if missing_predictions or missing_outcomes:
            raise ValueError(
                "Historical BacktestEngine scoring requires non-null mlb_game_pk "
                f"(missing projections={len(missing_predictions)}, "
                f"outcomes={len(missing_outcomes)})."
            )
        for label, rows in (("projections", projections), ("outcomes", outcomes)):
            keys = [(row.mlb_game_pk, row.player_id, row.category) for row in rows]
            if len(keys) != len(set(keys)):
                raise ValueError(
                    f"Historical BacktestEngine scoring requires unique "
                    f"(mlb_game_pk, player_id, category) {label}; refusing to "
                    "silently overwrite duplicate records."
                )

    @staticmethod
    def _compute_metrics(category: PropCategory, pairs: list[tuple[float, float]]) -> BacktestMetrics:
        if not pairs:
            return BacktestMetrics(
                category=category,
                n_samples=0,
                mae=0.0,
                rmse=0.0,
                mean_error=0.0,
                median_error=0.0,
                mean_abs_pct_error=0.0,
            )

        predicted = np.array([p for p, _ in pairs], dtype=float)
        actual = np.array([a for _, a in pairs], dtype=float)
        errors = predicted - actual

        abs_pct = np.abs(errors) / np.maximum(np.abs(actual), 0.25)

        return BacktestMetrics(
            category=category,
            n_samples=len(pairs),
            mae=float(np.mean(np.abs(errors))),
            rmse=float(np.sqrt(np.mean(errors**2))),
            mean_error=float(np.mean(errors)),
            median_error=float(np.median(errors)),
            mean_abs_pct_error=float(np.mean(abs_pct)),
        )

    @staticmethod
    def _unique_categories(
        projections: list[PropProjection],
        outcomes: list[OutcomeRecord],
    ) -> list[PropCategory]:
        cats = {p.category for p in projections} | {o.category for o in outcomes}
        return sorted(cats, key=str)
