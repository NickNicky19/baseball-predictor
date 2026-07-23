from __future__ import annotations

from copy import deepcopy

import pytest

from src.data.statcast_integrity import StatcastIntegrityError
from src.data.rolling_source_contract import RollingSourceSchemaError
from src.features.feature_store import bundle_to_dict
from src.features.rich_feature_enricher import RichFeatureEnricher
from src.models.dataclasses import (
    GameContext,
    HitterGameContext,
    MatchupContext,
    ParkFactors,
    PlayerFeatureBundle,
    PlayerIdentity,
    StatcastProfile,
    WeatherContext,
)
from src.simulation.pa_simulator import HybridPASimulator


def _profile() -> StatcastProfile:
    return StatcastProfile(
        player_id=7,
        player_name="Source Bound",
        sample_pa=100,
        xwoba=0.36,
        xba=0.31,
        xslg=0.58,
        barrel_rate=0.10,
        hard_hit_rate=0.40,
        batted_ball_denominator=10,
        barrel_count=1,
        hard_hit_count=4,
        batted_ball_rate_definition="fixture_shared_measured_ev",
        contact_rate=0.76,
        k_rate=0.21,
        bb_rate=0.08,
        source_kind="pybaseball_statcast",
        source_status="observed_complete",
        source_window_end="2026-04-29",
        source_row_count=100,
    )


def _rich(profile: StatcastProfile) -> dict:
    return RichFeatureEnricher().enrich(
        data={"game_date": "2026-04-30", "lineup_slot": 1},
        profile=profile,
    )


def _bundle(profile: StatcastProfile, rich: dict) -> PlayerFeatureBundle:
    game = GameContext(
        game_pk=99,
        game_date="2026-04-30",
        venue="Test Park",
        is_home=True,
        opponent="OPP",
        lineup_status="confirmed",
    )
    return PlayerFeatureBundle(
        hitter=HitterGameContext(
            player=PlayerIdentity(mlb_id=7, name=profile.player_name, team="TST"),
            game=game,
            lineup_slot=1,
        ),
        statcast=profile,
        park=ParkFactors(venue=game.venue),
        weather=WeatherContext(venue=game.venue, game_date=game.game_date),
        matchup=MatchupContext(),
        metadata={"rich_features": rich},
    )


def test_rich_pass_through_persists_exact_statcast_lineage() -> None:
    profile = _profile()
    rich = _rich(profile)
    barrel = rich["_statcast_lineage"]["barrel_rate"]
    assert barrel["source_kind"] == profile.source_kind
    assert barrel["source_window_end"] == profile.source_window_end
    assert barrel["batted_ball_denominator"] == 10
    assert barrel["barrel_count"] == 1
    assert barrel["hard_hit_count"] == 4


def test_mutation_rolling_provider_cannot_replace_source_bound_field() -> None:
    with pytest.raises(StatcastIntegrityError, match="cannot replace"):
        RichFeatureEnricher().enrich(
            data={"game_date": "2026-04-30"},
            profile=_profile(),
            rolling={"barrel_rate": 0.25},
        )


def test_mutation_plausible_xwoba_override_fails_at_probability_consumer() -> None:
    profile = _profile()
    rich = _rich(profile)
    rich["xwoba"] = 0.37
    with pytest.raises(StatcastIntegrityError, match="unauthorized xwoba override"):
        HybridPASimulator().expected_outcome_probabilities(
            statcast=profile,
            rich_features=rich,
        )


def test_mutation_deleted_lineage_fails_feature_serialization() -> None:
    profile = _profile()
    rich = _rich(profile)
    del rich["_statcast_lineage"]
    with pytest.raises(StatcastIntegrityError, match="missing lineage"):
        bundle_to_dict(_bundle(profile, rich))


def test_mutation_count_lineage_mismatch_fails_closed() -> None:
    profile = _profile()
    rich = _rich(profile)
    rich["_statcast_lineage"]["hard_hit_rate"]["hard_hit_count"] = 5
    with pytest.raises(StatcastIntegrityError, match="count lineage mismatch"):
        HybridPASimulator().expected_outcome_probabilities(
            statcast=profile,
            rich_features=rich,
        )


def test_truthful_rich_lineage_is_numerically_inert() -> None:
    profile = _profile()
    rich = _rich(profile)
    sim = HybridPASimulator()
    assert sim.expected_outcome_probabilities(
        statcast=profile,
        rich_features=rich,
    ) == sim.expected_outcome_probabilities(statcast=profile)


def test_mutation_lineage_source_status_mismatch_fails() -> None:
    profile = _profile()
    rich = deepcopy(_rich(profile))
    rich["_statcast_lineage"]["xslg"]["source_status"] = "observed_with_field_fallback"
    with pytest.raises(StatcastIntegrityError, match="lineage mismatch"):
        HybridPASimulator().expected_outcome_probabilities(
            statcast=profile,
            rich_features=rich,
        )


def test_mutation_nonempty_rolling_features_require_lineage() -> None:
    with pytest.raises(StatcastIntegrityError, match="require explicit source lineage"):
        RichFeatureEnricher().enrich(
            data={"game_date": "2026-04-30"},
            profile=_profile(),
            rolling={"recent_pa_15": 20, "roll15_k_rate": 0.20},
        )


def test_mutation_rolling_lineage_is_rechecked_at_serialization() -> None:
    profile = _profile()
    rolling = {
        "recent_pa_15": 20,
        "roll15_k_rate": 0.20,
        "rolling_source_kind": "mlb_statsapi_hitting_game_log",
        "rolling_source_status": "observed_strict_prior",
        "rolling_source_target_date": "2026-04-30",
        "rolling_source_max_game_date": "2026-04-29",
        "rolling_source_row_count": 20,
        "rolling_source_content_sha256": "a" * 64,
    }
    rich = RichFeatureEnricher().enrich(
        data={"game_date": "2026-04-30"},
        profile=profile,
        rolling=rolling,
    )
    rich["_rolling_lineage"]["rolling_source_max_game_date"] = "2026-04-30"
    with pytest.raises(RollingSourceSchemaError, match="not strictly prior"):
        bundle_to_dict(_bundle(profile, rich))
