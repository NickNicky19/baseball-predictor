#!/usr/bin/env python3
"""Offline/mutation harness for feature-bundle provenance manifests."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.features.feature_store import FeatureManifestError, FeatureStore
from src.models.dataclasses import (
    GameContext, HitterGameContext, MatchupContext, ParkFactors,
    PlayerFeatureBundle, PlayerIdentity, StatcastDistributionProfile,
    StatcastProfile, WeatherContext,
)

PASS = 0
FAIL = 0


def check(condition: bool, label: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


def example_bundle() -> PlayerFeatureBundle:
    game = GameContext(700001, "2026-07-13", "Test Park", True, "Away", "confirmed")
    player = PlayerIdentity(111, "Test Hitter", "HOME")
    return PlayerFeatureBundle(
        hitter=HitterGameContext(player, game, lineup_slot=3),
        statcast=StatcastProfile(
            111,
            "Test Hitter",
            sample_pa=12,
            xwoba=0.330,
            distribution=StatcastDistributionProfile(
                sample_bip=10,
                exit_velocity_mean=91.0,
                barrel_rate=0.12,
            ),
        ),
        park=ParkFactors("Test Park"),
        weather=WeatherContext("Test Park", "2026-07-13"),
        matchup=MatchupContext(),
    )


def main() -> int:
    root = Path(tempfile.mkdtemp())
    store = FeatureStore(root)
    written = store.save(
        [example_bundle()], "2026-07-13", write_parquet=False,
        provenance={"model_version": "test-model", "code": {"status": "available"}},
    )
    check("manifest" in written and written["manifest"].is_file(),
          "save emits a manifest after the bundle artifact")
    manifest = store.verify_manifest("2026-07-13")
    check(manifest is not None and manifest["bundle_count"] == 1,
          "manifest verifies saved bundle hash and count")
    check(manifest["provenance"]["model_version"] == "test-model",
          "caller provenance is preserved verbatim")
    replayed = store.load("2026-07-13")[0]
    check(isinstance(replayed.statcast.distribution, StatcastDistributionProfile),
          "mutation: nested Statcast distribution replays as its typed object")
    check(
        replayed.statcast.distribution is not None
        and replayed.statcast.distribution.sample_bip == 10
        and replayed.statcast.distribution.quality_score() > 0,
        "replayed distribution preserves values and executable behavior",
    )

    # Mutation: change a saved input after its manifest was written.  A hash
    # check must fail rather than letting an audit describe altered inputs.
    json_path = written["json"]
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data[0]["expected_pa"] = 9.99
    json_path.write_text(json.dumps(data), encoding="utf-8")
    try:
        store.verify_manifest("2026-07-13")
    except FeatureManifestError:
        check(True, "mutation: tampered bundle hash hard-fails verification")
    else:
        check(False, "mutation: tampered bundle hash hard-fails verification")
    try:
        store.load("2026-07-13")
    except FeatureManifestError:
        check(True, "mutation: ordinary load cannot bypass a contradictory manifest")
    else:
        check(False, "mutation: ordinary load cannot bypass a contradictory manifest")

    count_store = FeatureStore(root / "count")
    count_store.save([example_bundle()], "2026-07-11", write_parquet=False)
    count_manifest_path = count_store.manifest_path("2026-07-11")
    count_manifest = json.loads(count_manifest_path.read_text(encoding="utf-8"))
    count_manifest["bundle_count"] = 2
    count_manifest_path.write_text(json.dumps(count_manifest), encoding="utf-8")
    try:
        count_store.load("2026-07-11")
    except FeatureManifestError:
        check(True, "mutation: manifest population must match deserialized population")
    else:
        check(False, "mutation: manifest population must match deserialized population")

    legacy = FeatureStore(root / "legacy")
    legacy.save([example_bundle()], "2026-07-12", write_parquet=False)
    legacy.manifest_path("2026-07-12").unlink()
    check(legacy.verify_manifest("2026-07-12") is None,
          "legacy snapshot is labelled unverified, not assigned invented provenance")
    check(len(legacy.load("2026-07-12")) == 1,
          "legacy missing-manifest snapshot remains readable only as unverified")

    print(f"\n{PASS}/{PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
