"""
Data-driven park factor estimation from historical prediction-outcome pairs.

Replaces static hand-tuned park_factors with empirical venue rates shrunk
toward league average (factor = 1.0).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

from src.models.dataclasses import ParkFactors
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ParkFactorSettings:
    """
    Park factor estimation settings (config.json ``park_factors`` block).

    Keys: ``estimation_shrinkage``, ``min_games``, ``blend_with_static``.
    Venue-specific entries (e.g. ``Coors Field``) are static fallbacks, not settings.
    """

    shrinkage: float = 0.35
    min_games: int = 8
    blend_with_static: float = 0.5

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ParkFactorSettings:
        block = config.get("park_factors", {})
        return cls(
            shrinkage=float(block.get("estimation_shrinkage", block.get("shrinkage", 0.35))),
            min_games=int(block.get("min_games", 8)),
            blend_with_static=float(block.get("blend_with_static", 0.5)),
        )


class ParkFactorEstimator:
    """Estimate venue-specific HR/hit/run factors from paired outcomes."""

    def __init__(self, settings: Optional[ParkFactorSettings] = None):
        self.settings = settings or ParkFactorSettings()

    def estimate_from_pairs(
        self,
        pairs: pd.DataFrame,
        league_hr_rate: float = 0.028,
        league_hit_rate: float = 0.22,
        league_run_rate: float = 0.48,
    ) -> dict[str, ParkFactors]:
        """
        Fit park factors from pairs with columns: venue, category, actual_value.

        Uses category-specific actual rates vs league anchors, shrunk toward 1.0.
        """
        if pairs.empty or "venue" not in pairs.columns:
            return {}

        required = {"category", "actual_value"}
        if not required.issubset(pairs.columns):
            logger.warning("Pairs missing columns for park estimation: %s", required - set(pairs.columns))
            return {}

        factors: dict[str, ParkFactors] = {}
        for venue, group in pairs.groupby("venue"):
            venue_name = str(venue)
            if len(group) < self.settings.min_games:
                continue

            hr_mask = group["category"].isin(["home_runs", "hr"])
            hit_mask = group["category"] == "hits"
            hrr_mask = group["category"] == "hrr"

            hr_factor = self._rate_factor(group.loc[hr_mask, "actual_value"], league_hr_rate)
            hit_factor = self._rate_factor(group.loc[hit_mask, "actual_value"], league_hit_rate)
            run_factor = self._rate_factor(group.loc[hrr_mask, "actual_value"], league_run_rate, default=1.0)

            factors[venue_name] = ParkFactors(
                venue=venue_name,
                hits_factor=hit_factor,
                hr_factor=hr_factor,
                runs_factor=run_factor,
            )

        return factors

    def blend_with_static(
        self,
        estimated: dict[str, ParkFactors],
        static: dict[str, dict[str, float]],
    ) -> dict[str, ParkFactors]:
        """Blend empirical estimates with legacy static config values."""
        alpha = max(0.0, min(1.0, self.settings.blend_with_static))
        blended: dict[str, ParkFactors] = {}

        all_venues = set(estimated) | set(static)
        for venue in all_venues:
            est = estimated.get(venue)
            stat = static.get(venue, {})
            if est and stat:
                blended[venue] = ParkFactors(
                    venue=venue,
                    hits_factor=round(alpha * est.hits_factor + (1 - alpha) * float(stat.get("hits", 1.0)), 4),
                    hr_factor=round(alpha * est.hr_factor + (1 - alpha) * float(stat.get("hr", 1.0)), 4),
                    runs_factor=round(alpha * est.runs_factor + (1 - alpha) * float(stat.get("runs", 1.0)), 4),
                )
            elif est:
                blended[venue] = est
            elif stat:
                blended[venue] = ParkFactors(
                    venue=venue,
                    hits_factor=float(stat.get("hits", 1.0)),
                    hr_factor=float(stat.get("hr", 1.0)),
                    runs_factor=float(stat.get("runs", 1.0)),
                )
        return blended

    def _rate_factor(self, series: pd.Series, league_rate: float, default: float = 1.0) -> float:
        if series.empty or league_rate <= 0:
            return default
        observed = float(pd.to_numeric(series, errors="coerce").dropna().mean())
        raw = observed / league_rate if league_rate > 0 else 1.0
        shrink = self.settings.shrinkage
        return round(1.0 + (raw - 1.0) * shrink, 4)