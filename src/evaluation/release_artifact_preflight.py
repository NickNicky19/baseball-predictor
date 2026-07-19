"""Fail-closed release preflight for hash-bound runtime artifacts.

This checker exists because a frozen release can have a valid configuration yet
still be non-runnable if the file named by that configuration was omitted from
the release tree.  It intentionally performs no prediction or network work.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


class ReleaseArtifactPreflightError(ValueError):
    """Raised when a release lacks an artifact required by its locked config."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseArtifactPreflightError(f"{label} must be an object")
    return value


def _validate_hash_bound_file(
    *,
    root: Path,
    relative: Any,
    expected: Any,
    label: str,
) -> tuple[str, str]:
    """Resolve, contain, and hash-verify one config-declared release file."""
    if not isinstance(relative, str) or not isinstance(expected, str) or len(expected) != 64:
        raise ReleaseArtifactPreflightError(
            f"{label} requires a relative artifact path and SHA-256"
        )
    artifact = (root / relative).resolve()
    try:
        artifact.relative_to(root)
    except ValueError as exc:
        raise ReleaseArtifactPreflightError(
            f"{label} artifact path escapes the release root"
        ) from exc
    if not artifact.is_file():
        raise ReleaseArtifactPreflightError(
            f"{label} artifact does not exist in release: {relative}"
        )
    actual = _sha256(artifact)
    if actual != expected.lower():
        raise ReleaseArtifactPreflightError(
            f"{label} artifact SHA-256 differs from locked configuration"
        )
    return relative.replace("\\", "/"), actual


def validate_hash_bound_runtime_artifacts(*, repo_root: str | Path, config_path: str | Path) -> dict[str, str]:
    """Validate every fitted artifact required by the locked release config.

    A caller must bind any active fitted artifact with both its relative path
    and a SHA-256 in the exact config that the release will execute.  This is
    intentionally pre-network and pre-prediction: a missing fit is a failed
    release, never a reason to fall back silently to a legacy model.
    """
    root = Path(repo_root).resolve()
    config_file = Path(config_path).resolve()
    try:
        config = _mapping(json.loads(config_file.read_text(encoding="utf-8")), "config")
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseArtifactPreflightError(f"cannot read config: {exc}") from exc
    pa = _mapping(config.get("pa_simulator"), "pa_simulator")
    if pa.get("use_fitted_kbb") is not True:
        raise ReleaseArtifactPreflightError("preflight refuses a release without explicitly enabled fitted K/BB")
    kbb_path, kbb_hash = _validate_hash_bound_file(
        root=root,
        relative=pa.get("kbb_artifact_path"),
        expected=pa.get("kbb_artifact_sha256"),
        label="K/BB",
    )

    base_running = _mapping(config.get("base_running"), "base_running")
    pa_path, pa_hash = _validate_hash_bound_file(
        root=root,
        relative=base_running.get("pa_distribution_path"),
        expected=base_running.get("pa_distribution_sha256"),
        label="PA distribution",
    )
    return {
        "kbb_artifact_path": kbb_path,
        "kbb_artifact_sha256": kbb_hash,
        "pa_distribution_path": pa_path,
        "pa_distribution_sha256": pa_hash,
    }
