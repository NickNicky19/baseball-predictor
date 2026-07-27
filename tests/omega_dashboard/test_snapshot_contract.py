from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard.snapshot_store as store_module
from dashboard.snapshot_store import SnapshotStore, SnapshotUnavailable
from tests.omega_dashboard.conftest import (
    NOW,
    bind_row_hash,
    canonical_bytes,
    publish_fixture,
    unseal_tree,
    valid_manifest,
)


def store(
    root: Path,
    manifest: Path | None,
    digest: str | None,
    **overrides,
) -> SnapshotStore:
    values = {
        "root": root,
        "manifest_path": manifest,
        "allowed_producers": frozenset({"authorized-runner-v1"}),
        "trusted_manifest_sha256": digest,
        "maximum_snapshot_age_seconds": 600,
        "maximum_manifest_bytes": 1024 * 1024,
        "maximum_snapshot_bytes": 10 * 1024 * 1024,
        "immutability_anchor": root,
        "clock": lambda: NOW,
    }
    values.update(overrides)
    return SnapshotStore(**values)


def republish(root: Path, snapshot: dict, mutate_manifest=None) -> tuple[Path, str]:
    return publish_fixture(root, snapshot=snapshot, mutate_manifest=mutate_manifest)


def test_valid_independently_authorized_snapshot_loads(
    published_snapshot, synthetic_source_permission
) -> None:
    root, manifest, digest = published_snapshot
    loaded = store(root, manifest, digest).load()
    assert loaded.snapshot.rows[0].mlb_player_id == 111
    assert loaded.manifest_sha256 == digest
    assert loaded.snapshot_sha256 == hashlib.sha256(loaded.snapshot_bytes).hexdigest()
    assert loaded.loaded_age_seconds == 60


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("trusted_manifest_sha256", None, "not configured"),
        ("trusted_manifest_sha256", "not-a-digest", "malformed"),
        ("maximum_snapshot_age_seconds", None, "age"),
        ("maximum_manifest_bytes", None, "manifest bytes"),
        ("maximum_snapshot_bytes", None, "snapshot bytes"),
        ("immutability_anchor", None, "anchor"),
    ],
)
def test_required_trust_and_operational_configuration_fail_closed(
    published_snapshot, synthetic_source_permission, field, value, message
) -> None:
    root, manifest, digest = published_snapshot
    kwargs = {field: value}
    with pytest.raises(SnapshotUnavailable, match=message):
        store(root, manifest, digest, **kwargs).load()


def test_manifest_trust_root_rejects_each_replacement_class(
    tmp_path: Path, snapshot_document, synthetic_source_permission
) -> None:
    manifest_path, original_digest = republish(tmp_path, snapshot_document)

    changed_snapshot = copy.deepcopy(snapshot_document)
    changed_snapshot["model_banner"]["model_version"] = "frozen-research-model-v1.1"
    bind_row_hash(changed_snapshot["rows"][0])
    republish(tmp_path, changed_snapshot)
    with pytest.raises(SnapshotUnavailable, match="out-of-band trust root"):
        store(tmp_path, manifest_path, original_digest).load()

    republish(tmp_path, snapshot_document)
    raw = (tmp_path / "manifest.json").read_bytes()
    changed_manifest = json.loads(raw)
    changed_manifest["row_count"] = 2
    (tmp_path / "manifest.json").write_bytes(canonical_bytes(changed_manifest))
    with pytest.raises(SnapshotUnavailable, match="out-of-band trust root"):
        store(tmp_path, manifest_path, hashlib.sha256(raw).hexdigest()).load()

    republish(tmp_path, snapshot_document)
    original_manifest = (tmp_path / "manifest.json").read_bytes()
    (tmp_path / "snapshot.json").write_bytes(
        (tmp_path / "snapshot.json").read_bytes() + b" "
    )
    with pytest.raises(SnapshotUnavailable, match="SHA-256|byte size"):
        store(
            tmp_path, manifest_path, hashlib.sha256(original_manifest).hexdigest()
        ).load()


def test_equivalent_json_reserialization_is_not_authorized(
    published_snapshot, synthetic_source_permission
) -> None:
    root, manifest, original_digest = published_snapshot
    value = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    (root / "manifest.json").write_text(json.dumps(value, indent=2), encoding="utf-8")
    with pytest.raises(SnapshotUnavailable, match="out-of-band trust root"):
        store(root, manifest, original_digest).load()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "prediction-display-manifest-v1"),
        ("trust_mode", "manifest_self_asserted"),
        ("digest_algorithm", "sha1"),
        ("signature_algorithm", "custom"),
    ],
)
def test_schema_trust_and_algorithm_downgrades_fail_closed(
    tmp_path: Path, snapshot_document, synthetic_source_permission, field, value
) -> None:
    manifest, digest = republish(
        tmp_path, snapshot_document, lambda item: item.__setitem__(field, value)
    )
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest, digest).load()


