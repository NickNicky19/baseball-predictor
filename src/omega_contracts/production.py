"""Verification that the candidate did not mutate frozen production boundaries."""

from __future__ import annotations

import re
from pathlib import Path

from .activation import assert_research_only_manifest
from .canonical import require_sha256, sha256_file, strict_json_object_bytes
from .chronology import assert_may_safe_path
from .errors import ContractError


def verify_candidate_and_production_boundaries(repository_root: Path) -> dict[str, int]:
    root = repository_root.resolve(strict=True)
    candidate_path = assert_may_safe_path(root / "config/omega_candidate_v0.json", allowed_root=root)
    boundary_path = assert_may_safe_path(
        root / "config/omega_production_boundary_v1.json", allowed_root=root
    )
    candidate = _load_object(candidate_path)
    assert_research_only_manifest(candidate)
    for field in ("fits_model", "changes_production_probabilities", "collector_changes_permitted"):
        if candidate.get(field) is not False:
            raise ContractError(f"candidate must declare {field}=false")
    if candidate.get("may_2026_permitted") is not False:
        raise ContractError("candidate must declare May 2026 prohibited")
    boundary = _load_object(boundary_path)
    if set(boundary) != {"schema_version", "canonical_base_commit", "files"}:
        raise ContractError("production boundary manifest has unexpected fields")
    commit = boundary["canonical_base_commit"]
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ContractError("canonical base commit must be an exact lowercase 40-hex Git commit")
    files = boundary["files"]
    if not isinstance(files, dict) or not files:
        raise ContractError("production boundary file map must be non-empty")
    for relative, expected in files.items():
        if not isinstance(relative, str):
            raise ContractError("production boundary paths must be strings")
        expected_hash = require_sha256(expected, label=f"boundary hash for {relative}")
        path = assert_may_safe_path(root / relative, allowed_root=root)
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise ContractError(f"frozen production boundary changed: {relative}")
    return {"verified_files": len(files)}


def _load_object(path: Path) -> dict[str, object]:
    try:
        value = strict_json_object_bytes(path.read_bytes(), label=path.name)
    except OSError as exc:
        raise ContractError(f"invalid JSON boundary file: {path.name}") from exc
    return value
