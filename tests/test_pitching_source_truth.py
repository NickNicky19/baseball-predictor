"""Regression and mutation tests for the pitcher source-truth boundary."""

from __future__ import annotations

import pytest

from src.data.mlb_api import MLBStatsAPI, PitchingStatsSnapshot, _parse_pitching
from src.data.pitching_source_truth import (
    PitchingSourceValidationError,
    first_observed_rate,
    frozen_legacy_rate_fallback,
    parse_mlb_innings_to_outs,
    validate_candidate_ready_pitching_snapshot,
)
from src.data.point_in_time import GameLogRow, PointInTimeStats
from src.features.matchup_intelligence import MatchupIntelligence
from src.learning.training_set_builder import (
    BUILDER_SCHEMA,
    PITCHER_COLUMNS,
    PITCHER_SOURCE_TRUTH_BUILDER_SCHEMA,
    PITCHER_SOURCE_TRUTH_PITCHER_COLUMNS,
    TrainingSetBuilder,
    validate_pitcher_source_truth_training_row,
)
from src.models.dataclasses import PitcherStatcastProfile, StatcastProfile
from src.prediction.role_innings import RoleAwareInningsEstimator


def _season_stat(**overrides):
    stat = {
        "inningsPitched": "5.2",
        "strikeOuts": "6",
        "hits": "4",
        "homeRuns": "0",
        "baseOnBalls": "1",
        "strikeoutsPer9Inn": None,
        "homeRunsPer9": None,
        "walksPer9Inn": None,
        "gamesStarted": "0",
        "gamesPlayed": "1",
        "era": "2.50",
        "whip": "1.10",
    }
    stat.update(overrides)
    return stat


class _GameLogAPI:
    BASE_URL = "https://example.invalid/api"

    def __init__(self, stat):
        self.stat = stat

    def _get(self, _url, params=None):
        assert params == {"stats": "gameLog", "group": "pitching", "season": 2024}
        return {
            "stats": [
                {
                    "splits": [
                        {"date": "2024-04-01", "stat": self.stat},
                    ]
                }
            ]
        }


def _pit_snapshot(stat):
    pit = PointInTimeStats(mlb_api=_GameLogAPI(stat), season=2024)
    rows = pit._fetch_game_log(123, "pitching")
    return pit._aggregate_pitching(rows)


@pytest.mark.parametrize(
    ("raw", "expected_outs"),
    [
        ("0", 0),
        ("0.0", 0),
        ("5.0", 15),
        ("5.1", 16),
        ("5.2", 17),
        (12, 36),
    ],
)
def test_canonical_mlb_innings_parser_returns_exact_outs(raw, expected_outs):
    assert parse_mlb_innings_to_outs(raw) == expected_outs


@pytest.mark.parametrize(
    "raw",
    [None, "", "-", "-1.0", "5.3", "5.20", "5.02", " 5.2", 5.2, True],
)
def test_canonical_mlb_innings_parser_fails_closed(raw):
    with pytest.raises(PitchingSourceValidationError):
        parse_mlb_innings_to_outs(raw)


def test_mutation_decimal_float_ip_bug_is_rejected_by_live_parser():
    """Would fail if the old float('5.2') denominator mutation returned."""

    snapshot = _parse_pitching(_season_stat())
    assert snapshot.outs_recorded == 17
    assert snapshot.innings_pitched == pytest.approx(17 / 3)
    assert snapshot.k_per_9 == pytest.approx(6 * 9 / (17 / 3))


def test_live_and_point_in_time_paths_share_exact_outs_semantics():
    live = _parse_pitching(_season_stat())
    pit = _pit_snapshot(
        {
            "inningsPitched": "5.2",
            "strikeOuts": "6",
            "baseOnBalls": "1",
            "homeRuns": "0",
            "gamesStarted": "0",
        }
    )
    assert pit.outs_recorded == live.outs_recorded == 17
    assert pit.innings_pitched == live.innings_pitched
    assert pit.k_per_9 == pytest.approx(live.k_per_9)
    assert pit.bb_per_9 == pytest.approx(live.bb_per_9)
    assert pit.hr_per_9 == pytest.approx(live.hr_per_9)


