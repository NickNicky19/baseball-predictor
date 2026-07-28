"""Fail closed when the repaired 2023 panel dependency closure drifts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "direct-batter-pa-foundation-v4.1-integration-closure-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(root: Path, relative_path: str) -> Path:
    if not relative_path or Path(relative_path).is_absolute():
        raise ValueError(f"closure path must be nonempty and relative: {relative_path!r}")
    unresolved = root / relative_path
    if unresolved.is_symlink():
        raise ValueError(f"closure path must not be a symlink: {relative_path}")
    candidate = unresolved.resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"closure path escapes repository root: {relative_path}") from exc
    if not candidate.is_file():
        raise ValueError(f"closure path is not a regular file: {relative_path}")
    return candidate


def validate_integration_closure(
    root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    manifest_path = manifest_path.resolve(strict=True)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported integration-closure schema")
    if payload.get("status") != "RESEARCH_ONLY_DEPENDENCY_CLOSURE_NOT_A_MODEL_PROMOTION":
        raise ValueError("integration-closure status is not research-only")

    required = payload.get("required_files")
    if not isinstance(required, list) or not required:
        raise ValueError("integration closure has no required files")
    paths = [item.get("path") for item in required if isinstance(item, dict)]
    if len(paths) != len(required) or len(paths) != len(set(paths)):
        raise ValueError("integration closure has malformed or duplicate paths")

    verified: list[dict[str, Any]] = []
    for item in required:
        expected = str(item.get("sha256") or "")
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise ValueError(f"invalid expected SHA-256 for {item.get('path')}")
        candidate = _safe_file(root, str(item["path"]))
        actual = _sha256(candidate)
        if actual != expected:
            raise ValueError(
                f"integration dependency hash mismatch: {item['path']} expected={expected} actual={actual}"
            )
        verified.append({"path": item["path"], "sha256": actual, "role": item.get("role")})

    artifact = payload.get("artifact_registry")
    if not isinstance(artifact, dict):
        raise ValueError("artifact registry binding is missing")
    artifact_path = _safe_file(root, str(artifact.get("path") or ""))
    artifact_actual = _sha256(artifact_path)
    if artifact_actual != artifact.get("sha256"):
        raise ValueError("artifact registry binding hash mismatch")

    boundaries = payload.get("boundaries")
    expected_boundaries = {
        "development_years": [2023],
        "selection_or_confirmation_opened": False,
        "may_2026_opened": False,
        "production_changed": False,
        "betting_authorized": False,
    }
    if boundaries != expected_boundaries:
        raise ValueError("research boundary declaration changed")

    return {
        "status": "VALID_RESEARCH_ONLY_INTEGRATION_CLOSURE",
        "verified_files": verified,
        "artifact_registry_sha256": artifact_actual,
        "boundaries": boundaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        default="config/direct_batter_pa_foundation_v4_1_integration_closure.json",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = validate_integration_closure(root, Path(args.manifest))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
