"""Phase 10 tests — feature factory, matchup intelligence, probability engine."""

from __future__ import annotations

from src.data.mlb_api import HittingStatsSnapshot, PitchingStatsSnapshot
from src.evaluation.output_safeguards import OutputSafeguards
from src.features.feature_factory import FeatureFactory
from src.features.feature_vector import FeatureVectorBuilder
from src.features.matchup_intelligence import MatchupIntelligence
from src.models.dataclasses import (
    GameContext,
    HitterGameContext,
    LeagueBaselines,
    MatchupContext,
    ParkFactors,
    PitcherStatcastProfile,
    PlayerFeatureBundle,
    PlayerIdentity,
    StatcastDistributionProfile,
    StatcastProfile,
    WeatherContext,
)
from src.prediction.prop_engine import PropEngine
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig
from src.simulation.probability_engine import ProbabilityEngine


class MockMatchupAPI:
    def get_hitting_stats(self, player_id: int):
        season = HittingStatsSnapshot(
            obp=0.34, slg=0.44, pa=400, home_runs=18, avg=0.265
        )
        recent = HittingStatsSnapshot(
            obp=0.36, slg=0.48, pa=60, home_runs=4, avg=0.280
        )
        return season, recent

    def get_platoon_splits(self, player_id: int):
        vs_lhp = HittingStatsSnapshot(obp=0.32, slg=0.40, pa=80, avg=0.240)
        vs_rhp = HittingStatsSnapshot(obp=0.35, slg=0.46, pa=320, avg=0.270)
        return vs_lhp, vs_rhp

    def get_bvp_stats(self, hitter_id: int, pitcher_id: int):
        return HittingStatsSnapshot(obp=0.38, slg=0.55, pa=12, home_runs=2, avg=0.300)

    def get_pitching_stats(self, player_id: int):
        season = PitchingStatsSnapshot(
            innings_pitched=50.0, k_per_9=10.0, bb_per_9=2.5, hr_per_9=1.0
        )
        recent = PitchingStatsSnapshot(
            innings_pitched=20.0, k_per_9=11.0, bb_per_9=2.2, hr_per_9=0.9
        )
        return season, recent


def _sample_bundle() -> PlayerFeatureBundle:
    dist = StatcastDistributionProfile(
        launch_angle_mean=14.0,
        launch_angle_std=16.0,
        exit_velocity_mean=91.0,
        exit_velocity_std=9.0,
        max_exit_velocity=110.0,
        sweet_spot_rate=0.36,
        barrel_rate=0.10,
        hard_hit_rate=0.42,
        sample_bip=80,
    )
    statcast = StatcastProfile(
        player_id=1,
        player_name="Test Hitter",
        sample_pa=200,
        xwoba=0.340,
        xslg=0.480,
        barrel_rate=0.10,
        hard_hit_rate=0.42,
        contact_rate=0.78,
        whiff_rate=0.22,
        distribution=dist,
    )
    hitter = HitterGameContext(
        player=PlayerIdentity(mlb_id=1, name="Test Hitter", team="NYY", bats="L"),
        game=GameContext(
            game_pk=1,
            game_date="2026-07-01",
            venue="Yankee Stadium",
            is_home=True,
            opponent="BOS",
            lineup_status="confirmed",
        ),
        lineup_slot=3,
        opposing_pitcher_id=99,
        opposing_pitcher_name="Ace Pitcher",
        opposing_pitcher_throws="R",
    )
    pitcher = PitcherStatcastProfile(
        player_id=99,
        player_name="Ace Pitcher",
        k_rate=0.28,
        bb_rate=0.07,
        hr_per_9=1.2,
        sample_pa=500,
    )
    return PlayerFeatureBundle(
        hitter=hitter,
        statcast=statcast,
        park=ParkFactors(venue="Yankee Stadium", hits_factor=0.98, hr_factor=1.15),
        weather=WeatherContext(venue="Yankee Stadium", game_date="2026-07-01"),
        matchup=MatchupContext(
            platoon_advantage=0.25,
            bvp_pa=12,
            bvp_ops_factor=1.08,
            bvp_hr_factor=1.05,
            recent_form_multiplier=1.04,
        ),
        pitcher_statcast=pitcher,
        expected_pa=4.05,
        metadata={"weather_hr_factor": 1.02},
    )


def test_feature_vector_produces_hundreds_of_features():
    league = LeagueBaselines()
    builder = FeatureVectorBuilder(league_baselines=league)
    bundle = _sample_bundle()
    season = HittingStatsSnapshot(obp=0.34, slg=0.44, pa=400)
    recent = HittingStatsSnapshot(obp=0.36, slg=0.48, pa=60)
    fv = builder.build(bundle, season_hitting=season, recent_hitting=recent)
    assert fv.count() >= 120
    assert "statcast_z" in fv.groups
    assert "interactions" in fv.groups
    assert "pitcher_hitter_crosses" in fv.groups


def test_matchup_intelligence_populates_context():
    league = LeagueBaselines()
    mi = MatchupIntelligence(
        league_baselines=league,
        data_provider=MockMatchupAPI(),
    )
    bundle = _sample_bundle()
    enriched = mi.apply_to_bundle(bundle)
    assert enriched.matchup.recent_form_multiplier != 1.0
    assert enriched.matchup.bvp_pa == 12
    assert enriched.matchup.bvp_ops_factor > 1.0
    assert "matchup_bvp_pa" in enriched.metadata


