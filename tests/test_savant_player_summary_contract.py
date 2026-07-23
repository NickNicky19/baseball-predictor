from __future__ import annotations

import pandas as pd
import pytest

from src.data.savant import SavantClient, resolve_configured_savant_csv
from src.data.statcast_source_contract import (
    StatcastSourceSchemaError,
    StatcastSourceUnavailableError,
)


def _valid_summary(**updates) -> pd.DataFrame:
    row = {
        "player_id": 7,
        "player_name": "Truth Hitter",
        "pa": 300,
        "source_window_end": "2024-04-01",
        "xwoba": 0.350,
        "barrel_batted_rate": 0.5,
        "hard_hit_percent": 40.0,
        "batted_ball": 200,
        "barrel": 1,
        "hard_hit": 80,
    }
    row.update(updates)
    return pd.DataFrame([row])


def _write(frame: pd.DataFrame, path) -> None:
    frame.to_csv(path, index=False)


def test_configured_missing_savant_csv_fails_closed(tmp_path) -> None:
    with pytest.raises(StatcastSourceUnavailableError, match="configured Savant CSV"):
        resolve_configured_savant_csv(
            {"savant": {"csv_path": "missing.csv"}}, root=tmp_path
        )
    assert resolve_configured_savant_csv({}, root=tmp_path) is None
    with pytest.raises(StatcastSourceSchemaError, match="must be a string"):
        resolve_configured_savant_csv(
            {"savant": {"csv_path": 7}}, root=tmp_path
        )


def test_valid_player_summary_uses_explicit_percent_units_and_counts(tmp_path) -> None:
    path = tmp_path / "summary.csv"
    _write(_valid_summary(), path)
    profile = SavantClient().build_hitter_profiles_from_csv(
        path, target_date="2024-04-02"
    )[7]

    assert profile.barrel_rate == pytest.approx(0.005)
    assert profile.hard_hit_rate == pytest.approx(0.4)
    assert profile.batted_ball_denominator == 200
    assert profile.barrel_count == 1
    assert profile.hard_hit_count == 80
    assert profile.source_window_end == "2024-04-01"


def test_nan_legacy_alias_cannot_suppress_valid_canonical_barrel_rate(tmp_path) -> None:
    path = tmp_path / "summary.csv"
    _write(_valid_summary(barrel_rate=float("nan")), path)
    profile = SavantClient().build_hitter_profiles_from_csv(
        path, target_date="2024-04-02"
    )[7]
    assert profile.barrel_rate == pytest.approx(0.005)


def test_csv_profile_construction_requires_target_date(tmp_path) -> None:
    path = tmp_path / "summary.csv"
    _write(_valid_summary(), path)
    with pytest.raises(StatcastSourceSchemaError, match="explicit target_date"):
        SavantClient().build_hitter_profiles_from_csv(path)


def test_player_summary_batter_identity_is_not_misclassified_as_pitch_csv(tmp_path) -> None:
    path = tmp_path / "summary.csv"
    frame = _valid_summary().rename(columns={"player_id": "batter"})
    _write(frame, path)
    profile = SavantClient().build_hitter_profiles_from_csv(
        path, target_date="2024-04-02"
    )[7]
    assert profile.source_kind == "savant_player_csv"


@pytest.mark.parametrize(
    "mutation,match",
    [
        ({"barrel_rate": 0.005}, "ambiguous barrel_rate"),
        ({"hard_hit": None}, "partial batted-ball"),
        ({"barrel_batted_rate": 50.0}, "disagree"),
        ({"player_id": 0}, "must be >= 1"),
        ({"pa": "not-pa"}, "not numeric"),
        ({"xwoba": "not-xwoba"}, "not numeric"),
        ({"source_window_end": "2024-04-02"}, "not strict-prior"),
        ({"source_window_end": None}, "invalid source_window_end"),
    ],
)
def test_player_summary_mutations_fail_closed(tmp_path, mutation, match) -> None:
    path = tmp_path / "summary.csv"
    _write(_valid_summary(**mutation), path)
    with pytest.raises((StatcastSourceSchemaError, StatcastSourceUnavailableError), match=match):
        SavantClient().build_hitter_profiles_from_csv(
            path, target_date="2024-04-02"
        )


def test_duplicate_player_identity_fails_closed(tmp_path) -> None:
    path = tmp_path / "summary.csv"
    frame = pd.concat([_valid_summary(), _valid_summary()], ignore_index=True)
    _write(frame, path)
    with pytest.raises(StatcastSourceSchemaError, match="duplicate player_id"):
        SavantClient().build_hitter_profiles_from_csv(
            path, target_date="2024-04-02"
        )


def test_pitch_csv_filters_to_strict_prior_rows_and_binds_window(tmp_path) -> None:
    path = tmp_path / "pitches.csv"
    frame = pd.DataFrame(
        [
            {
                "batter": 7,
                "events": "single",
                "game_date": "2024-04-01",
                "description": "hit_into_play",
                "type": "X",
                "launch_speed": 100.0,
                "launch_speed_angle": 6,
                "zone": 5,
            },
            {
                "batter": 7,
                "events": "home_run",
                "game_date": "2024-04-02",
                "description": "hit_into_play",
                "type": "X",
                "launch_speed": 110.0,
                "launch_speed_angle": 6,
                "zone": 5,
            },
        ]
    )
    _write(frame, path)
    profile = SavantClient(min_pa=1).build_hitter_profiles_from_csv(
        path, target_date="2024-04-02"
    )[7]

    assert profile.sample_pa == 1
    assert profile.source_row_count == 1
    assert profile.source_window_end == "2024-04-01"


@pytest.mark.parametrize("bad_date", [None, "not-a-date"])
def test_pitch_csv_bad_game_date_fails_closed(tmp_path, bad_date) -> None:
    path = tmp_path / "pitches.csv"
    frame = pd.DataFrame(
        [{"batter": 7, "events": "single", "game_date": bad_date}]
    )
    _write(frame, path)
    with pytest.raises(StatcastSourceSchemaError, match="invalid game_date"):
        SavantClient(min_pa=1).build_hitter_profiles_from_csv(
            path, target_date="2024-04-02"
        )
