from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import scripts.run_shared_pa_pitch_exposure_provisional as runner
from src.evaluation.shared_pa_pitch_exposure_tournament import (
    DEVELOPMENT_SEASON,
    adjust_full_simplex_probability,
    apply_pitch_exposure_transform,
    fit_full_simplex_offset,
    fit_pitch_exposure_transform,
    synthetic_signal_control,
    validate_pitch_exposure_matrix,
)


def _rows() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for player in range(1, 9):
        for day in range(1, 5):
            pitches = player * day * 10
            missing = (player + day) % 3
            observed = pitches - missing
            row: dict[str, object] = {
                "game_date": f"2023-04-{day:02d}",
                "game_pk": player * 10 + day,
                "player_id": player,
                "max_source_date": "2023-03-31" if day == 1 else f"2023-04-{day - 1:02d}",
                "history_pitch_count": pitches,
                "history_distinct_pitch_types": 4 + player % 3,
                "history_pitch_type_entropy": 1.0 + player / 100 + day / 1000,
            }
            for prefix in (
                "history_release_speed",
                "history_pfx_x",
                "history_pfx_z",
                "history_plate_x",
                "history_plate_z",
            ):
                row[f"{prefix}_count"] = observed
                row[f"{prefix}_missing_count"] = missing
            row["history_pitch_type_denominator"] = observed
            row["history_pitch_type_missing_count"] = missing
            for name, center in (
                ("history_release_speed", 92.0),
                ("history_pfx_x", 0.1),
                ("history_pfx_z", 0.8),
                ("history_plate_x", 0.0),
                ("history_plate_z", 2.5),
            ):
                row[f"{name}_mean"] = center + player / 100 + day / 1000
                row[f"{name}_sd"] = 0.5 + player / 1000
            rows.append(row)
    return pd.DataFrame(rows)


def test_matrix_requires_strict_prior_and_exact_partitions() -> None:
    frame = _rows()
    validate_pitch_exposure_matrix(frame)
    same_day = frame.copy()
    same_day.loc[0, "max_source_date"] = same_day.loc[0, "game_date"]
    with pytest.raises(ValueError, match="same-day or future"):
        validate_pitch_exposure_matrix(same_day)
    broken = frame.copy()
    broken.loc[0, "history_plate_x_count"] += 1
    with pytest.raises(ValueError, match="partition differs"):
        validate_pitch_exposure_matrix(broken)


@pytest.mark.parametrize("variant", ["registered_pitch_exposure_block", "support_only", "deterministic_shuffle"])
def test_transforms_are_fold_fit_and_replay_exact(variant: str) -> None:
    frame = _rows()
    transform, fitted = fit_pitch_exposure_transform(frame, variant)
    replay = apply_pitch_exposure_transform(frame, transform)
    assert transform.names
    assert np.isfinite(fitted).all()
    assert np.array_equal(fitted, replay)


def test_full_simplex_adjustment_is_normalized_and_synthetic_signal_passes() -> None:
    assert synthetic_signal_control()
    feature = np.linspace(-2.0, 2.0, 200)[:, None]
    base = np.tile(np.asarray([0.22, 0.09, 0.14, 0.05, 0.01, 0.04, 0.40, 0.05]), (len(feature), 1))
    truth = adjust_full_simplex_probability(
        base,
        feature,
        np.asarray([[0.8], [-0.2], [0.3], [0.5], [0.1], [0.4], [-0.3]]),
    )
    fitted = fit_full_simplex_offset(base, feature, truth * 200.0)
    learned = adjust_full_simplex_probability(base, feature, fitted)
    assert np.allclose(learned.sum(axis=1), 1.0, rtol=0.0, atol=1e-12)


def test_runner_refuses_overwrite_before_opening_inputs() -> None:
    matrix = Path(runner.__file__)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        runner.run(
            panel_path=Path("sealed.csv.gz"),
            panel_manifest_path=Path("sealed.json"),
            matrix_output=matrix,
            result_output=Path("result.json"),
        )


def test_implementation_has_no_network_or_production_path() -> None:
    paths = [
        Path(runner.__file__),
        Path(__file__).resolve().parents[1]
        / "src/evaluation/shared_pa_pitch_exposure_tournament.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for forbidden in (
        "requests.",
        "urllib",
        "socket.",
        "pybaseball",
        "data/predictions",
        "data\\predictions",
    ):
        assert forbidden not in source
    assert DEVELOPMENT_SEASON == 2023
    assert "PROVISIONAL_NON_PROMOTABLE_PENDING_QUALIFIED_SOURCE_CONFIRMATION" in source
    assert '"target_pitcher_identity_consumed": False' in source
