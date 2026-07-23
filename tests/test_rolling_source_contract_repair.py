from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.data.point_in_time import PointInTimeStats
from src.data.rolling_source_contract import (
    RollingSourceSchemaError,
    RollingSourceUnavailableError,
)
from src.data.statcast_roller import RollerConfig, StatcastRoller
from src.features.feature_factory import FeatureFactory
from src.models.dataclasses import LeagueBaselines
from tests.test_phase10 import MockMatchupAPI, MockStatcastEngine, _sample_bundle


def _rows() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_date": ["2024-06-14", "2024-06-15"],
            "type": ["X", "X"],
            "description": ["hit_into_play", "hit_into_play"],
            "estimated_woba_using_speedangle": [0.30, 1.50],
            "estimated_ba_using_speedangle": [0.25, 1.00],
            "estimated_slg_using_speedangle": [0.40, 2.00],
            "launch_speed": [90.0, 115.0],
            "launch_angle": [10.0, 28.0],
            "launch_speed_angle": [2, 6],
        }
    )


def test_statcast_roller_provider_exception_fails_closed(tmp_path: Path) -> None:
    def fail(*_args):
        raise OSError("old silent outage")

    roller = StatcastRoller(
        cache_dir=tmp_path,
        config=RollerConfig(rate_limit_seconds=0),
        fetch_fn=fail,
    )
    with pytest.raises(RollingSourceUnavailableError, match="fetch failed"):
        roller.rolling_features(7, "2024-06-15")


def test_mutation_corrupt_cache_is_preserved_and_not_refetched(tmp_path: Path) -> None:
    path = tmp_path / "2024" / "batter_7.csv"
    path.parent.mkdir(parents=True)
    original = b'"unterminated\n'
    path.write_bytes(original)
    calls = []
    roller = StatcastRoller(
        cache_dir=tmp_path,
        config=RollerConfig(rate_limit_seconds=0),
        fetch_fn=lambda *_args: calls.append(True) or _rows(),
    )
    with pytest.raises(RollingSourceSchemaError, match="preserved for quarantine"):
        roller.rolling_features(7, "2024-06-15")
    assert path.read_bytes() == original
    assert calls == []


def test_lineage_hash_and_count_use_only_strictly_prior_rows(tmp_path: Path) -> None:
    frame = _rows()
    roller = StatcastRoller(
        cache_dir=tmp_path / "a",
        config=RollerConfig(rate_limit_seconds=0, min_bip=1),
        fetch_fn=lambda *_args: frame,
    )
    first = roller.rolling_features(7, "2024-06-15")
    assert first["rolling_source_status"] == "observed_strict_prior"
    assert first["rolling_source_row_count"] == 1
    assert first["rolling_source_max_game_date"] == "2024-06-14"

    mutated = frame.copy()
    mutated.loc[1, "estimated_woba_using_speedangle"] = 999.0
    second = StatcastRoller(
        cache_dir=tmp_path / "b",
        config=RollerConfig(rate_limit_seconds=0, min_bip=1),
        fetch_fn=lambda *_args: mutated,
    ).rolling_features(7, "2024-06-15")
    assert second["rolling_source_content_sha256"] == first[
        "rolling_source_content_sha256"
    ]


def test_successful_empty_history_is_explicit(tmp_path: Path) -> None:
    roller = StatcastRoller(
        cache_dir=tmp_path,
        config=RollerConfig(rate_limit_seconds=0),
        fetch_fn=lambda *_args: pd.DataFrame(),
    )
    features = roller.rolling_features(7, "2024-06-15")
    assert features["rolling_source_status"] == "confirmed_empty_history"
    assert features["rolling_source_row_count"] == 0
    assert features["rolling_source_max_game_date"] is None


class _FailingMLB:
    season = 2024
    BASE_URL = "https://example.invalid"

    def _get(self, *_args, **_kwargs):
        raise OSError("source down")


def test_point_in_time_game_log_failure_is_not_empty_history() -> None:
    pit = PointInTimeStats(mlb_api=_FailingMLB(), season=2024)
    with pytest.raises(RollingSourceUnavailableError, match="game-log fetch failed"):
        pit.rolling_features(7, "2024-06-15")


class _FailingRollingProvider:
    def rolling_features(self, *_args, **_kwargs):
        raise RollingSourceUnavailableError("configured provider failed")


def test_feature_factory_propagates_configured_rolling_failure() -> None:
    factory = FeatureFactory(
        league_baselines=LeagueBaselines(),
        mlb_api=MockMatchupAPI(),
        statcast_engine=MockStatcastEngine(),
        rolling_stats_provider=_FailingRollingProvider(),
    )
    with pytest.raises(RollingSourceUnavailableError, match="configured provider failed"):
        factory.build_bundles("2026-07-01", hitters=[_sample_bundle().hitter])
