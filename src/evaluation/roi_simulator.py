"""
Simulated +EV ROI calculator with vig — validates edge quality on historical lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from src.models.dataclasses import EdgeResult


class LegacyROISimulatorError(RuntimeError):
    """Raised when legacy flat-stake ROI is requested without an explicit opt-in."""


@dataclass
class ROISimulationResult:
    n_bets: int
    wins: int
    total_staked: float
    total_returned: float
    roi_pct: float
    hit_rate: float
    avg_edge_pct: float
    notes: str = ""
    status: str = "RESEARCH_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_bets": self.n_bets,
            "wins": self.wins,
            "total_staked": self.total_staked,
            "total_returned": round(self.total_returned, 2),
            "roi_pct": round(self.roi_pct, 2),
            "hit_rate": round(self.hit_rate, 4),
            "avg_edge_pct": round(self.avg_edge_pct, 2),
            "notes": self.notes,
            "status": self.status,
        }


@dataclass
class ROISimulationReport:
    results_by_category: dict[str, ROISimulationResult] = field(default_factory=dict)
    overall: Optional[ROISimulationResult] = None
    status: str = "RESEARCH_ONLY"
    reason: str = (
        "Legacy name-keyed, flat-stake ROI is not a valid market evaluation or betting result."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall.to_dict() if self.overall else None,
            "by_category": {k: v.to_dict() for k, v in self.results_by_category.items()},
            "status": self.status,
            "reason": self.reason,
        }


class ROISimulator:
    """Explicitly opted-in flat-stake legacy research only."""

    def __init__(
        self,
        flat_stake: float = 100.0,
        default_vig: int = -110,
        *,
        allow_legacy_research: bool = False,
    ):
        self.flat_stake = flat_stake
        self.default_vig = default_vig
        self.allow_legacy_research = allow_legacy_research

    def simulate(
        self,
        value_plays: list[EdgeResult],
        outcomes: dict[tuple[str, str], float],
        min_edge_pct: float = 0.0,
    ) -> ROISimulationReport:
        """
        outcomes: (player_name, category) → actual value for settlement.
        """
        if not self.allow_legacy_research:
            raise LegacyROISimulatorError(
                "Legacy ROI is disabled by default: it is name-keyed, flat-stake, and "
                "does not use a valid market/void/staking contract. Use the hard-keyed "
                "market A/B or forward shadow ledger instead."
            )
        filtered = [v for v in value_plays if abs(v.edge_pct) >= min_edge_pct]
        by_cat: dict[str, list[EdgeResult]] = {}
        for play in filtered:
            by_cat.setdefault(play.category, []).append(play)

        cat_results: dict[str, ROISimulationResult] = {}
        all_staked = 0.0
        all_returned = 0.0
        all_wins = 0
        all_bets = 0
        all_edges: list[float] = []

        for category, plays in by_cat.items():
            result = self._simulate_plays(plays, outcomes)
            cat_results[category] = result
            all_staked += result.total_staked
            all_returned += result.total_returned
            all_wins += result.wins
            all_bets += result.n_bets
            all_edges.extend([p.edge_pct for p in plays])

        overall = None
        if all_bets > 0:
            overall = ROISimulationResult(
                n_bets=all_bets,
                wins=all_wins,
                total_staked=all_staked,
                total_returned=all_returned,
                roi_pct=(all_returned - all_staked) / all_staked * 100.0,
                hit_rate=all_wins / all_bets,
                avg_edge_pct=sum(all_edges) / len(all_edges),
                notes="Legacy flat-stake research ROI; not an authorization or market result",
            )

        return ROISimulationReport(results_by_category=cat_results, overall=overall)

    def _simulate_plays(
        self,
        plays: list[EdgeResult],
        outcomes: dict[tuple[str, str], float],
    ) -> ROISimulationResult:
        staked = 0.0
        returned = 0.0
        wins = 0

        for play in plays:
            actual = outcomes.get((play.player_name, play.category))
            if actual is None:
                continue

            staked += self.flat_stake
            is_over = play.recommendation.value.endswith("over")
            won = actual > play.line if is_over else actual < play.line

            if won:
                wins += 1
                returned += self.flat_stake + self._profit_on_win(self.default_vig)
            else:
                returned += 0.0

        n_bets = int(staked / self.flat_stake) if self.flat_stake > 0 else 0
        roi = (returned - staked) / staked * 100.0 if staked > 0 else 0.0

        return ROISimulationResult(
            n_bets=n_bets,
            wins=wins,
            total_staked=staked,
            total_returned=returned,
            roi_pct=roi,
            hit_rate=wins / n_bets if n_bets else 0.0,
            avg_edge_pct=sum(p.edge_pct for p in plays) / len(plays) if plays else 0.0,
            notes=(
                f"Legacy research: simulated {n_bets} flat-stake bets at assumed vig "
                f"{self.default_vig}; not market evidence"
            ),
        )

    @staticmethod
    def _profit_on_win(american_odds: int) -> float:
        if american_odds > 0:
            return 100.0 * (american_odds / 100.0)
        return 100.0 * (100.0 / abs(american_odds))
