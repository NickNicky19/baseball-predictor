"""Semantic receipt resolution: bytes, parser, source, identity, and chronology."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .canonical import (
    canonical_json_bytes,
    require_nonempty_text,
    require_sha256,
    sha256_bytes,
    strict_json_object_bytes,
)
from .chronology import (
    assert_may_safe_path,
    parse_aware_utc,
    parse_date,
    require_before,
)
from .errors import ContractError
from .identity import positive_int

_RECEIPT_FIELDS = {
    "schema_version",
    "receipt_type",
    "source_id",
    "protocol_id",
    "parser_sha256",
    "raw_relative_path",
    "raw_sha256",
    "projection_sha256",
    "observed_at_utc",
    "decision_horizon_utc",
    "official_date",
    "identities",
}
_ESSENTIAL_IDENTITY_FIELDS = {
    "official_date",
    "game_pk",
    "team_id",
    "team_side",
    "scheduled_start_utc",
}


@dataclass(frozen=True)
class ReceiptReference:
    relative_path: str
    receipt_sha256: str

    def __post_init__(self) -> None:
        require_nonempty_text(self.relative_path, label="receipt relative path")
        require_sha256(self.receipt_sha256, label="receipt sha256")


@dataclass(frozen=True)
class ResolvedReceipt:
    receipt: Mapping[str, object]
    raw_bytes: bytes
    projection: Mapping[str, object]


def resolve_receipt(
    *,
    reference: ReceiptReference,
    receipt_root: Path,
    expected_schema_version: str,
    expected_receipt_type: str,
    expected_source_id: str,
    expected_protocol_id: str,
    expected_parser_sha256: str,
    expected_decision_horizon_utc: datetime | str,
    parser: Callable[[bytes], Mapping[str, object]],
    expected_identities: Mapping[str, object],
) -> ResolvedReceipt:
    receipt_path = assert_may_safe_path(
        receipt_root / reference.relative_path, allowed_root=receipt_root
    )
    try:
        receipt_bytes = receipt_path.read_bytes()
    except OSError as exc:
        raise ContractError("receipt reference cannot be opened") from exc
    if sha256_bytes(receipt_bytes) != reference.receipt_sha256:
        raise ContractError("receipt byte hash mismatch")
    payload = strict_json_object_bytes(receipt_bytes, label="receipt")
    if set(payload) != _RECEIPT_FIELDS:
        raise ContractError("receipt fields do not exactly match the approved schema")
    for key, expected in (
        (
            "schema_version",
            require_nonempty_text(
                expected_schema_version, label="expected schema_version"
            ),
        ),
        (
            "receipt_type",
            require_nonempty_text(expected_receipt_type, label="expected receipt_type"),
        ),
        (
            "source_id",
            require_nonempty_text(expected_source_id, label="expected source_id"),
        ),
        (
            "protocol_id",
            require_nonempty_text(expected_protocol_id, label="expected protocol_id"),
        ),
        (
            "parser_sha256",
            require_sha256(expected_parser_sha256, label="expected parser sha256"),
        ),
    ):
        if payload[key] != expected:
            raise ContractError(f"receipt {key} mismatch")
    observed = parse_aware_utc(
        payload["observed_at_utc"], label="receipt observed_at_utc"
    )
    horizon = parse_aware_utc(
        payload["decision_horizon_utc"], label="receipt decision_horizon_utc"
    )
    expected_horizon = parse_aware_utc(
        expected_decision_horizon_utc, label="expected decision_horizon_utc"
    )
    if horizon != expected_horizon:
        raise ContractError("receipt decision horizon does not match the external plan")
    require_before(observed, horizon, label="receipt observed_at_utc")
    official_date = parse_date(
        payload["official_date"], label="receipt official_date"
    ).isoformat()
    raw_relative = require_nonempty_text(
        payload["raw_relative_path"], label="raw_relative_path"
    )
    raw_path = assert_may_safe_path(
        receipt_root / raw_relative, allowed_root=receipt_root
    )
    try:
        raw_bytes = raw_path.read_bytes()
    except OSError as exc:
        raise ContractError("receipt raw object cannot be opened") from exc
    if sha256_bytes(raw_bytes) != require_sha256(
        payload["raw_sha256"], label="raw_sha256"
    ):
        raise ContractError("raw byte hash mismatch")
    try:
        projection = parser(raw_bytes)
    except Exception as exc:
        raise ContractError("receipt parser failed") from exc
    if not isinstance(projection, Mapping):
        raise ContractError("parser projection must be a mapping")
    if sha256_bytes(canonical_json_bytes(dict(projection))) != require_sha256(
        payload["projection_sha256"], label="projection_sha256"
    ):
        raise ContractError("semantic projection hash mismatch")
    identities = payload["identities"]
    if not isinstance(identities, dict):
        raise ContractError("receipt identities must be an object")
    expected_fields = set(expected_identities)
    if not _ESSENTIAL_IDENTITY_FIELDS.issubset(expected_fields):
        missing = sorted(_ESSENTIAL_IDENTITY_FIELDS - expected_fields)
        raise ContractError(
            f"expected receipt identities omit essential fields: {missing}"
        )
    if set(identities) != expected_fields:
        raise ContractError(
            "receipt identity fields do not exactly match the positive contract"
        )
    if identities.get("official_date") != official_date:
        raise ContractError(
            "receipt identity official_date does not match receipt date"
        )
    positive_int(identities.get("game_pk"), label="receipt identity game_pk")
    positive_int(identities.get("team_id"), label="receipt identity team_id")
    if identities.get("team_side") not in {"home", "away"}:
        raise ContractError("receipt identity team_side must be home or away")
    scheduled_start = parse_aware_utc(
        identities.get("scheduled_start_utc"),
        label="receipt identity scheduled_start_utc",
    )
    if horizon >= scheduled_start:
        raise ContractError("receipt decision horizon must be before scheduled start")
    if "player_id" in identities:
        positive_int(identities["player_id"], label="receipt identity player_id")
    for field, expected in expected_identities.items():
        if identities[field] != expected:
            raise ContractError(f"receipt relied-upon identity mismatch: {field}")
        if field not in projection or projection[field] != expected:
            raise ContractError(f"raw projection relied-upon field mismatch: {field}")
    return ResolvedReceipt(receipt=payload, raw_bytes=raw_bytes, projection=projection)
