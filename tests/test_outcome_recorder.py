"""Tests for outcome recording and self-improvement data loop."""

from datetime import date
from pathlib import Path

import pytest

from src.data.mlb_api import HittingStatsSnapshot, PitchingStatsSnapshot
from src.learning.outcome_recorder import OutcomeRecorder, OutcomeRecordingSettings, compute_actual_value
from src.learning.prediction_archive import PredictionArchive
from src.models.dataclasses import DailyPrediction, PropProjection
from src.simulation.monte_carlo import FantasyScoring
from src.utils.errors import RetrainError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MockMLBAPI:
    def get_final_game_pks(self, game_date: str) -> list[int]:
        return [1]

    def get_actuals_for_date(self, game_date: str):
        hitting = {
            1: HittingStatsSnapshot(hits=2, runs=1, rbi=1, home_runs=0, doubles=1, triples=0, walks=0),
        }
        pitching = {99: PitchingStatsSnapshot(strikeouts=7)}
        return hitting, pitching


def test_compute_actual_values():
    stats = HittingStatsSnapshot(hits=2, runs=1, rbi=1, home_runs=0, doubles=1, triples=0, walks=1)
    fantasy = FantasyScoring()
    assert compute_actual_value(stats, "hits", fantasy) == 2.0
    assert compute_actual_value(stats, "hrr", fantasy) == 4.0
    assert compute_actual_value(stats, "fantasy", fantasy) > 0


def test_outcome_recorder_appends_pairs(tmp_path):
    archive_dir = tmp_path / "predictions"
    pairs_path = tmp_path / "pairs.csv"
    settings = OutcomeRecordingSettings(
        enabled=True,
        pairs_csv_path=str(pairs_path),
        predictions_dir=str(archive_dir),
        archive_on_predict=True,
    )
    archive = PredictionArchive(archive_dir=str(archive_dir), project_root=tmp_path)
    recorder = OutcomeRecorder(
        mlb_api=MockMLBAPI(),
        archive=archive,
        settings=settings,
        project_root=tmp_path,
    )

    prediction = DailyPrediction(
        game_date=date(2026, 6, 25),
        hitter_projections=[
            PropProjection(1, "Hitter One", "hrr", "2026-06-25", 2.0, 0.7),
            PropProjection(1, "Hitter One", "hits", "2026-06-25", 1.2, 0.65),
        ],
        pitcher_projections=[
            PropProjection(99, "Pitcher One", "strikeouts", "2026-06-25", 6.5, 0.6),
        ],
    )
    archive.save(prediction)

    report = recorder.record_for_date("2026-06-25")
    assert report.pairs_appended == 3
    assert pairs_path.exists()

    report2 = recorder.record_for_date("2026-06-25")
    assert report2.pairs_appended == 0
    assert report2.pairs_skipped_duplicate == 3


def test_outcome_recorder_missing_archive():
    recorder = OutcomeRecorder(
        mlb_api=MockMLBAPI(),
        settings=OutcomeRecordingSettings(enabled=True),
        project_root=PROJECT_ROOT,
    )
    with pytest.raises(RetrainError, match="No archived predictions"):
        recorder.record_for_date("2099-01-01")