def test_mutation_phantom_start_floor_does_not_replace_true_zero():
    """Would fail if max(gamesStarted, 1) or a truthy clamp returned."""

    live = _parse_pitching(_season_stat(gamesStarted="0", gamesPlayed="9"))
    assert live.games_started == 0
    assert live.games == 9

    pit = _pit_snapshot(
        {
            "inningsPitched": "1.0",
            "strikeOuts": "1",
            "baseOnBalls": "0",
            "homeRuns": "0",
            "gamesStarted": "0",
        }
    )
    assert pit.games_started == 0


@pytest.mark.parametrize("bad", [None, "", "-", "abc", -1, 1.5, True])
def test_live_games_started_missing_or_malformed_fails_closed(bad):
    with pytest.raises(PitchingSourceValidationError):
        _parse_pitching(_season_stat(gamesStarted=bad))


@pytest.mark.parametrize("bad", [None, "", "-", "abc", -1, 2, 1.5, True])
def test_point_in_time_start_indicator_missing_or_malformed_fails_closed(bad):
    with pytest.raises(PitchingSourceValidationError):
        _pit_snapshot(
            {
                "inningsPitched": "1.0",
                "strikeOuts": "1",
                "baseOnBalls": "0",
                "homeRuns": "0",
                "gamesStarted": bad,
            }
        )


def test_games_started_cannot_exceed_appearances():
    with pytest.raises(PitchingSourceValidationError, match="cannot exceed"):
        _parse_pitching(_season_stat(gamesStarted="2", gamesPlayed="1"))


@pytest.mark.parametrize("bad", [None, "", "-", "abc", -1, 1.5, True])
def test_games_played_denominator_missing_or_malformed_fails_closed(bad):
    with pytest.raises(PitchingSourceValidationError):
        _parse_pitching(_season_stat(gamesPlayed=bad))


def test_zero_rates_are_observed_values_and_missing_rates_are_derived():
    explicit_zero = _parse_pitching(
        _season_stat(
            strikeOuts="0",
            baseOnBalls="0",
            homeRuns="0",
            strikeoutsPer9Inn="0.0",
            walksPer9Inn="0.0",
            homeRunsPer9="0.0",
        )
    )
    assert explicit_zero.k_per_9 == 0.0
    assert explicit_zero.bb_per_9 == 0.0
    assert explicit_zero.hr_per_9 == 0.0

    no_denominator = _parse_pitching(
        _season_stat(
            inningsPitched="0.0",
            strikeOuts="0",
            hits="0",
            baseOnBalls="0",
            homeRuns="0",
        )
    )
    assert no_denominator.k_per_9 is None
    assert no_denominator.bb_per_9 is None
    assert no_denominator.hr_per_9 is None
    assert no_denominator.k_pct is None
    assert no_denominator.bb_pct is None


def test_mutation_truthiness_fallback_cannot_replace_observed_zero():
    assert first_observed_rate(0.0, 7.5, default=8.8) == 0.0
    assert first_observed_rate(None, 7.5, default=8.8) == 7.5
    assert first_observed_rate(None, None, default=8.8) == 8.8


def test_frozen_matchup_consumption_preserves_legacy_zero_fallback():
    intelligence = MatchupIntelligence()
    hitter = StatcastProfile(
        player_id=1,
        player_name="Zero-vector hitter",
        contact_rate=0.0,
        barrel_rate=0.0,
        xwoba=0.0,
    )
    pitcher = PitcherStatcastProfile(
        player_id=2,
        player_name="Observed-zero pitcher",
        k_rate=0.0,
        bb_rate=0.0,
        hr_per_9=0.0,
    )
    assert intelligence._pitcher_archetype_similarity(hitter, pitcher) == 0.0


def test_candidate_and_frozen_rate_consumption_are_explicitly_separate():
    assert first_observed_rate(0.0, 7.5, default=8.8) == 0.0
    assert frozen_legacy_rate_fallback(0.0, 7.5, default=8.8) == 7.5


def test_positive_counts_with_zero_outs_fail_closed():
    with pytest.raises(PitchingSourceValidationError, match="counts cannot be positive"):
        _parse_pitching(_season_stat(inningsPitched="0.0", strikeOuts="1"))


def test_supplied_rate_must_agree_with_exact_count_and_outs():
    with pytest.raises(PitchingSourceValidationError, match="contradicts"):
        _parse_pitching(_season_stat(strikeoutsPer9Inn="0.0"))

    rounded = _parse_pitching(_season_stat(strikeoutsPer9Inn="9.53"))
    assert rounded.k_per_9 == 9.53


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"walksPer9Inn": "0.0"}, "walksPer9Inn"),
        ({"homeRuns": "1", "homeRunsPer9": "0.0"}, "homeRunsPer9"),
        ({"hitsPer9Inn": "0.0"}, "hitsPer9Inn"),
    ],
)
def test_each_supplied_per_nine_rate_must_match_counts_and_outs(overrides, field):
    with pytest.raises(PitchingSourceValidationError, match=field):
        _parse_pitching(_season_stat(**overrides))


