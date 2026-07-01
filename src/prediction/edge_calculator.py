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
        """Compare a single projection to a market line."""
        model_prob_over = self._model_prob_over(projection, odds_line.line)
        implied_prob_over = self._american_to_implied_prob(odds_line.over_odds_american)
        edge_pct = (model_prob_over - implied_prob_over) * 100.0

        recommendation = self._classify_edge(edge_pct, projection.confidence)
        notes: list[str] = []
        if projection.simulation:
            notes.append(f"MC sims: {projection.simulation.n_sims}")
        else:
            notes.append("Rate-based projection (no MC distribution)")

        return EdgeResult(
            player_name=projection.player_name,
            category=projection.category,
            line=odds_line.line,
            projected_value=projection.projected_value,
            implied_prob_over=round(implied_prob_over, 4),
            model_prob_over=round(model_prob_over, 4),
            edge_pct=round(edge_pct, 2),
            recommendation=recommendation,
            confidence=projection.confidence,
            notes=notes,
        )

    def find_value_plays(
        self,
        projections: list[PropProjection],
        odds_lines: list[OddsLine],
        category: Optional[PropCategory] = None,
        min_edge_pct: Optional[float] = None,
    ) -> list[EdgeResult]:
        """Match projections to odds and return plays above the edge threshold."""
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
            if abs(edge.edge_pct) >= min_edge and edge.recommendation != EdgeRecommendation.PASS:
                value_plays.append(edge)

        return sorted(value_plays, key=lambda e: abs(e.edge_pct), reverse=True)

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