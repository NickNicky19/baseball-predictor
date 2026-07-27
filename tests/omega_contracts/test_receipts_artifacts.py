import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.omega_contracts.artifacts import (
    ArtifactManifest,
    publish_immutable_artifact,
    validate_artifact,
)
from src.omega_contracts.canonical import canonical_json_bytes, sha256_bytes
from src.omega_contracts.errors import ContractError
from src.omega_contracts.receipts import ReceiptReference, resolve_receipt

REFERENCE_BYTES = {
    "code_sha256": b"synthetic code tree manifest",
    "config_sha256": b"synthetic config",
    "data_manifest_sha256": b"synthetic raw data manifest",
    "feature_schema_sha256": b"synthetic feature schema",
    "dependency_lock_sha256": b"synthetic dependency lock",
    "test_manifest_sha256": b"synthetic test manifest",
    "frozen_comparator_sha256": b"synthetic frozen comparator",
}


def parser(raw: bytes):
    return json.loads(raw)


def write_receipt(root: Path, *, projection=None, mutate=None) -> ReceiptReference:
    projection = projection or {
        "official_date": "2026-07-26",
        "game_pk": 1,
        "player_id": 2,
        "team_id": 3,
        "team_side": "home",
        "scheduled_start_utc": "2026-07-26T20:00:00Z",
    }
    raw = canonical_json_bytes(projection)
    (root / "raw.json").write_bytes(raw)
    payload = {
        "schema_version": "semantic-receipt-v1",
        "receipt_type": "test_source_v1",
        "source_id": "official-test-source",
        "protocol_id": "test-protocol-v1",
        "parser_sha256": "a" * 64,
        "raw_relative_path": "raw.json",
        "raw_sha256": sha256_bytes(raw),
        "projection_sha256": sha256_bytes(canonical_json_bytes(projection)),
        "observed_at_utc": "2026-07-26T10:00:00Z",
        "decision_horizon_utc": "2026-07-26T12:00:00Z",
        "official_date": "2026-07-26",
        "identities": dict(projection),
    }
    if mutate:
        mutate(payload)
    receipt_bytes = canonical_json_bytes(payload)
    (root / "receipt.json").write_bytes(receipt_bytes)
    return ReceiptReference("receipt.json", sha256_bytes(receipt_bytes))


def resolve(root: Path, reference: ReceiptReference):
    return resolve_receipt(
        reference=reference,
        receipt_root=root,
        expected_schema_version="semantic-receipt-v1",
        expected_receipt_type="test_source_v1",
        expected_source_id="official-test-source",
        expected_protocol_id="test-protocol-v1",
        expected_parser_sha256="a" * 64,
        expected_decision_horizon_utc="2026-07-26T12:00:00Z",
        parser=parser,
        expected_identities={
            "official_date": "2026-07-26",
            "game_pk": 1,
            "player_id": 2,
            "team_id": 3,
            "team_side": "home",
            "scheduled_start_utc": "2026-07-26T20:00:00Z",
        },
    )


def test_semantic_receipt_replays_raw_and_exact_identities(tmp_path: Path):
    reference = write_receipt(tmp_path)
    result = resolve(tmp_path, reference)
    assert result.projection["player_id"] == 2


@pytest.mark.parametrize("field", ["source_id", "protocol_id", "parser_sha256"])
def test_receipt_semantic_field_mutations_fail(tmp_path: Path, field: str):
    reference = write_receipt(
        tmp_path, mutate=lambda payload: payload.__setitem__(field, "wrong")
    )
    with pytest.raises(ContractError, match="mismatch"):
        resolve(tmp_path, reference)


def test_receipt_raw_and_identity_swaps_fail(tmp_path: Path):
    reference = write_receipt(tmp_path)
    (tmp_path / "raw.json").write_bytes(b"{}")
    with pytest.raises(ContractError, match="raw byte hash"):
        resolve(tmp_path, reference)

    other = tmp_path / "other"
    other.mkdir()
    reference = write_receipt(
        other,
        projection={
            "official_date": "2026-07-26",
            "game_pk": 99,
            "player_id": 2,
            "team_id": 3,
            "team_side": "home",
            "scheduled_start_utc": "2026-07-26T20:00:00Z",
        },
        mutate=lambda payload: payload["identities"].__setitem__("game_pk", 1),
    )
    with pytest.raises(ContractError, match="projection hash|relied-upon"):
        resolve(other, reference)


def test_receipt_horizon_equality_and_unknown_fields_fail(tmp_path: Path):
    reference = write_receipt(
        tmp_path,
        mutate=lambda payload: payload.__setitem__(
            "observed_at_utc", payload["decision_horizon_utc"]
        ),
    )
    with pytest.raises(ContractError, match="strictly before"):
        resolve(tmp_path, reference)

    other = tmp_path / "other"
    other.mkdir()
    reference = write_receipt(
        other, mutate=lambda payload: payload.__setitem__("extra", "x")
    )
    with pytest.raises(ContractError, match="exactly"):
        resolve(other, reference)


