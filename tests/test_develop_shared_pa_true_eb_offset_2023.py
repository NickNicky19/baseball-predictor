from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import scripts.develop_shared_pa_true_eb_offset_2023 as runner


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_chronological_splits_match_predeclared_expanding_boundaries() -> None:
    frame = pd.DataFrame(
        {
            "game_date": np.repeat(
                pd.date_range("2023-04-01", periods=10).strftime("%Y-%m-%d"), 2
            )
        }
    )
    folds = runner.chronological_splits(frame, 4)
    assert [(len(train), len(validation)) for train, validation in folds] == [
        (4, 4),
        (8, 4),
        (12, 4),
        (16, 4),
    ]
    for train, validation in folds:
        assert frame.iloc[train].game_date.max() < frame.iloc[validation].game_date.min()


def test_execution_lock_rejects_mutated_code_before_panel_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.py"
    source.write_text("original\n", encoding="utf-8")
    lock = {
        "schema_version": "shared-pa-true-eb-offset-execution-lock-v1",
        "status": "LOCKED_BEFORE_FIRST_V4_1_OUTCOME_SCORE",
        "development_years": [2023],
        "protected_boundaries": {
            "production_changed": False,
            "betting_authorized": False,
            "selection_or_confirmation_opened": False,
            "may_2026_sealed": True,
            "spent_2025_hr_not_reused": True,
        },
        "required_file_hashes": {"source.py": _sha(source)},
        "runtime": {
            "python_version": runner.sys.version.split()[0],
            "distributions": {"pytest": pytest.__version__},
        },
    }
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    runner.verify_execution_lock(lock_path)
    source.write_text("mutated\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        runner.verify_execution_lock(lock_path)


def test_historical_market_path_does_not_consume_target_lineup_slot() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert 'frame["lineup_slot"]' not in source
    protocol = runner.load_protocol(
        Path(runner.__file__).resolve().parents[1]
        / "config/shared_pa_true_eb_offset_development_2023_v1.json"
    )
    assert protocol["pa_opportunity"]["target_game_lineup_slot_consumed"] is False
    assert protocol["market_components"]["historical_game_market_status"].startswith(
        "POOLED_OPPORTUNITY"
    )


def test_output_refuses_overwrite_before_any_model_work(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(ValueError, match="refusing to overwrite"):
        runner.run(
            panel=tmp_path / "not-opened-panel.csv.gz",
            panel_manifest=tmp_path / "not-opened-manifest.json",
            panel_certificate=tmp_path / "not-opened-certificate.json",
            protocol_path=tmp_path / "not-opened-protocol.json",
            execution_lock_path=tmp_path / "not-opened-lock.json",
            output_dir=output,
        )

