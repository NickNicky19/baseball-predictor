from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import src.data.savant as savant_module
from src.data.savant import HITTER_LEAGUE_FALLBACK_FIELDS, SavantClient
from src.data.statcast_source_contract import (
    StatcastSourceSchemaError,
    StatcastSourceUnavailableError,
    validate_statcast_source_lineage,
)
from src.evaluation.prediction_health import health_for_bundle
from src.features.feature_store import (
    bundle_from_dict,
    bundle_to_dict,
    bundles_to_dataframe,
    dataframe_to_bundles,
)
from src.features.legacy_statcast_features import StatcastFeatureEngine
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


def _pitch_rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "batter": [7, 7],
            "player_name": ["Observed Hitter", "Observed Hitter"],
            "events": ["single", "field_out"],
            "game_date": ["2026-04-29", "2026-04-29"],
            "type": ["X", "X"],
            "launch_speed": [98.0, 88.0],
            "launch_angle": [18.0, 8.0],
            "launch_speed_angle": [6, 2],
            "estimated_woba_using_speedangle": [0.5, 0.1],
            "estimated_ba_using_speedangle": [0.4, 0.1],
            "estimated_slg_using_speedangle": [0.8, 0.1],
            "description": ["hit_into_play", "hit_into_play"],
            "zone": [5, 6],
        }
    )


def _multi_pitch_pa() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "batter": [7, 7, 7],
            # On Statcast pitch rows this is the pitcher, not the batter.
            "player_name": ["Opposing Pitcher"] * 3,
            "events": [pd.NA, pd.NA, "single"],
            "game_date": ["2026-04-29"] * 3,
            "type": ["S", "S", "X"],
            "launch_speed": [pd.NA, pd.NA, 98.0],
            "launch_angle": [pd.NA, pd.NA, 18.0],
            "launch_speed_angle": [pd.NA, pd.NA, 6],
            "estimated_woba_using_speedangle": [pd.NA, pd.NA, 0.5],
            "estimated_ba_using_speedangle": [pd.NA, pd.NA, 0.4],
            "estimated_slg_using_speedangle": [pd.NA, pd.NA, 0.8],
            "description": ["called_strike", "swinging_strike", "hit_into_play"],
            "zone": [5, 11, 6],
        }
    )


def _bundle(profile: StatcastProfile) -> PlayerFeatureBundle:
    game = GameContext(
        game_pk=99,
        game_date="2026-04-30",
        venue="Test Park",
        is_home=True,
        opponent="OPP",
        lineup_status="confirmed",
    )
    hitter = HitterGameContext(
        player=PlayerIdentity(mlb_id=profile.player_id, name=profile.player_name, team="TST"),
        game=game,
        lineup_slot=1,
    )
    return PlayerFeatureBundle(
        hitter=hitter,
        statcast=profile,
        park=ParkFactors(venue=game.venue),
        weather=WeatherContext(venue=game.venue, game_date=game.game_date),
        matchup=MatchupContext(),
    )


def test_dependency_absence_fails_instead_of_returning_empty_frame(monkeypatch) -> None:
    monkeypatch.setattr(savant_module, "pyb", None)
    with pytest.raises(StatcastSourceUnavailableError, match="pybaseball"):
        SavantClient().fetch_statcast_range(end_date="2026-04-29")


def test_provider_exception_mutation_cannot_become_league_fallback(monkeypatch) -> None:
    def fail(**_kwargs):
        raise OSError("mutated provider outage")

    monkeypatch.setattr(savant_module, "pyb", SimpleNamespace(statcast=fail))
    with pytest.raises(StatcastSourceUnavailableError, match="fetch failed"):
        SavantClient().fetch_statcast_range(end_date="2026-04-29")


@pytest.mark.parametrize("response", [None, pd.DataFrame()])
def test_empty_provider_response_fails_closed(monkeypatch, response) -> None:
    monkeypatch.setattr(
        savant_module,
        "pyb",
        SimpleNamespace(statcast=lambda **_kwargs: response),
    )
    with pytest.raises(StatcastSourceUnavailableError, match="returned no rows"):
        SavantClient().fetch_statcast_range(end_date="2026-04-29")


def test_nonempty_pitch_payload_without_terminal_event_schema_fails() -> None:
    mutated = _pitch_rows().drop(columns=["events"])
    with pytest.raises(StatcastSourceSchemaError, match="events"):
        SavantClient(min_pa=1).build_hitter_profiles_from_statcast(mutated)


def test_nonempty_pitch_payload_with_no_terminal_events_fails() -> None:
    mutated = _pitch_rows()
    mutated["events"] = pd.NA
    with pytest.raises(StatcastSourceSchemaError, match="no terminal batter events"):
        SavantClient(min_pa=1).build_hitter_profiles_from_statcast(mutated)


def test_pitch_rates_use_all_pitches_while_sample_pa_uses_terminal_events() -> None:
    profile = SavantClient(min_pa=1).build_hitter_profiles_from_statcast(
        _multi_pitch_pa()
    )[7]

    assert profile.sample_pa == 1
    assert profile.source_row_count == 3
    assert profile.contact_rate == pytest.approx(0.5)
    assert profile.whiff_rate == pytest.approx(0.5)
    assert profile.swing_rate == pytest.approx(2 / 3)
    assert profile.chase_rate == pytest.approx(1.0)
    assert profile.zone_rate == pytest.approx(2 / 3)


