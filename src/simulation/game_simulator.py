from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional, Any

from src.models.dataclasses import StatcastProfile, GameSimulationResult
from src.simulation.pa_simulator import HybridPASimulator
from src.simulation.base_state import BaseState


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
    rich_features: Optional[dict[str, Any]] = None


class GameSimulator:
    """
    Simulates a hitter's performance in a game using plate appearance simulation.
    """

    def __init__(
        self,
        pa_simulator: Optional[HybridPASimulator] = None,
        league_baselines=None,
        random_seed: Optional[int] = None,
    ):
        self.pa_simulator = pa_simulator
        self.league = league_baselines
        self.rng = random.Random(random_seed) if random_seed is not None else random.Random()

    def simulate_game(self, sim_input: GameSimulatorInput) -> GameSimulationResult:
        """
        Simulate one full game for a hitter and return aggregated results.
        """
        hits = 0
        singles = 0
        doubles = 0
        triples = 0
        home_runs = 0
        walks = 0
        strikeouts = 0
        runs = 0
        rbi = 0

        for _ in range(int(sim_input.expected_pa)):
            outcome = self.pa_simulator.simulate(
                pitcher_k_pct=sim_input.pitcher_k_pct,
                pitcher_bb_pct=sim_input.pitcher_bb_pct,
                park_hr_factor=sim_input.park_hr_factor,
                park_hits_factor=sim_input.park_hits_factor,
                handedness_advantage=sim_input.handedness_advantage,
                recent_form_mult=sim_input.recent_form_mult,
                bvp_ops_factor=sim_input.bvp_ops_factor,
                bvp_hr_factor=sim_input.bvp_hr_factor,
                statcast=sim_input.statcast,
                pitcher_hr_per_9=sim_input.pitcher_hr_per_9,
                rich_features=sim_input.rich_features,
            )

            if outcome.outcome == "home_run":
                home_runs += 1
                hits += 1
                runs += 1
                rbi += 1
            elif outcome.outcome == "triple":
                triples += 1
                hits += 1
                runs += 1
                rbi += 1
            elif outcome.outcome == "double":
                doubles += 1
                hits += 1
                runs += 1
                rbi += 1
            elif outcome.outcome == "single":
                singles += 1
                hits += 1
                runs += 1
                rbi += 1
            elif outcome.outcome == "walk":
                walks += 1
            elif outcome.outcome == "out" and outcome.is_strikeout:
                strikeouts += 1

        return GameSimulationResult(
            plate_appearances=int(sim_input.expected_pa),
            hits=hits,
            singles=singles,
            doubles=doubles,
            triples=triples,
            home_runs=home_runs,
            runs=runs,
            rbi=rbi,
            walks=walks,
            strikeouts=strikeouts,
        )