def test_probability_engine_outcomes_sum_to_one():
    league = LeagueBaselines()
    pa_sim = HybridPASimulator(
        config=PASimulatorConfig.from_league(league),
        league_baselines=league,
    )
    engine = ProbabilityEngine(pa_simulator=pa_sim, league_baselines=league)
    bundle = _sample_bundle()
    probs = engine.from_bundle(bundle)
    assert probs.is_valid(tolerance=0.02)
    safeguards = OutputSafeguards(league_baselines=league)
    assert safeguards.check_outcome_probabilities(probs).passed
    assert probs.home_run <= safeguards.limits.max_hr_prob_pa


def test_probability_engine_uses_same_context_bounds_as_monte_carlo_input():
    """The archived PA probabilities and sampled distribution must agree."""
    bundle = _sample_bundle()
    bundle.matchup.bvp_ops_factor = 4.0
    bundle.matchup.bvp_hr_factor = 3.0
    bundle.matchup.recent_form_multiplier = 2.0
    league = LeagueBaselines()
    pa_sim = HybridPASimulator(
        config=PASimulatorConfig.from_league(league), league_baselines=league
    )
    explicit = ProbabilityEngine(pa_simulator=pa_sim, league_baselines=league).from_bundle(bundle)
    direct = pa_sim.expected_outcome_probabilities(
        pitcher_k_pct=bundle.pitcher_statcast.k_rate * 100.0,
        pitcher_bb_pct=bundle.pitcher_statcast.bb_rate * 100.0,
        pitcher_hr_per_9=bundle.pitcher_statcast.hr_per_9,
        park_hr_factor=bundle.park.hr_factor * bundle.metadata["weather_hr_factor"],
        park_hits_factor=bundle.park.hits_factor,
        handedness_advantage=bundle.matchup.platoon_advantage,
        recent_form_mult=1.18,
        bvp_ops_factor=1.25,
        bvp_hr_factor=1.40,
        statcast=bundle.statcast,
        rich_features={},
    )
    for key, value in direct.items():
        assert getattr(explicit, key) == value


def test_league_recentering_preserves_configured_pa_model():
    """A live baseline refresh must not silently revert the fitted PA engine."""
    engine = PropEngine(
        config={
            "simulation": {"n_sims": 100},
            "pa_simulator": {
                "hit_prob_cap": 0.271,
                "hr_quality": 0.123,
            },
        }
    )
    assert engine.pa_config.hit_prob_cap == 0.271
    assert engine.pa_config.hr_quality == 0.123

    refreshed = LeagueBaselines(xwoba_on_contact=0.391, xslg_on_contact=0.651)
    engine.configure_simulation(league_baselines=refreshed)

    assert engine.pa_config.hit_prob_cap == 0.271
    assert engine.pa_config.hr_quality == 0.123
    assert engine.probability_engine.league is refreshed


def test_legacy_hr9_direction_is_explicitly_flagged_in_projection():
    bundle = _sample_bundle()
    frozen = PropEngine(config={"simulation": {"n_sims": 100}})
    candidate = PropEngine(
        config={
            "simulation": {"n_sims": 100},
            "pa_simulator": {"correct_pitcher_hr9_direction": True},
        }
    )

    frozen_projection = frozen.project_hitter(bundle, categories=("home_runs",))[0]
    candidate_projection = candidate.project_hitter(bundle, categories=("home_runs",))[0]

    assert "opposing_pitcher_hr9_direction_unqualified" in frozen_projection.input_health_flags
    assert "opposing_pitcher_hr9_direction_unqualified" not in candidate_projection.input_health_flags


def test_expected_outcome_probs_match_league_hit_rate():
    league = LeagueBaselines()
    pa_sim = HybridPASimulator(
        config=PASimulatorConfig.from_league(league),
        league_baselines=league,
        random_seed=42,
    )
    probs = pa_sim.expected_outcome_probabilities()
    hit_per_pa = probs["single"] + probs["double"] + probs["triple"] + probs["home_run"]
    expected_hits_per_game = hit_per_pa * league.pa_per_game
    assert 0.7 <= expected_hits_per_game <= 1.5


def test_output_safeguards_catch_inflated_hits():
    league = LeagueBaselines()
    safeguards = OutputSafeguards(league_baselines=league)
    from src.models.dataclasses import PropProjection

    inflated = PropProjection(
        player_id=1,
        player_name="Test",
        category="hits",
        game_date="2026-07-01",
        projected_value=2.8,
        confidence=0.5,
    )
    report = safeguards.check_projection(inflated)
    assert not report.passed
    assert any("hits projection" in v for v in report.violations)


class MockStatcastEngine:
    league = LeagueBaselines()

    def build_profiles_for_hitters(self, hitters, game_date=None, savant_csv_path=None):
        return {
            h.player.mlb_id: StatcastProfile(
                player_id=h.player.mlb_id,
                player_name=h.player.name,
                sample_pa=200,
                xwoba=0.340,
                xslg=0.480,
                barrel_rate=0.10,
                hard_hit_rate=0.42,
                contact_rate=0.78,
                distribution=StatcastDistributionProfile(sample_bip=80),
            )
            for h in hitters
        }


def test_feature_factory_builds_enriched_bundles():
    league = LeagueBaselines()
    factory = FeatureFactory(
        league_baselines=league,
        mlb_api=MockMatchupAPI(),
        statcast_engine=MockStatcastEngine(),
    )
    bundle = _sample_bundle()
    bundles = factory.build_bundles(
        "2026-07-01",
        hitters=[bundle.hitter],
    )
    assert len(bundles) == 1
    assert bundles[0].features is not None
    assert bundles[0].features.count() >= 120
    assert bundles[0].metadata.get("feature_count", 0) >= 120
