from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import scripts.run_shared_pa_batter_recency_provisional as runner
from src.evaluation.shared_pa_batter_recency_tournament import (
    DEVELOPMENT_SEASON,
    RECENCY_PAIRS,
    _raw_features,
    adjust_full_simplex_probability,
    apply_batter_recency_transform,
    fit_batter_recency_transform,
    fit_full_simplex_offset,
    synthetic_signal_control,
    validate_batter_recency_matrix,
)


def _rows() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for player in range(1, 9):
        for day in range(1, 6):
            history_pa = player * day + 8
            row: dict[str, object] = {
                "season": DEVELOPMENT_SEASON,
                "game_date": f"2023-04-{day:02d}",
                "game_pk": player * 10 + day,
                "player_id": player,
                "max_source_date": "2023-03-31" if day == 1 else f"2023-04-{day - 1:02d}",
                "history_pa": history_pa,
                "history_pa_age_days_count": history_pa,
                "history_pa_age_days_missing_count": 0,
                "history_pa_age_days_mean": 3.0 + player / 10 + day,
                "history_pa_age_days_sd": 1.0 + player / 100,
                "days_since_pa": 1 + (player + day) % 3,
            }
            for index, (count_name, recency_name) in enumerate(RECENCY_PAIRS):
                count = (player + day + index) % 4
                row[count_name] = count
                row[recency_name] = None if count == 0 else 1 + player + day + index
            rows.append(row)
    return pd.DataFrame(rows)


def test_matrix_requires_strict_prior_and_exact_recency_missingness() -> None:
    frame = _rows()
    validate_batter_recency_matrix(frame)
    same_day = frame.copy()
    same_day.loc[0, "max_source_date"] = same_day.loc[0, "game_date"]
    with pytest.raises(ValueError, match="same-day or future"):
        validate_batter_recency_matrix(same_day)
    broken = frame.copy()
    broken.loc[0, "history_pa_age_days_count"] += 1
    with pytest.raises(ValueError, match="PA-age partition"):
        validate_batter_recency_matrix(broken)
    fabricated = frame.copy()
    count_name, recency_name = RECENCY_PAIRS[0]
    fabricated.loc[0, count_name] = 0
    fabricated.loc[0, recency_name] = 4
    with pytest.raises(ValueError, match="zero count"):
        validate_batter_recency_matrix(fabricated)


def test_registered_variants_have_exact_nonduplicated_feature_membership() -> None:
    frame = _rows()
    recency_names, _ = _raw_features(frame, "outcome_recency")
    extended_names, _ = _raw_features(frame, "outcome_recency_plus_age")

    assert len(recency_names) == len(set(recency_names))
    assert "recency_log1p_history_pa" not in recency_names
    assert "recency_pa_age_missing_share" not in recency_names
    assert not any(name.startswith("history_pa_age_days_") for name in recency_names)

    assert len(extended_names) == len(set(extended_names))
    assert extended_names.count("recency_log1p_history_pa") == 1
    assert extended_names.count("recency_pa_age_missing_share") == 1
    assert "history_pa_age_days_mean_log1p" in extended_names
    assert "history_pa_age_days_sd_log1p" in extended_names


@pytest.mark.parametrize(
    "variant",
    [
        "support_only",
        "missingness_only",
        "outcome_recency",
        "outcome_recency_plus_age",
        "deterministic_shuffle",
    ],
)
def test_transforms_are_fold_fit_and_replay_exact(variant: str) -> None:
    frame = _rows()
    transform, fitted = fit_batter_recency_transform(frame, variant)
    replay = apply_batter_recency_transform(frame, transform)
    assert transform.names
    assert np.isfinite(fitted).all()
    assert np.array_equal(fitted, replay)


def test_full_simplex_adjustment_is_normalized_and_synthetic_signal_passes() -> None:
    assert synthetic_signal_control()
    feature = np.linspace(-2.0, 2.0, 200)[:, None]
    base = np.tile(
        np.asarray([0.22, 0.09, 0.14, 0.05, 0.01, 0.04, 0.40, 0.05]),
        (len(feature), 1),
    )
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
        / "src/evaluation/shared_pa_batter_recency_tournament.py",
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
    assert '"network_request_performed": False' in source