@pytest.mark.parametrize("bad", ["-", "NaN", "Infinity", "-0.1", True])
def test_malformed_supplied_rate_fails_closed(bad):
    with pytest.raises(PitchingSourceValidationError):
        _parse_pitching(_season_stat(strikeoutsPer9Inn=bad))


def test_manually_missing_game_start_state_cannot_masquerade_as_relief():
    row = GameLogRow(
        game_date="2024-04-01",
        outs_recorded=3,
        k_pitched=1,
        bb_pitched=0,
        hr_allowed=0,
    )
    with pytest.raises(PitchingSourceValidationError, match="required"):
        PointInTimeStats._aggregate_pitching([row])


def test_manually_missing_innings_denominator_cannot_masquerade_as_zero():
    row = GameLogRow(
        game_date="2024-04-01",
        games_started=0,
        k_pitched=0,
        bb_pitched=0,
        hr_allowed=0,
    )
    with pytest.raises(PitchingSourceValidationError, match="inningsPitched"):
        PointInTimeStats._aggregate_pitching([row])


def test_aggregate_positive_counts_with_zero_outs_fails_closed():
    row = GameLogRow(
        game_date="2024-04-01",
        games_started=0,
        outs_recorded=0,
        k_pitched=1,
        bb_pitched=0,
        hr_allowed=0,
    )
    with pytest.raises(PitchingSourceValidationError, match="cannot be positive"):
        PointInTimeStats._aggregate_pitching([row])


def test_legacy_missing_start_count_stays_behind_frozen_default_boundary():
    missing = PitchingStatsSnapshot(
        innings_pitched=12.0,
        games_started=None,
        games=None,
    )
    observed_zero = PitchingStatsSnapshot(
        innings_pitched=12.0,
        games_started=0,
        games=5,
    )
    assert RoleAwareInningsEstimator._legacy_expected_ip(
        missing
    ) == RoleAwareInningsEstimator._legacy_expected_ip(observed_zero)


def test_live_source_truth_and_frozen_expected_innings_are_both_preserved():
    snapshot = _parse_pitching(_season_stat(gamesStarted="0", gamesPlayed="1"))
    assert snapshot.outs_recorded == 17
    assert snapshot.innings_pitched == pytest.approx(17 / 3)
    assert snapshot.games_started == 0
    assert snapshot.legacy_innings_pitched == 5.2
    assert snapshot.legacy_games_started == 1
    assert RoleAwareInningsEstimator._legacy_expected_ip(snapshot) == 5.2


@pytest.mark.parametrize(
    "mutation",
    [
        {"outs_recorded": None},
        {"games_started": None},
        {"games": None},
        {"outs_recorded": 0},
    ],
)
def test_candidate_ready_snapshot_rejects_missing_source_identity(mutation):
    values = {
        "outs_recorded": 17,
        "innings_pitched": 17 / 3,
        "strikeouts": 6,
        "walks": 1,
        "home_runs": 0,
        "k_per_9": 6 * 27 / 17,
        "bb_per_9": 27 / 17,
        "hr_per_9": 0.0,
        "games_started": 0,
        "games": 1,
    }
    values.update(mutation)
    with pytest.raises(PitchingSourceValidationError, match="candidate-ready"):
        validate_candidate_ready_pitching_snapshot(
            PitchingStatsSnapshot(**values), label="fixture"
        )


def _api_with_pitching_payload(payload):
    api = object.__new__(MLBStatsAPI)
    api.season = 2024
    api._get = lambda *_args, **_kwargs: payload
    return api


def test_candidate_ready_live_fetch_rejects_missing_people():
    api = _api_with_pitching_payload({"people": []})
    with pytest.raises(PitchingSourceValidationError, match="no people"):
        api.get_pitching_stats(123, source_truth_required=True)
    season, recent = api.get_pitching_stats(123)
    assert season.outs_recorded is None
    assert recent.outs_recorded is None


