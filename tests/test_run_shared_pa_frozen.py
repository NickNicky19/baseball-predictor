from pathlib import Path

import run_shared_pa_frozen as runner


def test_runner_is_locked_frozen_and_writes_only_requested_archive(monkeypatch, tmp_path: Path) -> None:
    seen = {}

    class Archive:
        def __init__(self, archive_dir, project_root=None):
            seen["archive_dir"] = Path(archive_dir)

    class Recorder:
        archive = None

    class Predictor:
        def __init__(self, config_path):
            seen["config_path"] = Path(config_path)
            self.config = {"locked": True}
            self.outcome_recorder = Recorder()
            self.mlb_api = type("API", (), {"clear_cache": lambda self, day: None})()

        def predict(self, game_date, **kwargs):
            seen["date"] = game_date
            seen["kwargs"] = kwargs
            return type("Result", (), {"hitter_projections": [object()]})()

    monkeypatch.setattr(runner, "DailyPredictor", Predictor)
    monkeypatch.setattr(runner, "PredictionArchive", Archive)
    monkeypatch.setattr(runner, "model_version", lambda _: "8c7eed9bb0c3")
    monkeypatch.setattr(runner, "sha256_json", lambda _: "3a62fc507956752086919b757ae683e60f9df8fb2143f83292ac61f07830a60c")
    monkeypatch.setattr(runner, "load_comparator_contract", lambda **_: {
        "contract": {"frozen_formula": {
            "model_version": "8c7eed9bb0c3",
            "effective_config_sha256": "3a62fc507956752086919b757ae683e60f9df8fb2143f83292ac61f07830a60c",
        }}
    })
    assert runner.main(["--date", "2026-07-30", "--archive-dir", str(tmp_path)]) == 0
    assert seen["archive_dir"] == tmp_path.resolve()
    assert seen["kwargs"] == {
        "hitter_categories": ("hits", "hrr", "home_runs"),
        "include_pitchers": True,
        "persist_features": False,
        "archive_predictions": True,
        "capture_prediction_provenance": True,
        "apply_corrections": False,
        "include_edges": False,
        "use_projected_lineups": True,
    }


def test_runner_refuses_sealed_may_without_constructing_predictor(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "DailyPredictor", lambda **_: (_ for _ in ()).throw(AssertionError()))
    assert runner.main(["--date", "2026-05-15", "--archive-dir", str(tmp_path)]) == 3
