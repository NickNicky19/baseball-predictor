"""
Monte Carlo aggregation over repeated game simulations.

Produces MonteCarloResult distributions for hitter prop categories.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from src.models.dataclasses import (
    GameSimulationResult,
    LeagueBaselines,
    MonteCarloResult,
    PropCategory,
)
from src.simulation.game_simulator import GameSimulator, GameSimulatorInput


@dataclass(frozen=True)
class FantasyScoring:
    """Fantasy point weights loaded from config."""

    single: int = 3
    double: int = 5
    triple: int = 8
    home_run: int = 10
    rbi: int = 2
    run: int = 2
    walk: int = 2
    hit_by_pitch: int = 2
    stolen_base: int = 5

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> FantasyScoring:
        raw = config.get("fantasy_scoring", {})
        return cls(
            single=int(raw.get("single", 3)),
            double=int(raw.get("double", 5)),
            triple=int(raw.get("triple", 8)),
            home_run=int(raw.get("home_run", 10)),
            rbi=int(raw.get("rbi", 2)),
            run=int(raw.get("run", 2)),
            walk=int(raw.get("walk", 2)),
            hit_by_pitch=int(raw.get("hit_by_pitch", 2)),
            stolen_base=int(raw.get("stolen_base", 5)),
        )


class MonteCarloEngine:
    """Runs N game simulations and aggregates prop-category distributions."""

    DEFAULT_THRESHOLDS: dict[PropCategory, list[float]] = {
        "hits": [1.0, 2.0],
        "hrr": [2.0, 3.0],
        "home_runs": [1.0],
        "fantasy": [5.0, 10.0],
        "strikeouts": [5.0, 6.0],
    }

    def __init__(
        self,
        game_simulator: Optional[GameSimulator] = None,
        league_baselines: Optional[LeagueBaselines] = None,
        fantasy_scoring: Optional[FantasyScoring] = None,
        random_seed: Optional[int] = None,
    ):
        self.league = league_baselines or LeagueBaselines()
        self.game_simulator = game_simulator or GameSimulator(
            league_baselines=self.league,
            random_seed=random_seed,
        )
        self.fantasy_scoring = fantasy_scoring or FantasyScoring()
        self.rng = random.Random(random_seed)

    def run(
        self,
        inputs: GameSimulatorInput,
        category: PropCategory,
        n_sims: int = 8000,
        thresholds: Optional[list[float]] = None,
        store_samples: bool = False,
    ) -> MonteCarloResult:
        """Simulate n_sims games and aggregate for the requested prop category."""
        samples: list[float] = []
        game_results: list[GameSimulationResult] = []

        for i in range(n_sims):
            game_seed = self.rng.randint(0, 2**31 - 1)
            sim = GameSimulator(
                pa_simulator=self.game_simulator.pa_simulator,
                league_baselines=self.league,
                random_seed=game_seed,
            )
            result = sim.simulate_game(inputs)
            value = self._category_value(result, category)
            samples.append(value)
            if store_samples:
                game_results.append(result)

        arr = np.array(samples, dtype=float)
        thresh = thresholds or self.DEFAULT_THRESHOLDS.get(category, [])
        p_ge = {t: float((arr >= t).mean()) for t in thresh}

        return MonteCarloResult(
            n_sims=n_sims,
            category=category,
            mean=float(arr.mean()),
            median=float(np.median(arr)),
            p10=float(np.percentile(arr, 10)),
            p90=float(np.percentile(arr, 90)),
            p_ge_threshold=p_ge,
            per_game_samples=game_results if store_samples else None,
        )

    def _category_value(self, result: GameSimulationResult, category: PropCategory) -> float:
        if category == "hits":
            return float(result.hits)
        if category == "home_runs":
            return float(result.home_runs)
        if category == "hrr":
            return float(result.hrr)
        if category == "fantasy":
            return self._fantasy_points(result)
        if category == "strikeouts":
            return float(result.strikeouts)
        return float(result.hrr)

    def _fantasy_points(self, result: GameSimulationResult) -> float:
        fs = self.fantasy_scoring
        return (
            result.singles * fs.single
            + result.doubles * fs.double
            + result.triples * fs.triple
            + result.home_runs * fs.home_run
            + result.rbi * fs.rbi
            + result.runs * fs.run
            + result.walks * fs.walk
        )