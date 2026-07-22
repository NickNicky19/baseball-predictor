"""Tests for the simulation layer (Phase 1+ architecture).

Regression coverage is organized around the bugs that actually reached
production, so each is impossible to reintroduce silently:

- Base-state advancement (phantom-runner single, runner-on-third walk).
- CONTACT-CONDITIONAL NEUTRALITY: a hitter whose Statcast profile equals the
  league contact-conditional baselines must project league-average output.
  This is the guard that would have caught the leaguewide HRR overshoot —
  centering contact-conditional xSLG (~0.62) against season xSLG (0.41) gave
  every profiled hitter a phantom power boost.
- Elite/weak discrimination and HRR realism bands.
"""

from dataclasses import replace
import hashlib
import json

import pytest

from src.models.dataclasses import LeagueBaselines, StatcastProfile
from src.simulation import BaseState, HybridPASimulator, MonteCarloEngine, PASimulatorConfig
from src.simulation.game_simulator import GameSimulator, GameSimulatorInput


# ---------------------------------------------------------------------------
# Base state
# ---------------------------------------------------------------------------


def test_base_state_walk_bases_loaded():
    state = BaseState(bases=[1, 1, 1])
    state.advance_walk()
    assert state.runs == 1
    assert state.walks == 1


def test_base_state_single_no_phantom_runner():
    """Single with a runner on 2nd only: [0,1,0] -> [1,0,1], nobody scores."""
    state = BaseState(bases=[0, 1, 0])
    state.advance_single()
    assert state.bases == [1, 0, 1]
    assert state.runs == 0


def test_base_state_walk_runner_on_third_holds():
    """Walk with 1st and 3rd occupied: batter/1st advance, runner on 3rd holds."""
    state = BaseState(bases=[1, 0, 1])
    state.advance_walk()
    assert state.bases == [1, 1, 1]
    assert state.runs == 0


# ---------------------------------------------------------------------------
# PA simulator
# ---------------------------------------------------------------------------


def test_hybrid_pa_simulator_returns_outcome():
    sim = HybridPASimulator(random_seed=42)
    outcome = sim.simulate(pitcher_k_pct=22.5, pitcher_bb_pct=8.5)
    assert outcome.outcome in {"out", "walk", "single", "double", "triple", "home_run"}


def _contact_baseline_profile(league: LeagueBaselines) -> StatcastProfile:
    """A profile whose values equal the league CONTACT-CONDITIONAL baselines.

    StatcastProfile.xwoba/xslg are averaged over batted-ball events, so a truly
    league-average hitter has xslg ~= league.xslg_on_contact (~0.62), NOT the
    season xslg (~0.41). Such a hitter must be neutral.
    """
    return StatcastProfile(
        player_id=1,
        player_name="League Avg (on contact)",
        sample_pa=300,
        xwoba=league.xwoba_on_contact,
        xslg=league.xslg_on_contact,
        barrel_rate=league.barrel_rate,
        hard_hit_rate=league.hard_hit_rate,
        contact_rate=league.contact_rate,
    )


def test_contact_conditional_profile_is_neutral():
    """A league-average-on-contact profile must produce ~the same per-PA
    probabilities as no profile at all. Guards the centering-mismatch bug."""
    league = LeagueBaselines()
    sim = HybridPASimulator(league_baselines=league, random_seed=1)
    with_profile = sim.expected_outcome_probabilities(statcast=_contact_baseline_profile(league))
    without = sim.expected_outcome_probabilities()
    for key in with_profile:
        assert abs(with_profile[key] - without[key]) < 0.015, (
            f"{key}: {with_profile[key]:.4f} (profile) vs {without[key]:.4f} (none) "
            f"-- league-average-on-contact hitter is not neutral"
        )


def test_elite_hitter_beats_weak_hitter():
    """Model must discriminate on power and contact."""
    league = LeagueBaselines()
    sim = HybridPASimulator(league_baselines=league, random_seed=1)
    elite = StatcastProfile(
        player_id=2, player_name="Elite", sample_pa=300,
        xwoba=0.430, xslg=0.780, barrel_rate=0.18, hard_hit_rate=0.55, contact_rate=0.80,
    )
    weak = StatcastProfile(
        player_id=3, player_name="Weak", sample_pa=300,
        xwoba=0.330, xslg=0.540, barrel_rate=0.05, hard_hit_rate=0.33, contact_rate=0.70,
    )
    p_elite = sim.expected_outcome_probabilities(statcast=elite)
    p_weak = sim.expected_outcome_probabilities(statcast=weak)

    def hit_total(p):
        return p["single"] + p["double"] + p["triple"] + p["home_run"]

    assert p_elite["home_run"] > p_weak["home_run"]
    assert hit_total(p_elite) > hit_total(p_weak)


