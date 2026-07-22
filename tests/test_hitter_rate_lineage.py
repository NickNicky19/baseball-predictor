from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from src.data.point_in_time import PointInTimeStats
from src.features.feature_factory import FeatureFactory
from src.evaluation.prediction_health import health_for_bundle
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
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig
from src.utils.errors import DataFetchError


class StubAPI:
    BASE_URL = "https://example.invalid"
    season = 2026

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def _get(self, _url, params=None):
        self.calls += 1
        if self.fail:
            raise TimeoutError("old silent loss")
        splits = []
        for game_date, pa, strikeouts, walks in (
            ("2026-07-19", 4, 1, 1),
            ("2026-07-21", 5, 2, 0),
            ("2026-07-22", 4, 4, 0),
        ):
            splits.append(
                {
                    "date": game_date,
                    "stat": {
                        "plateAppearances": pa,
                        "atBats": pa - walks,
                        "hits": 1,
                        "doubles": 0,
                        "triples": 0,
                        "homeRuns": 0,
                        "rbi": 0,
                        "runs": 0,
                        "baseOnBalls": walks,
                        "strikeOuts": strikeouts,
                    },
                }
            )
        return {"stats": [{"splits": splits}]}


class MalformedAPI(StubAPI):
    def _get(self, _url, params=None):
        self.calls += 1
        return {}


def _bundle() -> PlayerFeatureBundle:
    hitter = HitterGameContext(
        PlayerIdentity(123, "Hitter", "HOME"),
        GameContext(900001, "2026-07-22", "Park", True, "AWAY", "unknown"),
        lineup_slot=1,
    )
    return PlayerFeatureBundle(
        hitter=hitter,
        statcast=StatcastProfile(123, "Hitter", sample_pa=100, xwoba=0.330),
        park=ParkFactors("Park"),
        weather=WeatherContext("Park", "2026-07-22"),
        matchup=MatchupContext(),
    )


def _fitted_config(**overrides) -> PASimulatorConfig:
    values = dict(
        use_fitted_kbb=True,
        require_hitter_rate_lineage=True,
        kbb_k_intercept=-1.0,
        kbb_k_hitter_season=1.0,
        kbb_k_hitter_recent=0.0,
        kbb_k_pitcher=0.0,
        kbb_bb_intercept=-2.0,
        kbb_bb_hitter_season=1.0,
        kbb_bb_hitter_recent=0.0,
        kbb_bb_pitcher=0.0,
    )
    values.update(overrides)
    return PASimulatorConfig(**values)


def _strict_profile():
    pit = PointInTimeStats(mlb_api=StubAPI(), season=2026)
    factory = FeatureFactory(
        config={
            "feature_factory": {
                "hitter_rate_source_mode": "point_in_time_required"
            }
        },
        mlb_api=None,
        rolling_stats_provider=pit,
    )
    season, recent, lineage = factory._hitting_stats(123, "2026-07-22")
    return factory._attach_hitter_rates(_bundle(), season, recent, lineage).statcast


def test_point_in_time_rate_lineage_excludes_target_date_and_binds_counts() -> None:
    profile = _strict_profile()
    assert profile.k_rate == 3 / 9
    assert profile.bb_rate == 1 / 9
    assert profile.k_rate_recent == 3 / 9
    assert profile.field_lineage["k_rate"]["source_max_game_date"] == "2026-07-21"
    assert profile.field_lineage["k_rate"]["source_cutoff_date"] == "2026-07-21"
    assert profile.field_lineage["k_rate"]["numerator"] == 3
    assert profile.field_lineage["k_rate"]["denominator"] == 9
    probs = HybridPASimulator(config=_fitted_config()).expected_outcome_probabilities(
        statcast=profile
    )
    assert abs(sum(probs.values()) - 1.0) < 1e-12
    health = health_for_bundle(replace(_bundle(), statcast=profile))
    assert health.hitter_rate_lineage_complete is True
    assert "hitter_rate_lineage_complete" in health.flags


def test_lineage_gate_is_numerically_inert_for_the_same_valid_rates() -> None:
    profile = _strict_profile()
    strict = HybridPASimulator(config=_fitted_config()).expected_outcome_probabilities(
        statcast=profile
    )
    legacy_config = _fitted_config(require_hitter_rate_lineage=False)
    legacy = HybridPASimulator(config=legacy_config).expected_outcome_probabilities(
        statcast=profile
    )
    assert strict == legacy


@pytest.mark.parametrize(
    "field, key, value, message",
    [
        ("k_rate", "player_id", 999, "player identity mismatch"),
        ("k_rate", "source_cutoff_date", "2026-07-22", "not pregame"),
        ("k_rate", "source_hash", "bad", "invalid source hash"),
        ("k_rate", "numerator", 4, "count/rate mismatch"),
    ],
)
def test_hitter_rate_lineage_mutations_fail_closed(field, key, value, message) -> None:
    profile = _strict_profile()
    profile.field_lineage = deepcopy(profile.field_lineage)
    profile.field_lineage[field][key] = value
    with pytest.raises(ValueError, match=message):
        HybridPASimulator(config=_fitted_config()).expected_outcome_probabilities(
            statcast=profile
        )


def test_strict_rate_mode_requires_provenance_provider() -> None:
    factory = FeatureFactory(
        config={
            "feature_factory": {
                "hitter_rate_source_mode": "point_in_time_required"
            }
        },
        mlb_api=None,
        rolling_stats_provider=None,
    )
    with pytest.raises(ValueError, match="provenance-capable provider"):
        factory._hitting_stats(123, "2026-07-22")


def test_point_in_time_source_failure_and_may_target_precede_fallback() -> None:
    failing = StubAPI(fail=True)
    pit = PointInTimeStats(mlb_api=failing, season=2026)
    with pytest.raises(DataFetchError, match="game log fetch failed"):
        pit.get_hitting_stats_with_lineage(123, "2026-07-22")
    malformed = PointInTimeStats(mlb_api=MalformedAPI(), season=2026)
    with pytest.raises(DataFetchError, match="schema drift"):
        malformed.get_hitting_stats_with_lineage(123, "2026-07-22")
    may_api = StubAPI()
    may = PointInTimeStats(mlb_api=may_api, season=2026)
    with pytest.raises(ValueError, match="sealed"):
        may.get_hitting_stats_with_lineage(123, "2026-05-15")
    assert may_api.calls == 0


def test_lineage_requirement_cannot_be_decorative_without_fitted_kbb() -> None:
    with pytest.raises(ValueError, match="requires use_fitted_kbb"):
        HybridPASimulator(
            config=PASimulatorConfig(require_hitter_rate_lineage=True)
        )
