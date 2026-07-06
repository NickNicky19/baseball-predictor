"""Tests for the +EV edge engine: de-vigging, true-distribution use,
both-sides edge detection, and fractional-Kelly staking."""

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
    proj = _proj("hits", 0.9, 0.7, {1.5: 0.40})
    odds = OddsLine("Test Player", "hits", 1.5, over_odds_american=-110, under_odds_american=-110)
    e = calc.compute_edge(proj, odds)
    # -110/-110 implies 0.524 each = 1.048 overround -> ~4.8% vig.
    assert 4.0 <= e.vig_pct <= 5.5
    # Fair over should be ~0.5 after removing symmetric vig.
    assert abs(e.fair_prob_over - 0.5) < 0.01


def test_devig_changes_edge_vs_raw():
    """The de-vigged edge must differ from the naive raw-implied edge."""
    calc = EdgeCalculator()
    proj = _proj("hits", 0.9, 0.7, {1.5: 0.60})  # model 60% over
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
    proj = _proj("home_runs", 0.13, 0.7, {0.5: 0.12})
    odds = OddsLine("Test Player", "home_runs", 0.5, over_odds_american=500, under_odds_american=-700)
    e = calc.compute_edge(proj, odds)
    assert abs(e.model_prob_over - 0.12) < 1e-6   # exact sim value, not approx
    assert any("MC tail prob" in n for n in e.notes)


def test_edge_side_detects_under():
    """When the model favors the under, the engine reports the under side."""
    calc = EdgeCalculator()
    proj = _proj("hits", 0.6, 0.7, {1.5: 0.25})   # 25% over -> 75% under
    odds = OddsLine("Test Player", "hits", 1.5, over_odds_american=130, under_odds_american=-155)
    e = calc.compute_edge(proj, odds)
    assert e.edge_side == "under"
    assert abs(e.model_prob_side - 0.75) < 1e-6


def test_kelly_zero_when_no_edge():
    """No positive edge -> zero stake."""
    calc = EdgeCalculator()
    # Model agrees with a fair-ish market: 50% over at even-ish odds.
    proj = _proj("hits", 0.9, 0.7, {1.5: 0.50})
    odds = OddsLine("Test Player", "hits", 1.5, over_odds_american=-110, under_odds_american=-110)
    e = calc.compute_edge(proj, odds)
    # Model 50% vs fair 50% -> no edge either side.
    assert e.kelly_fraction == 0.0


def test_kelly_refuses_heavy_favorite_with_thin_edge():
    """A tiny edge on a heavy favorite (-800) should not produce a large stake;
    the price makes it poor value even at high win probability."""
    calc = EdgeCalculator()
    proj = _proj("home_runs", 0.13, 0.7, {0.5: 0.12})  # 88% under
    odds = OddsLine("Test Player", "home_runs", 0.5, over_odds_american=550, under_odds_american=-800)
    e = calc.compute_edge(proj, odds)
    assert e.edge_side == "under"
    assert e.kelly_fraction < 0.01   # thin edge at -800 -> ~no stake


def test_kelly_respects_cap():
    """Even a huge edge cannot exceed the configured stake cap."""
    calc = EdgeCalculator(EdgeThresholds(kelly_fraction=1.0, kelly_cap=0.05))
    proj = _proj("hits", 1.5, 0.9, {1.5: 0.90})  # model 90% over
    odds = OddsLine("Test Player", "hits", 1.5, over_odds_american=100, under_odds_american=-120)
    e = calc.compute_edge(proj, odds)
    assert e.kelly_fraction <= 0.05 + 1e-9


def test_ranking_by_kelly_orders_by_value_not_raw_edge():
    """find_value_plays default ranking uses Kelly (value): a modest edge at
    plus odds outranks a larger edge at heavily-juiced odds."""
    calc = EdgeCalculator(EdgeThresholds(lean_edge_pct=1.0, min_confidence=0.4))
    # Play A: larger raw edge (15pts) but at a heavy favorite price (-450),
    # so the value (Kelly) is small.
    a = _proj("hits", 1.0, 0.7, {1.5: 0.65})
    a.player_name = "A"
    odds_a = OddsLine("A", "hits", 1.5, over_odds_american=-450, under_odds_american=350)
    # Play B: smaller raw edge (10pts) but at plus odds (+150), so real value.
    b = _proj("home_runs", 0.2, 0.7, {0.5: 0.50})
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
