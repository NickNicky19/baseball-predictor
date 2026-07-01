"""
Retrain runner — orchestrates automated correction updates from historical pairs.

Provides a clean interface for scheduled retraining jobs and CLI scripts.
Does not run automatically; callers invoke run() explicitly or via run_retrain.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import pandas as pd

from src.learning.bias_corrector import BiasCorrector
from src.learning.outcome_retrainer import OutcomeRetrainer, RetrainResult

if TYPE_CHECKING:
    from src.prediction.correction_manager import CorrectionManager
from src.utils.errors import ConfigError, RetrainError
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RetrainSettings:
    """Configuration for automated retraining (config.json retraining block)."""

    pairs_csv_path: str = "data/learning/prediction_outcomes.csv"
    output_state_path: str = "data/learning/bias_corrections.json"
    min_pairs: int = 25
    lookback_days: int = 30
    save_retrain_result: bool = True
    retrain_result_path: str = "data/learning/latest_retrain_result.json"
    auto_enable_corrections: bool = False

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> RetrainSettings:
        block = config.get("retraining", {})
        learning = config.get("learning", {})
        return cls(
            pairs_csv_path=str(block.get("pairs_csv_path", "data/learning/prediction_outcomes.csv")),
            output_state_path=str(
                block.get("output_state_path", learning.get("correction_state_path", "data/learning/bias_corrections.json"))
            ),
            min_pairs=int(block.get("min_pairs", 25)),
            lookback_days=int(block.get("lookback_days", 30)),
            save_retrain_result=bool(block.get("save_retrain_result", True)),
            retrain_result_path=str(block.get("retrain_result_path", "data/learning/latest_retrain_result.json")),
            auto_enable_corrections=bool(block.get("auto_enable_corrections", False)),
        )


@dataclass
class RetrainRunReport:
    """Summary of a completed retrain run."""

    sample_size: int
    confidence: float
    weighted_mae: float
    state_path: str
    notes: str
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "confidence": self.confidence,
            "weighted_mae": self.weighted_mae,
            "state_path": self.state_path,
            "notes": self.notes,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


class RetrainRunner:
    """
    Runs OutcomeRetrainer.fit() on historical pairs and persists corrections.

    Typical usage:
        runner = RetrainRunner.from_config(config)
        report = runner.run()
        # DailyPredictor will load the saved state via CorrectionManager
    """

    def __init__(
        self,
        settings: Optional[RetrainSettings] = None,
        retrainer: Optional[OutcomeRetrainer] = None,
        project_root: Optional[Path] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        self.config = config or {}
        self.settings = settings or RetrainSettings.from_config(self.config)
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        self.retrainer = retrainer or OutcomeRetrainer(config=self.config)

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        project_root: Optional[Path] = None,
    ) -> RetrainRunner:
        return cls(config=config, project_root=project_root)

    def run(
        self,
        pairs_path: Optional[str | Path] = None,
        lookback_days: Optional[int] = None,
        min_pairs: Optional[int] = None,
        save_state: bool = True,
        dry_run: bool = False,
    ) -> RetrainRunReport:
        """
        Load pairs, fit corrections, and optionally persist BiasCorrectionState.

        Returns a RetrainRunReport. Raises RetrainError on insufficient data.
        """
        resolved_pairs = self._resolve_path(pairs_path or self.settings.pairs_csv_path)
        if not resolved_pairs.exists():
            raise RetrainError(
                f"Pairs file not found: {resolved_pairs}",
                hint="Export prediction-outcome pairs to CSV or set retraining.pairs_csv_path",
            )

        df = pd.read_csv(resolved_pairs)
        lookback = lookback_days if lookback_days is not None else self.settings.lookback_days
        df = self._filter_lookback(df, lookback)

        self.retrainer.clear()
        ingested = self.retrainer.ingest_from_dataframe(df)
        required = min_pairs if min_pairs is not None else self.settings.min_pairs

        logger.info(
            "Retrain input: %d pairs ingested (required=%d, lookback=%d days, file=%s)",
            ingested,
            required,
            lookback,
            resolved_pairs.name,
        )

        if ingested < required:
            raise RetrainError(
                f"Insufficient pairs for retraining: {ingested} < {required}",
                hint=f"Need at least {required} aligned prediction-outcome rows",
            )

        result = self.retrainer.fit()
        if result.sample_size == 0:
            return RetrainRunReport(
                sample_size=0,
                confidence=0.0,
                weighted_mae=0.0,
                state_path="",
                notes=result.notes,
                skipped=True,
                skip_reason=result.notes,
            )

        state_path = self._resolve_path(self.settings.output_state_path)
        if dry_run:
            logger.info(
                "Dry run: would save state to %s (samples=%d, confidence=%.2f, MAE=%.3f)",
                state_path,
                result.sample_size,
                result.confidence,
                result.backtest_weighted_mae,
            )
            return RetrainRunReport(
                sample_size=result.sample_size,
                confidence=result.confidence,
                weighted_mae=result.backtest_weighted_mae,
                state_path="",
                notes=f"[dry-run] {result.notes}",
            )

        if save_state:
            corrector = BiasCorrector.from_retrain_result(result)
            corrector.save(state_path)
            logger.info("Saved correction state to %s", state_path)

        if self.settings.save_retrain_result:
            result_path = self._resolve_path(self.settings.retrain_result_path)
            self.retrainer.save_result(result, result_path)

        return RetrainRunReport(
            sample_size=result.sample_size,
            confidence=result.confidence,
            weighted_mae=result.backtest_weighted_mae,
            state_path=str(state_path) if save_state else "",
            notes=result.notes,
        )

    def apply_to_manager(self, manager: CorrectionManager, result: RetrainResult) -> None:
        """Load a fresh RetrainResult into a CorrectionManager without persisting."""
        manager.load_retrain_result(result)
        manager.enable()

    def load_latest_state_into(self, manager: CorrectionManager) -> bool:
        """Load persisted correction state if it exists."""
        path = self._resolve_path(self.settings.output_state_path)
        if not path.exists():
            logger.warning("No correction state at %s", path)
            return False
        manager.load_state(path)
        manager.enable()
        return True

    def _resolve_path(self, relative: str | Path) -> Path:
        path = Path(relative)
        if not path.is_absolute():
            path = self.project_root / path
        return path

    @staticmethod
    def _filter_lookback(df: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
        if "game_date" not in df.columns or lookback_days <= 0:
            return df
        cutoff = date.today() - timedelta(days=lookback_days)
        dates = pd.to_datetime(df["game_date"], errors="coerce").dt.date
        mask = dates >= cutoff
        filtered = df.loc[mask.fillna(False)]
        logger.info(
            "Filtered to %d pairs within last %d days (from %d)",
            len(filtered),
            lookback_days,
            len(df),
        )
        return filtered

    @staticmethod
    def load_config(config_path: Optional[str | Path] = None) -> dict[str, Any]:
        if config_path is None:
            root = Path(__file__).resolve().parents[2]
            config_path = root / "config" / "config.json"
        path = Path(config_path)
        if not path.exists():
            raise ConfigError(f"Config not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))