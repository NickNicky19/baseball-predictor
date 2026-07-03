"""
Output safeguards — detect unrealistic hit/HR projections during validation.

Limits are derived from LeagueBaselines and PASimulatorConfig clamps so
no hand-tuned thresholds are introduced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.models.dataclasses import (
    LeagueBaselines,
    OutcomeProbabilities,
    PropCategory,
    PropProjection,
)
from src.simulation.pa_simulator import PASimulatorConfig


@dataclass(frozen=True)
class SafeguardLimits:
    """League-derived bounds for per-game and per-PA outcomes."""

    min_hits_per_game: float
    max_hits_per_game: float
    min_hr_per_game: float
    max_hr_per_game: float
    min_hit_prob_pa: float
    max_hit_prob_pa: float
    min_hr_prob_pa: float
    max_hr_prob_pa: float

    @classmethod
    def from_league(
        cls,
        league: LeagueBaselines,
        pa_config: Optional[PASimulatorConfig] = None,
        config: Optional[dict[str, Any]] = None,
    ) -> SafeguardLimits:
        cfg = pa_config or PASimulatorConfig.from_league(league)
        hit_rate = league.hits_per_game / max(league.pa_per_game, 1.0)
        hr_rate_pa = (league.hr_per_9 / 9.0) / 4.2
        validation = (config or {}).get("validation", {})
        overshoot_margin = float(validation.get("overshoot_margin", 1.75))

        return cls(
            min_hits_per_game=league.hits_per_game * 0.55,
            max_hits_per_game=league.pa_per_game * min(0.50, hit_rate * overshoot_margin),
            min_hr_per_game=hr_rate_pa * league.pa_per_game * 0.35,
            max_hr_per_game=league.pa_per_game * cfg.hr_max * overshoot_margin,
            min_hit_prob_pa=hit_rate * 0.55,
            max_hit_prob_pa=min(0.42, hit_rate * overshoot_margin),
            min_hr_prob_pa=cfg.hr_min * 0.80,
            max_hr_prob_pa=cfg.hr_max * overshoot_margin,
        )


@dataclass
class SafeguardReport:
    passed: bool
    violations: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": self.violations,
            "warnings": self.warnings,
        }


class OutputSafeguards:
    """Validates projections and outcome probabilities against league-feasible bounds."""

    def __init__(
        self,
        limits: Optional[SafeguardLimits] = None,
        league_baselines: Optional[LeagueBaselines] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        self.config = config or {}
        self.league = league_baselines or LeagueBaselines.from_config(self.config)
        self.limits = limits or SafeguardLimits.from_league(
            self.league, config=self.config
        )

    def check_outcome_probabilities(self, probs: OutcomeProbabilities) -> SafeguardReport:
        violations: list[str] = []
        warnings: list[str] = []
        lim = self.limits

        if not probs.is_valid():
            violations.append(f"outcome probabilities sum to {probs.total():.4f}, not ~1.0")

        if probs.hit_prob > lim.max_hit_prob_pa:
            violations.append(
                f"hit_prob {probs.hit_prob:.4f} exceeds max {lim.max_hit_prob_pa:.4f}"
            )
        elif probs.hit_prob < lim.min_hit_prob_pa:
            warnings.append(
                f"hit_prob {probs.hit_prob:.4f} below min {lim.min_hit_prob_pa:.4f}"
            )

        if probs.home_run > lim.max_hr_prob_pa:
            violations.append(
                f"hr_prob {probs.home_run:.4f} exceeds max {lim.max_hr_prob_pa:.4f}"
            )
        elif probs.home_run < lim.min_hr_prob_pa:
            warnings.append(
                f"hr_prob {probs.home_run:.4f} below min {lim.min_hr_prob_pa:.4f}"
            )

        return SafeguardReport(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )

    def check_projection(
        self,
        projection: PropProjection,
        expected_pa: float = 4.05,
    ) -> SafeguardReport:
        violations: list[str] = []
        warnings: list[str] = []
        lim = self.limits
        value = projection.projected_value
        category = projection.category

        if category == "hits":
            if value > lim.max_hits_per_game:
                violations.append(
                    f"hits projection {value:.3f} exceeds max {lim.max_hits_per_game:.3f}"
                )
            elif value < lim.min_hits_per_game:
                warnings.append(
                    f"hits projection {value:.3f} below min {lim.min_hits_per_game:.3f}"
                )
        elif category == "home_runs":
            if value > lim.max_hr_per_game:
                violations.append(
                    f"HR projection {value:.3f} exceeds max {lim.max_hr_per_game:.3f}"
                )
            elif value < lim.min_hr_per_game:
                warnings.append(
                    f"HR projection {value:.3f} below min {lim.min_hr_per_game:.3f}"
                )
        elif category == "hrr" and expected_pa > 0:
            max_hrr = lim.max_hits_per_game + expected_pa * 0.35
            if value > max_hrr:
                violations.append(f"HRR projection {value:.3f} exceeds max {max_hrr:.3f}")

        if projection.outcome_probs:
            prob_report = self.check_outcome_probabilities(projection.outcome_probs)
            violations.extend(prob_report.violations)
            warnings.extend(prob_report.warnings)

        return SafeguardReport(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )

    def check_projections(
        self,
        projections: list[PropProjection],
        expected_pa: float = 4.05,
    ) -> SafeguardReport:
        all_violations: list[str] = []
        all_warnings: list[str] = []
        for proj in projections:
            report = self.check_projection(proj, expected_pa=expected_pa)
            all_violations.extend(report.violations)
            all_warnings.extend(report.warnings)
        return SafeguardReport(
            passed=len(all_violations) == 0,
            violations=all_violations,
            warnings=all_warnings,
        )