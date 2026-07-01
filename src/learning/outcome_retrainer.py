"""
Outcome retrainer — learns corrections from predicted vs actual results.

Ingests outcome records, fits data-driven adjustments, and produces a
RetrainResult that BiasCorrector can apply. No automatic daily retraining.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.evaluation.backtest_engine import BacktestEngine, OutcomeRecord
from src.evaluation.calibration import CalibrationConfig, CalibrationEngine, CalibrationResult
from src.models.dataclasses import LeagueBaselines, PropCategory, PropProjection
from src.simulation.pa_simulator import PASimulatorConfig
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PredictionOutcomePair:
    """Aligned prediction and observed outcome."""

    projection: PropProjection
    outcome: OutcomeRecord
    residual: float = 0.0

    def __post_init__(self) -> None:
        self.residual = self.projection.projected_value - self.outcome.actual_value


@dataclass
class RetrainResult:
    """
    Data-driven learning output.

    Intended to feed BiasCorrector and optionally update config/league/simulator.
    """

    sample_size: int
    category_bias_offsets: dict[PropCategory, float] = field(default_factory=dict)
    league_field_updates: dict[str, float] = field(default_factory=dict)
    pa_config_updates: dict[str, float] = field(default_factory=dict)
    backtest_weighted_mae: float = 0.0
    confidence: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_size": self.sample_size,
            "category_bias_offsets": dict(self.category_bias_offsets),
            "league_field_updates": self.league_field_updates,
            "pa_config_updates": self.pa_config_updates,
            "backtest_weighted_mae": self.backtest_weighted_mae,
            "confidence": self.confidence,
            "notes": self.notes,
        }

    def to_calibration_result(
        self,
        league: LeagueBaselines,
        pa_config: PASimulatorConfig,
    ) -> CalibrationResult:
        """Convert to CalibrationResult for unified apply/persist path."""
        updated_league = league
        if self.league_field_updates:
            from dataclasses import replace
            updated_league = replace(league, **self.league_field_updates)

        updated_pa = pa_config
        if self.pa_config_updates:
            updated_pa = PASimulatorConfig(**{**asdict(pa_config), **self.pa_config_updates})

        return CalibrationResult(
            league_baselines=updated_league,
            pa_config=updated_pa,
            category_bias_offsets=self.category_bias_offsets,
            sample_sizes={"total": self.sample_size},
            notes=self.notes,
        )


class OutcomeRetrainer:
    """
    Fits correction parameters from historical prediction-outcome pairs.

    Workflow:
        1. ingest() prediction + outcome records
        2. fit() -> RetrainResult
        3. BiasCorrector.apply_retrain_result() or save for later
    """

    def __init__(
        self,
        league_baselines: Optional[LeagueBaselines] = None,
        pa_config: Optional[PASimulatorConfig] = None,
        calibration_config: Optional[CalibrationConfig] = None,
        backtest_engine: Optional[BacktestEngine] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        self.league = league_baselines or LeagueBaselines.from_config(config or {})
        self.pa_config = pa_config or PASimulatorConfig.from_league(self.league)
        self.settings = calibration_config or CalibrationConfig.from_config(config or {})
        self.backtest_engine = backtest_engine or BacktestEngine()
        self.calibration_engine = CalibrationEngine(
            league_baselines=self.league,
            pa_config=self.pa_config,
            calibration_config=self.settings,
        )
        self._pairs: list[PredictionOutcomePair] = []

    def ingest(
        self,
        projections: list[PropProjection],
        outcomes: list[OutcomeRecord],
    ) -> int:
        """Align and store prediction-outcome pairs. Returns count ingested."""
        outcome_index = {
            (o.player_id, o.game_date, o.category): o for o in outcomes
        }
        ingested = 0
        for proj in projections:
            key = (proj.player_id, proj.game_date, proj.category)
            outcome = outcome_index.get(key)
            if outcome is None:
                continue
            self._pairs.append(PredictionOutcomePair(projection=proj, outcome=outcome))
            ingested += 1
        logger.info("Ingested %d prediction-outcome pairs", ingested)
        return ingested

    def ingest_from_dataframe(self, df: pd.DataFrame) -> int:
        """
        Ingest from a flat DataFrame with columns:
        player_id, player_name, game_date, category, predicted_value, actual_value
        """
        required = {"player_id", "game_date", "category", "predicted_value", "actual_value"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing columns: {missing}")

        projections: list[PropProjection] = []
        outcomes: list[OutcomeRecord] = []

        for _, row in df.iterrows():
            category = row["category"]
            projections.append(
                PropProjection(
                    player_id=int(row["player_id"]),
                    player_name=str(row.get("player_name", "")),
                    category=category,
                    game_date=str(row["game_date"]),
                    projected_value=float(row["predicted_value"]),
                    confidence=float(row.get("confidence", 0.5)),
                )
            )
            outcomes.append(
                OutcomeRecord(
                    player_id=int(row["player_id"]),
                    player_name=str(row.get("player_name", "")),
                    game_date=str(row["game_date"]),
                    category=category,
                    actual_value=float(row["actual_value"]),
                )
            )
        return self.ingest(projections, outcomes)

    def fit(
        self,
        league_observations: Optional[pd.DataFrame] = None,
    ) -> RetrainResult:
        """
        Fit corrections from ingested pairs.

        Returns RetrainResult with category biases and optional league/PA updates.
        """
        if not self._pairs:
            return RetrainResult(
                sample_size=0,
                notes="No ingested pairs. Call ingest() before fit().",
            )

        projections = [p.projection for p in self._pairs]
        outcomes = [p.outcome for p in self._pairs]
        report = self.backtest_engine.evaluate_predictions(projections, outcomes)

        calibration = self.calibration_engine.run_full_calibration(
            backtest_report=report,
            league_observations=league_observations,
        )

        league_updates = _diff_league_baselines(self.league, calibration.league_baselines)
        pa_updates = _diff_pa_config(self.pa_config, calibration.pa_config)

        confidence = _compute_confidence(report, self.settings.min_samples_per_category)

        result = RetrainResult(
            sample_size=len(self._pairs),
            category_bias_offsets=calibration.category_bias_offsets,
            league_field_updates=league_updates,
            pa_config_updates=pa_updates,
            backtest_weighted_mae=report.weighted_mae,
            confidence=confidence,
            notes=(
                f"Fitted on {len(self._pairs)} pairs. "
                f"Weighted MAE={report.weighted_mae:.3f}. "
                f"Confidence={confidence:.2f}."
            ),
        )
        return result

    def clear(self) -> None:
        """Reset ingested pairs."""
        self._pairs.clear()

    @property
    def pair_count(self) -> int:
        return len(self._pairs)

    def pairs_to_dataframe(self) -> pd.DataFrame:
        """Export ingested pairs for inspection."""
        rows = []
        for pair in self._pairs:
            rows.append(
                {
                    "player_id": pair.projection.player_id,
                    "player_name": pair.projection.player_name,
                    "game_date": pair.projection.game_date,
                    "category": pair.projection.category,
                    "predicted_value": pair.projection.projected_value,
                    "actual_value": pair.outcome.actual_value,
                    "residual": pair.residual,
                }
            )
        return pd.DataFrame(rows)

    def save_result(self, result: RetrainResult, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        logger.info("Saved retrain result to %s", path)
        return path

    def load_result(self, path: str | Path) -> RetrainResult:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return RetrainResult(
            sample_size=int(data["sample_size"]),
            category_bias_offsets=data.get("category_bias_offsets", {}),
            league_field_updates=data.get("league_field_updates", {}),
            pa_config_updates=data.get("pa_config_updates", {}),
            backtest_weighted_mae=float(data.get("backtest_weighted_mae", 0.0)),
            confidence=float(data.get("confidence", 0.0)),
            notes=data.get("notes", ""),
        )


def _diff_league_baselines(before: LeagueBaselines, after: LeagueBaselines) -> dict[str, float]:
    updates: dict[str, float] = {}
    for f in before.__dataclass_fields__:
        b_val = getattr(before, f)
        a_val = getattr(after, f)
        if isinstance(b_val, (int, float)) and b_val != a_val:
            updates[f] = a_val
    return updates


def _diff_pa_config(before: PASimulatorConfig, after: PASimulatorConfig) -> dict[str, float]:
    updates: dict[str, float] = {}
    for key, val in asdict(after).items():
        if key in asdict(before) and asdict(before)[key] != val:
            updates[key] = val
    return updates


def _compute_confidence(report, min_samples: int) -> float:
    if not report.metrics_by_category:
        return 0.0
    n = report.matched_pairs
    sample_factor = min(1.0, n / max(min_samples * 4, 1))
    stability = 1.0 - min(1.0, report.weighted_mae / 3.0)
    return round(max(0.0, min(1.0, 0.5 * sample_factor + 0.5 * stability)), 3)