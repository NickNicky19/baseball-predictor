from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import scripts.run_shared_pa_plate_discipline_provisional as runner
from src.evaluation.shared_pa_plate_discipline_tournament import (
    COUNT_COLUMNS,
    PA_OUTCOMES,
    adjusted_probability,
    apply_feature_transform,
    build_strict_prior_plate_matrix,
    chronological_splits,
    count_pmf,
    daily_plate_counts,
    fit_feature_transform,
    fit_kbb_offset,
    paired_cluster_interval,
    synthetic_signal_control,
)


def _pitch(
    number: int,
    description: str | None,
    zone: int | None,
    *,
    game_date: str = "2023-04-01",
    game_pk: int = 1,
) -> dict[str, object]:
    return {
        "game_date": game_date,
        "game_pk": game_pk,
        "batter": 7,
        "at_bat_number": 1,
        "pitch_number": number,
        "game_type": "R",
        "description": description,
        "zone": zone,
    }


def test_daily_plate_counts_cover_exact_nonredundant_partitions() -> None:
    frame = pd.DataFrame(
        [
            _pitch(1, "called_strike", 5),
            _pitch(2, "swinging_strike", 12),
            _pitch(3, "foul_bunt", 4),
            _pitch(4, "ball", 14),
            _pitch(5, None, None),
        ]
    )
    row = daily_plate_counts(frame, player_id=7).iloc[0]
    assert row["pd_pitch_count"] == 4
    assert row["pd_description_missing_count"] == 1
    assert row["pd_swing_count"] == 2
    assert row["pd_take_count"] == 2
    assert row["pd_contact_count"] == 1
    assert row["pd_whiff_count"] == 1
    assert row["pd_chase_opportunity_count"] == 2
    assert row["pd_chase_swing_count"] == 1
    assert row["pd_zone_opportunity_count"] == 2
    assert row["pd_zone_swing_count"] == 1
    assert row["pd_called_strike_count"] == 1


