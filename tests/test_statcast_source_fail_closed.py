from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import src.data.savant as savant_module
from src.data.savant import SavantClient
from src.features.legacy_statcast_features import StatcastFeatureEngine
from src.features.feature_store import bundle_from_dict, bundle_to_dict
from src.data.statcast_integrity import StatcastIntegrityError, validate_profile_and_rich_features
from src.evaluation.prediction_health import health_for_bundle
from src.models.dataclasses import GameContext, HitterGameContext, PlayerIdentity
from src.models.dataclasses import MatchupContext, ParkFactors, PlayerFeatureBundle, WeatherContext
from src.utils.errors import DataFetchError


def _valid_source() -> pd.DataFrame:
    return pd.DataFrame({
        "batter": [123],
        "game_date": ["2026-07-20"],
        "events": ["single"],
        "player_name": ["Source Hitter"],
        "type": ["X"],
        "description": ["hit_into_play"],
        "zone": [5],
        "launch_speed": [99.0],
        "launch_angle": [18.0],
        "launch_speed_angle": [5],
        "estimated_woba_using_speedangle": [0.540],
        "estimated_ba_using_speedangle": [0.610],
        "estimated_slg_using_speedangle": [0.920],
    })


def _hitter(player_id: int = 123) -> HitterGameContext:
    return HitterGameContext(
        PlayerIdentity(player_id, "Canonical Hitter", "HOME"),
        GameContext(900001, "2026-07-22", "Park", True, "AWAY", "unknown"),
        lineup_slot=1,
    )


def test_missing_pybaseball_is_source_failure_not_empty_player_pool(monkeypatch) -> None:
    monkeypatch.setattr(savant_module, "pyb", None)
    with pytest.raises(DataFetchError, match="not installed"):
        SavantClient().fetch_statcast_range(end_date="2026-07-20")


def test_transport_failure_is_not_converted_to_empty_frame(monkeypatch) -> None:
    def fail(**_kwargs):
        raise TimeoutError("old silent failure")

    monkeypatch.setattr(savant_module, "pyb", SimpleNamespace(statcast=fail))
    with pytest.raises(DataFetchError, match="source request failed") as caught:
        SavantClient().fetch_statcast_range(end_date="2026-07-20")
    assert isinstance(caught.value.__cause__, TimeoutError)


@pytest.mark.parametrize(
    "payload, message",
    [
        (pd.DataFrame(), "returned no rows"),
        (pd.DataFrame({"game_date": ["2026-07-20"]}), "identity fields"),
        (
            pd.DataFrame({"batter": [123], "game_date": ["2026-07-20"]}),
            "required fields",
        ),
        ({"batter": [123]}, "non-tabular"),
    ],
)
def test_malformed_or_empty_source_fails_closed(monkeypatch, payload, message) -> None:
    monkeypatch.setattr(
        savant_module,
        "pyb",
        SimpleNamespace(statcast=lambda **_kwargs: payload),
    )
    with pytest.raises(DataFetchError, match=message):
        SavantClient().fetch_statcast_range(end_date="2026-07-20")


def test_sealed_may_range_is_rejected_before_source_call(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        savant_module,
        "pyb",
        SimpleNamespace(statcast=lambda **kwargs: calls.append(kwargs) or _valid_source()),
    )
    with pytest.raises(DataFetchError, match="sealed May 2026"):
        SavantClient(lookback_days=45).fetch_statcast_range(end_date="2026-06-01")
    assert calls == []


def test_sealed_may_boundary_precedes_dependency_check(monkeypatch) -> None:
    monkeypatch.setattr(savant_module, "pyb", None)
    with pytest.raises(DataFetchError, match="sealed May 2026"):
        SavantClient(lookback_days=45).fetch_statcast_range(end_date="2026-06-01")


