from __future__ import annotations

import copy
import hashlib

import pytest

from src.evaluation.forward_pitcher_context import ForwardPitcherContext, ProbablePitcher
from src.evaluation.prediction_health import health_for_bundle
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.features.feature_factory import FeatureFactory
from src.features.pitcher_matchup_gate import (
    PitcherMatchupAuthorization,
    PitcherMatchupGateError,
    resolve_pitcher_probability_inputs,
)
from src.models.dataclasses import (
    GameContext,
    HitterGameContext,
    LeagueBaselines,
    MatchupContext,
    ParkFactors,
    PitcherStatcastProfile,
    PlayerFeatureBundle,
    PlayerIdentity,
    StatcastProfile,
    WeatherContext,
)
from src.prediction.prop_engine import PropEngine
from src.simulation.pa_simulator import PASimulatorConfig
from src.simulation.probability_engine import ProbabilityEngine


def _receipt_objects(game_date: str = "2026-07-22"):
    target = CaptureTarget(
        mlb_game_pk=777,
        official_game_date=game_date,
        official_start_time_utc=f"{game_date}T23:00:00Z",
        entry_target_at_utc=f"{game_date}T19:00:00Z",
        entry_hours=4,
    )
    plan = ShadowCapturePlan(
        official_game_date=game_date,
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot_sha256="b" * 64,
        targets=(target,),
    )
    context = ForwardPitcherContext(
        target_id=target.target_id,
        plan_sha256=plan.plan_sha256,
        captured_at_utc=f"{game_date}T18:55:00Z",
        source_name="mlb_statsapi_schedule",
        source_payload_sha256="c" * 64,
        mlb_game_pk=777,
        official_game_date=game_date,
        official_start_time_utc=f"{game_date}T23:00:00Z",
        game_type="R",
        home_team="New York Yankees",
        away_team="Boston Red Sox",
        home_probable_pitcher=ProbablePitcher("resolved", 501),
        away_probable_pitcher=ProbablePitcher("resolved", 502),
    )
    return plan, target, context


def _hitter(game_date: str = "2026-07-22") -> HitterGameContext:
    return HitterGameContext(
        player=PlayerIdentity(1, "Truth Hitter", "NYY", bats="L"),
        game=GameContext(777, game_date, "Yankee Stadium", True, "BOS", "confirmed"),
        lineup_slot=2,
        # Deliberately resembles a postgame/guessed starter. Strict missing-
        # receipt behavior must erase it rather than consume it.
        opposing_pitcher_id=999,
        opposing_pitcher_name="Unverified Starter",
        opposing_pitcher_throws="R",
    )


def _authorization(hitter: HitterGameContext | None = None) -> PitcherMatchupAuthorization:
    plan, target, context = _receipt_objects()
    return PitcherMatchupAuthorization.from_context(
        context=context, plan=plan, target=target, hitter=hitter or _hitter()
    )


def _profile():
    profile = PitcherStatcastProfile(
        player_id=502,
        player_name="Receipt Starter",
        sample_pa=100,
        k_rate=0.30,
        bb_rate=0.08,
        hr_per_9=1.0,
    )
    lineage = {
        "schema_version": "pitcher-profile-lineage-v1",
        "player_id": 502,
        "target_date": "2026-07-22",
        "source_cutoff_date": "2026-07-21",
        "source_max_game_date": "2026-07-20",
        "source_hash": "d" * 64,
        "source_dates_sha256": "e" * 64,
        "source_row_count": 100,
        "source_months": ["2026-04", "2026-06", "2026-07"],
        "fields": {
            "k_rate": {
                "numerator": 30,
                "denominator": 100,
                "sample_count": 100,
                "value_sha256": hashlib.sha256(b"0.3").hexdigest(),
            },
            "bb_rate": {
                "numerator": 8,
                "denominator": 100,
                "sample_count": 100,
                "value_sha256": hashlib.sha256(b"0.08").hexdigest(),
            },
            "hr_per_9": {
                "numerator": 10,
                "denominator": 90,
                "sample_count": 100,
                "value_sha256": hashlib.sha256(b"1.0").hexdigest(),
            },
        },
    }
    return profile, lineage


def _bundle(*, authorization=True, lineage=True) -> PlayerFeatureBundle:
    hitter = _hitter()
    auth = _authorization(hitter)
    hitter.opposing_pitcher_id = auth.expected_pitcher_id
    profile, profile_lineage = _profile()
    metadata = {"weather_hr_factor": 1.0}
    if authorization:
        metadata["pitcher_matchup_authorization"] = auth.to_dict()
    if lineage:
        metadata["pitcher_profile_lineage"] = profile_lineage
    return PlayerFeatureBundle(
        hitter=hitter,
        statcast=StatcastProfile(1, "Truth Hitter", sample_pa=100, xwoba=0.32, xba=0.31, xslg=0.42),
        park=ParkFactors("Yankee Stadium"),
        weather=WeatherContext("Yankee Stadium", "2026-07-22"),
        matchup=MatchupContext(platoon_advantage=0.9, bvp_ops_factor=1.2, bvp_hr_factor=1.3),
        pitcher_statcast=profile,
        metadata=metadata,
    )


