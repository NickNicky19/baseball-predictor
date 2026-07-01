"""Backtesting and calibration — component-level evaluation."""

from src.evaluation.backtest_engine import (
    BacktestEngine,
    BacktestMetrics,
    BacktestReport,
    OutcomeRecord,
)
from src.evaluation.calibration import (
    CalibrationConfig,
    CalibrationEngine,
    CalibrationResult,
    RateObservation,
)

__all__ = [
    "BacktestEngine",
    "BacktestMetrics",
    "BacktestReport",
    "CalibrationConfig",
    "CalibrationEngine",
    "CalibrationResult",
    "OutcomeRecord",
    "PipelineValidationReport",
    "PipelineValidator",
    "RateObservation",
]


def __getattr__(name: str):
    if name == "PipelineValidationReport":
        from src.evaluation.pipeline_validator import PipelineValidationReport

        return PipelineValidationReport
    if name == "PipelineValidator":
        from src.evaluation.pipeline_validator import PipelineValidator

        return PipelineValidator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")