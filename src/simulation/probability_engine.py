"""
Probability Engine

Generates per-plate-appearance outcome probabilities for a hitter given a
PlayerFeatureBundle.

FIX (this revision): the previous implementation returned a hardcoded
placeholder distribution (identical for every player in the league). It now
delegates to HybridPASimulator.expected_outcome_probabilities(), extracting
pitcher rates, park/weather factors, matchup context, Statcast profile, and
rich features from the bundle — the same inputs the Monte Carlo sampling
path uses, so explicit probabilities and sampled simulations agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any

from src.models.dataclasses import (
    LeagueBaselines,
    OutcomeProbabilities,
    PlayerFeatureBundle,
)
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig


@dataclass
class PerGameExpectations:
    """
    Expected per-game values for a player (used by calibration, backtesting,
    and correction layers).
    """
    hits: float = 0.0
    home_runs: float = 0.0
    walks: float = 0.0
    strikeouts: float = 0.0
    rbi: float = 0.0
    runs: float = 0.0
    hrr: float = 0.0


class ProbabilityEngine:
    """
    Generates outcome probabilities for a hitter given a PlayerFeatureBundle.

    Accepts an optional pre-built pa_simulator (the design the test suite
    expects); otherwise constructs one from league baselines + pa_config.
    """

    def __init__(
        self,
        league_baselines: Optional[LeagueBaselines] = None,
        pa_config: Optional[PASimulatorConfig] = None,
        pa_simulator: Optional[HybridPASimulator] = None,
    ):
        self.league = league_baselines or LeagueBaselines()
        self.pa_config = pa_config or PASimulatorConfig.from_league(self.league)
        self.pa_simulator = pa_simulator or HybridPASimulator(
            config=self.pa_config,
            league_baselines=self.league,
        )

    def from_bundle(
        self,
        bundle: PlayerFeatureBundle,
        rich_features: Optional[dict[str, Any]] = None,
    ) -> OutcomeProbabilities:
        """
        Generate outcome probabilities from a PlayerFeatureBundle.

        Uses the same inputs as the Monte Carlo sampling path so that the
        explicit distribution and sampled simulations are consistent.
        """
        rich = rich_features if rich_features is not None else (bundle.rich_features or {})

        pitcher_k = self.league.k_pct
        pitcher_bb = self.league.bb_pct
        pitcher_hr_per_9: Optional[float] = None
        if bundle.pitcher_statcast is not None:
            if bundle.pitcher_statcast.k_rate is not None:
                pitcher_k = bundle.pitcher_statcast.k_rate * 100.0
            if bundle.pitcher_statcast.bb_rate is not None:
                pitcher_bb = bundle.pitcher_statcast.bb_rate * 100.0
            if bundle.pitcher_statcast.hr_per_9 is not None:
                pitcher_hr_per_9 = bundle.pitcher_statcast.hr_per_9

        weather_hr = float(bundle.metadata.get("weather_hr_factor", 1.0))
        umpire_k_bias = bundle.umpire.k_bias if bundle.umpire else 0.0

        probs = self.pa_simulator.expected_outcome_probabilities(
            pitcher_k_pct=pitcher_k + umpire_k_bias,
            pitcher_bb_pct=pitcher_bb,
            pitcher_hr_per_9=pitcher_hr_per_9,
            park_hr_factor=bundle.park.hr_factor * weather_hr,
            park_hits_factor=bundle.park.hits_factor,
            handedness_advantage=bundle.matchup.platoon_advantage,
            recent_form_mult=bundle.matchup.recent_form_multiplier,
            bvp_ops_factor=bundle.matchup.bvp_ops_factor,
            bvp_hr_factor=bundle.matchup.bvp_hr_factor,
            statcast=bundle.statcast,
            rich_features=rich,
        )

        return OutcomeProbabilities(
            strikeout=probs["strikeout"],
            walk=probs["walk"],
            home_run=probs["home_run"],
            single=probs["single"],
            double=probs["double"],
            triple=probs["triple"],
            out_on_bip=probs["out_on_bip"],
        )

