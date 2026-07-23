from __future__ import annotations

import json

import pytest

from src.features.feature_store import FeatureManifestError, FeatureStore
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


def _bundle() -> PlayerFeatureBundle:
    game = GameContext(1, "2026-07-22", "Test Park", True, "AWY", "confirmed")
    return PlayerFeatureBundle(
        hitter=HitterGameContext(
            PlayerIdentity(7, "Test Hitter", "HME"), game, lineup_slot=1
        ),
        statcast=StatcastProfile(7, "Test Hitter"),
        park=ParkFactors("Test Park"),
        weather=WeatherContext("Test Park", "2026-07-22"),
        matchup=MatchupContext(),
    )


def test_load_fails_before_consuming_tampered_manifested_bundle(tmp_path) -> None:
    store = FeatureStore(tmp_path)
    written = store.save([_bundle()], "2026-07-22", write_parquet=False)
    payload = json.loads(written["json"].read_text(encoding="utf-8"))
    payload[0]["expected_pa"] = 9.99
    written["json"].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FeatureManifestError, match="hash mismatch"):
        store.load("2026-07-22")


def test_load_fails_when_manifest_population_is_mutated(tmp_path) -> None:
    store = FeatureStore(tmp_path)
    store.save([_bundle()], "2026-07-22", write_parquet=False)
    path = store.manifest_path("2026-07-22")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["bundle_count"] = 2
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(FeatureManifestError, match="bundle count mismatch"):
        store.load("2026-07-22")


def test_unlisted_sibling_cannot_override_manifested_json(tmp_path) -> None:
    store = FeatureStore(tmp_path)
    store.save([_bundle()], "2026-07-22", write_parquet=False)
    (tmp_path / "2026-07-22" / "bundles.parquet").write_bytes(b"not parquet")

    loaded = store.load("2026-07-22", prefer="parquet")
    assert len(loaded) == 1
    assert loaded[0].hitter.player.mlb_id == 7


def test_missing_manifest_remains_readable_as_legacy_unverified(tmp_path) -> None:
    store = FeatureStore(tmp_path)
    written = store.save([_bundle()], "2026-07-22", write_parquet=False)
    legacy = tmp_path / "2026-07-22" / "bundles.json"
    legacy.write_bytes(written["json"].read_bytes())
    store.manifest_path("2026-07-22").unlink()

    loaded = store.load("2026-07-22")
    assert len(loaded) == 1
