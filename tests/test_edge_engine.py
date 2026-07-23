"""Tests for the research edge engine: exact tails and fail-closed pricing."""

import pytest

from src.prediction.edge_calculator import EdgeCalculator, EdgeThresholds
from src.models.dataclasses import OddsLine, PropProjection, MonteCarloResult


def _proj(category, value, conf, thresholds):
    sim = MonteCarloResult(
        n_sims=8000, category=category, mean=value, median=value,
        p10=0.0, p90=value * 2 + 1, p_ge_threshold=thresholds,
    )
    return PropProjection(
        player_id=1, player_name="Test Player", category=category,
        game_date="2026-07-06", projected_value=value, confidence=conf, simulation=sim,
    )


def test_devig_removes_overround():
    """Fair probabilities must sum to ~1; raw implied probs sum to >1 (vig)."""
    calc = EdgeCalculator()
    proj = _proj("hits", 0.9, 0.7, {2.0: 0.40})
    odds = OddsLine("Test Player", "hits", 1.5, over_odds_american=-110, under_odds_american=-110)
    e = calc.compute_edge(proj, odds)
    # -110/-110 implies 0.524 each = 1.048 overround -> ~4.8% vig.
    assert 4.0 <= e.vig_pct <= 5.5
    # Fair over should be ~0.5 after removing symmetric vig.
    assert abs(e.fair_prob_over - 0.5) < 0.01


def test_devig_changes_edge_vs_raw():
    """The de-vigged edge must differ from the naive raw-implied edge."""
    calc = EdgeCalculator()
    proj = _proj("hits", 0.9, 0.7, {2.0: 0.60})  # model 60% over
    odds = OddsLine("Test Player", "hits", 1.5, over_odds_american=-110, under_odds_american=-110)
    e = calc.compute_edge(proj, odds)
    raw_edge = 0.60 - e.implied_prob_over       # naive (wrong)
    fair_edge = 0.60 - e.fair_prob_over         # correct
    assert abs(raw_edge - fair_edge) > 0.005    # they must not be equal
    assert e.fair_prob_over < e.implied_prob_over  # de-vig lowers implied over


def test_uses_true_simulation_tail_not_normal_approx():
    """When the line matches a stored MC threshold, that exact tail prob is
    used (not a normal approximation of the mean)."""
    calc = EdgeCalculator()
    proj = _proj("home_runs", 0.13, 0.7, {1.0: 0.12})
    odds = OddsLine("Test Player", "home_runs", 0.5, over_odds_american=500, under_odds_american=-700)
    e = calc.compute_edge(proj, odds)
    assert abs(e.model_prob_over - 0.12) < 1e-6   # exact sim value, not approx
    assert any("MC tail prob" in n for n in e.notes)


def test_missing_exact_tail_cannot_silently_price_a_market():
    """Mutation guard: removing the exact-tail check must fail this test."""
    calc = EdgeCalculator()
    proj = _proj("hits", 0.9, 0.7, {1.0: 0.60})
    odds = OddsLine("Test Player", "hits", 1.5, over_odds_american=-110, under_odds_american=-110)

    with pytest.raises(ValueError, match="exact simulated P\\(over\\) is unavailable"):
        calc.compute_edge(proj, odds)
    assert calc.find_value_plays([proj], [odds]) == []

    # Diagnostics may request the approximation explicitly, but that cannot
    # be mistaken for the market signal path above.
    diagnostic = calc.compute_edge(proj, odds, allow_normal_approximation=True)
    assert any("normal-approx" in note for note in diagnostic.notes)


def test_half_point_line_uses_integer_tail_key_not_the_line_key():
    """Regression: archived MC tails are count keys, never book-line keys."""
    calc = EdgeCalculator()
    proj = _proj("hits", 1.2, 0.7, {2.0: 0.77})
    odds = OddsLine("Test Player", "hits", 1.5, over_odds_american=100, under_odds_american=-120)
    edge = calc.compute_edge(proj, odds)
    assert edge.model_prob_over == 0.77


@pytest.mark.parametrize("bad_probability", [float("nan"), -0.01, 1.01])
def test_mutation_invalid_exact_tail_probability_fails_closed(bad_probability):
    calc = EdgeCalculator()
    proj = _proj("hits", 1.0, 0.7, {2.0: bad_probability})
    odds = OddsLine("Test Player", "hits", 1.5, -110, -110)
    with pytest.raises(ValueError, match="finite and in"):
        calc.compute_edge(proj, odds)