def test_authorization_joins_full_team_names_to_abbreviations_by_game_and_side():
    authorization = _authorization()
    assert authorization.expected_pitcher_id == 502
    assert authorization.hitter_is_home is True
    assert PitcherMatchupAuthorization.from_mapping(authorization.to_dict()) == authorization


def test_authorization_identity_and_timing_mutations_fail_closed():
    raw = _authorization().to_dict()
    raw["expected_pitcher_id"] = 999
    with pytest.raises(PitcherMatchupGateError, match="hash mismatch"):
        PitcherMatchupAuthorization.from_mapping(raw)

    raw = _authorization().to_dict()
    raw["captured_at_utc"] = "2026-07-22T20:00:00Z"
    raw.pop("authorization_sha256")
    with pytest.raises(PitcherMatchupGateError, match="after T-minus-4"):
        PitcherMatchupAuthorization(**{
            key: value for key, value in raw.items() if key != "schema_version"
        })


def test_may_receipt_is_rejected_without_source_access():
    plan, target, context = _receipt_objects("2026-05-12")
    with pytest.raises(PitcherMatchupGateError, match="May 2026"):
        PitcherMatchupAuthorization.from_context(
            context=context,
            plan=plan,
            target=target,
            hitter=_hitter("2026-05-12"),
        )


def test_strict_missing_receipt_excludes_every_pitcher_and_matchup_value():
    bundle = _bundle(authorization=False, lineage=False)
    resolved = resolve_pitcher_probability_inputs(
        bundle, mode="receipt_required_or_exclude", league=LeagueBaselines()
    )
    assert resolved.status == "excluded_missing_receipt"
    assert resolved.pitcher_k_pct == LeagueBaselines().k_pct
    assert resolved.pitcher_bb_pct == LeagueBaselines().bb_pct
    assert resolved.pitcher_hr_per_9 is None
    assert resolved.handedness_advantage == 0.0
    assert resolved.bvp_ops_factor == resolved.bvp_hr_factor == 1.0


def test_strict_receipt_without_profile_excludes_block():
    bundle = _bundle()
    bundle.pitcher_statcast = None
    bundle.metadata.pop("pitcher_profile_lineage")
    resolved = resolve_pitcher_probability_inputs(
        bundle, mode="receipt_required_or_exclude", league=LeagueBaselines()
    )
    assert resolved.status == "excluded_profile_unavailable"


def test_verified_profile_consumes_exact_rates_but_not_uncertified_bvp_or_platoon():
    resolved = resolve_pitcher_probability_inputs(
        _bundle(), mode="receipt_required_or_exclude", league=LeagueBaselines()
    )
    assert resolved.status == "receipt_and_profile_verified"
    assert (resolved.pitcher_k_pct, resolved.pitcher_bb_pct, resolved.pitcher_hr_per_9) == (30.0, 8.0, 1.0)
    assert resolved.handedness_advantage == 0.0
    assert resolved.bvp_ops_factor == resolved.bvp_hr_factor == 1.0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("source_months", ["2026-05"]), "sealed May"),
        (("player_id", 501), "identity mismatch"),
        (("source_cutoff_date", "2026-07-22"), "not strictly pregame"),
    ],
)
def test_profile_lineage_mutations_fail_closed(mutation, message):
    bundle = _bundle()
    bundle.metadata["pitcher_profile_lineage"][mutation[0]] = mutation[1]
    with pytest.raises(PitcherMatchupGateError, match=message):
        resolve_pitcher_probability_inputs(
            bundle, mode="receipt_required_or_exclude", league=LeagueBaselines()
        )


def test_count_denominator_and_value_hash_mutations_fail_closed():
    for key, value, message in (
        ("numerator", 31, "contradicts"),
        ("denominator", 0, "counts are invalid"),
        ("value_sha256", "f" * 64, "value hash mismatch"),
    ):
        bundle = _bundle()
        bundle.metadata["pitcher_profile_lineage"]["fields"]["k_rate"][key] = value
        with pytest.raises(PitcherMatchupGateError, match=message):
            resolve_pitcher_probability_inputs(
                bundle, mode="receipt_required_or_exclude", league=LeagueBaselines()
            )


