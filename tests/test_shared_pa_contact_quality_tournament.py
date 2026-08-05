from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import scripts.run_shared_pa_contact_quality_provisional as runner
from src.evaluation.shared_pa_contact_quality_tournament import (
    CONTACT_INDICES,
    DEVELOPMENT_SEASON,
    NONCONTACT_INDICES,
    adjust_contact_probability,
    apply_contact_transform,
    fit_contact_offset,
    fit_contact_transform,
    synthetic_signal_control,
    total_bases_pmf,
    validate_contact_matrix,
)


def _contact_rows() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for player in range(1, 9):
        for day in range(1, 5):
            bip = player * day * 5
            missing = (player + day) % 3
            joint = bip - missing
            ground = joint // 2
            line = joint // 5
            fly = joint // 5
            popup = joint - ground - line - fly
            hard = joint // 3
            sweet = joint // 4
            both = min(hard, sweet) // 2
            classified_hard = hard
            barrel = classified_hard // 4
            rows.append(
                {
                    "game_date": f"2023-04-{day:02d}",
                    "game_pk": player * 10 + day,
                    "player_id": player,
                    "history_contact_bip": bip,
                    "history_contact_ev_denominator": joint,
                    "history_contact_ev_missing_count": missing,
                    "history_contact_ev50_count": int(np.ceil(joint / 2)),
                    "history_contact_ev50_mean": 95.0 + player + day / 10,
                    "history_contact_joint_denominator": joint,
                    "history_contact_joint_missing_count": missing,
                    "history_contact_hard_hit_count_joint": hard,
                    "history_contact_sweet_spot_count_joint": sweet,
                    "history_contact_hard_hit_sweet_spot_count_joint": both,
                    "history_contact_ground_count_joint": ground,
                    "history_contact_line_count_joint": line,
                    "history_contact_fly_count_joint": fly,
                    "history_contact_popup_count_joint": popup,
                    "history_contact_speed_angle_denominator": joint,
                    "history_contact_speed_angle_missing_count": missing,
                    "history_contact_barrel_count_classified": barrel,
                    "history_contact_classified_hard_hit_count": classified_hard,
                    "history_contact_max_source_date": (
                        "2023-03-31" if day == 1 else f"2023-04-{day - 1:02d}"
                    ),
                }
            )
    return pd.DataFrame(rows)


def test_contact_matrix_requires_strict_prior_and_exact_populations() -> None:
    frame = _contact_rows()
    validate_contact_matrix(frame)
    same_day = frame.copy()
    same_day.loc[0, "history_contact_max_source_date"] = same_day.loc[0, "game_date"]
    with pytest.raises(ValueError, match="same-day or future"):
        validate_contact_matrix(same_day)
    broken = frame.copy()
    broken.loc[0, "history_contact_line_count_joint"] += 1
    with pytest.raises(ValueError, match="partition"):
        validate_contact_matrix(broken)


@pytest.mark.parametrize(
    "variant",
    ["raw_contact_block", "fold_shrunk_contact_block", "support_only", "deterministic_shuffle"],
)
def test_contact_feature_transforms_are_fold_fit_and_replay_exact(variant: str) -> None:
    frame = _contact_rows()
    transform, fitted = fit_contact_transform(frame, variant)
    replay = apply_contact_transform(frame, transform)
    assert transform.names
    assert np.isfinite(fitted).all()
    assert np.array_equal(fitted, replay)


def test_contact_adjustment_preserves_contact_mass_and_noncontact_classes() -> None:
    base = np.tile(
        np.asarray([0.22, 0.09, 0.14, 0.05, 0.01, 0.04, 0.40, 0.05]),
        (3, 1),
    )
    features = np.asarray([[-1.0], [0.0], [1.0]])
    coefficients = np.asarray([[0.2], [0.4], [-0.1], [0.8]])
    adjusted = adjust_contact_probability(base, features, coefficients)
    assert np.array_equal(adjusted[:, NONCONTACT_INDICES], base[:, NONCONTACT_INDICES])
    assert np.allclose(
        adjusted[:, CONTACT_INDICES].sum(axis=1),
        base[:, CONTACT_INDICES].sum(axis=1),
        rtol=0.0,
        atol=1e-12,
    )
    assert np.allclose(adjusted.sum(axis=1), 1.0, rtol=0.0, atol=1e-12)


def test_contact_synthetic_signal_and_offset_direction() -> None:
    assert synthetic_signal_control()
    x = np.linspace(-2.0, 2.0, 200)[:, None]
    base = np.tile(
        np.asarray([0.22, 0.09, 0.14, 0.05, 0.01, 0.04, 0.40, 0.05]),
        (len(x), 1),
    )
    truth = adjust_contact_probability(
        base, x, np.asarray([[0.3], [0.6], [0.1], [1.0]])
    )
    fitted = fit_contact_offset(base, x, truth * 200.0)
    assert np.all(fitted[:, 0] > 0.0)


def test_total_bases_pmf_is_normalized_and_uses_shared_pa_distribution() -> None:
    probability = np.tile(
        np.asarray([0.22, 0.09, 0.14, 0.05, 0.01, 0.04, 0.40, 0.05]),
        (4, 1),
    )
    pmf = total_bases_pmf(probability, [{3: 0.25, 4: 0.75}] * 4, 4)
    assert pmf.shape == (4, 17)
    assert np.allclose(pmf.sum(axis=1), 1.0, rtol=0.0, atol=1e-12)
    assert (pmf[:, 16] > 0.0).all()


def test_runner_refuses_overwrite_before_opening_inputs(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.csv.gz"
    matrix.write_bytes(b"existing")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        runner.run(
            panel_path=tmp_path / "sealed.csv.gz",
            contact_panel_path=tmp_path / "sealed-contact.csv.gz",
            contact_manifest_path=tmp_path / "sealed.json",
            matrix_output=matrix,
            result_output=tmp_path / "result.json",
        )


def test_contact_tournament_implementation_has_no_network_or_production_path() -> None:
    paths = [
        Path(runner.__file__),
        Path(__file__).resolve().parents[1]
        / "src/evaluation/shared_pa_contact_quality_tournament.py",
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
    assert "expected_stat_fields_consumed\": False" in source
