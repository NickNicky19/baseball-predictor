from __future__ import annotations

from datetime import date
from pathlib import Path

import run_shared_pa_total_bases as runner
from src.models.dataclasses import DailyPrediction, PropProjection


def test_runner_uses_separate_hash_bound_candidate_and_no_policy(monkeypatch, tmp_path) -> None:
    seen: dict = {}

    class ArchiveBoundary:
        def __init__(self, archive_dir, project_root=None):
            seen["archive_dir"] = Path(archive_dir)
            seen["project_root"] = Path(project_root)

    class RecorderBoundary:
        archive = object()

    class PredictorBoundary:
        def __init__(self, config=None):
            seen["config"] = config
            self.mlb_api = object()
            self.outcome_recorder = RecorderBoundary()

        def predict(self, *args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            return DailyPrediction(
                game_date=date(2026, 7, 30),
                hitter_projections=[PropProjection(
                    player_id=1, player_name="Player", category="total_bases",
                    game_date="2026-07-30", projected_value=1.4, confidence=0.5,
                )],
            )

    monkeypatch.setattr(runner, "DailyPredictor", PredictorBoundary)
    monkeypatch.setattr(runner, "PredictionArchive", ArchiveBoundary)
    output = tmp_path / "tb"
    assert runner.main(["--date", "2026-07-30", "--archive-dir", str(output)]) == runner.EXIT_OK
    assert seen["archive_dir"] == output.resolve()
    assert seen["config"]["total_bases"]["status"] == "candidate_unpromoted"
    assert seen["kwargs"] == {
        "hitter_categories": ("total_bases",),
        "include_pitchers": False,
        "persist_features": False,
        "archive_predictions": True,
        "capture_prediction_provenance": True,
        "apply_corrections": False,
        "include_edges": False,
        "use_projected_lineups": True,
    }


def test_runner_rejects_may_before_constructing_predictor(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        runner, "DailyPredictor",
        lambda **_: (_ for _ in ()).throw(AssertionError("predictor must not be constructed")),
    )
    assert runner.main([
        "--date", "2026-05-15", "--archive-dir", str(tmp_path)
    ]) == runner.EXIT_CONFIG