def test_probability_and_simulation_paths_share_the_same_strict_inputs():
    bundle = _bundle()
    config = {"pa_simulator": {"pitcher_context_identity_mode": "receipt_required_or_exclude"}}
    prop = PropEngine(config=config, n_sims=500)
    sim = prop._bundle_to_sim_input(bundle)
    assert (sim.pitcher_k_pct, sim.pitcher_bb_pct, sim.pitcher_hr_per_9) == (30.0, 8.0, 1.0)
    assert sim.handedness_advantage == 0.0
    assert sim.bvp_ops_factor == sim.bvp_hr_factor == 1.0
    assert prop.probability_engine.pa_config.pitcher_context_identity_mode == "receipt_required_or_exclude"
    assert prop.probability_engine.from_bundle(bundle).is_valid()


def test_strict_missing_receipt_is_numerically_invariant_to_malicious_pitcher_values():
    malicious = _bundle(authorization=False, lineage=False)
    neutral = copy.deepcopy(malicious)
    neutral.pitcher_statcast = None
    neutral.matchup = MatchupContext()
    config = PASimulatorConfig.from_league(
        LeagueBaselines(), pitcher_context_identity_mode="receipt_required_or_exclude"
    )
    engine = ProbabilityEngine(league_baselines=LeagueBaselines(), pa_config=config)
    assert engine.from_bundle(malicious).to_dict() == engine.from_bundle(neutral).to_dict()


def test_legacy_default_preserves_frozen_pitcher_consumption():
    bundle = _bundle(authorization=False, lineage=False)
    resolved = resolve_pitcher_probability_inputs(
        bundle, mode="legacy_frozen", league=LeagueBaselines()
    )
    assert (resolved.pitcher_k_pct, resolved.pitcher_bb_pct, resolved.pitcher_hr_per_9) == (30.0, 8.0, 1.0)
    assert resolved.handedness_advantage == 0.9
    assert resolved.bvp_ops_factor == 1.2
    assert resolved.bvp_hr_factor == 1.3


class _AuthorizationProvider:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def authorization_for(self, hitter, game_date):
        self.calls += 1
        return self.value


class _ProfileProvider:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def profile_for(self, authorization):
        self.calls += 1
        return self.value


def test_feature_factory_erases_unverified_starter_and_never_calls_profile_provider():
    auth = _AuthorizationProvider(None)
    profiles = _ProfileProvider(_profile())
    factory = FeatureFactory(
        config={"pa_simulator": {"pitcher_context_identity_mode": "receipt_required_or_exclude"}},
        pitcher_authorization_provider=auth,
        certified_pitcher_profile_provider=profiles,
    )
    hitter, profile, metadata = factory._strict_pitcher_inputs(_hitter(), "2026-07-22")
    assert hitter.opposing_pitcher_id is None
    assert hitter.opposing_pitcher_name == ""
    assert hitter.opposing_pitcher_throws == "U"
    assert profile is None
    assert metadata["pitcher_matchup_status"] == "excluded_missing_receipt"
    assert auth.calls == 1 and profiles.calls == 0


def test_feature_factory_consumes_only_receipt_bound_certified_profile():
    authorization = _authorization()
    auth = _AuthorizationProvider(authorization.to_dict())
    profiles = _ProfileProvider(_profile())
    factory = FeatureFactory(
        config={"pa_simulator": {"pitcher_context_identity_mode": "receipt_required_or_exclude"}},
        pitcher_authorization_provider=auth,
        certified_pitcher_profile_provider=profiles,
    )
    hitter, profile, metadata = factory._strict_pitcher_inputs(_hitter(), "2026-07-22")
    assert hitter.opposing_pitcher_id == 502
    assert profile.player_id == 502
    assert metadata["pitcher_matchup_status"] == "receipt_and_profile_verified"
    assert profiles.calls == 1


def test_health_record_exposes_receipt_consumption_status():
    bundle = _bundle()
    bundle.metadata["pitcher_matchup_status"] = "receipt_and_profile_verified"
    health = health_for_bundle(bundle)
    assert health.pitcher_matchup_status == "receipt_and_profile_verified"
    assert "pitcher_matchup_receipt_and_profile_verified" in health.flags


def test_feature_factory_rejects_sealed_may_before_hitter_provider_call():
    auth = _AuthorizationProvider(None)
    factory = FeatureFactory(
        config={"pa_simulator": {"pitcher_context_identity_mode": "receipt_required_or_exclude"}},
        pitcher_authorization_provider=auth,
    )
    with pytest.raises(ValueError, match="May 2026 is sealed"):
        factory.build_bundles("2026-05-12", hitters=[_hitter("2026-05-12")])
    assert auth.calls == 0


def test_unknown_identity_mode_fails_closed():
    with pytest.raises(PitcherMatchupGateError, match="pitcher_context_identity_mode"):
        FeatureFactory(config={"pa_simulator": {"pitcher_context_identity_mode": "guess_actual"}})
