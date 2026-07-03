"""
Probability engine — explicit outcome probabilities as the modeling foundation.

Wraps HybridPASimulator.expected_outcome_probabilities() and provides
per-game expectations, tail probabilities, and explainability hooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from src.models.dataclasses import (
    FeatureVector,
    LeagueBaselines,
    OutcomeProbabilities,
    PlayerFeatureBundle,
    PropCategory,
    StatcastProfile,
)
from src.simulation.game_simulator import GameSimulatorInput
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig


@dataclass(frozen=True)
class PerGameExpectations:
    """Analytic per-game expectations from PA probabilities × expected PA."""

    expected_pa: float
    expected_hits: float
    expected_home_runs: float
    expected_walks: float
    expected_strikeouts: float
    expected_hrr: float

    def get(self, category: PropCategory) -> float:
        if category == "hits":
            return self.expected_hits
        if category == "home_runs":
            return self.expected_home_runs
        if category == "hrr":
            return self.expected_hrr
        if category == "strikeouts":
            return self.expected_strikeouts
        return self.expected_hrr


class ProbabilityEngine:
    """
    Central probability estimation layer for Phase 10.

    Computes explicit outcome vectors, per-game expectations, and contribution
    summaries without replacing Monte Carlo simulation.
    """

    def __init__(
        self,
        pa_simulator: Optional[HybridPASimulator] = None,
        league_baselines: Optional[LeagueBaselines] = None,
        pa_config: Optional[PASimulatorConfig] = None,
    ):
        self.league = league_baselines or LeagueBaselines()
        self.pa_simulator = pa_simulator or HybridPASimulator(
            config=pa_config or PASimulatorConfig.from_league(self.league),
            league_baselines=self.league,
        )

    def from_bundle(self, bundle: PlayerFeatureBundle) -> OutcomeProbabilities:
        """Compute PA outcome probabilities from a feature bundle."""
        sim_input = self._bundle_to_kwargs(bundle)
        return self.compute_pa_probabilities(**sim_input)

    def compute_pa_probabilities(
        self,
        pitcher_k_pct: Optional[float] = None,
        pitcher_bb_pct: Optional[float] = None,
        pitcher_hr_per_9: Optional[float] = None,
        park_hr_factor: float = 1.0,
        park_hits_factor: float = 1.0,
        handedness_advantage: float = 0.0,
        recent_form_mult: float = 1.0,
        bvp_ops_factor: float = 1.0,
        bvp_hr_factor: float = 1.0,
        statcast: Optional[StatcastProfile] = None,
    ) -> OutcomeProbabilities:
        raw = self.pa_simulator.expected_outcome_probabilities(
            pitcher_k_pct=pitcher_k_pct,
            pitcher_bb_pct=pitcher_bb_pct,
            pitcher_hr_per_9=pitcher_hr_per_9,
            park_hr_factor=park_hr_factor,
            park_hits_factor=park_hits_factor,
            handedness_advantage=handedness_advantage,
            recent_form_mult=recent_form_mult,
            bvp_ops_factor=bvp_ops_factor,
            bvp_hr_factor=bvp_hr_factor,
            statcast=statcast,
        )
        return OutcomeProbabilities(
            strikeout=raw["strikeout"],
            walk=raw["walk"],
            home_run=raw["home_run"],
            single=raw["single"],
            double=raw["double"],
            triple=raw["triple"],
            out_on_bip=raw["out_on_bip"],
        )

    def per_game_expectations(
        self,
        probs: OutcomeProbabilities,
        expected_pa: float,
    ) -> PerGameExpectations:
        """Convert per-PA probabilities to per-game counting expectations."""
        pa = expected_pa
        hits = pa * probs.hit_prob
        return PerGameExpectations(
            expected_pa=pa,
            expected_hits=hits,
            expected_home_runs=pa * probs.home_run,
            expected_walks=pa * probs.walk,
            expected_strikeouts=pa * probs.strikeout,
            expected_hrr=pa * probs.hrr_prob + hits * 0.15,
        )

    def explain_contributions(
        self,
        bundle: PlayerFeatureBundle,
        probs: OutcomeProbabilities,
    ) -> dict[str, Any]:
        """
        Light explainability scaffold: group-level feature signals vs outcome rates.

        Full SHAP-style decomposition deferred; this maps feature groups to
        implied outcome probabilities for inspection and future UI.
        """
        groups: dict[str, dict[str, float]] = {}
        if bundle.features:
            for group_name, keys in bundle.features.groups.items():
                group_vals = bundle.features.group_values(group_name)
                if group_vals:
                    groups[group_name] = {
                        "mean_abs": sum(abs(v) for v in group_vals.values()) / len(group_vals),
                        "max_feature": max(group_vals, key=lambda k: abs(group_vals[k])),
                    }

        return {
            "outcome_probs": probs.to_dict(),
            "matchup": {
                "platoon_advantage": bundle.matchup.platoon_advantage,
                "bvp_ops_factor": bundle.matchup.bvp_ops_factor,
                "bvp_hr_factor": bundle.matchup.bvp_hr_factor,
                "recent_form": bundle.matchup.recent_form_multiplier,
            },
            "feature_groups": groups,
            "feature_count": bundle.features.count() if bundle.features else 0,
        }

    def from_sim_input(self, inputs: GameSimulatorInput) -> OutcomeProbabilities:
        return self.compute_pa_probabilities(
            pitcher_k_pct=inputs.pitcher_k_pct,
            pitcher_bb_pct=inputs.pitcher_bb_pct,
            pitcher_hr_per_9=inputs.pitcher_hr_per_9,
            park_hr_factor=inputs.park_hr_factor * inputs.weather_hr_factor,
            park_hits_factor=inputs.park_hits_factor,
            handedness_advantage=inputs.handedness_advantage,
            recent_form_mult=inputs.recent_form_mult,
            bvp_ops_factor=inputs.bvp_ops_factor,
            bvp_hr_factor=inputs.bvp_hr_factor,
            statcast=inputs.statcast,
        )

    def _bundle_to_kwargs(self, bundle: PlayerFeatureBundle) -> dict[str, Any]:
        pitcher_k = self.league.k_pct
        pitcher_bb = self.league.bb_pct
        pitcher_hr_per_9 = None

        if bundle.pitcher_statcast:
            if bundle.pitcher_statcast.k_rate is not None:
                pitcher_k = bundle.pitcher_statcast.k_rate * 100.0
            if bundle.pitcher_statcast.bb_rate is not None:
                pitcher_bb = bundle.pitcher_statcast.bb_rate * 100.0
            pitcher_hr_per_9 = bundle.pitcher_statcast.hr_per_9

        weather_hr = float(bundle.metadata.get("weather_hr_factor", 1.0))

        return {
            "pitcher_k_pct": pitcher_k,
            "pitcher_bb_pct": pitcher_bb,
            "pitcher_hr_per_9": pitcher_hr_per_9,
            "park_hr_factor": bundle.park.hr_factor * weather_hr,
            "park_hits_factor": bundle.park.hits_factor,
            "handedness_advantage": bundle.matchup.platoon_advantage,
            "recent_form_mult": bundle.matchup.recent_form_multiplier,
            "bvp_ops_factor": bundle.matchup.bvp_ops_factor,
            "bvp_hr_factor": bundle.matchup.bvp_hr_factor,
            "statcast": bundle.statcast,
        }