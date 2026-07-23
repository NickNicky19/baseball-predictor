from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.evaluation.multi_market_foundation import PA_OUTCOMES
from src.learning.shared_pa_model import derived_market_probabilities


def _probability_row(**updates: float) -> np.ndarray:
    values = {
        "strikeout": 0.20,
        "walk": 0.10,
        "single": 0.15,
        "double": 0.05,
        "triple": 0.01,
        "home_run": 0.04,
        "bip_out": 0.45,
        "other_non_ab": 0.0,
    }
    values.update(updates)
    return np.asarray([[values[name] for name in PA_OUTCOMES]], dtype=float)


def _pa_distribution() -> dict[str, dict[str, float]]:
    return {str(slot): {"4": 1.0} for slot in range(1, 10)}


def test_valid_exact_market_derivation_matches_closed_forms() -> None:
    probability = _probability_row()
    result = derived_market_probabilities(
        probability,
        pd.Series([3]),
        _pa_distribution(),
    )
    q_hit = 0.15 + 0.05 + 0.01 + 0.04
    assert result["hits_0.5"][0] == pytest.approx(1.0 - (1.0 - q_hit) ** 4)
    assert result["home_runs_0.5"][0] == pytest.approx(1.0 - (1.0 - 0.04) ** 4)
    assert 0.0 <= result["total_bases_5.5"][0] <= result["total_bases_0.5"][0] <= 1.0


@pytest.mark.parametrize(
    "probability,match",
    [
        (_probability_row(home_run=float("nan")), "finite"),
        (_probability_row(home_run=-0.01, bip_out=0.50), "within"),
        (_probability_row(bip_out=0.40), "sum to one"),
        (np.ones((1, len(PA_OUTCOMES) - 1)), "wrong shape"),
    ],
)
def test_mutation_invalid_pa_probability_matrix_fails_closed(probability, match) -> None:
    with pytest.raises(ValueError, match=match):
        derived_market_probabilities(probability, pd.Series([3]), _pa_distribution())


@pytest.mark.parametrize(
    "slot,match",
    [
        (True, "invalid lineup slot"),
        (0, "invalid lineup slot"),
        (10, "invalid lineup slot"),
        (1.5, "invalid lineup slot"),
        (float("nan"), "invalid lineup slot"),
    ],
)
def test_mutation_invalid_row_lineup_identity_fails_closed(slot, match) -> None:
    with pytest.raises(ValueError, match=match):
        derived_market_probabilities(_probability_row(), pd.Series([slot]), _pa_distribution())


@pytest.mark.parametrize(
    "mutation,match",
    [
        ({str(slot): {"4": 1.0} for slot in range(1, 9)}, "missing lineup slots"),
        ({**_pa_distribution(), "10": {"4": 1.0}}, "lineup slot"),
        ({**_pa_distribution(), "3": {"4": -0.1, "5": 1.1}}, "finite within"),
        ({**_pa_distribution(), "3": {"4": 0.9}}, "sum to one"),
        ({**_pa_distribution(), "3": {"4.5": 1.0}}, "invalid PA support"),
        ({**_pa_distribution(), "3": {"4": float("nan")}}, "finite within"),
    ],
)
def test_mutation_invalid_pa_distribution_fails_closed(mutation, match) -> None:
    with pytest.raises(ValueError, match=match):
        derived_market_probabilities(_probability_row(), pd.Series([3]), mutation)


def test_mutation_row_count_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="row counts differ"):
        derived_market_probabilities(_probability_row(), pd.Series([], dtype=int), _pa_distribution())