def test_mutation_noninteger_or_ambiguous_threshold_fails_closed():
    calc = EdgeCalculator()
    odds = OddsLine("Test Player", "hits", 1.5, -110, -110)
    with pytest.raises(ValueError, match="positive integers"):
        calc.compute_edge(_proj("hits", 1.0, 0.7, {1.5: 0.2, 2.0: 0.7}), odds)
    with pytest.raises(ValueError, match="repeat"):
        calc.compute_edge(_proj("hits", 1.0, 0.7, {2: 0.7, "2.0": 0.7}), odds)


def test_mutation_nonmonotone_survival_tail_fails_closed():
    calc = EdgeCalculator()
    proj = _proj("hits", 1.0, 0.7, {1.0: 0.4, 2.0: 0.5})
    odds = OddsLine("Test Player", "hits", 1.5, -110, -110)
    with pytest.raises(ValueError, match="nonincreasing"):
        calc.compute_edge(proj, odds)


def test_mutation_projection_quote_identity_mismatch_fails_closed():
    calc = EdgeCalculator()
    proj = _proj("hits", 1.0, 0.7, {2.0: 0.5})
    with pytest.raises(ValueError, match="player identity"):
        calc.compute_edge(proj, OddsLine("Other Player", "hits", 1.5, -110, -110))
    with pytest.raises(ValueError, match="category differ"):
        calc.compute_edge(proj, OddsLine("Test Player", "home_runs", 1.5, -110, -110))


def test_total_bases_exact_tail_cannot_bypass_supported_line_contract():
    calc = EdgeCalculator()
    proj = _proj("total_bases", 2.0, 0.7, {7.0: 0.1})
    odds = OddsLine("Test Player", "total_bases", 6.5, 100, -120)
    with pytest.raises(ValueError, match="outside the observed candidate contract"):
        calc.compute_edge(proj, odds)


def test_distinct_sportsbooks_are_not_pooled_or_overwritten():
    calc = EdgeCalculator(EdgeThresholds(lean_edge_pct=0.1, min_confidence=0.4))
    proj = _proj("hits", 1.0, 0.7, {2.0: 0.8})
    quotes = [
        OddsLine("Test Player", "hits", 1.5, 100, -120, sportsbook="a"),
        OddsLine("Test Player", "hits", 1.5, 120, -140, sportsbook="b"),
    ]
    plays = calc.find_value_plays([proj], quotes)
    assert {play.sportsbook for play in plays} == {"a", "b"}


def test_duplicate_same_product_quote_fails_instead_of_last_write_wins():
    calc = EdgeCalculator()
    proj = _proj("hits", 1.0, 0.7, {2.0: 0.8})
    quote = OddsLine("Test Player", "hits", 1.5, 100, -120, sportsbook="a")
    with pytest.raises(ValueError, match="duplicate"):
        calc.find_value_plays([proj], [quote, quote])


def test_same_sportsbook_alternate_lines_remain_separate_products():
    calc = EdgeCalculator(EdgeThresholds(lean_edge_pct=0.1, min_confidence=0.4))
    proj = _proj("hits", 1.0, 0.7, {1.0: 0.9, 2.0: 0.7})
    quotes = [
        OddsLine("Test Player", "hits", 0.5, -150, 130, sportsbook="a"),
        OddsLine("Test Player", "hits", 1.5, 120, -140, sportsbook="a"),
    ]
    plays = calc.find_value_plays([proj], quotes)
    assert {play.line for play in plays} == {0.5, 1.5}


def test_mutation_invalid_quote_cannot_be_silently_skipped():
    calc = EdgeCalculator()
    proj = _proj("hits", 1.0, 0.7, {2.0: 0.7})
    with pytest.raises(ValueError, match="half-point"):
        calc.find_value_plays(
            [proj], [OddsLine("Test Player", "hits", 1.25, -110, -110)]
        )
    with pytest.raises(ValueError, match="cannot be 0"):
        calc.find_value_plays(
            [proj], [OddsLine("Test Player", "hits", 1.5, 0, -110)]
        )


def test_edge_side_detects_under():
    """When the model favors the under, the engine reports the under side."""
    calc = EdgeCalculator()
    proj = _proj("hits", 0.6, 0.7, {2.0: 0.25})   # 25% over -> 75% under
    odds = OddsLine("Test Player", "hits", 1.5, over_odds_american=130, under_odds_american=-155)
    e = calc.compute_edge(proj, odds)
    assert e.edge_side == "under"
    assert abs(e.model_prob_side - 0.75) < 1e-6


