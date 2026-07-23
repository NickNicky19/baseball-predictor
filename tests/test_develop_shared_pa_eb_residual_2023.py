from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "develop_shared_pa_eb_residual_2023",
    ROOT / "scripts" / "develop_shared_pa_eb_residual_2023.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_locked_protocol_has_no_later_year_or_context_escape_hatch():
    contract = MODULE._load_contract(ROOT / "config" / "shared_pa_eb_augmented_development_2023_v3.json")
    boundary = contract["evidence_boundary"]
    assert boundary["development_years"] == [2023]
    forbidden = " ".join(boundary["forbidden"]).lower()
    for token in ("2024", "2025", "may 2026", "pitcher", "lineup", "market", "realized pa"):
        assert token in forbidden


def test_chronological_split_rejects_insufficient_dates():
    frame = pd.DataFrame({"game_date": ["2023-03-30", "2023-03-31", "2023-04-01"]})
    with pytest.raises(ValueError, match="not enough distinct dates"):
        MODULE._date_splits(frame, 3)


def test_chronological_split_has_strictly_prior_training_dates():
    frame = pd.DataFrame({"game_date": [f"2023-04-{day:02d}" for day in range(1, 13)]})
    splits = MODULE._date_splits(frame, 3)
    dates = pd.to_datetime(frame["game_date"]).dt.date
    for train_rows, validation_rows in splits:
        assert dates.iloc[train_rows].max() < dates.iloc[validation_rows].min()


def test_oof_coverage_does_not_label_initial_training_rows_as_zero_loss():
    outer_splits = [(pd.array([0, 1]).to_numpy(dtype=int), pd.array([2, 3]).to_numpy(dtype=int)), (pd.array([0, 1, 2, 3]).to_numpy(dtype=int), pd.array([4, 5]).to_numpy(dtype=int))]
    summary = MODULE._oof_coverage_summary(total_rows=6, outer_splits=outer_splits, evaluated=pd.array([False, False, True, True, True, True]).to_numpy(dtype=bool))
    assert summary["initial_training_only_rows"] == 2
    assert summary["coverage_loss_within_scored_windows"] == 0
    assert summary["oof_coverage_of_all_2023_rows"] == pytest.approx(4 / 6)


def test_oof_coverage_fails_closed_when_a_validation_prediction_is_missing():
    outer_splits = [(pd.array([0, 1]).to_numpy(dtype=int), pd.array([2, 3]).to_numpy(dtype=int))]
    with pytest.raises(ValueError, match="does not exactly match"):
        MODULE._oof_coverage_summary(total_rows=4, outer_splits=outer_splits, evaluated=pd.array([False, False, True, False]).to_numpy(dtype=bool))


def test_existing_evidence_output_is_never_overwritten(tmp_path: Path):
    report = tmp_path / "report.json"
    predictions = tmp_path / "predictions.csv"
    report.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        MODULE.run(
            panel=tmp_path / "missing.csv.gz",
            manifest=tmp_path / "missing.json",
            protocol=ROOT / "config" / "shared_pa_eb_augmented_development_2023_v3.json",
            report=report,
            predictions=predictions,
        )