def test_receipt_duplicate_keys_and_nonfinite_constants_fail_closed(tmp_path: Path):
    duplicate = b'{"schema_version":"one","schema_version":"two"}'
    (tmp_path / "receipt.json").write_bytes(duplicate)
    reference = ReceiptReference("receipt.json", sha256_bytes(duplicate))
    with pytest.raises(ContractError, match="duplicate JSON key"):
        resolve(tmp_path, reference)

    nonfinite = b'{"value":NaN}'
    (tmp_path / "receipt.json").write_bytes(nonfinite)
    reference = ReceiptReference("receipt.json", sha256_bytes(nonfinite))
    with pytest.raises(ContractError, match="non-finite JSON"):
        resolve(tmp_path, reference)


def test_receipt_identity_contract_rejects_empty_partial_and_extra_sets(tmp_path: Path):
    reference = write_receipt(tmp_path)
    with pytest.raises(ContractError, match="essential fields"):
        resolve_receipt(
            reference=reference,
            receipt_root=tmp_path,
            expected_schema_version="semantic-receipt-v1",
            expected_receipt_type="test_source_v1",
            expected_source_id="official-test-source",
            expected_protocol_id="test-protocol-v1",
            expected_parser_sha256="a" * 64,
            expected_decision_horizon_utc="2026-07-26T12:00:00Z",
            parser=parser,
            expected_identities={},
        )

    partial_root = tmp_path / "partial"
    partial_root.mkdir()
    reference = write_receipt(
        partial_root,
        mutate=lambda payload: payload["identities"].pop("team_side"),
    )
    with pytest.raises(ContractError, match="exactly match"):
        resolve(partial_root, reference)

    extra_root = tmp_path / "extra"
    extra_root.mkdir()
    reference = write_receipt(
        extra_root,
        mutate=lambda payload: payload["identities"].__setitem__("extra_identity", 99),
    )
    with pytest.raises(ContractError, match="exactly match"):
        resolve(extra_root, reference)


def test_receipt_cannot_self_declare_a_later_decision_horizon(tmp_path: Path):
    reference = write_receipt(
        tmp_path,
        mutate=lambda payload: payload.__setitem__(
            "decision_horizon_utc", "2026-07-26T13:00:00Z"
        ),
    )
    with pytest.raises(ContractError, match="external plan"):
        resolve(tmp_path, reference)


def artifact_payload(data: bytes) -> dict[str, object]:
    return {
        "schema_version": "omega-artifact-manifest-v1",
        "artifact_type": "synthetic-contract-fixture",
        "candidate_id": "omega-integration-candidate-v0",
        "qualification_state": "IMPLEMENTED_UNQUALIFIED",
        "active": False,
        "betting_authorized": False,
        "artifact_sha256": sha256_bytes(data),
        "artifact_size": len(data),
        "code_sha256": sha256_bytes(REFERENCE_BYTES["code_sha256"]),
        "config_sha256": sha256_bytes(REFERENCE_BYTES["config_sha256"]),
        "data_manifest_sha256": sha256_bytes(REFERENCE_BYTES["data_manifest_sha256"]),
        "feature_schema_sha256": sha256_bytes(REFERENCE_BYTES["feature_schema_sha256"]),
        "dependency_lock_sha256": sha256_bytes(
            REFERENCE_BYTES["dependency_lock_sha256"]
        ),
        "test_manifest_sha256": sha256_bytes(REFERENCE_BYTES["test_manifest_sha256"]),
        "fit_start": "2023-01-01",
        "fit_end": "2023-12-31",
        "selection_start": "2024-01-01",
        "selection_end": "2024-12-31",
        "created_at_utc": "2025-01-01T00:00:00Z",
        "published_at_utc": "2025-01-01T00:00:01Z",
        "frozen_comparator_sha256": sha256_bytes(
            REFERENCE_BYTES["frozen_comparator_sha256"]
        ),
        "promotion_approval_sha256": None,
    }


