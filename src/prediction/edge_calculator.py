"""
+EV edge calculator.

Compares model projections to market odds lines using Monte Carlo tail
probabilities when available, with configurable thresholds and spreads.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from src.models.dataclasses import (
    EdgeRecommendation,
    EdgeResult,
    OddsLine,
    PropCategory,
    PropProjection,
)


@dataclass
class EdgeThresholds:
    """Configurable edge classification thresholds."""

    strong_edge_pct: float = 8.0
    lean_edge_pct: float = 4.0
    min_confidence: float = 0.45
    # Fractional-Kelly staking. kelly_fraction=0.25 means quarter-Kelly — the
    # standard defensive choice given high single-game prop variance. kelly_cap
    # hard-limits any single stake as a fraction of bankroll regardless of edge.
    kelly_fraction: float = 0.25
    kelly_cap: float = 0.05
    default_spreads: dict[str, float] = field(
        default_factory=lambda: {
            "hits": 0.9,
            "hrr": 1.6,
            "home_runs": 0.45,
            "fantasy": 4.0,
            "strikeouts": 1.8,
        }
    )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> EdgeThresholds:
        edge = config.get("edge", {})
        spreads = edge.get("default_spreads", {})
        return cls(
            strong_edge_pct=float(edge.get("strong_edge_pct", 8.0)),
            lean_edge_pct=float(edge.get("lean_edge_pct", 4.0)),
            min_confidence=float(edge.get("min_confidence", 0.45)),
            kelly_fraction=float(edge.get("kelly_fraction", 0.25)),
            kelly_cap=float(edge.get("kelly_cap", 0.05)),
            default_spreads={
                "hits": float(spreads.get("hits", 0.9)),
                "hrr": float(spreads.get("hrr", 1.6)),
                "home_runs": float(spreads.get("home_runs", 0.45)),
                "fantasy": float(spreads.get("fantasy", 4.0)),
                "strikeouts": float(spreads.get("strikeouts", 1.8)),
            },
        )


class EdgeCalculator:
    """
    Computes betting edge from projections vs odds.

    Uses simulation tail probabilities when available; otherwise falls back
    to a normal approximation around the projected mean.
    """

    def __init__(
        self,
        thresholds: Optional[EdgeThresholds] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        self.thresholds = thresholds or EdgeThresholds.from_config(config or {})

    def compute_edge(
        self,
        projection: PropProjection,
        odds_line: OddsLine,
    ) -> EdgeResult:
        """Compare a projection to a market line, de-vigged, with Kelly stake.

        The book's two-sided prices imply probabilities that sum to >100% —
        the excess is the vig (its margin). Comparing the model against the raw
        one-sided implied prob systematically distorts edge. We remove the vig
        by normalizing both sides to sum to 1, then compute edge on BOTH the
        over and the under and keep whichever side the model favors.
        """
        model_prob_over = self._model_prob_over(projection, odds_line.line)
        model_prob_under = 1.0 - model_prob_over

        raw_over = self._american_to_implied_prob(odds_line.over_odds_american)
        raw_under = self._american_to_implied_prob(odds_line.under_odds_american)
        overround = raw_over + raw_under
        vig_pct = max(0.0, (overround - 1.0) * 100.0)

        # No-vig (fair) probabilities.
        if overround > 0:
            fair_over = raw_over / overround
            fair_under = raw_under / overround
        else:
            fair_over = raw_over
            fair_under = raw_under

        edge_over = model_prob_over - fair_over
        edge_under = model_prob_under - fair_under

        # Keep the side the model actually favors (larger positive edge).
        if edge_over >= edge_under:
            edge_side = "over"
            edge_val = edge_over
            model_side = model_prob_over
            fair_side = fair_over
            payout_odds = odds_line.over_odds_american
        else:
            edge_side = "under"
            edge_val = edge_under
            model_side = model_prob_under
            fair_side = fair_under
            payout_odds = odds_line.under_odds_american

        edge_pct = edge_val * 100.0
        # edge_pct on the OVER (signed) is retained for backward compatibility
        # and for the over/under recommendation classifier.
        signed_over_edge_pct = edge_over * 100.0

        recommendation = self._classify_edge(signed_over_edge_pct, projection.confidence)

        kelly = self._fractional_kelly(model_side, payout_odds)
        # Only stake the favored side when the model actually has positive edge
        # and clears the confidence floor; otherwise stake is zero.
        if edge_val <= 0 or projection.confidence < self.thresholds.min_confidence:
            kelly = 0.0

        notes: list[str] = []
        if projection.simulation:
            exact = projection.simulation.p_ge_threshold.get(odds_line.line) if projection.simulation.p_ge_threshold else None
            if exact is not None:
                notes.append(f"MC tail prob (n={projection.simulation.n_sims})")
            else:
                notes.append(f"MC normal-approx (line {odds_line.line} not a sim threshold)")
        else:
            notes.append("Rate-based projection (no MC distribution)")
        if vig_pct > 0:
            notes.append(f"vig {vig_pct:.1f}% removed")

        return EdgeResult(
            player_name=projection.player_name,
            category=projection.category,
            line=odds_line.line,
            projected_value=projection.projected_value,
            implied_prob_over=round(raw_over, 4),
            model_prob_over=round(model_prob_over, 4),
            edge_pct=round(signed_over_edge_pct, 2),
            recommendation=recommendation,
            confidence=projection.confidence,
            fair_prob_over=round(fair_over, 4),
            vig_pct=round(vig_pct, 2),
            edge_side=edge_side,
            model_prob_side=round(model_side, 4),
            fair_prob_side=round(fair_side, 4),
            payout_odds_american=payout_odds,
            kelly_fraction=round(kelly, 4),
            notes=notes,
        )

    def _fractional_kelly(self, model_prob: float, american_odds: int) -> float:
        """Fractional-Kelly stake as a bankroll fraction, capped.

        Full Kelly f* = (b*p - q) / b, where b = net decimal payout, p = win
        prob, q = 1 - p. We apply kelly_fraction (default quarter-Kelly) and a
        hard cap, and never return negative (that's just 'no bet').
        """
        b = self._american_to_decimal_payout(american_odds)
        if b <= 0:
            return 0.0
        p = max(0.0, min(1.0, model_prob))
        q = 1.0 - p
        full_kelly = (b * p - q) / b
        if full_kelly <= 0:
            return 0.0
        staked = full_kelly * self.thresholds.kelly_fraction
        return min(staked, self.thresholds.kelly_cap)

    @staticmethod
    def _american_to_decimal_payout(american_odds: int) -> float:
        """Net payout multiple b (profit per unit staked)."""
        if american_odds > 0:
            return american_odds / 100.0
        if american_odds < 0:
            return 100.0 / abs(american_odds)
        return 0.0

    def find_value_plays(
        self,
        projections: list[PropProjection],
        odds_lines: list[OddsLine],
        category: Optional[PropCategory] = None,
        min_edge_pct: Optional[float] = None,
        sort_by: str = "kelly",
    ) -> list[EdgeResult]:
        """Match projections to odds and return plays above the edge threshold.

        sort_by: "kelly" (default) ranks by fractional-Kelly stake — the
        value-correct ordering (edge adjusted for odds and confidence).
        "edge" ranks by raw model-vs-market gap. Both columns are present on
        every result regardless of sort, so the GUI can re-sort freely.
        """
        min_edge = min_edge_pct if min_edge_pct is not None else self.thresholds.lean_edge_pct
        odds_index = {
            (line.player_name.lower(), line.category): line for line in odds_lines
        }

        value_plays: list[EdgeResult] = []
        for proj in projections:
            if category and proj.category != category:
                continue
            if proj.confidence < self.thresholds.min_confidence:
                continue

            odds = odds_index.get((proj.player_name.lower(), proj.category))
            if odds is None:
                continue

            edge = self.compute_edge(proj, odds)
            # Use the FAVORED-SIDE edge magnitude for the threshold gate.
            favored_edge_pct = abs(edge.model_prob_side - edge.fair_prob_side) * 100.0
            if favored_edge_pct >= min_edge and edge.recommendation != EdgeRecommendation.PASS:
                value_plays.append(edge)

        if sort_by == "edge":
            return sorted(value_plays, key=lambda e: abs(e.edge_pct), reverse=True)
        # Default: value-correct ranking by fractional Kelly, edge as tiebreak.
        return sorted(
            value_plays,
            key=lambda e: (e.kelly_fraction, abs(e.edge_pct)),
            reverse=True,
        )

    def _model_prob_over(self, projection: PropProjection, line: float) -> float:
        if projection.simulation and projection.simulation.p_ge_threshold:
            exact = projection.simulation.p_ge_threshold.get(line)
            if exact is not None:
                return exact
            for threshold, prob in projection.simulation.p_ge_threshold.items():
                if abs(threshold - line) < 0.01:
                    return prob

        if projection.simulation:
            spread = max(0.35, projection.simulation.p90 - projection.simulation.p10)
            return self._normal_cdf_over(projection.projected_value, spread, line)

        spread = self._default_spread(projection.category)
        return self._normal_cdf_over(projection.projected_value, spread, line)

    def _classify_edge(self, edge_pct: float, confidence: float) -> EdgeRecommendation:
        if confidence < self.thresholds.min_confidence:
            return EdgeRecommendation.PASS

        t_strong = self.thresholds.strong_edge_pct
        t_lean = self.thresholds.lean_edge_pct

        if edge_pct >= t_strong:
            return EdgeRecommendation.STRONG_OVER
        if edge_pct >= t_lean:
            return EdgeRecommendation.LEAN_OVER
        if edge_pct <= -t_strong:
            return EdgeRecommendation.STRONG_UNDER
        if edge_pct <= -t_lean:
            return EdgeRecommendation.LEAN_UNDER
        return EdgeRecommendation.PASS

    def _default_spread(self, category: PropCategory) -> float:
        return self.thresholds.default_spreads.get(category, 1.5)

    @staticmethod
    def _american_to_implied_prob(american_odds: int) -> float:
        if american_odds > 0:
            return 100.0 / (american_odds + 100.0)
        return abs(american_odds) / (abs(american_odds) + 100.0)

    @staticmethod
    def _normal_cdf_over(mean: float, spread: float, line: float) -> float:
        """P(X > line) under a normal approximation (p90 - p10 ≈ 2.56σ)."""
        sd = spread / 2.56
        if sd <= 0:
            return 1.0 if mean > line else 0.0
        z = (line - mean) / sd
        return 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))