"""Automation must preserve the audit evidence needed for forward shadowing."""

from __future__ import annotations

from datetime import date

import run_slate
from src.models.dataclasses import DailyPrediction, PropProjection


def test_automation_persists_features_and_decision_time_provenance(monkeypatch):
    """Mutation guard: dropping either flag makes an automation slate unverifiable."""
    seen: dict = {}

    class PredictorBoundary:
        def __init__(self, config_path=None):
            self.mlb_api = object()

        def predict(self, *args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            hitter = PropProjection(
                player_id=1,
                player_name="Test Hitter",
                category="hits",
                game_date="2026-07-14",
                projected_value=1.0,
                confidence=0.5,
            )
            return DailyPrediction(
                game_date=date(2026, 7, 14), hitter_projections=[hitter]
            )

    monkeypatch.setattr(run_slate, "DailyPredictor", PredictorBoundary)

    assert run_slate.main(["--date", "2026-07-14"]) == run_slate.EXIT_OK
    assert seen["kwargs"]["persist_features"] is True
    assert seen["kwargs"]["capture_prediction_provenance"] is True
