from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from dashboard.snapshot_store import SnapshotStore, SnapshotUnavailable, _contains_may_2026_token
from tests.omega_dashboard.conftest import canonical_bytes, publish_fixture, valid_manifest


def store(root: Path, manifest: Path | None) -> SnapshotStore:
    return SnapshotStore(
        root=root,
        manifest_path=manifest,
        allowed_producers=frozenset({"authorized-runner-v1"}),
    )


def republish(root: Path, snapshot: dict) -> Path:
    return publish_fixture(root, snapshot=snapshot)


def test_valid_content_addressed_snapshot_loads(published_snapshot) -> None:
    root, manifest = published_snapshot
    loaded = store(root, manifest).load()
    assert loaded.snapshot.rows[0].player_name == "Synthetic Batter"
    assert loaded.snapshot_sha256 == hashlib.sha256(loaded.snapshot_bytes).hexdigest()
    assert loaded.manifest.row_count == 1


def test_missing_manifest_is_truthful_no_data(tmp_path: Path) -> None:
    with pytest.raises(SnapshotUnavailable, match="no prediction snapshot"):
        store(tmp_path, None).load()


def test_unknown_fields_fail_closed(tmp_path: Path, snapshot_document) -> None:
    snapshot_document["rows"][0]["sportsbook_edge"] = 0.2
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()


def test_prohibited_advice_language_fails_closed(tmp_path: Path, snapshot_document) -> None:
    snapshot_document["source_health"]["message"] = "A synthetic best bet label"
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()


def test_hash_and_size_mutations_fail_closed(published_snapshot) -> None:
    root, manifest_path = published_snapshot
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot_sha256"] = "0" * 64
    manifest_path.write_bytes(canonical_bytes(manifest))
    with pytest.raises(SnapshotUnavailable, match="SHA-256"):
        store(root, manifest_path).load()

    payload = (root / "snapshot.json").read_bytes()
    manifest = valid_manifest(payload)
    manifest["snapshot_byte_size"] += 1
    manifest_path.write_bytes(canonical_bytes(manifest))
    with pytest.raises(SnapshotUnavailable, match="byte size"):
        store(root, manifest_path).load()


def test_duplicate_json_keys_are_rejected_before_schema(published_snapshot) -> None:
    root, manifest = published_snapshot
    raw = (root / "snapshot.json").read_text(encoding="utf-8")
    duplicated = raw.replace(
        '"schema_version":"prediction-display-snapshot-v1"',
        '"schema_version":"prediction-display-snapshot-v1","schema_version":"prediction-display-snapshot-v1"',
        1,
    ).encode("utf-8")
    (root / "snapshot.json").write_bytes(duplicated)
    manifest.write_bytes(canonical_bytes(valid_manifest(duplicated)))
    with pytest.raises(SnapshotUnavailable, match="strict UTF-8 JSON"):
        store(root, manifest).load()


def test_duplicate_row_identity_and_row_id_fail_closed(tmp_path: Path, snapshot_document) -> None:
    snapshot_document["rows"].append(copy.deepcopy(snapshot_document["rows"][0]))
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("official_slate_date", "2026-05-10"),
        ("created_utc", "2026-05-10T12:00:00Z"),
    ],
)
def test_sealed_month_values_fail_closed(
    tmp_path: Path, snapshot_document, mutation: str, value: str
) -> None:
    snapshot_document[mutation] = value
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()


def test_sealed_month_path_rejected_before_open(tmp_path: Path) -> None:
    sealed_name = tmp_path / "snapshot-2026%2D05%2D10.json"
    with pytest.raises(SnapshotUnavailable, match="sealed"):
        store(tmp_path, sealed_name).load()


@pytest.mark.parametrize(
    "value",
    [
        "2026-05-10",
        "2026/5/10",
        r"2026\05\10",
        "2026_05_10",
        "20260510",
        "2026%2D05%2D10",
        "May 2026",
        "May-2026",
        "2026 May",
        "2026-May",
    ],
)
def test_may_path_token_normalization(value: str) -> None:
    assert _contains_may_2026_token(value)


def test_path_traversal_is_rejected(published_snapshot) -> None:
    root, manifest_path = published_snapshot
    payload = (root / "snapshot.json").read_bytes()
    manifest = valid_manifest(payload)
    manifest["snapshot_path"] = "../snapshot.json"
    manifest_path.write_bytes(canonical_bytes(manifest))
    with pytest.raises(SnapshotUnavailable, match="safe relative"):
        store(root, manifest_path).load()