def test_pitcher_hr9_direction_candidate_is_opt_in_and_monotone():
    """The correction changes only the known-bad HR/9 direction when enabled."""
    league = LeagueBaselines()
    frozen_config = PASimulatorConfig.from_league(league)
    candidate_config = replace(frozen_config, correct_pitcher_hr9_direction=True)
    frozen = HybridPASimulator(config=frozen_config, league_baselines=league)
    candidate = HybridPASimulator(config=candidate_config, league_baselines=league)

    neutral_frozen = frozen.expected_outcome_probabilities(
        pitcher_hr_per_9=league.hr_per_9
    )
    neutral_candidate = candidate.expected_outcome_probabilities(
        pitcher_hr_per_9=league.hr_per_9
    )
    assert neutral_candidate == neutral_frozen

    low = max(0.1, league.hr_per_9 * 0.5)
    high = league.hr_per_9 * 1.5
    assert (
        frozen.expected_outcome_probabilities(pitcher_hr_per_9=high)["home_run"]
        < frozen.expected_outcome_probabilities(pitcher_hr_per_9=low)["home_run"]
    )
    assert (
        candidate.expected_outcome_probabilities(pitcher_hr_per_9=high)["home_run"]
        > candidate.expected_outcome_probabilities(pitcher_hr_per_9=low)["home_run"]
    )


def test_fitted_pa_distribution_is_hash_bound_and_missing_fails(tmp_path):
    artifact = tmp_path / "pa.json"
    complete = {
        str(slot): {"4": 0.5, "5": 0.5} for slot in range(1, 10)
    }
    artifact.write_text(
        json.dumps({"by_lineup_slot": complete}),
        encoding="utf-8",
    )
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    valid = {
        "base_running": {
            "pa_distribution_path": str(artifact),
            "pa_distribution_sha256": digest,
        }
    }
    simulator = GameSimulator(config=valid, random_seed=1)
    assert simulator._pa_dist_states[1] == [4, 5]

    artifact.write_text(
        json.dumps({"by_lineup_slot": {str(i): {"3": 1.0} for i in range(1, 10)}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="PA distribution hash mismatch"):
        GameSimulator(config=valid, random_seed=1)

    missing = {
        "base_running": {
            "pa_distribution_path": str(tmp_path / "missing.json"),
            "pa_distribution_sha256": "0" * 64,
        }
    }
    with pytest.raises(ValueError, match="does not exist"):
        GameSimulator(config=missing, random_seed=1)


@pytest.mark.parametrize(
    ("bad_distribution", "message"),
    [
        ({4: -0.1, 5: 1.1}, "negative probability"),
        ({4: float("nan"), 5: 1.0}, "non-finite probability"),
        ({4: 0.4, 5: 0.4}, "not 1.0"),
    ],
)
def test_pa_distribution_probabilities_fail_closed(bad_distribution, message):
    with pytest.raises(ValueError, match=message):
        GameSimulator(pa_distribution={1: bad_distribution}, random_seed=1)


def test_fitted_pa_distribution_requires_all_lineup_slots(tmp_path):
    artifact = tmp_path / "partial_pa.json"
    artifact.write_text(
        json.dumps({"by_lineup_slot": {"1": {"4": 1.0}}}), encoding="utf-8"
    )
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="must cover lineup slots 1-9"):
        GameSimulator(
            config={
                "base_running": {
                    "pa_distribution_path": str(artifact),
                    "pa_distribution_sha256": digest,
                }
            },
            random_seed=1,
        )


# ---------------------------------------------------------------------------
# Game simulator / Monte Carlo
# ---------------------------------------------------------------------------


def test_game_simulator_produces_stats():
    engine = GameSimulator(random_seed=1)
    result = engine.simulate_game(
        GameSimulatorInput(expected_pa=4.0, pitcher_k_pct=22.0, pitcher_bb_pct=8.0)
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


def test_league_average_on_contact_hrr_realistic():
    """End-to-end HRR for a league-average-on-contact hitter must land in a
    realistic band, NOT the ~3.3 the centering bug produced."""
    league = LeagueBaselines()
    mc = MonteCarloEngine(league_baselines=league, random_seed=7)
    inputs = GameSimulatorInput(
        expected_pa=league.pa_per_game,
        pitcher_k_pct=league.k_pct,
        pitcher_bb_pct=league.bb_pct,
        statcast=_contact_baseline_profile(league),
    )
    result = mc.run(inputs, category="hrr", n_sims=4000)
    assert 1.2 <= result.mean <= 2.2, (
        f"league-average-on-contact HRR {result.mean:.3f} outside realistic band "
        f"(centering bug produced ~3.3)"
    )


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

