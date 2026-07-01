"""Tests for end-to-end pipeline validation."""

from datetime import date
from pathlib import Path

from src.evaluation.pipeline_validator import PipelineValidator
from src.learning.prediction_archive import PredictionArchive
from src.models.dataclasses import DailyPrediction, PropProjection

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_validate_from_pairs_csv(tmp_path):
    pairs_path = tmp_path / "pairs.csv"
    pairs_path.write_text(
        "player_id,player_name,game_date,category,predicted_value,actual_value,confidence\n"
        "1,Player A,2026-06-25,hrr,2.0,1.8,0.7\n"
        "1,Player A,2026-06-25,hits,1.1,1.0,0.65\n",
        encoding="utf-8",
    )

    archive_dir = tmp_path / "predictions"
    archive = PredictionArchive(archive_dir=str(archive_dir), project_root=tmp_path)
    archive.save(
        DailyPrediction(
            game_date=date(2026, 6, 25),
            hitter_projections=[
                PropProjection(1, "Player A", "hrr", "2026-06-25", 2.0, 0.7),
                PropProjection(1, "Player A", "hits", "2026-06-25", 1.1, 0.65),
            ],
        )
    )

    config = {
        "retraining": {"pairs_csv_path": str(pairs_path), "lookback_days": 0},
        "outcome_recording": {"predictions_dir": str(archive_dir)},
    }
    validator = PipelineValidator(config=config, project_root=tmp_path)
    report = validator.validate_from_pairs_csv(pairs_path=str(pairs_path), lookback_days=0)

    assert report.dates_evaluated == 1
    assert report.total_matched_pairs == 2
    assert report.mean_weighted_mae > 0