def write_artifact_references(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for field, content in REFERENCE_BYTES.items():
        path = root / f"{field}.fixture"
        path.write_bytes(content)
        result[field] = path
    return result


def test_manifest_rejects_reversed_window_invalid_time_and_active():
    data = b"synthetic artifact"
    payload = artifact_payload(data)
    payload["fit_end"] = "2024-01-01"
    with pytest.raises(ContractError, match="windows"):
        ArtifactManifest.from_mapping(payload)
    payload = artifact_payload(data)
    payload["created_at_utc"] = "not-a-time"
    with pytest.raises(ContractError):
        ArtifactManifest.from_mapping(payload)
    payload = artifact_payload(data)
    payload["active"] = True
    with pytest.raises(ContractError, match="state|active"):
        ArtifactManifest.from_mapping(payload)


def test_content_addressed_publication_and_eligibility(tmp_path: Path):
    data = b"synthetic artifact"
    payload = artifact_payload(data)
    artifact, sidecar = publish_immutable_artifact(
        artifact_bytes=data, manifest_payload=payload, artifact_root=tmp_path
    )
    references = write_artifact_references(tmp_path)
    assert artifact.read_bytes() == data
    assert sidecar.read_bytes() == canonical_json_bytes(
        ArtifactManifest.from_mapping(payload).canonical_dict()
    )
    validated = validate_artifact(
        manifest=ArtifactManifest.from_mapping(payload),
        artifact_path=artifact,
        evaluation_date="2025-07-26",
        decision_horizon_utc=datetime(2025, 7, 26, tzinfo=timezone.utc),
        expected_candidate_id="omega-integration-candidate-v0",
        expected_artifact_type="synthetic-contract-fixture",
        referenced_files=references,
    )
    assert validated.artifact_bytes == data
    with pytest.raises(ContractError, match="candidate identity"):
        validate_artifact(
            manifest=ArtifactManifest.from_mapping(payload),
            artifact_path=artifact,
            evaluation_date="2025-07-26",
            decision_horizon_utc=datetime(2025, 7, 26, tzinfo=timezone.utc),
            expected_candidate_id="different-candidate",
            expected_artifact_type="synthetic-contract-fixture",
            referenced_files=references,
        )
    references["config_sha256"].write_bytes(b"tampered config")
    with pytest.raises(ContractError, match="referenced bytes"):
        validate_artifact(
            manifest=ArtifactManifest.from_mapping(payload),
            artifact_path=artifact,
            evaluation_date="2025-07-26",
            decision_horizon_utc=datetime(2025, 7, 26, tzinfo=timezone.utc),
            expected_candidate_id="omega-integration-candidate-v0",
            expected_artifact_type="synthetic-contract-fixture",
            referenced_files=references,
        )
    references["config_sha256"].write_bytes(REFERENCE_BYTES["config_sha256"])
    artifact.write_bytes(b"changed after validation")
    assert validated.artifact_bytes == data
    artifact.write_bytes(data)
    artifact.write_bytes(b"tampered")
    with pytest.raises(ContractError, match="bytes"):
        validate_artifact(
            manifest=ArtifactManifest.from_mapping(payload),
            artifact_path=artifact,
            evaluation_date="2025-07-26",
            decision_horizon_utc=datetime(2025, 7, 26, tzinfo=timezone.utc),
            expected_candidate_id="omega-integration-candidate-v0",
            expected_artifact_type="synthetic-contract-fixture",
            referenced_files=references,
        )


def test_artifact_requires_prior_selection_and_prior_publication(tmp_path: Path):
    data = b"synthetic artifact"
    payload = artifact_payload(data)
    artifact, _ = publish_immutable_artifact(
        artifact_bytes=data, manifest_payload=payload, artifact_root=tmp_path
    )
    manifest = ArtifactManifest.from_mapping(payload)
    references = write_artifact_references(tmp_path)
    with pytest.raises(ContractError, match="selection"):
        validate_artifact(
            manifest=manifest,
            artifact_path=artifact,
            evaluation_date="2024-12-31",
            decision_horizon_utc=datetime(2025, 7, 26, tzinfo=timezone.utc),
            expected_candidate_id="omega-integration-candidate-v0",
            expected_artifact_type="synthetic-contract-fixture",
            referenced_files=references,
        )
    with pytest.raises(ContractError, match="published"):
        validate_artifact(
            manifest=manifest,
            artifact_path=artifact,
            evaluation_date="2025-07-26",
            decision_horizon_utc=datetime(2024, 12, 31, tzinfo=timezone.utc),
            expected_candidate_id="omega-integration-candidate-v0",
            expected_artifact_type="synthetic-contract-fixture",
            referenced_files=references,
        )
    with pytest.raises(ContractError, match="strictly before"):
        validate_artifact(
            manifest=manifest,
            artifact_path=artifact,
            evaluation_date="2025-07-26",
            decision_horizon_utc=manifest.published_at_utc,
            expected_candidate_id="omega-integration-candidate-v0",
            expected_artifact_type="synthetic-contract-fixture",
            referenced_files=references,
        )


def test_artifact_rejects_unknown_schema_early_creation_and_wrong_loader_identity():
    data = b"synthetic artifact"
    payload = artifact_payload(data)
    payload["schema_version"] = "unknown-artifact-schema"
    with pytest.raises(ContractError, match="schema"):
        ArtifactManifest.from_mapping(payload)

    payload = artifact_payload(data)
    payload["created_at_utc"] = "2023-01-01T00:00:00Z"
    payload["published_at_utc"] = "2023-01-01T00:00:01Z"
    with pytest.raises(ContractError, match="selection inputs"):
        ArtifactManifest.from_mapping(payload)
