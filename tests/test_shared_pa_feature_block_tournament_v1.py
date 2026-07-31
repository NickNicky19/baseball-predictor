from __future__ import annotations

from collections import Counter

import pytest

from src.evaluation.shared_pa_feature_block_tournament_v1 import (
    FeatureBlockTournamentError,
    _decision,
    _weighted_counts,
)


def test_weighted_counts_use_only_strictly_prior_dates() -> None:
    counts, denominator = _weighted_counts(
        [("2023-04-01", Counter({"home_run": 1, "bip_out": 3}))],
        "2023-04-03",
        2,
    )
    assert denominator == pytest.approx(2.0)
    assert counts["home_run"] == pytest.approx(0.5)
    assert counts["bip_out"] == pytest.approx(1.5)


def test_weighted_counts_reject_same_day_history() -> None:
    with pytest.raises(FeatureBlockTournamentError, match="crosses target cutoff"):
        _weighted_counts([("2023-04-03", Counter({"home_run": 1}))], "2023-04-03", 30)


def test_market_retention_requires_both_scores_and_month_consistency() -> None:
    decision = _decision(
        {
            "brier_delta_candidate_minus_baseline": {"point": -0.0006, "lower_95": -0.001, "upper_95": -0.0001},
            "log_loss_delta_candidate_minus_baseline": {"point": -0.006, "lower_95": -0.01, "upper_95": -0.001},
        },
        [{"brier_delta_candidate_minus_parent": -0.01, "log_loss_delta_candidate_minus_parent": -0.01} for _ in range(4)],
        {
            "retain": {"minimum_absolute_brier_improvement": 0.0005, "minimum_absolute_log_loss_improvement": 0.005, "minimum_consistent_months": 4},
            "reject": {"reject_if_either_95_lower_bound_above_zero": True},
        },
    )
    assert decision["status"] == "RETAIN"


def test_market_rejection_is_conclusive_not_a_silent_fallback() -> None:
    decision = _decision(
        {
            "brier_delta_candidate_minus_baseline": {"point": 0.001, "lower_95": 0.0001, "upper_95": 0.002},
            "log_loss_delta_candidate_minus_baseline": {"point": -0.1, "lower_95": -0.2, "upper_95": -0.01},
        },
        [],
        {
            "retain": {"minimum_absolute_brier_improvement": 0.0005, "minimum_absolute_log_loss_improvement": 0.005, "minimum_consistent_months": 4},
            "reject": {"reject_if_either_95_lower_bound_above_zero": True},
        },
    )
    assert decision["status"] == "REJECT"
