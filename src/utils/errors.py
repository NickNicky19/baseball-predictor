"""Structured exceptions for the prediction pipeline."""

from __future__ import annotations


class PredictorError(Exception):
    """Base exception for prediction system failures."""

    def __init__(self, message: str, *, hint: str = ""):
        self.hint = hint
        full = message
        if hint:
            full = f"{message} — {hint}"
        super().__init__(full)


class ConfigError(PredictorError):
    """Invalid or missing configuration."""


class DataFetchError(PredictorError):
    """External data source unavailable or returned an error."""


class OddsLoadError(PredictorError):
    """Odds file missing, malformed, or unreadable."""


class RetrainError(PredictorError):
    """Retraining pipeline failed due to insufficient or invalid data."""


class PredictionPipelineError(PredictorError):
    """Unrecoverable error during daily prediction."""