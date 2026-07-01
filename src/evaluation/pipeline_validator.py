"""
Pipeline validator — end-to-end validation on historical dates.

Runs predictions (optionally with corrections), compares to recorded or
live actuals, and produces summary metrics for monitoring model quality.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.evaluation.backtest_engine import BacktestEngine, OutcomeRecord
from src.learning.outcome_recorder import OutcomeRecorder
from src.learning.prediction_archive import PredictionArchive
from src.learning.retrain_runner import RetrainRunner
from src.models.dataclasses import DailyPrediction, PropCategory, PropProjection
from src.prediction import DailyPredictor
from src.utils.errors import RetrainError
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DateValidationResult:
    game_date: str
    matched_pairs: int
    weighted_mae: float
    value_plays: int
    corrections_applied: bool
    metrics_by_category: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_date": self.game_date,
            "matched_pairs": self.matched_pairs,
            "weighted_mae": self.weighted_mae,
            "value_plays": self.value_plays,
            "corrections_applied": self.corrections_applied,
            "metrics_by_category": self.metrics_by_category,
            "error": self.error,
        }


@dataclass
class PipelineValidationReport:
    dates_evaluated: int
    total_matched_pairs: int
    mean_weighted_mae: float
    results: list[DateValidationResult] = field(default_factory=list)
    retrain_report: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dates_evaluated": self.dates_evaluated,
            "total_matched_pairs": self.total_matched_pairs,
            "mean_weighted_mae": self.mean_weighted_mae,
            "results": [r.to_dict() for r in self.results],
            "retrain_report": self.retrain_report,
        }


class PipelineValidator:
    """
    Validates the full prediction pipeline on historical slates.

    Modes:
        - Use archived predictions + pairs CSV actuals
        - Re-run live predictions for dates (slower, needs API)
    """

    def __init__(
        self,
        predictor: Optional[DailyPredictor] = None,
        config: Optional[dict[str, Any]] = None,
        project_root: Optional[Path] = None,
    ):
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        self.config = config or RetrainRunner.load_config()
        self.predictor = predictor or DailyPredictor(config=self.config)
        self.backtest = BacktestEngine()
        self.archive = PredictionArchive.from_config(self.config, self.project_root)
        self.recorder = OutcomeRecorder.from_config(self.config, self.project_root)

    def validate_dates(
        self,
        dates: list[str],
        apply_corrections: bool = False,
        include_edges: bool = False,
        categories: Optional[tuple[PropCategory, ...]] = None,
        rerun_predictions: bool = False,
    ) -> PipelineValidationReport:
        """Validate one or more historical dates."""
        results: list[DateValidationResult] = []
        maes: list[float] = []
        total_pairs = 0

        for game_date in dates:
            result = self._validate_single_date(
                game_date,
                apply_corrections=apply_corrections,
                include_edges=include_edges,
                categories=categories,
                rerun_predictions=rerun_predictions,
            )
            results.append(result)
            if result.matched_pairs > 0 and not result.error:
                maes.append(result.weighted_mae)
                total_pairs += result.matched_pairs

        mean_mae = sum(maes) / len(maes) if maes else 0.0
        return PipelineValidationReport(
            dates_evaluated=len(dates),
            total_matched_pairs=total_pairs,
            mean_weighted_mae=round(mean_mae, 4),
            results=results,
        )

    def validate_from_pairs_csv(
        self,
        pairs_path: Optional[str] = None,
        lookback_days: Optional[int] = None,
        apply_corrections: bool = False,
    ) -> PipelineValidationReport:
        """Validate using existing prediction_outcomes.csv without re-fetching."""
        settings = RetrainRunner.from_config(self.config).settings
        path = Path(pairs_path or settings.pairs_csv_path)
        if not path.is_absolute():
            path = self.project_root / path
        if not path.exists():
            raise RetrainError(f"Pairs file not found: {path}")

        df = pd.read_csv(path)
        df = RetrainRunner._filter_lookback(df, lookback_days or settings.lookback_days)
        dates = sorted(df["game_date"].astype(str).unique().tolist())
        return self.validate_dates(
            dates,
            apply_corrections=apply_corrections,
            rerun_predictions=False,
        )

    def validate_and_retrain(
        self,
        pairs_path: Optional[str] = None,
        min_pairs: Optional[int] = None,
        save_state: bool = True,
    ) -> PipelineValidationReport:
        """Validate recent pairs, then run RetrainRunner if enough data."""
        report = self.validate_from_pairs_csv(pairs_path=pairs_path)
        if report.total_matched_pairs == 0:
            return report

        runner = RetrainRunner.from_config(self.config, self.project_root)
        try:
            retrain = runner.run(
                pairs_path=pairs_path,
                min_pairs=min_pairs,
                save_state=save_state,
            )
            report.retrain_report = retrain.to_dict()
        except RetrainError as exc:
            report.retrain_report = {"error": str(exc)}
        return report

    def _validate_single_date(
        self,
        game_date: str,
        apply_corrections: bool,
        include_edges: bool,
        categories: Optional[tuple[PropCategory, ...]],
        rerun_predictions: bool,
    ) -> DateValidationResult:
        try:
            if rerun_predictions:
                prediction = self.predictor.predict(
                    game_date,
                    hitter_categories=categories or ("hits", "hrr", "home_runs", "fantasy"),
                    include_pitchers=True,
                    apply_corrections=apply_corrections,
                    include_edges=include_edges,
                )
            else:
                prediction = self.archive.load(game_date)
                if prediction is None:
                    return DateValidationResult(
                        game_date=game_date,
                        matched_pairs=0,
                        weighted_mae=0.0,
                        value_plays=0,
                        corrections_applied=apply_corrections,
                        error="No archived prediction",
                    )

            projections, outcomes = self._load_pairs_for_date(game_date, prediction)
            if not projections:
                return DateValidationResult(
                    game_date=game_date,
                    matched_pairs=0,
                    weighted_mae=0.0,
                    value_plays=len(prediction.value_plays) if prediction else 0,
                    corrections_applied=apply_corrections,
                    error="No pairs for date",
                )

            bt_report = self.backtest.evaluate_predictions(projections, outcomes)
            return DateValidationResult(
                game_date=game_date,
                matched_pairs=bt_report.matched_pairs,
                weighted_mae=round(bt_report.weighted_mae, 4),
                value_plays=len(prediction.value_plays) if hasattr(prediction, "value_plays") else 0,
                corrections_applied=apply_corrections,
                metrics_by_category={
                    cat: m.to_dict() for cat, m in bt_report.metrics_by_category.items()
                },
            )
        except Exception as exc:
            logger.warning("Validation failed for %s: %s", game_date, exc)
            return DateValidationResult(
                game_date=game_date,
                matched_pairs=0,
                weighted_mae=0.0,
                value_plays=0,
                corrections_applied=apply_corrections,
                error=str(exc),
            )

    def _load_pairs_for_date(
        self,
        game_date: str,
        prediction: DailyPrediction,
    ) -> tuple[list[PropProjection], list[OutcomeRecord]]:
        pairs_path = self.recorder._pairs_path()
        if not pairs_path.exists():
            return [], []

        df = pd.read_csv(pairs_path)
        df = df[df["game_date"].astype(str) == game_date]

        outcome_index = {
            (int(row["player_id"]), str(row["category"])): float(row["actual_value"])
            for _, row in df.iterrows()
        }

        projections: list[PropProjection] = []
        outcomes: list[OutcomeRecord] = []

        for proj in prediction.hitter_projections + prediction.pitcher_projections:
            key = (proj.player_id, proj.category)
            actual = outcome_index.get(key)
            if actual is None:
                continue
            projections.append(proj)
            outcomes.append(
                OutcomeRecord(
                    player_id=proj.player_id,
                    player_name=proj.player_name,
                    game_date=game_date,
                    category=proj.category,
                    actual_value=actual,
                )
            )
        return projections, outcomes

    def save_report(self, report: PipelineValidationReport, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        return path