def test_kelly_zero_when_no_edge():
    """No positive edge -> zero stake."""
    calc = EdgeCalculator()
    # Model agrees with a fair-ish market: 50% over at even-ish odds.
    proj = _proj("hits", 0.9, 0.7, {2.0: 0.50})
    odds = OddsLine("Test Player", "hits", 1.5, over_odds_american=-110, under_odds_american=-110)
    e = calc.compute_edge(proj, odds)
    # Model 50% vs fair 50% -> no edge either side.
    assert e.kelly_fraction == 0.0


def test_devig_positive_but_negative_posted_ev_is_not_a_value_play():
    """Mutation guard: fair-probability disagreement is not enough to bet.

    At -120/+100, 53% is above the de-vigged Over probability (~52.17%) but
    below the posted-price break-even probability (~54.55%).  The old engine
    admitted this row when the hand-set edge threshold was low enough.
    """
    calc = EdgeCalculator(EdgeThresholds(lean_edge_pct=0.1, min_confidence=0.4))
    proj = _proj("hits", 0.9, 0.7, {2.0: 0.53})
    odds = OddsLine("Test Player", "hits", 1.5,
                    over_odds_american=-120, under_odds_american=100)
    edge = calc.compute_edge(proj, odds)
    assert edge.model_prob_side > edge.fair_prob_side
    assert edge.expected_profit_per_unit < 0.0
    assert edge.kelly_fraction == 0.0
    assert calc.find_value_plays([proj], [odds]) == []


def test_kelly_refuses_heavy_favorite_with_thin_edge():
    """A tiny edge on a heavy favorite (-800) should not produce a large stake;
    the price makes it poor value even at high win probability."""
    calc = EdgeCalculator()
    proj = _proj("home_runs", 0.13, 0.7, {1.0: 0.12})  # 88% under
    odds = OddsLine("Test Player", "home_runs", 0.5, over_odds_american=550, under_odds_american=-800)
    e = calc.compute_edge(proj, odds)
    assert e.edge_side == "under"
    assert e.kelly_fraction < 0.01   # thin edge at -800 -> ~no stake


def test_kelly_respects_cap():
    """Even a huge edge cannot exceed the configured stake cap."""
    calc = EdgeCalculator(EdgeThresholds(kelly_fraction=1.0, kelly_cap=0.05))
    proj = _proj("hits", 1.5, 0.9, {2.0: 0.90})  # model 90% over
    odds = OddsLine("Test Player", "hits", 1.5, over_odds_american=100, under_odds_american=-120)
    e = calc.compute_edge(proj, odds)
    assert e.kelly_fraction <= 0.05 + 1e-9


def test_ranking_by_kelly_orders_by_value_not_raw_edge():
    """find_value_plays default ranking uses Kelly (value): a modest edge at
    plus odds outranks a larger edge at heavily-juiced odds."""
    calc = EdgeCalculator(EdgeThresholds(lean_edge_pct=1.0, min_confidence=0.4))
    # Play A: larger raw edge (15pts) but at a heavy favorite price (-450),
    # so the value (Kelly) is small.
    a = _proj("hits", 1.0, 0.7, {2.0: 0.65})
    a.player_name = "A"
    odds_a = OddsLine("A", "hits", 1.5, over_odds_american=-450, under_odds_american=350)
    # Play B: smaller raw edge (10pts) but at plus odds (+150), so real value.
    b = _proj("home_runs", 0.2, 0.7, {1.0: 0.50})
    b.player_name = "B"
    odds_b = OddsLine("B", "home_runs", 0.5, over_odds_american=150, under_odds_american=-180)
    plays = calc.find_value_plays([a, b], [odds_a, odds_b], sort_by="kelly")
    assert plays, "expected at least one value play"
    names = [p.player_name for p in plays]
    # By Kelly (value), B should rank at or above A.
    if "A" in names and "B" in names:
        assert names.index("B") <= names.index("A")
    # And sorting by raw edge should be able to flip the order (A's edge larger).
    by_edge = calc.find_value_plays([a, b], [odds_a, odds_b], sort_by="edge")
    assert by_edge  # both orderings produce results; they need not match
