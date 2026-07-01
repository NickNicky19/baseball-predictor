"""Tests for the simulation layer (Phase 1+ architecture)."""

from src.models.dataclasses import LeagueBaselines, StatcastProfile
from src.simulation import BaseState, HybridPASimulator, MonteCarloEngine
from src.simulation.game_simulator import GameSimulator, GameSimulatorInput


def test_base_state_walk_bases_loaded():
    state = BaseState(bases=[1, 1, 1])
    state.advance_walk()
    assert state.runs == 1
    assert state.walks == 1


def test_hybrid_pa_simulator_returns_outcome():
    sim = HybridPASimulator(random_seed=42)
    outcome = sim.simulate(pitcher_k_pct=22.5, pitcher_bb_pct=8.5)
    assert outcome.outcome in {"out", "walk", "single", "double", "triple", "home_run"}


def test_game_simulator_produces_stats():
    engine = GameSimulator(random_seed=1)
    result = engine.simulate_game(
        GameSimulatorInput(expected_pa=4.0, pitcher_k_pct=22.0, pitcher_bb_pct=8.0),
        plate_appearances=4,
    )
    assert result.plate_appearances == 4
    assert result.hrr >= 0


def test_monte_carlo_hrr_distribution():
    league = LeagueBaselines()
    mc = MonteCarloEngine(league_baselines=league, random_seed=7)
    inputs = GameSimulatorInput(
        expected_pa=league.pa_per_game,
        pitcher_k_pct=league.k_pct,
        pitcher_bb_pct=league.bb_pct,
        statcast=StatcastProfile(player_id=1, player_name="Test", sample_pa=100, xwoba=league.xwoba),
    )
    result = mc.run(inputs, category="hrr", n_sims=500)
    assert result.n_sims == 500
    assert result.mean > 0
    assert result.p10 <= result.mean <= result.p90