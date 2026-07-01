"""
Walk-forward validation — rolling time-series backtests without lookahead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Optional

import pandas as pd

from src.evaluation.backtest_engine import BacktestEngine, BacktestReport, OutcomeRecord
from src.models.dataclasses import PropProjection
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class WalkForwardFold:
    train_end: str
    test_start: str
    test_end: str
    report: BacktestReport
    combined_score: float


@dataclass
class WalkForwardReport:
    folds: list[WalkForwardFold] = field(default_factory=list)
    mean_combined_score: float = 0.0
    mean_weighted_mae: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "folds": [
                {
                    "train_end": f.train_end,
                    "test_start": f.test_start,
                    "test_end": f.test_end,
                    "combined_score": f.combined_score,
                    "weighted_mae": f.report.weighted_mae,
                    "metrics": {k: v.to_dict() for k, v in f.report.metrics_by_category.items()},
                }
                for f in self.folds
            ],
            "mean_combined_score": self.mean_combined_score,
            "mean_weighted_mae": self.mean_weighted_mae,
            "notes": self.notes,
        }


class WalkForwardValidator:
    """
    Evaluate predictions with expanding/rolling walk-forward folds.

    Default fold count aligns with config ``validation.walk_forward_folds`` (caller-supplied).
    """

    def __init__(self, backtest_engine: Optional[BacktestEngine] = None):
        self.backtest = backtest_engine or BacktestEngine()

    def validate(
        self,
        pairs: pd.DataFrame,
        predict_fn: Callable[[pd.DataFrame], list[PropProjection]],
        n_folds: int = 5,
        test_window_days: int = 7,
        min_fold_samples: int = 20,
        segments: Optional[dict[str, pd.Series]] = None,
    ) -> WalkForwardReport:
        """
        Run walk-forward validation on pairs with game_date column.

        predict_fn receives training pairs and must return projections for test window.
        segments: optional column → mask for segmented evaluation metadata.
        """
        if pairs.empty or "game_date" not in pairs.columns:
            logger.debug("Walk-forward skipped: empty pairs or missing game_date column")
            return WalkForwardReport(notes="No pairs with game_date for walk-forward.")

        df = pairs.copy()
        df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
        df = df.dropna(subset=["game_date"]).sort_values("game_date")
        if df.empty:
            logger.debug("Walk-forward skipped: no parseable game_date values")
            return WalkForwardReport(notes="Invalid game_date values.")

        dates = sorted(df["game_date"].dt.date.unique())
        if len(dates) < n_folds + 1:
            n_folds = max(1, len(dates) - 1)

        folds: list[WalkForwardFold] = []
        step = max(1, len(dates) // (n_folds + 1))

        for i in range(1, n_folds + 1):
            split_idx = min(i * step, len(dates) - 1)
            train_end = dates[split_idx - 1]
            test_start = dates[split_idx]
            test_end = min(test_start + timedelta(days=test_window_days - 1), dates[-1])

            train = df[df["game_date"].dt.date <= train_end]
            test = df[(df["game_date"].dt.date >= test_start) & (df["game_date"].dt.date <= test_end)]
            if len(test) < min_fold_samples:
                continue

            projections = predict_fn(train)
            outcomes = _pairs_to_outcomes(test)
            report = self.backtest.evaluate_predictions(projections, outcomes)
            score = self.backtest.combined_score(report)

            folds.append(
                WalkForwardFold(
                    train_end=train_end.isoformat(),
                    test_start=test_start.isoformat(),
                    test_end=test_end.isoformat(),
                    report=report,
                    combined_score=score,
                )
            )

        if not folds:
            logger.debug("Walk-forward produced 0 folds (insufficient test samples per fold)")

        mean_score = sum(f.combined_score for f in folds) / len(folds) if folds else 0.0
        mean_mae = sum(f.report.weighted_mae for f in folds) / len(folds) if folds else 0.0

        seg_note = ""
        if segments:
            seg_note = f" Segments available: {list(segments.keys())}."

        return WalkForwardReport(
            folds=folds,
            mean_combined_score=round(mean_score, 4),
            mean_weighted_mae=round(mean_mae, 4),
            notes=f"Completed {len(folds)} walk-forward folds.{seg_note}",
        )


def _pairs_to_outcomes(df: pd.DataFrame) -> list[OutcomeRecord]:
    outcomes: list[OutcomeRecord] = []
    for _, row in df.iterrows():
        outcomes.append(
            OutcomeRecord(
                player_id=int(row.get("player_id", 0)),
                player_name=str(row.get("player_name", "")),
                game_date=str(row["game_date"].date()) if hasattr(row["game_date"], "date") else str(row["game_date"]),
                category=row.get("category", "hrr"),
                actual_value=float(row.get("actual_value", 0)),
            )
        )
    return outcomes