def test_candidate_ready_live_fetch_rejects_empty_or_missing_stat_blocks():
    empty = _api_with_pitching_payload(
        {
            "people": [
                {
                    "stats": [
                        {
                            "type": {"displayName": "season"},
                            "splits": [],
                        }
                    ]
                }
            ]
        }
    )
    with pytest.raises(PitchingSourceValidationError, match="stat block is empty"):
        empty.get_pitching_stats(123, source_truth_required=True)

    season_only = _api_with_pitching_payload(
        {
            "people": [
                {
                    "stats": [
                        {
                            "type": {"displayName": "season"},
                            "splits": [{"stat": _season_stat(gamesStarted="1")}],
                        }
                    ]
                }
            ]
        }
    )
    with pytest.raises(PitchingSourceValidationError, match="missing stat blocks"):
        season_only.get_pitching_stats(123, source_truth_required=True)


def test_candidate_ready_pit_fetch_rejects_no_prior_rows():
    class EmptyGameLogAPI:
        BASE_URL = "https://example.invalid/api"

        def _get(self, _url, params=None):
            return {"stats": []}

    pit = PointInTimeStats(mlb_api=EmptyGameLogAPI(), season=2024)
    with pytest.raises(PitchingSourceValidationError, match="candidate-ready"):
        pit.get_pitching_stats_as_of(
            123,
            "2024-04-02",
            source_truth_required=True,
        )


def _candidate_training_row():
    return {
        "builder_schema": PITCHER_SOURCE_TRUTH_BUILDER_SCHEMA,
        "pit_outs": 17,
        "pit_ip": 5.7,
        "pit_k": 6,
        "pit_bb": 1,
        "pit_hr": 0,
        "pit_recent_outs": 8,
        "pit_recent_ip": 2.7,
        "out_outs": 17,
        "out_ip": 5.7,
        "out_k": 6,
        "out_bb": 1,
        "out_hr": 0,
    }


def test_candidate_training_schema_adds_exact_outs_without_mutating_a3_2():
    assert BUILDER_SCHEMA == "a3.2"
    assert "pit_outs" not in PITCHER_COLUMNS
    assert "pit_recent_outs" not in PITCHER_COLUMNS
    assert "out_outs" not in PITCHER_COLUMNS
    assert {
        "pit_outs",
        "pit_recent_outs",
        "out_outs",
    }.issubset(PITCHER_SOURCE_TRUTH_PITCHER_COLUMNS)
    validate_pitcher_source_truth_training_row(_candidate_training_row())


def test_mutation_training_exact_outs_round_trip_fails_closed():
    row = _candidate_training_row()
    row["pit_outs"] = 16
    with pytest.raises(PitchingSourceValidationError, match="round-trip"):
        validate_pitcher_source_truth_training_row(row)


def test_mutation_training_schema_downgrade_fails_closed():
    row = _candidate_training_row()
    row["builder_schema"] = BUILDER_SCHEMA
    with pytest.raises(PitchingSourceValidationError, match="wrong builder schema"):
        validate_pitcher_source_truth_training_row(row)


def test_mutation_training_downstream_zero_outs_with_positive_counts_fails_closed():
    row = _candidate_training_row()
    row["out_outs"] = 0
    row["out_ip"] = 0.0
    with pytest.raises(PitchingSourceValidationError, match="positive outcome counts"):
        validate_pitcher_source_truth_training_row(row)


def test_frozen_a3_2_and_candidate_outcome_parsers_are_version_separated():
    players = {
        "ID123": {
            "stats": {
                "pitching": {
                    "inningsPitched": "5.2",
                    "strikeOuts": "6",
                    "hits": "4",
                    "homeRuns": "0",
                    "baseOnBalls": "1",
                }
            }
        }
    }

    frozen = object.__new__(TrainingSetBuilder)
    frozen.builder_schema = BUILDER_SCHEMA
    frozen_snapshot = frozen._pitching_actual(players, 123)
    assert frozen_snapshot is not None
    assert frozen_snapshot.innings_pitched == 5.2
    assert frozen_snapshot.outs_recorded is None
    assert frozen_snapshot.games_started == 1

    candidate = object.__new__(TrainingSetBuilder)
    candidate.builder_schema = PITCHER_SOURCE_TRUTH_BUILDER_SCHEMA
    candidate_snapshot = candidate._pitching_actual(players, 123)
    assert candidate_snapshot is not None
    assert candidate_snapshot.outs_recorded == 17
    assert candidate_snapshot.innings_pitched == pytest.approx(17 / 3)
