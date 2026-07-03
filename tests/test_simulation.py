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


def test_league_average_hits_per_game_realistic():
    """BIP hit-type sampling must include outs or hits inflate to ~3 per game."""
    league = LeagueBaselines()
    sim = HybridPASimulator(league_baselines=league, random_seed=99)
    n_sims = 8000
    pa_per_game = int(round(league.pa_per_game))

    total_hits = 0
    for _ in range(n_sims):
        game_hits = 0
        for _ in range(pa_per_game):
            outcome = sim.simulate(
                pitcher_k_pct=league.k_pct,
                pitcher_bb_pct=league.bb_pct,
            )
            if outcome.outcome in {"single", "double", "triple", "home_run"}:
                game_hits += 1
        total_hits += game_hits

    mean_hits = total_hits / n_sims
    assert 0.7 <= mean_hits <= 1.6, f"mean hits/game {mean_hits:.3f} outside realistic range"