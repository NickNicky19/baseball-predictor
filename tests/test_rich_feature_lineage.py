from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pandas as pd
import pytest

from src.data.savant import SavantClient
from src.data.statcast_integrity import (
    RICH_FEATURE_LINEAGE_KEY,
    StatcastIntegrityError,
    sha256_feature_value,
    validate_profile_and_rich_features,
)
from src.features.rich_feature_enricher import RichFeatureEnricher
from src.features.feature_store import bundle_from_dict, bundle_to_dict
from src.evaluation.prediction_health import health_for_bundle
from src.models.dataclasses import (
    GameContext,
    HitterGameContext,
    MatchupContext,
    ParkFactors,
    PlayerFeatureBundle,
    PlayerIdentity,
    WeatherContext,
)
from src.simulation.pa_simulator import HybridPASimulator


def _source(rows: int = 2) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "batter": [123] * rows,
            "game_date": ["2026-07-19", "2026-07-20"][:rows],
            "events": ["single", "field_out"][:rows],
            "player_name": ["Pitcher Label"] * rows,
            "type": ["X"] * rows,
            "description": ["hit_into_play"] * rows,
            "zone": [5, 6][:rows],
            "launch_speed": [99.0, 88.0][:rows],
            "launch_angle": [18.0, 4.0][:rows],
            "launch_speed_angle": [5, 1][:rows],
            "estimated_woba_using_speedangle": [0.540, 0.120][:rows],
            "estimated_ba_using_speedangle": [0.610, 0.090][:rows],
            "estimated_slg_using_speedangle": [0.920, 0.130][:rows],
        }
    )
    return frame


def _profile():
    return SavantClient(min_pa=1).build_hitter_profiles_from_statcast(_source())[123]


def _rich(profile=None):
    profile = profile or _profile()
    return RichFeatureEnricher().enrich(
        {"game_date": "2026-07-22"},
        profile,
    )


def test_profile_source_hash_is_order_invariant_and_value_sensitive() -> None:
    original = _profile()
    reordered = SavantClient(min_pa=1).build_hitter_profiles_from_statcast(
        _source().iloc[::-1]
    )[123]
    assert reordered.source_hash == original.source_hash
    mutated = _source()
    mutated.loc[0, "estimated_woba_using_speedangle"] = 0.541
    changed = SavantClient(min_pa=1).build_hitter_profiles_from_statcast(mutated)[123]
    assert changed.source_hash != original.source_hash


def test_probability_overrides_carry_identity_cutoff_hash_and_denominator() -> None:
    profile = _profile()
    rich = _rich(profile)
    validate_profile_and_rich_features(profile, rich, context="valid")
    fields = rich[RICH_FEATURE_LINEAGE_KEY]["fields"]
    assert fields["xwoba"]["player_id"] == profile.player_id
    assert fields["xwoba"]["source_hash"] == profile.source_hash
    assert fields["xwoba"]["source_cutoff_date"] == "2026-07-20"
    assert fields["barrel_rate"]["denominator"] == 2
    assert fields["hard_hit_rate"]["denominator"] == 2


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda rich: rich.pop(RICH_FEATURE_LINEAGE_KEY), "lacks lineage"),
        (
            lambda rich: rich.__setitem__("xwoba", rich["xwoba"] + 0.01),
            "value/lineage hash mismatch",
        ),
        (
            lambda rich: rich[RICH_FEATURE_LINEAGE_KEY]["fields"]["xwoba"].__setitem__(
                "player_id", 999
            ),
            "player identity mismatch",
        ),
        (
            lambda rich: rich[RICH_FEATURE_LINEAGE_KEY]["fields"]["xwoba"].__setitem__(
                "source_cutoff_date", "2026-07-22"
            ),
            "source cutoff is not pregame",
        ),
        (
            lambda rich: rich[RICH_FEATURE_LINEAGE_KEY]["fields"]["xwoba"].__setitem__(
                "source_hash", "0" * 64
            ),
            "profile source hash mismatch",
        ),
        (
            lambda rich: rich[RICH_FEATURE_LINEAGE_KEY]["fields"][
                "barrel_rate"
            ].__setitem__("denominator", 999),
            "batted-ball denominator mismatch",
        ),
    ],
)
def test_rich_lineage_mutations_fail_at_probability_boundary(mutation, message) -> None:
    profile = _profile()
    rich = deepcopy(_rich(profile))
    mutation(rich)
    with pytest.raises(StatcastIntegrityError, match=message):
        validate_profile_and_rich_features(profile, rich, context="mutation")


