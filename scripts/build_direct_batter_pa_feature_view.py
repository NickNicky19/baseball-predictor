#!/usr/bin/env python3
"""Build immutable, label-free 2023 batter PA features and separate targets."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.direct_batter_pa_feature_view import (  # noqa: E402
    deterministic_csv_gzip,
    load_contract,
    sha256_file,
    split_panel,
)


IMPLEMENTATION_FILES = (
    "config/direct_batter_pa_feature_view_v1.json",
    "scripts/build_direct_batter_pa_feature_view.py",
    "scripts/validate_direct_batter_pa_feature_view.py",
    "src/features/direct_batter_pa_feature_view.py",
    "tests/test_direct_batter_pa_feature_view.py",
)
DEPENDENCY_FILES = ("requirements.txt", "pyproject.toml")
BASE_COMMIT = "7ad2b847e8422169955114c01dea80bcf3751661"
UPSTREAM_SOURCE_COMMIT = "c394dea5a0028b8f78e8ab78daed0d44cde5d771"
UPSTREAM_REGISTRY_PATH = "config/direct_batter_pa_foundation_v4_1_artifact_registry.json"
UPSTREAM_REGISTRY_SHA256 = "f62983689106e37e57eb6ae2374c688f00c4764f75ee1a412b0f7542648e8096"
UPSTREAM_PANEL_PATH = "data/analysis/system_integrity_v2/direct_batter_pa_foundation_v4_1/panel_2023.csv.gz"
UPSTREAM_PANEL_SHA256 = "643a4c6533dbe59fc4e6b5e5932683c0877ae0a5d982945c799976d36af1ebf6"
UPSTREAM_PANEL_BYTES = 14364814
UPSTREAM_PANEL_ROWS = 43740
UPSTREAM_MANIFEST_PATH = "data/analysis/system_integrity_v2/direct_batter_pa_foundation_v4_1/panel_manifest.json"
UPSTREAM_MANIFEST_SHA256 = "083fe961b00299f0561130e204e5264dfa05f0b234b72ce2af214ac5f681e9a4"
UPSTREAM_CERTIFICATE_PATH = "data/analysis/system_integrity_v2/direct_batter_pa_foundation_v4_1/panel_certificate.json"
UPSTREAM_CERTIFICATE_SHA256 = "aa3fd225f946b91ab1e1249eb8b7ce415f0fccabb18a041e3960552b2aeaf8ae"
CONTRACT_PATH = "config/direct_batter_pa_feature_view_v1.json"
FEATURE_OUTPUT_PATH = "data/analysis/system_integrity_v2/direct_batter_pa_feature_view_v1/features_2023.csv.gz"
TARGET_OUTPUT_PATH = "data/analysis/system_integrity_v2/direct_batter_pa_feature_view_v1/targets_2023.csv.gz"
MANIFEST_OUTPUT_PATH = "data/analysis/system_integrity_v2/direct_batter_pa_feature_view_v1/manifest.json"
CERTIFICATE_OUTPUT_PATH = "data/analysis/system_integrity_v2/direct_batter_pa_feature_view_v1/certificate.json"
REGISTRY_OUTPUT_PATH = "config/direct_batter_pa_feature_view_v1_artifact_registry.json"
RELEASE_BINDING_STATE = "UNBOUND_SOURCE_PANEL_NOT_DELIVERED"


def _has_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _assert_existing_path_safe(path: Path, *, context: str) -> Path:
    if not path.exists() or not path.is_file():
        raise ValueError(f"{context} is missing or not a regular file")
    resolved = path.resolve(strict=True)
    current = resolved
    while True:
        if _has_reparse_point(current):
            raise ValueError(f"{context} traverses a symlink, junction, or reparse point")
        if current.parent == current:
            break
        current = current.parent
    return resolved


def safe_release_path(path: Path, *, expected: str, must_exist: bool = False) -> Path:
    expected_path = ROOT / Path(expected)
    candidate = path if path.is_absolute() else ROOT / path
    root_resolved = ROOT.resolve(strict=True)
    parent = candidate.parent.resolve(strict=True) if candidate.parent.exists() else candidate.parent.parent.resolve(strict=True)
    try:
        parent.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"release path escapes canonical root: {path}") from exc
    if candidate.absolute() != expected_path.absolute():
        raise ValueError(f"release path differs from fixed logical path: {expected}")
    current = candidate.parent
    while current.exists():
        if _has_reparse_point(current):
            raise ValueError(f"release path traverses a symlink, junction, or reparse point: {current}")
        if current.resolve(strict=True) == root_resolved:
            break
        current = current.parent
    if must_exist:
        _assert_existing_path_safe(candidate, context=expected)
    return candidate


def assert_directory_entries(directory: Path, *, allowed: tuple[frozenset[str], ...], context: str) -> None:
    entries = frozenset(path.name for path in directory.iterdir()) if directory.exists() else frozenset()
    if entries not in allowed:
        raise ValueError(f"{context} contains orphan or unexpected entries: {sorted(entries)}")


def atomic_bytes(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    atomic_bytes(path, payload)


def _current_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ).stdout.strip()


def _verify_upstream(
    *, panel: Path, panel_manifest: Path, panel_certificate: Path, artifact_registry: Path,
) -> dict[str, Any]:
    # The upstream release may be supplied from an isolated immutable worktree.
    # Its logical path, source commit, and bytes are fixed below; requiring its
    # physical path to live under this candidate checkout would make a truthful
    # isolated build impossible.
    artifact_registry = _assert_existing_path_safe(
        artifact_registry, context="upstream artifact registry",
    )
    if sha256_file(artifact_registry) != UPSTREAM_REGISTRY_SHA256:
        raise ValueError("upstream registry differs from fixed release digest")
    registry = json.loads(artifact_registry.read_text(encoding="utf-8"))
    if set(registry) != {"schema_version", "status", "source_commit", "artifacts", "source_and_test_hashes", "population", "tests", "boundaries"}:
        raise ValueError("upstream artifact registry top-level schema changed")
    if registry.get("schema_version") != "direct-batter-pa-foundation-v4.1-artifact-registry-v1" or registry.get("status") != "HASH_BOUND_2023_RESEARCH_PANEL_NOT_A_MODEL_PROMOTION":
        raise ValueError("upstream artifact registry schema/status changed")
    if registry.get("source_commit") != UPSTREAM_SOURCE_COMMIT:
        raise ValueError("upstream source commit differs from fixed release identity")
    fixed = {
        "panel": (UPSTREAM_PANEL_PATH, UPSTREAM_PANEL_SHA256, UPSTREAM_PANEL_BYTES),
        "panel_manifest": (UPSTREAM_MANIFEST_PATH, UPSTREAM_MANIFEST_SHA256, 68197),
        "panel_certificate": (UPSTREAM_CERTIFICATE_PATH, UPSTREAM_CERTIFICATE_SHA256, 1176),
    }
    for name, path in (
        ("panel", panel), ("panel_manifest", panel_manifest),
        ("panel_certificate", panel_certificate),
    ):
        path = _assert_existing_path_safe(path, context=f"upstream {name}")
        declared = registry.get("artifacts", {}).get(name, {})
        logical, digest, size = fixed[name]
        if declared.get("path") != logical or declared.get("sha256") != digest or declared.get("bytes") != size:
            raise ValueError(f"upstream {name} registry entry differs from fixed release identity")
        if sha256_file(path) != digest or path.stat().st_size != size:
            raise ValueError(f"upstream {name} hash mismatch")
    source_manifest = json.loads(panel_manifest.read_text(encoding="utf-8"))
    source_certificate = json.loads(panel_certificate.read_text(encoding="utf-8"))
    if set(source_manifest) != {"betting_authorized", "build_runtime_identity", "confirmation_2025_opened", "dependency_identity", "feature_policy", "identity_files_sha256", "identity_policy", "may_2026_opened", "missing_prior_year_files", "official", "outcome_truth", "output", "population", "production_changed", "protocol", "raw_root", "raw_source_sha256", "schema_version", "script_sha256", "status", "zero_pa_evidence"}:
        raise ValueError("upstream panel manifest schema changed")
    if source_manifest.get("schema_version") != "direct-batter-pa-panel-manifest-v4" or source_manifest.get("status") != "DIRECT_BATTER_PA_TIMING_CONTRACT_PASSED_RESEARCH_ONLY":
        raise ValueError("upstream panel manifest schema/status changed")
    if source_manifest.get("output") != {"path": UPSTREAM_PANEL_PATH.replace("/", "\\"), "rows": UPSTREAM_PANEL_ROWS, "sha256": UPSTREAM_PANEL_SHA256}:
        raise ValueError("upstream manifest output identity changed")
    if source_manifest.get("may_2026_opened") is not False or source_manifest.get("confirmation_2025_opened") is not False or source_manifest.get("production_changed") is not False or source_manifest.get("betting_authorized") is not False:
        raise ValueError("upstream manifest immutable safety values changed")
    if set(source_certificate) != {"manifest", "panel", "protected_invariants", "schema_version", "status", "validation", "validator_sha256"}:
        raise ValueError("upstream panel certificate schema changed")
    if source_certificate.get("schema_version") != "direct-batter-pa-panel-certificate-v1" or source_certificate.get("status") != "DIRECT_BATTER_PA_PANEL_CERTIFIED_RESEARCH_ONLY":
        raise ValueError("upstream panel certificate schema/status changed")
    if source_certificate.get("panel", {}).get("sha256") != UPSTREAM_PANEL_SHA256 or source_certificate.get("panel", {}).get("rows") != UPSTREAM_PANEL_ROWS:
        raise ValueError("upstream certificate does not bind fixed panel identity")
    if source_certificate.get("manifest", {}).get("sha256") != UPSTREAM_MANIFEST_SHA256:
        raise ValueError("upstream certificate does not bind fixed manifest identity")
    protected = source_certificate.get("protected_invariants", {})
    if protected != {"betting_authorized": False, "confirmation_2025_opened": False, "may_2026_opened": False, "pitcher_matchup_features": False, "production_changed": False}:
        raise ValueError("upstream certificate safety values changed")
    return registry


def _environment_observation() -> dict[str, Any]:
    versions: dict[str, str] = {}
    for name in ("numpy", "pandas"):
        versions[name] = importlib.metadata.version(name)
    return {
        "exact_environment_lock_claimed": False,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable_sha256": sha256_file(Path(sys.executable)),
        "platform": platform.platform(),
        "distributions_used": versions,
        "limitation": "requirements.txt contains minimum versions; this observation binds the build environment but is not a reproducible exact dependency lock",
    }


def build(
    *, panel: Path, panel_manifest: Path, panel_certificate: Path,
    artifact_registry: Path, contract_path: Path, feature_output: Path,
    target_output: Path, output_manifest: Path, repository_base_commit: str,
) -> dict[str, Any]:
    if repository_base_commit != BASE_COMMIT or _current_commit() != BASE_COMMIT:
        raise ValueError("repository base commit differs from the predeclared implementation base")
    contract_path = safe_release_path(contract_path, expected=CONTRACT_PATH, must_exist=True)
    artifact_registry = _assert_existing_path_safe(
        artifact_registry, context="upstream artifact registry",
    )
    feature_output = safe_release_path(feature_output, expected=FEATURE_OUTPUT_PATH)
    target_output = safe_release_path(target_output, expected=TARGET_OUTPUT_PATH)
    output_manifest = safe_release_path(output_manifest, expected=MANIFEST_OUTPUT_PATH)
    output_parent = feature_output.parent
    assert_directory_entries(
        output_parent, allowed=(frozenset(),), context="feature-view output directory",
    )
    upstream = _verify_upstream(
        panel=panel, panel_manifest=panel_manifest, panel_certificate=panel_certificate,
        artifact_registry=artifact_registry,
    )
    contract = load_contract(contract_path)
    source = pd.read_csv(panel, low_memory=False)
    feature_view, targets = split_panel(source, contract)
    feature_payload = deterministic_csv_gzip(feature_view)
    target_payload = deterministic_csv_gzip(targets)
    atomic_bytes(feature_output, feature_payload)
    try:
        atomic_bytes(target_output, target_payload)
    except Exception:
        feature_output.unlink(missing_ok=True)
        raise
    implementation_hashes = {path: sha256_file(ROOT / path) for path in IMPLEMENTATION_FILES}
    dependency_hashes = {path: sha256_file(ROOT / path) for path in DEPENDENCY_FILES}
    manifest = {
        "schema_version": "direct-batter-pa-feature-view-manifest-v1",
        "status": "LOCAL_UNBOUND_LABEL_FREE_2023_FEATURE_VIEW_RESEARCH_ONLY",
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
        "implementation_source_base_commit": repository_base_commit,
        "upstream": {
            "registry_path": UPSTREAM_REGISTRY_PATH,
            "registry_sha256": sha256_file(artifact_registry),
            "foundation_source_commit": upstream["source_commit"],
            "panel_logical_path": upstream["artifacts"]["panel"]["path"],
            "panel_sha256": sha256_file(panel),
            "panel_manifest_sha256": sha256_file(panel_manifest),
            "panel_certificate_sha256": sha256_file(panel_certificate),
        },
        "contract": {"path": CONTRACT_PATH, "sha256": sha256_file(contract_path)},
        "implementation_sha256": implementation_hashes,
        "dependency_identity": {
            "declaration_sha256": dependency_hashes,
            "build_environment_observation": _environment_observation(),
        },
        "outputs": {
            "features": {
                "path": FEATURE_OUTPUT_PATH, "sha256": hashlib.sha256(feature_payload).hexdigest(),
                "bytes": len(feature_payload), "rows": len(feature_view),
                "columns": list(feature_view.columns),
            },
            "targets": {
                "path": TARGET_OUTPUT_PATH, "sha256": hashlib.sha256(target_payload).hexdigest(),
                "bytes": len(target_payload), "rows": len(targets),
                "columns": list(targets.columns),
            },
        },
        "population": {
            "physical_rows_2023": len(feature_view),
            "fit_eligible_positive_pa_rows": int(targets[contract["target_columns"]].sum(axis=1).gt(0).sum()),
            "explicit_zero_pa_rows": int(targets[contract["target_columns"]].sum(axis=1).eq(0).sum()),
            "identity_duplicates": 0,
            "feature_columns": len(contract["feature_columns"]),
            "lineage_columns": len(contract["lineage_columns"]),
        },
        "separation": {
            "join_keys": contract["identity_columns"],
            "feature_artifact_contains_labels": False,
            "feature_artifact_contains_actual_lineup_slot": False,
            "feature_artifact_contains_target_day_context": False,
            "target_artifact_contains_features": False,
        },
        "limitations": contract["limitations"],
        "full_game_boundary": contract["full_game_boundary"],
        "release_binding": {
            "state": RELEASE_BINDING_STATE,
            "expected_external_release_digest": None,
            "source_panel_present_in_release_root": False,
            "release_certifiable": False,
            "reason": "the immutable upstream source panel is not delivered inside this worktree/release",
        },
    }
    try:
        atomic_json(output_manifest, manifest)
    except Exception:
        feature_output.unlink(missing_ok=True)
        target_output.unlink(missing_ok=True)
        raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--panel-manifest", type=Path, required=True)
    parser.add_argument("--panel-certificate", type=Path, required=True)
    parser.add_argument("--artifact-registry", type=Path, required=True)
    parser.add_argument("--contract", dest="contract_path", type=Path, required=True)
    parser.add_argument("--feature-output", type=Path, required=True)
    parser.add_argument("--target-output", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--repository-base-commit", required=True)
    result = build(**vars(parser.parse_args()))
    print(json.dumps({"status": result["status"], "population": result["population"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
