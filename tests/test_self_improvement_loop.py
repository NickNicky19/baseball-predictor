"""End-to-end self-improvement loop test with mocks."""

from datetime import date
from pathlib import Path

from src.learning.outcome_recorder import OutcomeRecorder, OutcomeRecordingSettings
from src.learning.prediction_archive import PredictionArchive
from src.learning.retrain_runner import RetrainRunner, RetrainSettings
from src.models.dataclasses import DailyPrediction, PropProjection

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


class MockMLBAPI:
    def get_final_game_pks(self, game_date: str) -> list[int]:
        return [1]

    def get_actuals_by_game_for_date(self, game_date: str):
        from src.data.mlb_api import HittingStatsSnapshot

        return {1: ({
            1: HittingStatsSnapshot(hits=2, runs=1, rbi=1, home_runs=0, doubles=0, triples=0, walks=0),
        }, {})}


def test_full_loop_archive_record_retrain(tmp_path):
    archive_dir = tmp_path / "predictions"
    pairs_path = tmp_path / "pairs.csv"

    prediction = DailyPrediction(
        game_date=date(2026, 6, 25),
        hitter_projections=[
            PropProjection(1, "Player A", "hrr", "2026-06-25", 2.0, 0.7, mlb_game_pk=1),
        ],
    )

    archive = PredictionArchive(archive_dir=str(archive_dir), project_root=tmp_path)
    archive.save(prediction)

    recorder = OutcomeRecorder(
        mlb_api=MockMLBAPI(),
        archive=archive,
        settings=OutcomeRecordingSettings(
            enabled=True,
            pairs_csv_path=str(pairs_path),
            predictions_dir=str(archive_dir),
        ),
        project_root=tmp_path,
    )
    report = recorder.record_for_date("2026-06-25")
    assert report.pairs_appended == 1

    runner = RetrainRunner(
        settings=RetrainSettings(
            pairs_csv_path=str(pairs_path),
            output_state_path=str(tmp_path / "bias.json"),
            min_pairs=1,
            lookback_days=0,
            save_retrain_result=False,
        ),
        project_root=tmp_path,
        config={"calibration": {"min_samples_per_category": 1}},
    )
    retrain = runner.run()
    assert retrain.sample_size >= 1
    assert (tmp_path / "bias.json").exists()
