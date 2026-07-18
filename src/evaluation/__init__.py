"""Backtesting and calibration -- component-level evaluation.

Public exports are resolved lazily. Simulation modules consume focused
evaluation adapters, while calibration itself imports the PA simulator; eager
package imports therefore create an import cycle that depends on import order.
Lazy exports preserve the public API without coupling unrelated submodules.
"""

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
    if name in {"BacktestEngine", "BacktestMetrics", "BacktestReport", "OutcomeRecord"}:
        from src.evaluation import backtest_engine

        return getattr(backtest_engine, name)
    if name in {"CalibrationConfig", "CalibrationEngine", "CalibrationResult", "RateObservation"}:
        from src.evaluation import calibration

        return getattr(calibration, name)
    if name == "PipelineValidationReport":
        from src.evaluation.pipeline_validator import PipelineValidationReport

        return PipelineValidationReport
    if name == "PipelineValidator":
        from src.evaluation.pipeline_validator import PipelineValidator

        return PipelineValidator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
