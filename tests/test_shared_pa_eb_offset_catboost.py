from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.evaluation.shared_pa_eb_offset_catboost import (
    PA_OUTCOMES,
    SharedPAEBCatBoost,
    _params,
    empirical_bayes_probability,
    expanded_events,
    fit_dirichlet_concentration,
    history_counts,
    league_probability,
    strict_numeric_features,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = json.loads(
    (ROOT / "config/shared_pa_true_eb_offset_development_2023_v1.json").read_text(
        encoding="utf-8"
    )
)


def _frame() -> pd.DataFrame:
    rows = []
    for player, shift in ((11, 0), (22, 1), (33, 2), (44, 3)):
        target = np.roll(np.arange(1, 9), shift)
        history = np.roll(np.arange(8, 0, -1), shift)
        row = {
            "player_id": player,
            "out_pa": int(target.sum()),
            "out_ab": int(target[0] + target[2:7].sum()),
            "out_hits": int(target[2:6].sum()),
            "out_doubles": int(target[3]),
            "out_triples": int(target[4]),
            "out_hr": int(target[5]),
            "out_bb": int(target[1]),
            "out_k": int(target[0]),
            "history_pa": int(history.sum()),
            "x": float(player),
        }
        for name, value in zip(PA_OUTCOMES, history):
            row[f"history_{name}_count"] = int(value)
        rows.append(row)
    return pd.DataFrame(rows)


def test_history_count_denominator_mutation_fails_closed() -> None:
    frame = _frame()
    frame.loc[0, "history_pa"] += 1
    with pytest.raises(ValueError, match="do not equal history PA"):
        history_counts(frame)


def test_empirical_bayes_is_strict_prior_and_unit_mass() -> None:
    frame = _frame()
    league = league_probability(frame)
    posterior = empirical_bayes_probability(frame, league, 20.0)
    assert posterior.shape == (4, 8)
    assert np.allclose(posterior.sum(axis=1), 1.0)
    changed = frame.copy()
    changed.loc[0, "history_home_run_count"] += 1
    changed.loc[0, "history_pa"] += 1
    mutated = empirical_bayes_probability(changed, league, 20.0)
    assert mutated[0, PA_OUTCOMES.index("home_run")] > posterior[0, PA_OUTCOMES.index("home_run")]
    assert np.array_equal(mutated[1:], posterior[1:])


def test_dirichlet_concentration_is_fitted_and_inside_safety_bounds() -> None:
    frame = pd.concat([_frame()] * 5, ignore_index=True)
    frame["player_id"] = np.arange(20)
    league = league_probability(frame)
    concentration = fit_dirichlet_concentration(frame, league)
    assert np.isfinite(concentration)
    assert 0.001 < concentration < 1_000_000.0


def test_expanded_events_keep_each_row_baseline_with_its_weighted_class() -> None:
    frame = _frame()
    league = league_probability(frame)
    baseline = empirical_bayes_probability(frame, league, 20.0)
    features, labels, weights, expanded_baseline = expanded_events(frame, ["x"], baseline)
    assert set(labels) == set(range(8))
    assert len(features) == len(labels) == len(weights) == len(expanded_baseline)
    cursor = 0
    from src.evaluation.shared_pa_eb_offset_catboost import target_counts

    target = target_counts(frame)
    for class_index in range(8):
        mask = target[:, class_index] > 0
        width = int(mask.sum())
        assert np.all(labels[cursor : cursor + width] == class_index)
        assert np.allclose(weights[cursor : cursor + width], target[mask, class_index])
        assert np.allclose(expanded_baseline[cursor : cursor + width], np.log(baseline[mask]))
        cursor += width


def test_wrapper_passes_log_empirical_bayes_as_catboost_baseline() -> None:
    frame = _frame()
    league = league_probability(frame)
    expected = empirical_bayes_probability(frame, league, 20.0)

    class FakeModel:
        def predict_proba(self, pool):
            assert np.allclose(pool.get_baseline(), np.log(expected))
            return expected

    model = SharedPAEBCatBoost(FakeModel(), ("x",), league, 20.0)
    assert np.allclose(model.predict_proba(frame), expected)


def test_catboost_multiclass_pool_baseline_has_numerical_effect() -> None:
    from catboost import CatBoostClassifier, Pool

    x = pd.DataFrame({"x": np.tile([0.0, 1.0], 8)})
    y = np.tile(np.arange(8), 2)
    baseline = np.log(np.full((16, 8), 1.0 / 8.0))
    fitted = CatBoostClassifier(
        loss_function="MultiClass",
        classes_count=8,
        iterations=2,
        depth=1,
        learning_rate=0.1,
        random_seed=1,
        allow_writing_files=False,
        verbose=False,
    ).fit(Pool(x, label=y, baseline=baseline))
    test_x = pd.DataFrame({"x": [0.0, 0.0]})
    test_baseline = np.log(
        np.array(
            [
                [0.80, *([0.20 / 7] * 7)],
                [0.20 / 7, 0.80, *([0.20 / 7] * 6)],
            ]
        )
    )
    predicted = fitted.predict_proba(Pool(test_x, baseline=test_baseline))
    assert predicted[0, 0] > predicted[1, 0]
    assert predicted[1, 1] > predicted[0, 1]


def test_locked_model_parameter_mutation_is_rejected() -> None:
    locked = PROTOCOL["candidate"]["residual_model"]
    assert _params(locked)["iterations"] == 1200
    mutated = dict(locked)
    mutated["iterations"] = 1199
    with pytest.raises(ValueError, match="locked protocol"):
        _params(mutated)


def test_feature_boundary_rejects_missing_nonnumeric_and_infinite() -> None:
    with pytest.raises(ValueError, match="missing"):
        strict_numeric_features(pd.DataFrame({"x": [1]}), ["x", "y"])
    with pytest.raises(ValueError, match="nonnumeric"):
        strict_numeric_features(pd.DataFrame({"x": ["bad"]}), ["x"])
    with pytest.raises(ValueError, match="infinite"):
        strict_numeric_features(pd.DataFrame({"x": [np.inf]}), ["x"])
