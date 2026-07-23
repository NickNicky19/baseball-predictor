"""Automation must preserve the audit evidence needed for forward shadowing."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import run_slate
from src.models.dataclasses import DailyPrediction, PropProjection


class _FutureSlateAPI:
    def get_schedule(self, game_date, include_lineups=False):
        assert include_lineups is False
        return [{"gamePk": 99, "gameDate": "2026-07-14T18:00:00Z"}]


def _at_pregame_boundary(monkeypatch):
    monkeypatch.setattr(
        run_slate,
        "utc_now",
        lambda: datetime(2026, 7, 14, 17, 59, tzinfo=timezone.utc),
    )


def test_automation_persists_features_and_decision_time_provenance(monkeypatch):
    """Mutation guard: dropping either flag makes an automation slate unverifiable."""
    seen: dict = {}

    class PredictorBoundary:
        def __init__(self, config_path=None):
            seen["config_path"] = Path(config_path)
            self.mlb_api = _FutureSlateAPI()

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
    _at_pregame_boundary(monkeypatch)

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
            self.mlb_api = _FutureSlateAPI()
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
    _at_pregame_boundary(monkeypatch)
    external = tmp_path / "external_archive"

    assert run_slate.main([
        "--date", "2026-07-14",
        "--archive-dir", str(external),
    ]) == run_slate.EXIT_OK
    assert seen["archive_dir"] == external.resolve()
    assert seen["kwargs"]["persist_features"] is True
    assert seen["kwargs"]["capture_prediction_provenance"] is True


def test_started_slate_fails_before_feature_or_prediction_consumption(monkeypatch):
    """Regression: a post-start archive must not touch the predictor input path."""
    seen = {"predict_called": False}

    class StartedSlateAPI:
        def get_schedule(self, game_date, include_lineups=False):
            return [{"gamePk": 991, "gameDate": "2026-07-14T17:00:00Z"}]

    class PredictorBoundary:
        def __init__(self, config_path=None):
            self.mlb_api = StartedSlateAPI()

        def predict(self, *args, **kwargs):
            seen["predict_called"] = True
            raise AssertionError("post-start slate reached prediction")

    monkeypatch.setattr(run_slate, "DailyPredictor", PredictorBoundary)
    monkeypatch.setattr(
        run_slate,
        "utc_now",
        lambda: datetime(2026, 7, 14, 17, 0, tzinfo=timezone.utc),
    )

    assert run_slate.main(["--date", "2026-07-14"]) == run_slate.EXIT_NO_DATA
    assert seen["predict_called"] is False


def test_missing_schedule_start_fails_closed_before_prediction(monkeypatch):
    """Mutation: a missing start timestamp cannot be treated as safely future."""
    seen = {"predict_called": False}

    class MalformedSlateAPI:
        def get_schedule(self, game_date, include_lineups=False):
            return [{"gamePk": 992}]

    class PredictorBoundary:
        def __init__(self, config_path=None):
            self.mlb_api = MalformedSlateAPI()

        def predict(self, *args, **kwargs):
            seen["predict_called"] = True
            raise AssertionError("malformed schedule reached prediction")

    monkeypatch.setattr(run_slate, "DailyPredictor", PredictorBoundary)
    _at_pregame_boundary(monkeypatch)

    assert run_slate.main(["--date", "2026-07-14"]) == run_slate.EXIT_NO_DATA
    assert seen["predict_called"] is False
