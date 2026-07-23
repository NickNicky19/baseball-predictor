from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.evaluation.multi_market_foundation import PA_OUTCOMES
from src.learning.shared_pa_model import (
    SharedPACatBoost,
    binary_class_metrics,
    fit_catboost,
    fit_rate_baseline,
    paired_date_block_interval,
    per_row_proper_loss,
    proper_scores,
    temperature_scale,
)


def _counts() -> pd.DataFrame:
    return pd.DataFrame(
        [
            [1, 1, 1, 0, 0, 0, 1, 0],
            [1, 0, 1, 1, 0, 0, 1, 0],
        ],
        columns=PA_OUTCOMES,
    )


def _probabilities() -> np.ndarray:
    counts = _counts().to_numpy(float)
    return counts / counts.sum(axis=1, keepdims=True)


@pytest.mark.parametrize("consumer", [proper_scores, per_row_proper_loss, binary_class_metrics])
@pytest.mark.parametrize(
    "bad_value,match",
    [
        (float("nan"), "finite"),
        (-1.0, "nonnegative"),
        (0.5, "integral"),
    ],
)
def test_mutation_invalid_outcome_counts_cannot_enter_scoring(consumer, bad_value, match) -> None:
    counts = _counts().astype(float)
    counts.iloc[0, 0] = bad_value
    with pytest.raises(ValueError, match=match):
        consumer(counts, _probabilities())


@pytest.mark.parametrize("consumer", [proper_scores, per_row_proper_loss, binary_class_metrics])
@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda values: values.__setitem__((0, 0), float("nan")), "finite"),
        (lambda values: (values.__setitem__((0, 0), -0.1), values.__setitem__((0, 1), 0.6)), "within"),
        (lambda values: values.__setitem__((0, 0), values[0, 0] + 0.1), "sum to one"),
    ],
)
def test_mutation_invalid_probabilities_cannot_enter_scoring(consumer, mutation, match) -> None:
    probabilities = _probabilities()
    mutation(probabilities)
    with pytest.raises(ValueError, match=match):
        consumer(_counts(), probabilities)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda values: values.__setitem__((0, 0), float("nan")), "finite"),
        (lambda values: (values.__setitem__((0, 0), -0.1), values.__setitem__((0, 1), 0.6)), "within"),
        (lambda values: values.__setitem__((0, 0), values[0, 0] + 0.1), "sum to one"),
    ],
)
def test_mutation_temperature_scaling_cannot_repair_invalid_inputs(mutation, match) -> None:
    probabilities = _probabilities()
    mutation(probabilities)
    with pytest.raises(ValueError, match=match):
        temperature_scale(probabilities, 1.0)


@pytest.mark.parametrize("temperature", [True, 0.0, -1.0, float("nan"), float("inf"), "bad"])
def test_mutation_invalid_temperature_fails_closed(temperature) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        temperature_scale(_probabilities(), temperature)


@pytest.mark.parametrize("draws", [0, -1, True, 1.5])
def test_mutation_invalid_bootstrap_draw_count_fails_closed(draws) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        paired_date_block_interval(
            _counts(), _probabilities(), _probabilities(),
            pd.Series(["2024-04-01", "2024-04-02"]),
            metric="brier", draws=draws, seed=7,
        )


@pytest.mark.parametrize("seed", [True, 1.5, "7"])
def test_mutation_invalid_bootstrap_seed_fails_closed(seed) -> None:
    with pytest.raises(ValueError, match="seed must be an integer"):
        paired_date_block_interval(
            _counts(), _probabilities(), _probabilities(),
            pd.Series(["2024-04-01", "2024-04-02"]),
            metric="brier", draws=10, seed=seed,
        )


def test_mutation_bootstrap_date_count_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="date/count row mismatch"):
        paired_date_block_interval(
            _counts(), _probabilities(), _probabilities(),
            pd.Series(["2024-04-01"]), metric="brier", draws=10, seed=7,
        )


@pytest.mark.parametrize("prior", [True, -1.0, float("nan"), float("inf"), "bad"])
def test_mutation_invalid_empirical_bayes_prior_fails_closed(prior) -> None:
    frame = pd.DataFrame({
        "player_id": [1, 2],
        "lineup_slot": [1, 2],
        "out_pa": [4, 4],
        "out_ab": [3, 4],
        "out_hits": [1, 2],
        "out_doubles": [0, 1],
        "out_triples": [0, 0],
        "out_hr": [0, 0],
        "out_bb": [1, 0],
        "out_k": [1, 1],
    })
    with pytest.raises(ValueError, match="finite and nonnegative"):
        fit_rate_baseline(
            frame,
            kind="empirical_bayes_player_rate",
            prior_strength_pa=prior,
        )


def test_valid_scoring_and_bootstrap_remain_reproducible() -> None:
    scores = proper_scores(_counts(), _probabilities())
    assert scores["multiclass_brier"] >= 0.0
    first = paired_date_block_interval(
        _counts(), _probabilities(), np.roll(_probabilities(), 1, axis=1),
        pd.Series(["2024-04-01", "2024-04-02"]),
        metric="log_loss", draws=50, seed=7,
    )
    second = paired_date_block_interval(
        _counts(), _probabilities(), np.roll(_probabilities(), 1, axis=1),
        pd.Series(["2024-04-01", "2024-04-02"]),
        metric="log_loss", draws=50, seed=7,
    )
    assert first == second


@pytest.mark.parametrize("reserved", ["loss_function", "random_seed", "verbose"])
def test_mutation_catboost_params_cannot_override_locked_identity(reserved) -> None:
    pytest.importorskip("catboost")
    with pytest.raises(ValueError, match="cannot override locked fields"):
        fit_catboost(
            pd.DataFrame({"x": [1.0]}),
            features=["x"],
            params={reserved: 123},
            seed=7,
        )


@pytest.mark.parametrize("seed", [True, 1.5, "7"])
def test_mutation_catboost_seed_must_be_exact_integer(seed) -> None:
    pytest.importorskip("catboost")
    with pytest.raises(ValueError, match="seed must be an integer"):
        fit_catboost(
            pd.DataFrame({"x": [1.0]}),
            features=["x"],
            params={},
            seed=seed,
        )


class _InvalidProbabilityModel:
    def __init__(self, probabilities: np.ndarray) -> None:
        self.probabilities = probabilities

    def predict_proba(self, pool) -> np.ndarray:
        return self.probabilities


@pytest.mark.parametrize(
    "probabilities",
    [
        np.full((1, len(PA_OUTCOMES)), float("nan")),
        np.asarray([[-0.1, 0.2, 0.2, 0.2, 0.1, 0.1, 0.2, 0.1]]),
        np.full((1, len(PA_OUTCOMES)), 0.2),
        np.full((1, len(PA_OUTCOMES) - 1), 1.0 / (len(PA_OUTCOMES) - 1)),
    ],
)
def test_mutation_invalid_catboost_probability_output_fails_closed(probabilities) -> None:
    pytest.importorskip("catboost")
    model = SharedPACatBoost(
        model=_InvalidProbabilityModel(probabilities),
        features=["x"],
        categorical_features=[],
    )
    with pytest.raises(ValueError, match="violates the PA outcome contract"):
        model.predict_proba(pd.DataFrame({"x": [1.0]}))
