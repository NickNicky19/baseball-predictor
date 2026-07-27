from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.evaluation.shared_pa_offset_evaluation import (
    binary_market_counts,
    binary_market_probability,
    component,
    derive_market_probabilities,
    fit_pooled_pa_distribution,
    market_target,
    metrics,
    paired_date_interval,
)
from src.evaluation.shared_pa_eb_offset_catboost import PA_OUTCOMES


def _outcome_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "out_pa": [4, 4],
            "out_ab": [4, 3],
            "out_hits": [2, 1],
            "out_doubles": [1, 0],
            "out_triples": [0, 0],
            "out_hr": [1, 0],
            "out_bb": [0, 1],
            "out_k": [1, 1],
        }
    )


def _pa_probability() -> np.ndarray:
    row = np.array([0.20, 0.10, 0.25, 0.10, 0.05, 0.05, 0.20, 0.05])
    assert np.isclose(row.sum(), 1.0)
    return np.vstack([row, row])


def test_per_pa_market_components_are_exact_and_exhaustive() -> None:
    frame = _outcome_frame()
    counts, probability = component(frame, _pa_probability(), "per_pa_hit_event")
    assert counts.tolist() == [[2.0, 2.0], [3.0, 1.0]]
    assert np.allclose(probability[:, 1], 0.45)
    hr_counts, hr_probability = component(frame, _pa_probability(), "per_pa_home_run_event")
    assert hr_counts.tolist() == [[3.0, 1.0], [4.0, 0.0]]
    assert np.allclose(hr_probability[:, 1], 0.05)
    tb_counts, tb_probability = component(
        frame, _pa_probability(), "per_pa_total_bases_distribution"
    )
    assert tb_counts.tolist() == [[2.0, 0.0, 1.0, 0.0, 1.0], [3.0, 1.0, 0.0, 0.0, 0.0]]
    assert np.allclose(tb_probability.sum(axis=1), 1.0)


def test_pooled_pa_opportunity_never_requires_target_lineup_slot() -> None:
    distribution = fit_pooled_pa_distribution(pd.DataFrame({"out_pa": [3, 4, 4, 5]}))
    assert distribution == {"3": 0.25, "4": 0.5, "5": 0.25}
    with pytest.raises(ValueError, match="positive integer"):
        fit_pooled_pa_distribution(pd.DataFrame({"out_pa": [4, 0]}))


def test_game_market_derivation_matches_closed_form_for_one_pa() -> None:
    probability = _pa_probability()[:1]
    result = derive_market_probabilities(probability, {"1": 1.0})
    assert result["hits_over_0_5"][0] == pytest.approx(0.45)
    assert result["hits_over_1_5"][0] == pytest.approx(0.0)
    assert result["home_runs_over_0_5"][0] == pytest.approx(0.05)
    assert result["total_bases_over_0_5"][0] == pytest.approx(0.45)
    assert result["total_bases_over_1_5"][0] == pytest.approx(0.20)
    assert result["total_bases_over_2_5"][0] == pytest.approx(0.10)
    assert result["total_bases_over_3_5"][0] == pytest.approx(0.05)
    assert result["total_bases_over_4_5"][0] == pytest.approx(0.0)


def test_market_targets_use_exact_hits_hr_and_total_bases_definitions() -> None:
    frame = _outcome_frame()
    assert market_target(frame, "hits_over_0_5").tolist() == [1.0, 1.0]
    assert market_target(frame, "hits_over_1_5").tolist() == [1.0, 0.0]
    assert market_target(frame, "home_runs_over_0_5").tolist() == [1.0, 0.0]
    # TB = H + 2B + 2*3B + 3*HR: row one is 6, row two is 1.
    assert market_target(frame, "total_bases_over_5_5").tolist() == [1.0, 0.0]
    assert binary_market_counts(frame, "home_runs_over_0_5").tolist() == [[0.0, 1.0], [1.0, 0.0]]


def test_scores_and_date_block_interval_are_deterministic() -> None:
    counts = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 0.0], [0.0, 1.0]])
    candidate = binary_market_probability(np.array([0.1, 0.9, 0.2, 0.8]))
    comparator = binary_market_probability(np.array([0.4, 0.6, 0.4, 0.6]))
    score = metrics(counts, candidate)
    assert score["brier"] < metrics(counts, comparator)["brier"]
    assert score["log_loss"] < metrics(counts, comparator)["log_loss"]
    dates = pd.Series(["2023-04-01", "2023-04-01", "2023-04-02", "2023-04-02"])
    first = paired_date_interval(counts, candidate, comparator, dates, draws=100, seed=7)
    second = paired_date_interval(counts, candidate, comparator, dates, draws=100, seed=7)
    assert first == second
    assert first["brier"]["upper"] < 0.0
    assert first["log_loss"]["upper"] < 0.0


def test_probability_and_distribution_mutations_fail_closed() -> None:
    bad = _pa_probability()
    bad[0, PA_OUTCOMES.index("home_run")] = 2.0
    with pytest.raises(ValueError, match="outside"):
        derive_market_probabilities(bad, {"4": 1.0})
    with pytest.raises(ValueError, match="does not sum"):
        derive_market_probabilities(_pa_probability(), {"4": 0.9})
    with pytest.raises(ValueError, match="one-dimensional"):
        binary_market_probability(np.array([[0.5]]))
