from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from src.evaluation.pa_volume_historical_source_access_v1 import (
    AUTHORIZED_ACTIONS,
    PROTECTED_BOUNDARIES,
    SCHEMA,
    SOURCE_SCOPE,
    STATUS,
    HistoricalSourceAccessError,
    authorization_semantic_sha256,
    canonical_json_bytes,
    verify_historical_source_access_authorization,
)


RUNTIME_SHA = "a" * 64
SOURCE_SHA = "b" * 64
ACCESS_TIME = "2026-07-29T12:00:00.000000Z"


def document() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": SCHEMA,
        "authorization_id": "human-approved-2023-official-mlb-source-v1",
        "status": STATUS,
        "authorized_at_utc": "2026-07-29T10:00:00.000000Z",
        "valid_from_utc": "2026-07-29T10:01:00.000000Z",
        "expires_at_utc": "2026-07-29T14:00:00.000000Z",
        "research_only": True,
        "betting_authorized": False,
        "network_fetch_authorized": True,
        "source_scope": copy.deepcopy(SOURCE_SCOPE),
        "authorized_actions": copy.deepcopy(AUTHORIZED_ACTIONS),
        "protected_boundaries": copy.deepcopy(PROTECTED_BOUNDARIES),
        "runtime_policy_sha256": RUNTIME_SHA,
        "source_bundle_sha256": SOURCE_SHA,
        "authorization_sha256": None,
    }
    value["authorization_sha256"] = authorization_semantic_sha256(value)
    return value


def write_document(path: Path, value: dict[str, object]) -> str:
    value["authorization_sha256"] = authorization_semantic_sha256(value)
    payload = canonical_json_bytes(value)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def verify(path: Path, digest: str):
    return verify_historical_source_access_authorization(
        authorization_path=path,
        expected_authorization_sha256=digest,
        expected_runtime_policy_sha256=RUNTIME_SHA,
        expected_source_bundle_sha256=SOURCE_SHA,
        access_time_utc=ACCESS_TIME,
    )


def test_exact_externally_anchored_authorization_passes(tmp_path: Path) -> None:
    path = tmp_path / "authorization.json"
    digest = write_document(path, document())

    result = verify(path, digest)

    assert result.authorization_file_sha256 == digest
    assert result.runtime_policy_sha256 == RUNTIME_SHA
    assert result.source_bundle_sha256 == SOURCE_SHA


def test_missing_authorization_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(HistoricalSourceAccessError, match="missing"):
        verify(tmp_path / "missing.json", "c" * 64)


def test_wrong_external_digest_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "authorization.json"
    write_document(path, document())
    with pytest.raises(HistoricalSourceAccessError, match="external expected digest"):
        verify(path, "c" * 64)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda value: value["source_scope"].update({"season": 2024}),
            "source scope",
        ),
        (
            lambda value: value["authorized_actions"].update(
                {"prediction_generation": True}
            ),
            "allowed actions",
        ),
        (
            lambda value: value.update({"source_bundle_sha256": "d" * 64}),
            "different runtime or source bytes",
        ),
    ],
)
def test_rehashed_widened_or_rebound_scope_fails_closed(
    tmp_path: Path, mutation, match: str
) -> None:
    value = document()
    mutation(value)
    path = tmp_path / "authorization.json"
    digest = write_document(path, value)
    with pytest.raises(HistoricalSourceAccessError, match=match):
        verify(path, digest)


def test_rehashed_may_scope_fails_closed(tmp_path: Path) -> None:
    value = document()
    value["protected_boundaries"]["may_2026_accessed"] = True
    path = tmp_path / "authorization.json"
    digest = write_document(path, value)
    with pytest.raises(HistoricalSourceAccessError, match="May"):
        verify(path, digest)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("authorized_at_utc", "2026-07-29T12:01:00.000000Z", "late"),
        ("valid_from_utc", "2026-07-29T12:01:00.000000Z", "late"),
        ("expires_at_utc", ACCESS_TIME, "expired"),
    ],
)
def test_late_or_expired_authorization_fails_closed(
    tmp_path: Path, field: str, value: str, match: str
) -> None:
    authorization = document()
    authorization[field] = value
    path = tmp_path / "authorization.json"
    digest = write_document(path, authorization)
    with pytest.raises(HistoricalSourceAccessError, match=match):
        verify(path, digest)
