"""Tests for automated retraining infrastructure."""

from pathlib import Path
import json

import pytest

from src.learning.retrain_runner import RetrainRunner, RetrainSettings
from src.utils.errors import RetrainError

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_retrain_runner_fits_and_saves(tmp_path):
    settings = RetrainSettings(
        pairs_csv_path=str(FIXTURES / "sample_pairs.csv"),
        output_state_path=str(tmp_path / "bias_corrections.json"),
        min_pairs=10,
        lookback_days=0,
        save_retrain_result=False,
    )
    runner = RetrainRunner(settings=settings, project_root=PROJECT_ROOT, config={})
    report = runner.run(save_state=True)

    assert report.sample_size >= 10
    assert report.confidence > 0
    assert Path(report.state_path).exists()
    payload = json.loads(Path(report.state_path).read_text(encoding="utf-8"))
    assert payload["version"] == "2.0"
    assert payload["promotion_status"] == "RESEARCH_ONLY"
    assert payload["training_cutoff"]
    assert payload["market_scope"]
    for name in (
        "source_sha256", "protocol_sha256", "code_sha256",
        "config_sha256", "test_sha256",
    ):
        assert len(payload[name]) == 64


def test_retrain_runner_insufficient_pairs():
    settings = RetrainSettings(
        pairs_csv_path=str(FIXTURES / "sample_pairs.csv"),
        min_pairs=1000,
        lookback_days=0,
    )
    runner = RetrainRunner(settings=settings, project_root=PROJECT_ROOT, config={})
    with pytest.raises(RetrainError, match="Insufficient pairs"):
        runner.run()


def test_retrain_runner_missing_file():
    settings = RetrainSettings(pairs_csv_path="nonexistent.csv", min_pairs=5)
    runner = RetrainRunner(settings=settings, project_root=PROJECT_ROOT, config={})
    with pytest.raises(RetrainError, match="not found"):
        runner.run()
