"""Tests for lineup certainty scoring and bundle adjustments."""

from __future__ import annotations

from src.features.lineup_intelligence import LineupIntelligence
from src.models.dataclasses import (
    GameContext,
    HitterGameContext,
    LeagueBaselines,
    PlayerFeatureBundle,
    PlayerIdentity,
    StatcastProfile,
    ParkFactors,
    WeatherContext,
    MatchupContext,
)


def _sample_bundle(status: str) -> PlayerFeatureBundle:
    hitter = HitterGameContext(
        player=PlayerIdentity(mlb_id=1, name="Test", team="NYY", bats="R"),
        game=GameContext(
            game_pk=1,
            game_date="2026-07-01",
            venue="Yankee Stadium",
            is_home=True,
            opponent="BOS",
            lineup_status=status,  # type: ignore[arg-type]
        ),
        lineup_slot=1,
        opposing_pitcher_name="Ace",
    )
    return PlayerFeatureBundle(
        hitter=hitter,
        statcast=StatcastProfile(player_id=1, player_name="Test", sample_pa=100, xwoba=0.330),
        park=ParkFactors(venue="Yankee Stadium"),
        weather=WeatherContext(venue="Yankee Stadium", game_date="2026-07-01"),
        matchup=MatchupContext(),
    )


def test_confirmed_has_higher_certainty_than_projected():
    intel = LineupIntelligence.from_config(
        {
            "lineup_intelligence": {},
            "simulation_slot_pa": {"1": 1.117},
        },
        league_baselines=LeagueBaselines(pa_per_game=4.05),
    )
    confirmed = intel.evaluate(_sample_bundle("confirmed").hitter)
    projected = intel.evaluate(_sample_bundle("projected").hitter)
    assert confirmed.certainty_score > projected.certainty_score
    assert confirmed.status == "confirmed"
    assert projected.status == "projected"


def test_apply_to_bundle_adjusts_expected_pa_and_metadata():
    intel = LineupIntelligence.from_config(
        {
            "lineup_intelligence": {
                "expected_pa_scale": {"confirmed": 1.0, "projected": 0.94, "unknown": 0.88},
                "use_simulation_slot_pa": True,
            },
            "simulation_slot_pa": {str(i): 1.0 for i in range(1, 10)},
        },
        league_baselines=LeagueBaselines(pa_per_game=4.0),
    )
    bundle = intel.apply_to_bundle(_sample_bundle("projected"))
    assert bundle.expected_pa == 3.76
    assert bundle.metadata["lineup_status"] == "projected"
    assert bundle.metadata["lineup_confidence_multiplier"] == 0.88
    assert bundle.metadata["lineup_simulation_n_sims_scale"] == 0.9


def test_leadoff_slot_gets_higher_expected_pa_than_nine_hole():
    intel = LineupIntelligence.from_config(
        {
            "lineup_intelligence": {"use_slot_pa_factors": True, "use_simulation_slot_pa": True},
            "simulation_slot_pa": {"1": 1.12, "9": 0.95},
        },
        league_baselines=LeagueBaselines(pa_per_game=4.0),
    )
    leadoff = _sample_bundle("confirmed")
    nine_hole = _sample_bundle("confirmed")
    nine_hole.hitter.lineup_slot = 9

    leadoff_adj = intel.build_adjustment(leadoff.hitter)
    nine_adj = intel.build_adjustment(nine_hole.hitter)
    assert leadoff_adj.expected_pa > nine_adj.expected_pa