def test_manifest_provided_or_in_directory_digest_never_becomes_trust_root(
    tmp_path: Path, snapshot_document, synthetic_source_permission
) -> None:
    manifest, digest = republish(
        tmp_path,
        snapshot_document,
        lambda item: item.__setitem__("trusted_manifest_sha256", "0" * 64),
    )
    (tmp_path / "trusted-manifest.sha256").write_text(digest, encoding="ascii")
    with pytest.raises(SnapshotUnavailable, match="not configured"):
        store(tmp_path, manifest, None).load()


def test_duplicate_trust_field_fails_before_schema(
    published_snapshot, synthetic_source_permission
) -> None:
    root, manifest, _ = published_snapshot
    raw = (root / "manifest.json").read_text(encoding="utf-8")
    duplicated = raw.replace(
        '"trust_mode":"out_of_band_manifest_sha256_v1"',
        '"trust_mode":"out_of_band_manifest_sha256_v1","trust_mode":"out_of_band_manifest_sha256_v1"',
        1,
    ).encode("utf-8")
    (root / "manifest.json").write_bytes(duplicated)
    with pytest.raises(SnapshotUnavailable, match="strict UTF-8 JSON"):
        store(root, manifest, hashlib.sha256(duplicated).hexdigest()).load()


@pytest.mark.parametrize(
    "path", ["../manifest.json", "/tmp/manifest.json", r"C:\manifest.json"]
)
def test_manifest_path_traversal_and_absolute_escape_fail_closed(
    tmp_path: Path, path: str, synthetic_source_permission
) -> None:
    with pytest.raises(SnapshotUnavailable, match="safe relative POSIX"):
        store(tmp_path, Path(path), "0" * 64).load()


def test_manifest_snapshot_path_traversal_fails_closed(
    tmp_path: Path, snapshot_document, synthetic_source_permission
) -> None:
    manifest, digest = republish(
        tmp_path,
        snapshot_document,
        lambda item: item.__setitem__("snapshot_path", "../snapshot.json"),
    )
    with pytest.raises(SnapshotUnavailable, match="safe relative POSIX"):
        store(tmp_path, manifest, digest).load()


def test_symlink_and_reparse_markers_are_rejected(
    tmp_path: Path, snapshot_document, synthetic_source_permission
) -> None:
    payload = canonical_bytes(snapshot_document)
    target = tmp_path / "target.json"
    target.write_bytes(payload)
    link = tmp_path / "snapshot.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        assert os.name == "nt"
    else:
        manifest = valid_manifest(payload)
        manifest_bytes = canonical_bytes(manifest)
        (tmp_path / "manifest.json").write_bytes(manifest_bytes)
        with pytest.raises(SnapshotUnavailable, match="regular file|symlink"):
            store(
                tmp_path,
                Path("manifest.json"),
                hashlib.sha256(manifest_bytes).hexdigest(),
            ).load()
    assert store_module._is_reparse_point(SimpleNamespace(st_file_attributes=0x400))


def test_time_of_check_to_time_of_use_replacement_fails(
    tmp_path: Path, snapshot_document, synthetic_source_permission
) -> None:
    manifest, digest = republish(tmp_path, snapshot_document)
    original = (tmp_path / "manifest.json").read_bytes()
    called = False

    def replace(label: str, phase: str, path: Path) -> None:
        nonlocal called
        if label == "snapshot manifest" and phase == "before_open" and not called:
            called = True
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(original)
            os.replace(replacement, path)

    with pytest.raises(SnapshotUnavailable, match="identity changed"):
        store(tmp_path, manifest, digest, read_phase_hook=replace).load()


def test_file_changed_during_verified_descriptor_read_fails(
    tmp_path: Path, snapshot_document, synthetic_source_permission
) -> None:
    manifest, digest = republish(tmp_path, snapshot_document)
    called = False

    def mutate(label: str, phase: str, path: Path) -> None:
        nonlocal called
        if label == "prediction snapshot" and phase == "after_open" and not called:
            called = True
            with path.open("ab") as handle:
                handle.write(b" ")

    with pytest.raises(SnapshotUnavailable, match="changed during"):
        store(tmp_path, manifest, digest, read_phase_hook=mutate).load()


