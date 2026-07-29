"""Generated invariants for payout arithmetic at the market boundary.

These tests supplement the repository's example and mutation harnesses.  They
do not choose a bet threshold or exercise model quality; they enforce payout
identities that must hold for every valid posted American price.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest
from hypothesis import given, settings, strategies as st

from src.evaluation.market_economics import (
    expected_profit_per_unit,
    fair_over_probability,
    implied_probability,
    net_payout_multiple,
)


VALID_AMERICAN_ODDS = st.one_of(
    st.integers(min_value=-10_000, max_value=-100),
    st.integers(min_value=100, max_value=10_000),
)
PROBABILITIES = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


@settings(max_examples=250, derandomize=True, deadline=None)
@given(VALID_AMERICAN_ODDS)
def test_posted_break_even_probability_has_zero_expected_profit(odds: int) -> None:
    p_break_even = implied_probability(odds)
    assert math.isclose(
        expected_profit_per_unit(p_break_even, odds),
        0.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


@settings(max_examples=250, derandomize=True, deadline=None)
@given(VALID_AMERICAN_ODDS, VALID_AMERICAN_ODDS)
def test_two_sided_devig_is_complementary(over_odds: int, under_odds: int) -> None:
    over = fair_over_probability(over_odds, under_odds)
    under = fair_over_probability(under_odds, over_odds)
    assert 0.0 < over < 1.0
    assert math.isclose(over + under, 1.0, rel_tol=0.0, abs_tol=1e-12)


@settings(max_examples=250, derandomize=True, deadline=None)
@given(VALID_AMERICAN_ODDS, PROBABILITIES, PROBABILITIES)
def test_expected_profit_is_monotone_in_win_probability(
    odds: int, first: float, second: float
) -> None:
    low, high = sorted((first, second))
    assert expected_profit_per_unit(low, odds) <= expected_profit_per_unit(high, odds)


def _assert_break_even_property(
    expected_profit: Callable[[float, int], float]
) -> None:
    """Run the load-bearing generated property against one implementation."""

    @settings(max_examples=100, derandomize=True, deadline=None)
    @given(VALID_AMERICAN_ODDS)
    def property_check(odds: int) -> None:
        p_break_even = implied_probability(odds)
        assert math.isclose(
            expected_profit(p_break_even, odds),
            0.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        )

    property_check()


def test_mutation_dropping_the_loss_term_is_detected() -> None:
    """Prove the property fails on the specific payout mutation it guards."""

    def broken_expected_profit(win_probability: float, odds: int) -> float:
        return win_probability * net_payout_multiple(odds)

    with pytest.raises(AssertionError):
        _assert_break_even_property(broken_expected_profit)
