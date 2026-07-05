"""
Game simulator — one hitter's game line from repeated PA simulation.

FIXES (this revision):
1. Runs/RBI no longer use the "+1 run, +1 RBI per hit" shortcut. Each PA now
   samples a realistic base-state (runners on) from the league base-state
   distribution, applies the PA outcome through BaseState advancement to get
   RBI credit, and models the batter's own run scored via base-reached
   scoring probabilities. A bases-empty single now credits 0 RBI; a HR always
   credits at least 1 RBI and 1 run.
2. pa_simulator now defaults to a real HybridPASimulator instead of silently
   staying None (which crashed MonteCarloEngine when constructed bare).
3. weather_hr_factor and umpire_k_bias were accepted in GameSimulatorInput
   but never used — they are now applied (weather multiplies the park HR
   factor; umpire bias shifts pitcher K%).
4. Fractional expected_pa is honored: floor(expected_pa) PAs plus one more
   with probability equal to the fractional part (4.6 PA no longer silently
   truncates to 4).

League base-state distribution and base-reached scoring probabilities are
long-run MLB empirical rates (Retrosheet-era stable values), exposed as
constructor parameters so the calibration layer can re-fit them from pairs
data rather than trusting the constants.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional, Any

from src.models.dataclasses import StatcastProfile, GameSimulationResult
from src.simulation.pa_simulator import HybridPASimulator
from src.simulation.base_state import BaseState


# Long-run MLB frequency of each base state at the start of a PA.
# Order: (runner on 1st, runner on 2nd, runner on 3rd) -> probability.
# Empirically stable across seasons to within ~1-2 points.
DEFAULT_BASE_STATE_DIST: dict[tuple[int, int, int], float] = {
    (0, 0, 0): 0.545,
    (1, 0, 0): 0.175,
    (0, 1, 0): 0.070,
    (0, 0, 1): 0.020,
    (1, 1, 0): 0.085,
    (1, 0, 1): 0.035,
    (0, 1, 1): 0.025,
    (1, 1, 1): 0.045,
}

# P(batter eventually scores | base reached on this PA). Long-run MLB values;
# HR is definitionally 1.0. Calibratable via constructor.
DEFAULT_P_SCORE_FROM_BASE: dict[str, float] = {
    "walk": 0.27,
    "single": 0.28,
    "double": 0.40,
    "triple": 0.56,
    "home_run": 1.0,
}


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
    """Simulates a hitter's performance in a game using PA-level simulation."""

    def __init__(
        self,
        pa_simulator: Optional[HybridPASimulator] = None,
        league_baselines=None,
        random_seed: Optional[int] = None,
        base_state_dist: Optional[dict[tuple[int, int, int], float]] = None,
        p_score_from_base: Optional[dict[str, float]] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        # Config-driven base-running parameters (config/base_running block)
        # take effect unless explicitly overridden by the kwargs above.
        if config and p_score_from_base is None:
            p_score_from_base = (
                config.get("base_running", {}).get("p_score_from_base") or None
            )
        self.league = league_baselines
        # FIX: construct a default simulator instead of holding None.
        self.pa_simulator = pa_simulator or HybridPASimulator(
            league_baselines=league_baselines,
            random_seed=random_seed,
        )
        self.rng = random.Random(random_seed)
        self._base_states, self._base_state_weights = self._normalize_dist(
            base_state_dist or DEFAULT_BASE_STATE_DIST
        )
        self.p_score_from_base = dict(DEFAULT_P_SCORE_FROM_BASE)
        if p_score_from_base:
            self.p_score_from_base.update(p_score_from_base)

    def seed(self, seed: int) -> None:
        """Reseed both the game-level and PA-level RNGs (reproducibility)."""
        self.rng.seed(seed)
        self.pa_simulator.rng.seed(seed + 1)

    def simulate_game(self, sim_input: GameSimulatorInput) -> GameSimulationResult:
        """Simulate one full game for a hitter and return aggregated results."""
        hits = singles = doubles = triples = home_runs = 0
        walks = strikeouts = runs = rbi = 0

        # FIX: apply weather and umpire context (previously accepted, unused).
        effective_park_hr = sim_input.park_hr_factor * sim_input.weather_hr_factor
        effective_pitcher_k = sim_input.pitcher_k_pct + sim_input.umpire_k_bias

        n_pa = self._sample_pa_count(sim_input.expected_pa)

        for _ in range(n_pa):
            outcome = self.pa_simulator.simulate(
                pitcher_k_pct=effective_pitcher_k,
                pitcher_bb_pct=sim_input.pitcher_bb_pct,
                park_hr_factor=effective_park_hr,
                park_hits_factor=sim_input.park_hits_factor,
                handedness_advantage=sim_input.handedness_advantage,
                recent_form_mult=sim_input.recent_form_mult,
                bvp_ops_factor=sim_input.bvp_ops_factor,
                bvp_hr_factor=sim_input.bvp_hr_factor,
                statcast=sim_input.statcast,
                pitcher_hr_per_9=sim_input.pitcher_hr_per_9,
                rich_features=sim_input.rich_features,
            )

            kind = outcome.outcome
            if kind == "home_run":
                home_runs += 1
                hits += 1
            elif kind == "triple":
                triples += 1
                hits += 1
            elif kind == "double":
                doubles += 1
                hits += 1
            elif kind == "single":
                singles += 1
                hits += 1
            elif kind == "walk":
                walks += 1
            elif kind == "out" and outcome.is_strikeout:
                strikeouts += 1

            # --- FIX: realistic RBI via sampled base state + BaseState logic
            if kind in ("single", "double", "triple", "home_run", "walk"):
                rbi += self._rbi_for_outcome(kind)
                runs += self._run_for_batter(kind)

        return GameSimulationResult(
            plate_appearances=n_pa,
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

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sample_pa_count(self, expected_pa: float) -> int:
        """floor(expected_pa) PAs, plus one with p = fractional part."""
        base = int(expected_pa)
        frac = max(0.0, expected_pa - base)
        if frac > 0 and self.rng.random() < frac:
            return base + 1
        return max(0, base)

    def _sample_base_state(self) -> tuple[int, int, int]:
        return self.rng.choices(self._base_states, weights=self._base_state_weights, k=1)[0]

    def _rbi_for_outcome(self, kind: str) -> int:
        """RBI credited to the batter, given a sampled runners-on context."""
        b1, b2, b3 = self._sample_base_state()
        state = BaseState(bases=[b1, b2, b3])
        if kind == "single":
            state.advance_single()
        elif kind == "double":
            state.advance_double()
        elif kind == "triple":
            state.advance_triple()
        elif kind == "home_run":
            state.advance_home_run()
        elif kind == "walk":
            state.advance_walk()
        return state.rbi

    def _run_for_batter(self, kind: str) -> int:
        """Whether the batter himself scores after reaching base."""
        if kind == "home_run":
            return 1
        p = self.p_score_from_base.get(kind, 0.0)
        return 1 if self.rng.random() < p else 0

    @staticmethod
    def _normalize_dist(
        dist: dict[tuple[int, int, int], float]
    ) -> tuple[list[tuple[int, int, int]], list[float]]:
        states = list(dist.keys())
        weights = [max(0.0, dist[s]) for s in states]
        total = sum(weights) or 1.0
        return states, [w / total for w in weights]

