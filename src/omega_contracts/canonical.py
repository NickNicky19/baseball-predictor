"""Canonical serialization and digest helpers."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .errors import ContractError


def strict_json_object_bytes(value: bytes, *, label: str) -> dict[str, Any]:
    """Parse one strict UTF-8 JSON object and reject duplicate keys/constants."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ContractError(f"{label} has duplicate JSON key: {key}")
            result[key] = item
        return result

    def reject_constant(token: str) -> None:
        raise ContractError(f"{label} contains non-finite JSON number: {token}")

    try:
        text = value.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise ContractError(f"{label} must be a JSON object")
    return parsed


def canonical_json_bytes(value: object) -> bytes:
    """Return deterministic UTF-8 JSON bytes after rejecting non-finite floats."""
    _reject_nonfinite(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ContractError(f"{label} must be a lowercase SHA-256 hex digest")
    if value.lower() != value or any(ch not in "0123456789abcdef" for ch in value):
        raise ContractError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def require_nonempty_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractError(f"{label} must be a non-empty, trimmed string")
    return value


def _reject_nonfinite(value: Any, *, path: str = "$") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError(f"non-finite number at {path}")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_nonfinite(item, path=f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for index, item in enumerate(value):
            _reject_nonfinite(item, path=f"{path}[{index}]")
        return
    raise ContractError(
        f"unsupported canonical JSON value at {path}: {type(value).__name__}"
    )