def test_writable_file_root_and_parent_are_rejected(
    tmp_path: Path, snapshot_document
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    manifest, digest = republish(root, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="writable"):
        store(root, manifest, digest).load()

    with pytest.raises(SnapshotUnavailable, match="writable"):
        store(root, manifest, digest, immutability_anchor=tmp_path).load()


def test_posix_immutable_mode_positive_control(
    tmp_path: Path, snapshot_document
) -> None:
    root = tmp_path / "sealed"
    root.mkdir()
    manifest, digest = republish(root, snapshot_document)
    if os.name == "posix":
        try:
            for path in root.iterdir():
                path.chmod(0o444)
            root.chmod(0o555)
            assert store(root, manifest, digest).load().manifest_sha256 == digest
        finally:
            unseal_tree(root)
    else:
        with pytest.raises(SnapshotUnavailable, match="writable"):
            store(root, manifest, digest).load()


def test_inconclusive_permission_state_fails_closed(
    published_snapshot, monkeypatch
) -> None:
    root, manifest, digest = published_snapshot

    def inconclusive(*args, **kwargs):
        raise SnapshotUnavailable("permission state inconclusive")

    monkeypatch.setattr(store_module, "_assert_not_writable", inconclusive)
    with pytest.raises(SnapshotUnavailable, match="inconclusive"):
        store(root, manifest, digest).load()


@pytest.mark.parametrize(
    ("clock", "max_age"),
    [
        ("2026-07-27T11:59:59Z", 600),
        ("2026-07-27T12:10:01Z", 600),
        ("2026-07-27T23:00:00Z", 99999),
    ],
)
def test_future_stale_and_post_start_loads_fail_closed(
    published_snapshot, synthetic_source_permission, clock, max_age
) -> None:
    root, manifest, digest = published_snapshot
    with pytest.raises(SnapshotUnavailable, match="chronology/freshness"):
        store(
            root,
            manifest,
            digest,
            maximum_snapshot_age_seconds=max_age,
            clock=lambda: clock,
        ).load()


def test_naive_capture_and_noncanonical_timestamp_fail_schema(
    tmp_path: Path, snapshot_document, synthetic_source_permission
) -> None:
    snapshot_document["captured_at_utc"] = "2026-07-27T12:00:00"
    manifest, digest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest, digest).load()


def _mutate_row_and_expect_rejection(
    tmp_path: Path,
    snapshot: dict,
    mutation,
    *,
    rebind: bool = True,
) -> None:
    mutation(snapshot, snapshot["rows"][0])
    if rebind and "row_content_sha256" in snapshot["rows"][0]:
        bind_row_hash(snapshot["rows"][0])
    manifest, digest = republish(tmp_path, snapshot)
    with pytest.raises(
        SnapshotUnavailable, match="positive schema|receipt|identity|semantics"
    ):
        store(tmp_path, manifest, digest).load()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda snap, row: row.pop("mlb_player_id"),
        lambda snap, row: row.pop("game_pk"),
        lambda snap, row: row.pop("mlb_team_id"),
        lambda snap, row: row.pop("mlb_opponent_team_id"),
        lambda snap, row: row.__setitem__("identity_state", "UNRESOLVED"),
        lambda snap, row: row.__setitem__("identity_receipt_sha256", "bad"),
        lambda snap, row: row.__setitem__("identity_receipt_sha256", "9" * 64),
        lambda snap, row: row.__setitem__("player_name", "Different Batter"),
        lambda snap, row: row.__setitem__("mlb_team_id", 999),
        lambda snap, row: row.__setitem__("metadata", {"nested": {"result": "won"}}),
        lambda snap, row: row.__setitem__("player_name", "ROI Profit"),
        lambda snap, row: row.__setitem__("product_id", "model-odds-price"),
        lambda snap, row: row.__setitem__("player_name", "ＲＯＩ"),
        lambda snap, row: row.__setitem__("player_name", "X" * 161),
    ],
)
def test_identity_and_semantic_mutations_fail_closed(
    tmp_path: Path, snapshot_document, synthetic_source_permission, mutation
) -> None:
    _mutate_row_and_expect_rejection(tmp_path, snapshot_document, mutation)


def test_unbound_receipt_and_name_only_abstention_fail_closed(
    tmp_path: Path, snapshot_document, synthetic_source_permission
) -> None:
    row = snapshot_document["rows"][0]
    row["point_prediction"] = None
    row["count_distribution"] = None
    row["uncertainty"] = None
    row["comparison"] = {
        "current_live_prediction": None,
        "frozen_comparator_prediction": None,
        "research_candidate_prediction": None,
        "research_candidate_state": "not_present",
    }
    row["abstention_reason"] = "NO_AUTHORIZED_VALUE"
    row.pop("mlb_player_id")
    bind_row_hash(row)
    manifest, digest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest, digest).load()


def test_row_content_hash_and_duplicate_json_key_fail_closed(
    tmp_path: Path, snapshot_document, synthetic_source_permission
) -> None:
    snapshot_document["rows"][0]["row_content_sha256"] = "0" * 64
    manifest, digest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest, digest).load()

    snapshot_document = copy.deepcopy(snapshot_document)
    bind_row_hash(snapshot_document["rows"][0])
    payload = canonical_bytes(snapshot_document).replace(
        b'"schema_version":"prediction-display-snapshot-v2"',
        b'"schema_version":"prediction-display-snapshot-v2","schema_version":"prediction-display-snapshot-v2"',
        1,
    )
    (tmp_path / "snapshot.json").write_bytes(payload)
    manifest_bytes = canonical_bytes(valid_manifest(payload))
    (tmp_path / "manifest.json").write_bytes(manifest_bytes)
    with pytest.raises(SnapshotUnavailable, match="strict UTF-8 JSON"):
        store(
            tmp_path, Path("manifest.json"), hashlib.sha256(manifest_bytes).hexdigest()
        ).load()
