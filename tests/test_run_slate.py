"""Automation must preserve the audit evidence needed for forward shadowing."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import run_slate
from src.models.dataclasses import DailyPrediction, PropProjection


def test_automation_persists_features_and_decision_time_provenance(monkeypatch):
    """Mutation guard: dropping either flag makes an automation slate unverifiable."""
    seen: dict = {}

    class PredictorBoundary:
        def __init__(self, config_path=None):
            seen["config_path"] = Path(config_path)
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
    assert seen["config_path"] == Path("config/config.kbb.json")
    assert seen["kwargs"]["persist_features"] is True
    assert seen["kwargs"]["capture_prediction_provenance"] is True
    assert seen["kwargs"]["hitter_categories"] == ("hits", "hrr", "home_runs")


def test_archive_dir_redirects_output_without_changing_prediction_call(monkeypatch, tmp_path):
    """The forward collector must not dirty its clean Git checkout."""
    seen: dict = {}

    class ArchiveBoundary:
        def __init__(self, archive_dir, project_root=None):
            seen["archive_dir"] = Path(archive_dir)
            seen["project_root"] = Path(project_root)

    class RecorderBoundary:
        archive = object()

    class PredictorBoundary:
        def __init__(self, config_path=None):
            self.mlb_api = object()
            self.outcome_recorder = RecorderBoundary()

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
    monkeypatch.setattr(run_slate, "PredictionArchive", ArchiveBoundary)
    external = tmp_path / "external_archive"

    assert run_slate.main([
        "--date", "2026-07-14",
        "--archive-dir", str(external),
    ]) == run_slate.EXIT_OK
    assert seen["archive_dir"] == external.resolve()
    assert seen["kwargs"]["persist_features"] is True
    assert seen["kwargs"]["capture_prediction_provenance"] is True
