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
from src.models.total_bases_contract import (
    over_threshold_for_half_point_line,
    require_supported_total_bases_line,
)
from src.evaluation.market_economics import (
    expected_profit_per_unit,
    fair_over_probability,
    implied_probability,
    net_payout_multiple,
    probability as validate_probability,
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

    Requires an exact simulated tail probability for an actionable/research
    market signal.  A normal approximation is available only when explicitly
    requested for a diagnostic; it is never the default market-pricing path.
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
        *,
        allow_normal_approximation: bool = False,
    ) -> EdgeResult:
        """Compare a projection to a market line, de-vigged, with Kelly stake.

        The book's two-sided prices imply probabilities that sum to >100% —
        the excess is the vig (its margin). Comparing the model against the raw
        one-sided implied prob systematically distorts edge. We remove the vig
        by normalizing both sides to sum to 1, then compute edge on BOTH the
        over and the under and keep whichever side the model favors.
        """
        self._validate_projection_quote_identity(projection, odds_line)
        if projection.category == "total_bases":
            require_supported_total_bases_line(odds_line.line)
        model_prob_over = self._model_prob_over(
            projection,
            odds_line.line,
            allow_normal_approximation=allow_normal_approximation,
        )
        model_prob_under = 1.0 - model_prob_over

        raw_over = implied_probability(odds_line.over_odds_american)
        raw_under = implied_probability(odds_line.under_odds_american)
        overround = raw_over + raw_under
        vig_pct = max(0.0, (overround - 1.0) * 100.0)

        # No-vig (fair) probabilities.
        if overround > 0:
            fair_over = fair_over_probability(
                odds_line.over_odds_american, odds_line.under_odds_american
            )
            fair_under = 1.0 - fair_over
        else:
            # Defensive only: valid American prices always have a positive
            # overround, but preserve the old fallback rather than inventing a
            # numerical policy for malformed input.
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
        expected_profit = expected_profit_per_unit(model_side, payout_odds)

        recommendation = self._classify_edge(signed_over_edge_pct, projection.confidence)

        kelly = self._fractional_kelly(model_side, payout_odds)
        # Only stake the favored side when the model actually has positive edge
        # and clears the confidence floor; otherwise stake is zero.
        if (edge_val <= 0 or expected_profit <= 0 or
                projection.confidence < self.thresholds.min_confidence):
            kelly = 0.0

        notes: list[str] = []
        if projection.simulation:
            exact = self._exact_tail_probability(projection, odds_line.line)
            if exact is not None:
                notes.append(f"MC tail prob (n={projection.simulation.n_sims})")
            elif allow_normal_approximation:
                notes.append(f"MC normal-approx (line {odds_line.line} not a sim threshold)")
            else:  # Defensive: _model_prob_over would already have raised.
                raise AssertionError("exact-tail guard was bypassed")
        else:
            if allow_normal_approximation:
                notes.append("Rate-based diagnostic (normal approximation)")
            else:  # Defensive: _model_prob_over would already have raised.
                raise AssertionError("exact-tail guard was bypassed")
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
            expected_profit_per_unit=round(expected_profit, 6),
            sportsbook=str(odds_line.sportsbook).strip().lower(),
            kelly_fraction=round(kelly, 4),
            notes=notes,
        )

    def _fractional_kelly(self, model_prob: float, american_odds: int) -> float:
        """Fractional-Kelly stake as a bankroll fraction, capped.

        Full Kelly f* = (b*p - q) / b, where b = net decimal payout, p = win
        prob, q = 1 - p. We apply kelly_fraction (default quarter-Kelly) and a
        hard cap, and never return negative (that's just 'no bet').
        """
        b = net_payout_multiple(american_odds)
        if b <= 0:
            return 0.0
        p = max(0.0, min(1.0, model_prob))
        q = 1.0 - p
        full_kelly = (b * p - q) / b
        if full_kelly <= 0:
            return 0.0
        staked = full_kelly * self.thresholds.kelly_fraction
        return min(staked, self.thresholds.kelly_cap)

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

        A projection without an exact simulated tail for a quoted line is not
        a market signal.  It is skipped rather than silently priced with a
        normal approximation.  ``compute_edge(...,
        allow_normal_approximation=True)`` remains available only for an
        explicitly labelled diagnostic.
        """
        min_edge = min_edge_pct if min_edge_pct is not None else self.thresholds.lean_edge_pct
        odds_index: dict[tuple[str, str], list[OddsLine]] = {}
        seen_products: set[tuple[str, str, str, float]] = set()
        for line in odds_lines:
            player_key = str(line.player_name).strip().casefold()
            category_key = str(line.category)
            if not player_key:
                raise ValueError("quote player identity cannot be blank")
            if category_key not in {
                "hits", "hrr", "home_runs", "fantasy", "strikeouts", "total_bases"
            }:
                raise ValueError(f"unknown quote category: {category_key!r}")
            over_threshold_for_half_point_line(line.line)
            if category_key == "total_bases":
                require_supported_total_bases_line(line.line)
            implied_probability(line.over_odds_american)
            implied_probability(line.under_odds_american)
            product_key = (
                player_key,
                category_key,
                str(line.sportsbook).strip().casefold(),
                float(line.line),
            )
            if product_key in seen_products:
                raise ValueError(
                    "duplicate player/category/sportsbook/line quote cannot be selected by order"
                )
            seen_products.add(product_key)
            odds_index.setdefault((player_key, category_key), []).append(line)

        value_plays: list[EdgeResult] = []
        for proj in projections:
            if category and proj.category != category:
                continue
            if proj.confidence < self.thresholds.min_confidence:
                continue

            quotes = odds_index.get((proj.player_name.strip().casefold(), proj.category), [])
            if not quotes:
                continue
            for odds in quotes:
                if self._exact_tail_probability(proj, odds.line) is None:
                    continue

                edge = self.compute_edge(proj, odds)
                # Use the FAVORED-SIDE edge magnitude for the research threshold,
                # but never present a negative-payout row as a value play.  The
                # latter is a mechanical wager condition, not a tuned cutoff.
                favored_edge_pct = abs(edge.model_prob_side - edge.fair_prob_side) * 100.0
                if (edge.expected_profit_per_unit > 0 and
                        favored_edge_pct >= min_edge and
                        edge.recommendation != EdgeRecommendation.PASS):
                    value_plays.append(edge)

        if sort_by == "edge":
            return sorted(value_plays, key=lambda e: abs(e.edge_pct), reverse=True)
        # Default: value-correct ranking by fractional Kelly, edge as tiebreak.
        return sorted(
            value_plays,
            key=lambda e: (e.kelly_fraction, abs(e.edge_pct)),
            reverse=True,
        )

    def _model_prob_over(
        self,
        projection: PropProjection,
        line: float,
        *,
        allow_normal_approximation: bool = False,
    ) -> float:
        exact = self._exact_tail_probability(projection, line)
        if exact is not None:
            return exact

        if not allow_normal_approximation:
            raise ValueError(
                "exact simulated P(over) is unavailable for "
                f"category={projection.category!r}, line={line}; refusing to price a market "
                "with a normal approximation"
            )

        if projection.category == "total_bases":
            # This candidate is evaluated on its actual discrete distribution.
            # A normal fallback would silently price an unsupported market line
            # and make the later gate incapable of seeing that defect.
            require_supported_total_bases_line(line)
            raise ValueError(
                "total_bases projection lacks its required exact Monte Carlo tail "
                f"probability for line {line}; refusing a normal approximation"
            )

        if projection.simulation:
            spread = max(0.35, projection.simulation.p90 - projection.simulation.p10)
            return self._normal_cdf_over(projection.projected_value, spread, line)

        spread = self._default_spread(projection.category)
        return self._normal_cdf_over(projection.projected_value, spread, line)

    @staticmethod
    def _exact_tail_probability(projection: PropProjection, line: float) -> Optional[float]:
        """Read P(actual > line) from an integer-threshold MC distribution.

        Archives store count thresholds (``P(actual >= 2)``), whereas books
        expose half-point lines (``Over 1.5``). Looking up 1.5 directly misses
        the exact tail and silently falls through to a Gaussian approximation.
        That is wrong for every discrete prop, and forbidden for total bases.
        """
        sim = projection.simulation
        if sim is None:
            return None
        tails = EdgeCalculator._validated_tail_map(projection)
        if not tails:
            return None
        threshold = over_threshold_for_half_point_line(line)
        return tails.get(int(threshold))

    @staticmethod
    def _validated_tail_map(projection: PropProjection) -> dict[int, float]:
        sim = projection.simulation
        if sim is None:
            return {}
        if isinstance(sim.n_sims, bool) or not isinstance(sim.n_sims, int) or sim.n_sims <= 0:
            raise ValueError("simulation n_sims must be a positive integer")
        if sim.category != projection.category:
            raise ValueError("simulation category does not match projection category")
        summary = [sim.mean, sim.median, sim.p10, sim.p90, projection.projected_value]
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not math.isfinite(float(value)) for value in summary):
            raise ValueError("simulation summaries and projected value must be finite")
        if sim.p10 > sim.median or sim.median > sim.p90:
            raise ValueError("simulation quantiles must satisfy p10 <= median <= p90")
        if not math.isfinite(float(projection.confidence)) or not 0.0 <= projection.confidence <= 1.0:
            raise ValueError("projection confidence must be finite and within [0,1]")
        if not isinstance(sim.p_ge_threshold, dict):
            raise ValueError("simulation tail probabilities must be an object")
        tails: dict[int, float] = {}
        for raw_threshold, raw_probability in sim.p_ge_threshold.items():
            if isinstance(raw_threshold, bool):
                raise ValueError("simulation thresholds must be positive integers")
            try:
                threshold = float(raw_threshold)
            except (TypeError, ValueError) as exc:
                raise ValueError("simulation thresholds must be numeric") from exc
            if (not math.isfinite(threshold) or threshold <= 0
                    or not threshold.is_integer()):
                raise ValueError("simulation thresholds must be positive integers")
            integer = int(threshold)
            if integer in tails:
                raise ValueError("simulation tail probabilities repeat a threshold")
            tails[integer] = validate_probability(
                raw_probability, f"p_ge_threshold[{integer}]"
            )
        ordered = sorted(tails.items())
        if any(right_probability > left_probability
               for (_, left_probability), (_, right_probability) in zip(ordered, ordered[1:])):
            raise ValueError("simulation survival probabilities must be nonincreasing")
        return tails

    @staticmethod
    def _validate_projection_quote_identity(
        projection: PropProjection,
        odds_line: OddsLine,
    ) -> None:
        projection_player = str(projection.player_name).strip().casefold()
        quote_player = str(odds_line.player_name).strip().casefold()
        if not projection_player or not quote_player or projection_player != quote_player:
            raise ValueError("projection and quote player identity differ")
        if projection.category != odds_line.category:
            raise ValueError("projection and quote category differ")

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
        """Backward-compatible wrapper around the shared price arithmetic."""
        return implied_probability(american_odds)

    @staticmethod
    def _normal_cdf_over(mean: float, spread: float, line: float) -> float:
        """P(X > line) under a normal approximation (p90 - p10 ≈ 2.56σ)."""
        sd = spread / 2.56
        if sd <= 0:
            return 1.0 if mean > line else 0.0
        z = (line - mean) / sd
        return 1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
