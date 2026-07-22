"""Tests for outcome recording and self-improvement data loop."""

from datetime import date
import json
import logging
from pathlib import Path

import pytest
import run_record_outcomes

from src.data.mlb_api import HittingStatsSnapshot, PitchingStatsSnapshot
from src.learning.outcome_recorder import OutcomeRecorder, OutcomeRecordingSettings, compute_actual_value
from src.learning.prediction_archive import PredictionArchive
from src.models.dataclasses import (
    DailyPrediction,
    MonteCarloResult,
    OutcomeProbabilities,
    PropProjection,
)
from src.simulation.monte_carlo import FantasyScoring
from src.utils.errors import ConfigError, RetrainError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MockMLBAPI:
    def get_final_game_pks(self, game_date: str) -> list[int]:
        return [1]

    def get_actuals_by_game_for_date(self, game_date: str):
        hitting = {
            1: HittingStatsSnapshot(hits=2, runs=1, rbi=1, home_runs=0, doubles=1, triples=0, walks=0),
        }
        pitching = {99: PitchingStatsSnapshot(strikeouts=7)}
        return {1: (hitting, pitching)}


def test_compute_actual_values():
    stats = HittingStatsSnapshot(hits=2, runs=1, rbi=1, home_runs=0, doubles=1, triples=0, walks=1)
    fantasy = FantasyScoring()
    assert compute_actual_value(stats, "hits", fantasy) == 2.0
    assert compute_actual_value(stats, "total_bases", fantasy) == 3.0
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
            PropProjection(1, "Hitter One", "hrr", "2026-06-25", 2.0, 0.7, mlb_game_pk=1),
            PropProjection(1, "Hitter One", "hits", "2026-06-25", 1.2, 0.65, mlb_game_pk=1),
        ],
        pitcher_projections=[
            PropProjection(99, "Pitcher One", "strikeouts", "2026-06-25", 6.5, 0.6, mlb_game_pk=1),
        ],
        prediction_provenance={"model_version": recorder._model_version},
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


def test_outcome_recorder_rejects_archive_without_decision_time_provenance(tmp_path):
    archive = PredictionArchive(archive_dir="predictions", project_root=tmp_path)
    archive.save(
        DailyPrediction(
            game_date=date(2026, 6, 25),
            hitter_projections=[
                PropProjection(1, "Hitter One", "hits", "2026-06-25", 1.2, 0.65, mlb_game_pk=1)
            ],
        )
    )
    recorder = OutcomeRecorder(
        mlb_api=MockMLBAPI(), archive=archive, project_root=tmp_path, config={"season": 2026}
    )

    with pytest.raises(RetrainError, match="lacks decision-time prediction_provenance"):
        recorder.record_for_date("2026-06-25")


def test_prediction_archive_preserves_exact_probability_artifacts(tmp_path):
    archive = PredictionArchive(archive_dir="predictions", project_root=tmp_path)
    probabilities = OutcomeProbabilities(
        strikeout=0.22,
        walk=0.08,
        home_run=0.04,
        single=0.15,
        double=0.05,
        triple=0.01,
        out_on_bip=0.45,
    )
    simulation = MonteCarloResult(
        n_sims=8000,
        category="hits",
        mean=1.2,
        median=1.0,
        p10=0.0,
        p90=3.0,
        p_ge_threshold={1.0: 0.71, 2.0: 0.34},
    )
    prediction = DailyPrediction(
        game_date=date(2026, 7, 20),
        hitter_projections=[
            PropProjection(
                1,
                "Hitter One",
                "hits",
                "2026-07-20",
                1.2,
                0.6,
                simulation=simulation,
                outcome_probs=probabilities,
                mlb_game_pk=123,
            )
        ],
    )

    archive.save(prediction)
    restored = archive.load("2026-07-20")

    assert restored is not None
    projection = restored.hitter_projections[0]
    assert projection.simulation is not None
    assert projection.simulation.p_ge_threshold == {1.0: 0.71, 2.0: 0.34}
    assert projection.outcome_probs == probabilities


def test_prediction_archive_rejects_malformed_recorded_distribution(tmp_path):
    archive = PredictionArchive(archive_dir="predictions", project_root=tmp_path)
    prediction = DailyPrediction(
        game_date=date(2026, 7, 20),
        hitter_projections=[
            PropProjection(1, "Hitter One", "hits", "2026-07-20", 1.2, 0.6)
        ],
    )
    path = archive.save(prediction)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["hitter_projections"][0]["outcome_probs"] = {"strikeout": 1.0}
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ConfigError, match="outcome_probs missing fields"):
        archive.load("2026-07-20")


def test_prediction_archive_rejects_partial_simulation_probability_map(tmp_path):
    archive = PredictionArchive(archive_dir="predictions", project_root=tmp_path)
    prediction = DailyPrediction(
        game_date=date(2026, 7, 20),
        hitter_projections=[
            PropProjection(
                1,
                "Hitter One",
                "hits",
                "2026-07-20",
                1.2,
                0.6,
                simulation=MonteCarloResult(
                    n_sims=8000,
                    category="hits",
                    mean=1.2,
                    median=1.0,
                    p10=0.0,
                    p90=3.0,
                    p_ge_threshold={1.0: 0.71},
                ),
            )
        ],
    )
    path = archive.save(prediction)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["hitter_projections"][0]["simulation"]["p_ge_threshold"]["bad"] = 0.2
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ConfigError, match="unparseable entry"):
        archive.load("2026-07-20")


def test_outcome_recorder_rejects_grading_time_model_relabel(tmp_path):
    archive = PredictionArchive(archive_dir="predictions", project_root=tmp_path)
    settings = OutcomeRecordingSettings(
        enabled=True,
        pairs_csv_path=str(tmp_path / "pairs.csv"),
        predictions_dir="predictions",
        archive_on_predict=True,
    )
    prediction = DailyPrediction(
        game_date=date(2026, 6, 25),
        hitter_projections=[
            PropProjection(1, "Hitter One", "hits", "2026-06-25", 1.2, 0.65, mlb_game_pk=1)
        ],
        prediction_provenance={"model_version": "prediction-model"},
    )
    archive.save(prediction)
    recorder = OutcomeRecorder(
        mlb_api=MockMLBAPI(),
        archive=archive,
        settings=settings,
        project_root=tmp_path,
        config={"season": 2026},
    )

    with pytest.raises(RetrainError, match="Prediction-time model_version differs"):
        recorder.record_for_date("2026-06-25")


def test_backfill_treats_ineligible_archive_as_failure():
    class IneligibleRecorder:
        def record_for_date(self, game_date):
            raise RetrainError("Prediction archive lacks decision-time prediction_provenance")

    code, reports = run_record_outcomes.run_backfill(
        IneligibleRecorder(), ["2026-07-19"], logging.getLogger("test")
    )

    assert code == run_record_outcomes.EXIT_ERROR
    assert reports[0]["status"] == "ineligible_archive"