def test_strict_prior_matrix_excludes_same_day(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    source_path = raw_root / "2023" / "batter_7.csv"
    source_path.parent.mkdir(parents=True)
    pd.DataFrame(
        [
            _pitch(1, "called_strike", 5, game_date="2023-04-01", game_pk=1),
            _pitch(1, "swinging_strike", 12, game_date="2023-04-02", game_pk=2),
        ]
    ).to_csv(source_path, index=False)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    targets = pd.DataFrame(
        {
            "game_date": ["2023-04-02", "2023-04-03"],
            "game_pk": [2, 3],
            "player_id": [7, 7],
        }
    )
    matrix = build_strict_prior_plate_matrix(
        targets,
        raw_root=raw_root,
        raw_source_sha256={"2023\\batter_7.csv": source_hash},
    )
    assert matrix["pd_pitch_count"].tolist() == [1, 2]
    assert matrix["pd_max_source_date"].dt.strftime("%Y-%m-%d").tolist() == [
        "2023-04-01",
        "2023-04-02",
    ]


def test_raw_source_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    source_path = raw_root / "2023" / "batter_7.csv"
    source_path.parent.mkdir(parents=True)
    pd.DataFrame([_pitch(1, "ball", 14)]).to_csv(source_path, index=False)
    targets = pd.DataFrame({"game_date": ["2023-04-02"], "game_pk": [2], "player_id": [7]})
    with pytest.raises(ValueError, match="hash differs"):
        build_strict_prior_plate_matrix(
            targets,
            raw_root=raw_root,
            raw_source_sha256={"2023\\batter_7.csv": "0" * 64},
        )


def test_unknown_description_and_duplicate_pitch_fail() -> None:
    with pytest.raises(ValueError, match="unknown plate-discipline"):
        daily_plate_counts(pd.DataFrame([_pitch(1, "future_new_event", 5)]), player_id=7)
    duplicated = pd.DataFrame([_pitch(1, "ball", 14), _pitch(1, "ball", 14)])
    with pytest.raises(ValueError, match="duplicated"):
        daily_plate_counts(duplicated, player_id=7)


def _plate_rows() -> pd.DataFrame:
    rows = []
    for player in range(1, 9):
        for day in range(1, 5):
            pitches = player * day * 10
            swings = pitches // 3 + player
            whiffs = min(swings, swings // 4 + day % 2)
            chase_opp = pitches // 3
            chase = min(chase_opp, player * day + day % 2)
            zone_opp = pitches // 2
            zone_swing = min(zone_opp, max(0, swings - chase))
            takes = pitches - swings
            rows.append(
                {
                    "player_id": player,
                    "game_date": f"2023-04-{day:02d}",
                    "pd_pitch_count": pitches,
                    "pd_description_missing_count": 0,
                    "pd_swing_count": swings,
                    "pd_take_count": takes,
                    "pd_contact_count": swings - whiffs,
                    "pd_whiff_count": whiffs,
                    "pd_chase_opportunity_count": chase_opp,
                    "pd_chase_swing_count": chase,
                    "pd_zone_opportunity_count": zone_opp,
                    "pd_zone_swing_count": zone_swing,
                    "pd_zone_missing_count": pitches - chase_opp - zone_opp,
                    "pd_called_strike_count": min(takes, player * day),
                }
            )
    return pd.DataFrame(rows)


@pytest.mark.parametrize(
    "variant",
    ["raw_rates", "fold_shrunk_rates", "fold_shrunk_rates_support", "missingness_only"],
)
def test_feature_transforms_are_fold_fit_and_finite(variant: str) -> None:
    frame = _plate_rows()
    if variant == "missingness_only":
        zero_rows = frame.index[::2]
        for name in (
            "pd_pitch_count",
            "pd_swing_count",
            "pd_take_count",
            "pd_contact_count",
            "pd_whiff_count",
            "pd_chase_opportunity_count",
            "pd_chase_swing_count",
            "pd_zone_opportunity_count",
            "pd_zone_swing_count",
            "pd_called_strike_count",
        ):
            frame.loc[zero_rows, name] = 0
    transform, fitted = fit_feature_transform(frame, variant)
    replay = apply_feature_transform(frame, transform)
    assert transform.names
    assert np.isfinite(fitted).all()
    assert np.array_equal(fitted, replay)


def test_adjustment_preserves_one_simplex_and_non_kbb_relative_odds() -> None:
    base = np.tile(np.asarray([0.22, 0.09, 0.14, 0.05, 0.01, 0.04, 0.40, 0.05]), (3, 1))
    features = np.asarray([[-1.0], [0.0], [1.0]])
    adjusted = adjusted_probability(base, features, np.asarray([[0.5], [-0.3]]))
    assert np.allclose(adjusted.sum(axis=1), 1.0, rtol=0.0, atol=1e-12)
    assert np.allclose(
        adjusted[:, 2] / adjusted[:, 6],
        base[:, 2] / base[:, 6],
        rtol=0.0,
        atol=1e-12,
    )


def test_synthetic_signal_control_and_offset_direction() -> None:
    assert synthetic_signal_control()
    x = np.linspace(-2.0, 2.0, 200)[:, None]
    base = np.tile(np.asarray([0.22, 0.09, 0.14, 0.05, 0.01, 0.04, 0.40, 0.05]), (len(x), 1))
    truth = adjusted_probability(base, x, np.asarray([[0.9], [-0.7]]))
    counts = truth * 100.0
    coefficient = fit_kbb_offset(base, x, counts)
    assert coefficient[0, 0] > 0
    assert coefficient[1, 0] < 0


def test_chronological_folds_never_mix_target_dates() -> None:
    frame = pd.DataFrame({"game_date": np.repeat(pd.date_range("2023-04-01", periods=10).strftime("%Y-%m-%d"), 2)})
    folds = chronological_splits(frame, 4)
    assert [(len(train), len(validation)) for train, validation in folds] == [(4, 4), (8, 4), (12, 4), (16, 4)]
    for train, validation in folds:
        assert frame.iloc[train]["game_date"].max() < frame.iloc[validation]["game_date"].min()


def test_count_pmf_and_cluster_interval_are_deterministic() -> None:
    probability = np.asarray([0.1, 0.2, 0.3, 0.4])
    pmf = count_pmf(probability, [{3: 1.0}] * 4, 4)
    assert np.allclose(pmf.sum(axis=1), 1.0)
    first = paired_cluster_interval(
        np.asarray([0.1, 0.2, 0.3, 0.4]),
        np.asarray([0.2, 0.3, 0.4, 0.5]),
        [1, 1, 2, 2],
        draws=100,
        seed=7,
    )
    second = paired_cluster_interval(
        np.asarray([0.1, 0.2, 0.3, 0.4]),
        np.asarray([0.2, 0.3, 0.4, 0.5]),
        [1, 1, 2, 2],
        draws=100,
        seed=7,
    )
    assert first == second


def test_runner_refuses_overwrite_before_opening_inputs(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.csv.gz"
    matrix.write_bytes(b"existing")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        runner.run(
            panel_path=tmp_path / "sealed.csv.gz",
            panel_manifest_path=tmp_path / "sealed.json",
            raw_root=tmp_path / "raw",
            matrix_output=matrix,
            result_output=tmp_path / "result.json",
        )


def test_implementation_has_no_network_or_production_path() -> None:
    paths = [
        Path(runner.__file__),
        Path(__file__).resolve().parents[1] / "src/evaluation/shared_pa_plate_discipline_tournament.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    for forbidden in ("requests.", "urllib", "socket.", "pybaseball", "data/predictions", "data\\predictions"):
        assert forbidden not in source
    assert "DEVELOPMENT_SEASON = 2023" in source
    assert all(name in source for name in COUNT_COLUMNS)
