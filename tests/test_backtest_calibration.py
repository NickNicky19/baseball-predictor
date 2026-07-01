"""Tests for backtesting and calibration pipeline."""

from src.evaluation import BacktestEngine, CalibrationEngine, CalibrationConfig
from src.evaluation.backtest_engine import OutcomeRecord
from src.models.dataclasses import LeagueBaselines, PropProjection
from src.simulation.pa_simulator import PASimulatorConfig


def _sample_pairs():
    projections = [
        PropProjection(1, "A", "hrr", "2026-06-25", 2.0, 0.7),
        PropProjection(2, "B", "hrr", "2026-06-25", 1.5, 0.65),
        PropProjection(3, "C", "hits", "2026-06-25", 1.2, 0.6),
    ]
    outcomes = [
        OutcomeRecord(1, "A", "2026-06-25", "hrr", 2.3),
        OutcomeRecord(2, "B", "2026-06-25", "hrr", 1.4),
        OutcomeRecord(3, "C", "2026-06-25", "hits", 1.0),
    ]
    return projections, outcomes


def test_backtest_engine_metrics():
    engine = BacktestEngine()
    projections, outcomes = _sample_pairs()
    report = engine.evaluate_predictions(projections, outcomes)
    assert report.matched_pairs == 3
    assert "hrr" in report.metrics_by_category
    assert report.metrics_by_category["hrr"].n_samples == 2
    assert report.weighted_mae > 0


def test_calibration_category_biases():
    engine = BacktestEngine()
    projections, outcomes = _sample_pairs()
    report = engine.evaluate_predictions(projections, outcomes)

    calibration = CalibrationEngine(
        league_baselines=LeagueBaselines(),
        pa_config=PASimulatorConfig.from_league(LeagueBaselines()),
        calibration_config=CalibrationConfig(min_samples_per_category=1, bias_shrinkage=0.5),
    )
    result = calibration.run_full_calibration(backtest_report=report)
    assert isinstance(result.category_bias_offsets, dict)
    assert result.league_baselines is not None


def test_outcome_retrainer_fit_integration():
    from src.learning import OutcomeRetrainer

    projections, outcomes = _sample_pairs()
    retrainer = OutcomeRetrainer(
        config={"calibration": {"min_samples_per_category": 1, "bias_shrinkage": 0.4}}
    )
    retrainer.ingest(projections, outcomes)
    result = retrainer.fit()
    assert result.sample_size == 3
    assert "hrr" in result.category_bias_offsets or result.sample_size > 0