def test_feature_engine_propagates_source_failure_instead_of_league_fallback() -> None:
    engine = StatcastFeatureEngine(min_pa=1)

    def fail(**_kwargs):
        raise DataFetchError("source unavailable")

    engine.savant.fetch_statcast_range = fail  # type: ignore[method-assign]
    with pytest.raises(DataFetchError, match="source unavailable"):
        engine.build_profiles_for_hitters([_hitter()], game_date="2026-07-22")


def test_missing_player_after_valid_source_remains_explicit_player_fallback() -> None:
    engine = StatcastFeatureEngine(min_pa=1)
    engine.savant.fetch_statcast_range = (  # type: ignore[method-assign]
        lambda **_kwargs: _valid_source()
    )
    profiles = engine.build_profiles_for_hitters([_hitter(999)], game_date="2026-07-22")
    fallback = profiles[999]
    assert fallback.sample_pa == 0
    assert fallback.has_advanced_data() is False
    assert fallback.player_id == 999
    assert fallback.source_status == "league_fallback"
    assert "barrel_rate" in fallback.fallback_fields


def test_missing_or_empty_csv_override_is_not_silently_ignored(tmp_path) -> None:
    client = SavantClient()
    with pytest.raises(DataFetchError, match="does not exist"):
        client.load_savant_csv(tmp_path / "missing.csv")
    empty = tmp_path / "empty.csv"
    empty.write_text("batter,game_date\n", encoding="utf-8")
    with pytest.raises(DataFetchError, match="empty"):
        client.load_savant_csv(empty)


def test_empty_or_identity_free_profile_source_is_rejected() -> None:
    client = SavantClient(min_pa=1)
    with pytest.raises(DataFetchError, match="empty Statcast"):
        client.build_hitter_profiles_from_statcast(pd.DataFrame())
    with pytest.raises(DataFetchError, match="batter identity"):
        client.build_hitter_profiles_from_statcast(pd.DataFrame({"events": ["single"]}))


def test_may_target_and_2026_csv_override_fail_before_fetch(tmp_path) -> None:
    engine = StatcastFeatureEngine(min_pa=1)
    calls: list[str] = []
    engine.savant.fetch_statcast_range = (  # type: ignore[method-assign]
        lambda **kwargs: calls.append(str(kwargs)) or _valid_source()
    )
    with pytest.raises(ValueError, match="sealed"):
        engine.build_profiles_for_date("2026-05-15")
    with pytest.raises(ValueError, match="CSV overrides are forbidden"):
        engine.build_profiles_for_date("2026-07-22", str(tmp_path / "unused.csv"))
    assert calls == []


def test_partial_fallback_lineage_survives_serialization_and_health_boundary() -> None:
    client = SavantClient(min_pa=1)
    profile = client.build_hitter_profiles_from_statcast(_valid_source())[123]
    assert profile.source_status == "partial_league_fallback"
    assert "sweet_spot_rate" in profile.fallback_fields
    bundle = PlayerFeatureBundle(
        hitter=_hitter(),
        statcast=profile,
        park=ParkFactors("Park"),
        weather=WeatherContext("Park", "2026-07-22"),
        matchup=MatchupContext(),
    )
    restored = bundle_from_dict(bundle_to_dict(bundle))
    assert restored.statcast.fallback_fields == profile.fallback_fields
    health = health_for_bundle(restored)
    assert health.hitter_statcast_source_status == "partial_league_fallback"
    assert "hitter_statcast_fallback_sweet_spot_rate" in health.flags


def test_source_status_mutations_fail_closed_at_probability_input_validation() -> None:
    profile = SavantClient().league_fallback_profile(123, "Fallback")
    validate_profile_and_rich_features(profile, {}, context="valid")
    profile.source_status = "observed"
    with pytest.raises(StatcastIntegrityError, match="observed profile"):
        validate_profile_and_rich_features(profile, {}, context="mutated")
    profile.source_status = "league_fallback"
    profile.fallback_fields = profile.fallback_fields + ("invented_metric",)
    with pytest.raises(StatcastIntegrityError, match="unknown fallback fields"):
        validate_profile_and_rich_features(profile, {}, context="mutated")