def test_mutation_nonterminal_pitch_changes_pitch_rates_not_pa_count() -> None:
    source = _multi_pitch_pa()
    original = SavantClient(min_pa=1).build_hitter_profiles_from_statcast(source)[7]
    extra = source.iloc[[0]].copy()
    extra["description"] = "called_strike"
    extra["events"] = pd.NA
    mutated = SavantClient(min_pa=1).build_hitter_profiles_from_statcast(
        pd.concat([source, extra], ignore_index=True)
    )[7]

    assert mutated.sample_pa == original.sample_pa == 1
    assert mutated.source_row_count == original.source_row_count + 1
    assert mutated.swing_rate == pytest.approx(0.5)
    assert mutated.swing_rate != original.swing_rate


def test_pitch_level_pitcher_name_cannot_enter_batter_profile() -> None:
    profile = SavantClient(min_pa=1).build_hitter_profiles_from_statcast(
        _multi_pitch_pa()
    )[7]
    assert profile.player_name == ""


def test_missing_and_empty_csvs_fail_closed(tmp_path) -> None:
    client = SavantClient()
    with pytest.raises(StatcastSourceUnavailableError, match="not found"):
        client.load_savant_csv(tmp_path / "missing.csv")
    empty = tmp_path / "empty.csv"
    empty.write_text("batter,events\n", encoding="utf-8")
    with pytest.raises(StatcastSourceUnavailableError, match="contains no rows"):
        client.load_savant_csv(empty)


def test_observed_profile_records_window_rows_and_exact_fallbacks() -> None:
    engine = StatcastFeatureEngine(min_pa=1)
    engine.savant.fetch_statcast_range = lambda **_kwargs: _pitch_rows()  # type: ignore[method-assign]
    profile = engine.build_profiles_for_date("2026-04-30")[7]

    assert profile.source_kind == "pybaseball_statcast"
    assert profile.source_status == "observed_with_field_fallback"
    assert profile.source_window_end == "2026-04-29"
    assert profile.source_row_count == 2
    assert profile.fallback_fields == tuple(sorted(profile.fallback_fields))
    assert "sweet_spot_rate" in profile.fallback_fields
    validate_statcast_source_lineage(profile, context="test observed")


def test_real_missing_player_is_explicit_not_source_outage() -> None:
    profile = SavantClient().league_fallback_profile(88, "No Prior Sample")
    assert profile.source_kind == "league_baseline"
    assert profile.source_status == "league_fallback_no_player_profile"
    assert profile.sample_pa == 0
    assert profile.fallback_fields == HITTER_LEAGUE_FALLBACK_FIELDS

    health = health_for_bundle(_bundle(profile))
    assert health.hitter_statcast_source_status == profile.source_status
    assert health.hitter_statcast_fallback_fields == profile.fallback_fields
    assert "hitter_statcast_player_history_fallback" in health.flags


def test_mutation_complete_status_with_fallback_fields_fails_lineage() -> None:
    mutated = StatcastProfile(
        player_id=7,
        player_name="Mutated",
        sample_pa=2,
        source_kind="pybaseball_statcast",
        source_status="observed_complete",
        source_row_count=2,
        fallback_fields=("xwoba",),
    )
    with pytest.raises(StatcastSourceSchemaError, match="observed_complete"):
        validate_statcast_source_lineage(mutated, context="mutation")


def test_source_lineage_survives_json_and_dataframe_round_trips() -> None:
    profile = SavantClient().league_fallback_profile(88, "No Prior Sample")
    bundle = _bundle(profile)

    from_json = bundle_from_dict(bundle_to_dict(bundle))
    assert from_json.statcast.fallback_fields == profile.fallback_fields
    assert from_json.statcast.source_status == profile.source_status

    frame = bundles_to_dataframe([bundle])
    assert frame.loc[0, "statcast_source_status"] == profile.source_status
    from_frame = dataframe_to_bundles(frame)[0]
    assert from_frame.statcast.source_kind == profile.source_kind
    assert from_frame.statcast.fallback_fields == profile.fallback_fields


def test_mutation_serialization_rejects_contradictory_lineage() -> None:
    profile = StatcastProfile(
        player_id=7,
        player_name="Mutated",
        sample_pa=2,
        xwoba=0.3,
        barrel_rate=0.1,
        hard_hit_rate=0.4,
        source_kind="pybaseball_statcast",
        source_status="observed_complete",
        source_row_count=2,
        fallback_fields=("xwoba",),
    )
    with pytest.raises(StatcastSourceSchemaError, match="observed_complete"):
        bundle_to_dict(_bundle(profile))


def test_mutation_probability_consumer_rejects_contradictory_lineage() -> None:
    profile = StatcastProfile(
        player_id=7,
        player_name="Mutated",
        sample_pa=2,
        xwoba=0.3,
        barrel_rate=0.1,
        hard_hit_rate=0.4,
        source_kind="pybaseball_statcast",
        source_status="observed_complete",
        source_row_count=2,
        fallback_fields=("xwoba",),
    )
    with pytest.raises(StatcastSourceSchemaError, match="observed_complete"):
        HybridPASimulator().expected_outcome_probabilities(statcast=profile)


def test_valid_source_lineage_is_numerically_inert() -> None:
    numeric = dict(
        player_id=7,
        player_name="Valid",
        sample_pa=100,
        xwoba=0.36,
        xba=0.31,
        xslg=0.58,
        barrel_rate=0.09,
        hard_hit_rate=0.40,
        contact_rate=0.76,
        k_rate=0.21,
        bb_rate=0.08,
    )
    legacy = StatcastProfile(**numeric)
    source_bound = StatcastProfile(
        **numeric,
        source_kind="pybaseball_statcast",
        source_status="observed_complete",
        source_window_end="2026-04-29",
        source_row_count=100,
    )
    sim = HybridPASimulator()
    assert sim.expected_outcome_probabilities(
        statcast=source_bound
    ) == sim.expected_outcome_probabilities(statcast=legacy)
