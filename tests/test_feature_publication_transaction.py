from __future__ import annotations

import json

import pytest

from src.features.feature_store import FeatureManifestError, FeatureStore
from tests.test_feature_manifest_consumption_repair import _bundle


def test_new_generation_uses_content_addressed_artifact_names(tmp_path) -> None:
    store = FeatureStore(tmp_path)
    written = store.save([_bundle()], "2026-07-22", write_parquet=False)
    manifest = json.loads(written["manifest"].read_text(encoding="utf-8"))

    assert written["json"].name.startswith("bundles.")
    assert written["json"].name.endswith(".json")
    assert manifest["schema_version"] == "feature-bundle-manifest-v2"
    assert manifest["artifacts"]["json"]["path"] == written["json"].name
    assert len(store.load("2026-07-22")) == 1


def test_interruption_before_first_manifest_leaves_no_loadable_generation(
    tmp_path, monkeypatch
) -> None:
    store = FeatureStore(tmp_path)

    def interrupt(**_kwargs):
        raise RuntimeError("simulated manifest interruption")

    monkeypatch.setattr(store, "_write_manifest", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        store.save([_bundle()], "2026-07-22", write_parquet=False)

    assert store.load("2026-07-22") == []
    assert store.exists("2026-07-22") is False


def test_uncommitted_new_generation_cannot_displace_old_manifest(
    tmp_path, monkeypatch
) -> None:
    store = FeatureStore(tmp_path)
    first = store.save([_bundle()], "2026-07-22", write_parquet=False)
    old_manifest = first["manifest"].read_bytes()

    changed = _bundle()
    changed.expected_pa = 9.0

    def interrupt(**_kwargs):
        raise RuntimeError("simulated manifest interruption")

    monkeypatch.setattr(store, "_write_manifest", interrupt)
    with pytest.raises(RuntimeError, match="interruption"):
        store.save([changed], "2026-07-22", write_parquet=False)

    assert store.manifest_path("2026-07-22").read_bytes() == old_manifest
    assert store.load("2026-07-22")[0].expected_pa != 9.0


def test_manifest_path_traversal_and_byte_count_mutations_fail_closed(tmp_path) -> None:
    store = FeatureStore(tmp_path)
    written = store.save([_bundle()], "2026-07-22", write_parquet=False)
    path = written["manifest"]
    manifest = json.loads(path.read_text(encoding="utf-8"))

    traversed = json.loads(json.dumps(manifest))
    traversed["artifacts"]["json"]["path"] = "../escape.json"
    path.write_text(json.dumps(traversed), encoding="utf-8")
    with pytest.raises(FeatureManifestError, match="unsafe path"):
        store.load("2026-07-22")

    byte_mutated = json.loads(json.dumps(manifest))
    byte_mutated["artifacts"]["json"]["bytes"] += 1
    path.write_text(json.dumps(byte_mutated), encoding="utf-8")
    with pytest.raises(FeatureManifestError, match="byte-count mismatch"):
        store.load("2026-07-22")
