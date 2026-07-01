"""
Calibration engine for league baselines, PA simulator config, and prop biases.

Produces data-driven parameter updates from backtest metrics and observed
rate data. Does not auto-apply changes — callers persist via config or
BiasCorrector.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.evaluation.backtest_engine import BacktestMetrics, BacktestReport
from src.models.dataclasses import LeagueBaselines, PropCategory
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RateObservation:
    """Observed rate target for PA-level calibration."""

    k_rate: Optional[float] = None
    bb_rate: Optional[float] = None
    hr_rate_on_contact: Optional[float] = None
    sample_pa: int = 0


@dataclass
class CalibrationConfig:
    """Tunable calibration behavior (stored in config.json calibration block)."""

    league_shrinkage: float = 0.35
    pa_intercept_shrinkage: float = 0.25
    bias_shrinkage: float = 0.40
    min_samples_per_category: int = 25
    min_pa_for_rate_calibration: int = 200

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> CalibrationConfig:
        calibration = config.get("calibration", {})
        return cls(
            league_shrinkage=float(calibration.get("league_shrinkage", 0.35)),
            pa_intercept_shrinkage=float(calibration.get("pa_intercept_shrinkage", 0.25)),
            bias_shrinkage=float(calibration.get("bias_shrinkage", 0.40)),
            min_samples_per_category=int(calibration.get("min_samples_per_category", 25)),
            min_pa_for_rate_calibration=int(calibration.get("min_pa_for_rate_calibration", 200)),
        )


@dataclass
class CalibrationResult:
    """Output of a calibration run — ready to apply via BiasCorrector."""

    league_baselines: LeagueBaselines
    pa_config: PASimulatorConfig
    category_bias_offsets: dict[PropCategory, float] = field(default_factory=dict)
    metrics_before: dict[str, Any] = field(default_factory=dict)
    metrics_after_estimate: dict[str, Any] = field(default_factory=dict)
    sample_sizes: dict[str, int] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "league_baselines": asdict(self.league_baselines),
            "pa_config": asdict(self.pa_config),
            "category_bias_offsets": dict(self.category_bias_offsets),
            "metrics_before": self.metrics_before,
            "metrics_after_estimate": self.metrics_after_estimate,
            "sample_sizes": self.sample_sizes,
            "notes": self.notes,
        }


class CalibrationEngine:
    """
    Derives parameter updates from historical performance.

    Methods are pure given inputs; persistence is explicit via save_result().
    """

    def __init__(
        self,
        league_baselines: Optional[LeagueBaselines] = None,
        pa_config: Optional[PASimulatorConfig] = None,
        calibration_config: Optional[CalibrationConfig] = None,
    ):
        self.league = league_baselines or LeagueBaselines()
        self.pa_config = pa_config or PASimulatorConfig.from_league(self.league)
        self.settings = calibration_config or CalibrationConfig()

    def calibrate_league_baselines(
        self,
        observations: pd.DataFrame,
        column_map: Optional[dict[str, str]] = None,
    ) -> LeagueBaselines:
        """
        Update league baselines from an observation DataFrame.

        column_map maps LeagueBaselines field names to DataFrame column names.
        Values are shrunk toward current league baselines by league_shrinkage.
        """
        col_map = column_map or _default_league_column_map()
        shrink = self.settings.league_shrinkage
        updates: dict[str, float] = {}

        for field_name, col in col_map.items():
            if col not in observations.columns:
                continue
            series = pd.to_numeric(observations[col], errors="coerce").dropna()
            if series.empty:
                continue
            observed_mean = float(series.mean())
            current = getattr(self.league, field_name)
            updates[field_name] = _shrink_toward(observed_mean, current, shrink)

        if not updates:
            return self.league

        return replace(self.league, **updates)

    def calibrate_pa_intercepts(
        self,
        predicted_rates: list[dict[str, float]],
        observed: RateObservation,
    ) -> PASimulatorConfig:
        """
        Adjust PA logit intercepts when aggregate predicted rates diverge from observed.

        predicted_rates: output of HybridPASimulator.expected_rates() per context.
        """
        if observed.sample_pa < self.settings.min_pa_for_rate_calibration:
            return self.pa_config

        if not predicted_rates:
            return self.pa_config

        pred_k = float(np.mean([r["k_prob"] for r in predicted_rates]))
        pred_bb = float(np.mean([r["bb_prob"] for r in predicted_rates]))
        pred_hr = float(np.mean([r["hr_prob_on_bip"] for r in predicted_rates]))

        shrink = self.settings.pa_intercept_shrinkage
        cfg = self.pa_config

        k_target = observed.k_rate / 100.0 if observed.k_rate and observed.k_rate > 1 else observed.k_rate
        bb_target = observed.bb_rate / 100.0 if observed.bb_rate and observed.bb_rate > 1 else observed.bb_rate

        k_delta = 0.0
        bb_delta = 0.0
        hr_delta = 0.0

        if k_target is not None:
            k_delta = (k_target - pred_k) * shrink
        if bb_target is not None:
            bb_delta = (bb_target - pred_bb) * shrink
        if observed.hr_rate_on_contact is not None:
            hr_delta = (observed.hr_rate_on_contact - pred_hr) * shrink

        return PASimulatorConfig(
            **{
                **asdict(cfg),
                "k_intercept": cfg.k_intercept + k_delta * 4.0,
                "bb_intercept": cfg.bb_intercept + bb_delta * 4.0,
                "hr_intercept": cfg.hr_intercept + hr_delta * 4.0,
            }
        )

    def calibrate_category_biases(
        self,
        backtest_report: BacktestReport,
    ) -> dict[PropCategory, float]:
        """
        Derive per-category mean-error corrections from a BacktestReport.

        Positive offset means model under-predicts (add to projection).
        Corrections are shrunk by bias_shrinkage and gated by min_samples.
        """
        biases: dict[PropCategory, float] = {}
        shrink = self.settings.bias_shrinkage
        min_n = self.settings.min_samples_per_category

        for category, metrics in backtest_report.metrics_by_category.items():
            if metrics.n_samples < min_n:
                continue
            # mean_error = predicted - actual; negate to get correction to add
            correction = -metrics.mean_error * shrink
            biases[category] = round(correction, 4)

        return biases

    def run_full_calibration(
        self,
        backtest_report: BacktestReport,
        league_observations: Optional[pd.DataFrame] = None,
        predicted_pa_rates: Optional[list[dict[str, float]]] = None,
        observed_pa_rates: Optional[RateObservation] = None,
    ) -> CalibrationResult:
        """Run league, PA, and bias calibration in one pass."""
        league = self.league
        if league_observations is not None and not league_observations.empty:
            league = self.calibrate_league_baselines(league_observations)

        pa_config = self.pa_config
        if predicted_pa_rates and observed_pa_rates:
            pa_config = self.calibrate_pa_intercepts(predicted_pa_rates, observed_pa_rates)

        biases = self.calibrate_category_biases(backtest_report)
        sample_sizes = {
            cat: m.n_samples for cat, m in backtest_report.metrics_by_category.items()
        }

        after_estimate = {
            cat: round(m.mae * 0.95, 4) if cat in biases else m.mae
            for cat, m in backtest_report.metrics_by_category.items()
        }

        return CalibrationResult(
            league_baselines=league,
            pa_config=pa_config,
            category_bias_offsets=biases,
            metrics_before={cat: m.to_dict() for cat, m in backtest_report.metrics_by_category.items()},
            metrics_after_estimate=after_estimate,
            sample_sizes=sample_sizes,
            notes="Calibration complete. Apply via BiasCorrector or persist to config.",
        )

    def estimate_pa_rates_from_simulator(
        self,
        simulator: HybridPASimulator,
        pa_inputs: list[dict[str, Any]],
    ) -> list[dict[str, float]]:
        """Collect expected PA rates for a batch of contexts (backtest helper)."""
        from src.simulation.game_simulator import GameSimulatorInput

        rates: list[dict[str, float]] = []
        for raw in pa_inputs:
            sim_input = GameSimulatorInput(**raw)
            rates.append(simulator.expected_rates(
                pitcher_k_pct=sim_input.pitcher_k_pct,
                pitcher_bb_pct=sim_input.pitcher_bb_pct,
                park_hr_factor=sim_input.park_hr_factor,
                handedness_advantage=sim_input.handedness_advantage,
                recent_form_mult=sim_input.recent_form_mult,
                statcast=sim_input.statcast,
            ))
        return rates

    def save_result(
        self,
        result: CalibrationResult,
        path: str | Path,
    ) -> Path:
        """Persist calibration output for the learning layer."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        logger.info("Saved calibration result to %s", path)
        return path

    def load_result(self, path: str | Path) -> CalibrationResult:
        """Load a previously saved calibration result."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return CalibrationResult(
            league_baselines=LeagueBaselines(**data["league_baselines"]),
            pa_config=PASimulatorConfig(**data["pa_config"]),
            category_bias_offsets=data.get("category_bias_offsets", {}),
            metrics_before=data.get("metrics_before", {}),
            metrics_after_estimate=data.get("metrics_after_estimate", {}),
            sample_sizes=data.get("sample_sizes", {}),
            notes=data.get("notes", ""),
        )


def _shrink_toward(observed: float, current: float, shrinkage: float) -> float:
    """Empirical-Bayes style shrinkage: pull observed toward current baseline."""
    shrinkage = max(0.0, min(1.0, shrinkage))
    return round(current + (observed - current) * shrinkage, 6)


def _default_league_column_map() -> dict[str, str]:
    return {
        "k_pct": "k_pct",
        "bb_pct": "bb_pct",
        "xwoba": "xwoba",
        "xslg": "xslg",
        "barrel_rate": "barrel_rate",
        "hard_hit_rate": "hard_hit_rate",
        "sweet_spot_rate": "sweet_spot_rate",
        "whiff_rate": "whiff_rate",
        "chase_rate": "chase_rate",
        "contact_rate": "contact_rate",
        "swing_rate": "swing_rate",
        "zone_rate": "zone_rate",
        "hr_per_9": "hr_per_9",
        "pa_per_game": "pa_per_game",
    }