def test_symlink_snapshot_is_rejected_when_supported(tmp_path: Path, snapshot_document) -> None:
    target = tmp_path / "target.json"
    payload = canonical_bytes(snapshot_document)
    target.write_bytes(payload)
    link = tmp_path / "snapshot.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    manifest = valid_manifest(payload)
    path = tmp_path / "manifest.json"
    path.write_bytes(canonical_bytes(manifest))
    with pytest.raises(SnapshotUnavailable, match="symlink"):
        store(tmp_path, path).load()


def test_unauthorized_producer_fails_closed(published_snapshot) -> None:
    root, manifest = published_snapshot
    unauthorized = SnapshotStore(
        root=root,
        manifest_path=manifest,
        allowed_producers=frozenset({"different-runner"}),
    )
    with pytest.raises(SnapshotUnavailable, match="not allowlisted"):
        unauthorized.load()


def test_unsupported_market_and_manifest_binding_fail_closed(tmp_path: Path, snapshot_document) -> None:
    snapshot_document["rows"][0]["market"] = "rbi"
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()

    snapshot_document["rows"][0]["market"] = "hits"
    manifest = republish(tmp_path, snapshot_document)
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_data["config_sha256"] = "c" * 64
    manifest.write_bytes(canonical_bytes(manifest_data))
    with pytest.raises(SnapshotUnavailable, match="binding mismatch"):
        store(tmp_path, manifest).load()


def test_post_start_snapshot_and_freshness_mutation_fail_closed(
    tmp_path: Path, snapshot_document
) -> None:
    snapshot_document["created_utc"] = "2026-07-28T00:00:00Z"
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()

    snapshot_document["created_utc"] = "2026-07-27T12:00:00Z"
    snapshot_document["rows"][0]["source_freshness_seconds"] = 59
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()


def test_quarantined_or_abstained_rows_cannot_carry_predictions(
    tmp_path: Path, snapshot_document
) -> None:
    snapshot_document["rows"][0]["data_health_tier"] = "quarantined"
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()

    snapshot_document["rows"][0]["data_health_tier"] = "healthy"
    snapshot_document["rows"][0]["abstention_reason"] = "missing-source-receipt"
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()


def test_distribution_and_nonfinite_mutations_fail_closed(tmp_path: Path, snapshot_document) -> None:
    snapshot_document["rows"][0]["count_distribution"][0]["probability"] = 0.5
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()

    snapshot_document["rows"][0]["count_distribution"][0]["probability"] = 0.4
    raw = canonical_bytes(snapshot_document).replace(
        b'"point_prediction":0.8', b'"point_prediction":NaN'
    )
    (tmp_path / "snapshot.json").write_bytes(raw)
    (tmp_path / "manifest.json").write_bytes(canonical_bytes(valid_manifest(raw)))
    with pytest.raises(SnapshotUnavailable, match="strict UTF-8 JSON"):
        store(tmp_path, tmp_path / "manifest.json").load()


def test_probability_rows_require_exact_product_side_line_and_bounded_comparisons(
    tmp_path: Path, snapshot_document
) -> None:
    row = snapshot_document["rows"][0]
    row["point_prediction_kind"] = "probability"
    row["point_prediction"] = 0.6
    row["market_side"] = "over"
    row["market_line"] = 0.5
    row["comparison"]["current_live_prediction"] = 1.2
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()


def test_hr_line_and_point_distribution_contradictions_fail_closed(
    tmp_path: Path, snapshot_document
) -> None:
    row = snapshot_document["rows"][0]
    row["point_prediction"] = 0.82
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()


def test_prediction_rows_require_mlb_identity_and_count_line_semantics(
    tmp_path: Path, snapshot_document
) -> None:
    row = snapshot_document["rows"][0]
    row["mlb_player_id"] = None
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()

    row["mlb_player_id"] = 111
    row["point_prediction_kind"] = "probability"
    row["point_prediction"] = 0.6
    row["market_side"] = "over"
    row["market_line"] = 0.25
    row["uncertainty"] = {"method": "display-bound", "lower": 0.5, "upper": 0.7}
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()

    row["market"] = "home_runs"
    row["point_prediction_kind"] = "probability"
    row["point_prediction"] = 0.6
    row["market_side"] = "over"
    row["market_line"] = 7.3
    row["uncertainty"] = {"method": "display-bound", "lower": 0.5, "upper": 0.7}
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()

    row["comparison"]["current_live_prediction"] = 0.6
    row["market_line"] = None
    manifest = republish(tmp_path, snapshot_document)
    with pytest.raises(SnapshotUnavailable, match="positive schema"):
        store(tmp_path, manifest).load()