def test_rehashed_direct_override_cannot_depart_from_bound_profile() -> None:
    profile = _profile()
    rich = deepcopy(_rich(profile))
    rich["xwoba"] += 0.01
    rich[RICH_FEATURE_LINEAGE_KEY]["fields"]["xwoba"][
        "value_sha256"
    ] = sha256_feature_value(rich["xwoba"])
    with pytest.raises(StatcastIntegrityError, match="differs from bound profile"):
        validate_profile_and_rich_features(profile, rich, context="mutation")


def test_explicit_league_fallback_has_reason_without_fabricated_source_hash() -> None:
    profile = replace(
        SavantClient().league_fallback_profile(999, "Missing"),
        source_cutoff_date="2026-07-21",
    )
    rich = _rich(profile)
    validate_profile_and_rich_features(profile, rich, context="fallback")
    entry = rich[RICH_FEATURE_LINEAGE_KEY]["fields"]["xwoba"]
    assert entry["source_hash"] is None
    assert entry["fallback_reason"] == "player_missing_from_valid_source"


def test_unbound_source_and_unproven_active_rolling_features_fail_closed() -> None:
    unbound = replace(_profile(), source_cutoff_date=None)
    with pytest.raises(StatcastIntegrityError, match="bound source cutoff"):
        _rich(unbound)
    with pytest.raises(StatcastIntegrityError, match="without source lineage"):
        RichFeatureEnricher().enrich(
            {"game_date": "2026-07-22"},
            _profile(),
            rolling={"roll15_xwoba": 0.400, "recent_pa_15": 30},
        )


def test_feature_store_round_trip_preserves_source_and_effective_lineage() -> None:
    profile = _profile()
    rich = _rich(profile)
    hitter = HitterGameContext(
        PlayerIdentity(123, "Canonical Hitter", "HOME"),
        GameContext(900001, "2026-07-22", "Park", True, "AWAY", "unknown"),
        lineup_slot=1,
    )
    bundle = PlayerFeatureBundle(
        hitter=hitter,
        statcast=profile,
        park=ParkFactors("Park"),
        weather=WeatherContext("Park", "2026-07-22"),
        matchup=MatchupContext(),
        metadata={"rich_features": rich},
    )
    restored = bundle_from_dict(bundle_to_dict(bundle))
    assert restored.statcast.source_hash == profile.source_hash
    assert restored.statcast.source_cutoff_date == profile.source_cutoff_date
    assert restored.rich_features == rich
    health = health_for_bundle(restored)
    assert health.rich_feature_lineage_present is True
    assert health.rich_probability_lineage_complete is True
    assert "rich_probability_lineage_complete" in health.flags


def test_rich_lineage_metadata_is_numerically_inert_for_valid_values() -> None:
    profile = _profile()
    rich = _rich(profile)
    strict = HybridPASimulator().expected_outcome_probabilities(
        statcast=profile, rich_features=rich
    )
    legacy_profile = replace(
        profile,
        source_status="unverified",
        fallback_fields=(),
        source_hash=None,
        source_max_game_date=None,
        source_cutoff_date=None,
    )
    legacy_rich = {
        key: value for key, value in rich.items() if key != RICH_FEATURE_LINEAGE_KEY
    }
    legacy = HybridPASimulator().expected_outcome_probabilities(
        statcast=legacy_profile, rich_features=legacy_rich
    )
    assert strict == legacy
