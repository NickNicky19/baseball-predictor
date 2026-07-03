"""
Single-game hitter simulation using the PA simulator.

One game = a sampled number of plate appearances against a pitcher profile.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from src.models.dataclasses import GameSimulationResult, LeagueBaselines, StatcastProfile
from src.simulation.base_state import BaseState
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig


@dataclass(frozen=True)
class GameSimulatorInput:
    """Inputs required to simulate one hitter's game."""

    expected_pa: float
    pitcher_k_pct: float
    pitcher_bb_pct: float
    park_hr_factor: float = 1.0
    park_hits_factor: float = 1.0
    weather_hr_factor: float = 1.0
    umpire_k_bias: float = 0.0
    handedness_advantage: float = 0.0
    recent_form_mult: float = 1.0
    bvp_ops_factor: float = 1.0
    bvp_hr_factor: float = 1.0
    statcast: Optional[StatcastProfile] = None
    pitcher_hr_per_9: Optional[float] = None


class GameSimulator:
    """
    Simulates a single game for one hitter by chaining plate appearances.

    PA count is sampled from a Poisson distribution centered on expected_pa.
    """

    def __init__(
        self,
        pa_simulator: Optional[HybridPASimulator] = None,
        league_baselines: Optional[LeagueBaselines] = None,
        pa_config: Optional[PASimulatorConfig] = None,
        random_seed: Optional[int] = None,
    ):
        self.league = league_baselines or LeagueBaselines()
        self.pa_simulator = pa_simulator or HybridPASimulator(
            config=pa_config or PASimulatorConfig.from_league(self.league),
            league_baselines=self.league,
            random_seed=random_seed,
        )
        self.rng = random.Random(random_seed)

    def simulate_game(
        self,
        inputs: GameSimulatorInput,
        plate_appearances: Optional[int] = None,
    ) -> GameSimulationResult:
        """Run one simulated game and return counting stats."""
        pa_count = plate_appearances
        if pa_count is None:
            pa_count = max(1, int(self.rng.gauss(inputs.expected_pa, 0.65)))

        effective_k = inputs.pitcher_k_pct + inputs.umpire_k_bias
        combined_hr_factor = inputs.park_hr_factor * inputs.weather_hr_factor

        state = BaseState()
        for _ in range(pa_count):
            outcome = self.pa_simulator.simulate(
                pitcher_k_pct=effective_k,
                pitcher_bb_pct=inputs.pitcher_bb_pct,
                pitcher_hr_per_9=inputs.pitcher_hr_per_9,
                park_hr_factor=combined_hr_factor,
                park_hits_factor=inputs.park_hits_factor,
                handedness_advantage=inputs.handedness_advantage,
                recent_form_mult=inputs.recent_form_mult,
                bvp_ops_factor=inputs.bvp_ops_factor,
                bvp_hr_factor=inputs.bvp_hr_factor,
                statcast=inputs.statcast,
            )
            self.pa_simulator.apply_to_state(state, outcome)

        return GameSimulationResult(
            plate_appearances=pa_count,
            hits=state.hits,
            singles=state.singles,
            doubles=state.doubles,
            triples=state.triples,
            home_runs=state.home_runs,
            runs=state.runs,
            rbi=state.rbi,
            walks=state.walks,
            strikeouts=state.strikeouts,
        )