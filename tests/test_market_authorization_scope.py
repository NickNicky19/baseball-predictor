"""The final presentation boundary must honor an authorization's exact scope."""

from __future__ import annotations

from src.evaluation.market_output_policy import (
    BETTING_AUTHORIZED,
    ApprovedMarket,
    MarketOutputPolicy,
)
from src.models.dataclasses import EdgeRecommendation, EdgeResult
from src.prediction.daily_predictor import DailyPredictor


class _OddsBoundary:
    def load(self, game_date: str):
        return [object()]


class _EdgeBoundary:
    """Stub the edge-calculation boundary, not authorization itself."""

    def find_value_plays(self, *_args, **_kwargs):
        return [
            EdgeResult(
                player_name="Approved hitter", category="hits", line=0.5,
                projected_value=1.0, implied_prob_over=0.50, model_prob_over=0.60,
                edge_pct=10.0, recommendation=EdgeRecommendation.LEAN_OVER,
                confidence=0.7, edge_side="over", sportsbook="draftkings",
                kelly_fraction=0.02,
            ),
            EdgeResult(
                player_name="Wrong category", category="home_runs", line=0.5,
                projected_value=0.2, implied_prob_over=0.20, model_prob_over=0.30,
                edge_pct=10.0, recommendation=EdgeRecommendation.LEAN_OVER,
                confidence=0.7, edge_side="over", sportsbook="draftkings",
                kelly_fraction=0.02,
            ),
            EdgeResult(
                player_name="Wrong side", category="hits", line=0.5,
                projected_value=1.0, implied_prob_over=0.50, model_prob_over=0.40,
                edge_pct=-10.0, recommendation=EdgeRecommendation.LEAN_UNDER,
                confidence=0.7, edge_side="under", sportsbook="draftkings",
                kelly_fraction=0.02,
            ),
        ]


def test_authorization_does_not_spill_into_other_market_scopes():
    policy = MarketOutputPolicy(
        status=BETTING_AUTHORIZED,
        actionable=True,
        policy_path="config/future.json",
        policy_sha256="a" * 64,
        reason="test certificate",
        approved_markets=(
            ApprovedMarket("draftkings", "hits", frozenset({"over"}), "b" * 64),
        ),
    )
    predictor = DailyPredictor.__new__(DailyPredictor)
    predictor.odds_loader = _OddsBoundary()
    predictor.edge_calculator = _EdgeBoundary()
    predictor.config = {"odds": {}}

    rows = predictor._compute_value_plays(
        hitter_projections=[], pitcher_projections=[], game_date="2026-07-14",
        compute_edges=True, odds_lines=None, min_edge_pct=None, market_policy=policy,
    )

    assert [row.actionable for row in rows] == [True, False, False]
    assert rows[0].kelly_fraction == 0.02
    assert rows[1].kelly_fraction == 0.0
    assert rows[2].kelly_fraction == 0.0
    assert "does not authorize this exact market scope" in rows[1].notes[-1]


def test_research_policy_remains_non_actionable_even_for_matching_scope():
    policy = MarketOutputPolicy(
        status="RESEARCH_ONLY", actionable=False, policy_path=None,
        policy_sha256=None, reason="research only",
    )
    predictor = DailyPredictor.__new__(DailyPredictor)
    predictor.odds_loader = _OddsBoundary()
    predictor.edge_calculator = _EdgeBoundary()
    predictor.config = {"odds": {}}

    rows = predictor._compute_value_plays(
        hitter_projections=[], pitcher_projections=[], game_date="2026-07-14",
        compute_edges=True, odds_lines=None, min_edge_pct=None, market_policy=policy,
    )

    assert not any(row.actionable for row in rows)
    assert all(row.kelly_fraction == 0.0 for row in rows)
