#!/usr/bin/env python3
"""Validate and certify the immutable direct-batter PA feature/target split."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_direct_batter_pa_feature_view import (  # noqa: E402
    BASE_COMMIT,
    CERTIFICATE_OUTPUT_PATH,
    CONTRACT_PATH,
    DEPENDENCY_FILES,
    FEATURE_OUTPUT_PATH,
    IMPLEMENTATION_FILES,
    MANIFEST_OUTPUT_PATH,
    REGISTRY_OUTPUT_PATH,
    RELEASE_BINDING_STATE,
    TARGET_OUTPUT_PATH,
    UPSTREAM_CERTIFICATE_SHA256,
    UPSTREAM_MANIFEST_SHA256,
    UPSTREAM_PANEL_PATH,
    UPSTREAM_PANEL_SHA256,
    UPSTREAM_REGISTRY_PATH,
    UPSTREAM_REGISTRY_SHA256,
    UPSTREAM_SOURCE_COMMIT,
    atomic_json,
    assert_directory_entries,
    safe_release_path,
)
from src.features.direct_batter_pa_feature_view import (  # noqa: E402
    IDENTITY,
    LINEAGE,
    TARGETS,
    load_contract,
    sha256_file,
    validate_feature_frame,
    validate_target_frame,
)


MANIFEST_KEYS = {"schema_version", "status", "research_only", "betting_authorized", "production_changed", "implementation_source_base_commit", "upstream", "contract", "implementation_sha256", "dependency_identity", "outputs", "population", "separation", "limitations", "full_game_boundary", "release_binding"}
CERTIFICATE_KEYS = {"schema_version", "status", "manifest", "features", "targets", "validation", "limitations", "full_game_boundary", "release_binding", "implementation_sha256", "dependency_identity", "protected_invariants"}
REGISTRY_KEYS = {"schema_version", "status", "implementation_source_base_commit", "artifacts", "contract", "implementation_sha256", "dependency_identity", "release_binding", "limitations", "full_game_boundary", "research_only", "model_fitted", "betting_authorized"}


def _exact_sha_map(value: Any, *, expected_paths: tuple[str, ...], context: str) -> dict[str, str]:
    if not isinstance(value, dict) or tuple(value) != tuple(sorted(expected_paths)):
        raise ValueError(f"{context} map is incomplete or has unexpected entries")
    if any(not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest) for digest in value.values()):
        raise ValueError(f"{context} map contains an invalid SHA-256")
    return value


def validate_manifest_envelope(manifest: dict[str, Any], contract: dict[str, Any]) -> None:
    if set(manifest) != MANIFEST_KEYS:
        raise ValueError("feature-view manifest top-level schema changed")
    if manifest.get("schema_version") != "direct-batter-pa-feature-view-manifest-v1" or manifest.get("status") != "LOCAL_UNBOUND_LABEL_FREE_2023_FEATURE_VIEW_RESEARCH_ONLY":
        raise ValueError("feature-view manifest schema/status changed")
    if manifest.get("research_only") is not True or manifest.get("betting_authorized") is not False or manifest.get("production_changed") is not False:
        raise ValueError("feature-view manifest immutable safety values changed")
    if manifest.get("implementation_source_base_commit") != BASE_COMMIT:
        raise ValueError("feature-view base commit identity changed")
    expected_binding = {
        "state": RELEASE_BINDING_STATE, "expected_external_release_digest": None,
        "source_panel_present_in_release_root": False, "release_certifiable": False,
        "reason": "the immutable upstream source panel is not delivered inside this worktree/release",
    }
    if manifest.get("release_binding") != expected_binding:
        raise ValueError("unbound release safety state changed")
    expected_upstream = {
        "registry_path": UPSTREAM_REGISTRY_PATH, "registry_sha256": UPSTREAM_REGISTRY_SHA256,
        "foundation_source_commit": UPSTREAM_SOURCE_COMMIT, "panel_logical_path": UPSTREAM_PANEL_PATH,
        "panel_sha256": UPSTREAM_PANEL_SHA256, "panel_manifest_sha256": UPSTREAM_MANIFEST_SHA256,
        "panel_certificate_sha256": UPSTREAM_CERTIFICATE_SHA256,
    }
    if manifest.get("upstream") != expected_upstream:
        raise ValueError("upstream fixed identity binding changed")
    if manifest.get("contract") != {"path": CONTRACT_PATH, "sha256": feature_boundary_contract_sha()}:
        raise ValueError("feature-view contract identity changed")
    _exact_sha_map(manifest.get("implementation_sha256"), expected_paths=IMPLEMENTATION_FILES, context="implementation identity")
    dependency = manifest.get("dependency_identity")
    if not isinstance(dependency, dict) or set(dependency) != {"declaration_sha256", "build_environment_observation"}:
        raise ValueError("dependency identity schema changed")
    _exact_sha_map(dependency.get("declaration_sha256"), expected_paths=DEPENDENCY_FILES, context="dependency declaration")
    observation = dependency.get("build_environment_observation")
    if not isinstance(observation, dict) or set(observation) != {"exact_environment_lock_claimed", "python_version", "python_implementation", "python_executable_sha256", "platform", "distributions_used", "limitation"} or observation.get("exact_environment_lock_claimed") is not False:
        raise ValueError("dependency observation schema/lock limitation changed")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != {"features", "targets"}:
        raise ValueError("output map schema changed")
    for name, logical in (("features", FEATURE_OUTPUT_PATH), ("targets", TARGET_OUTPUT_PATH)):
        entry = outputs.get(name)
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes", "rows", "columns"} or entry.get("path") != logical:
            raise ValueError(f"{name} output schema/path changed")
    if manifest.get("population") != {"physical_rows_2023": 43740, "fit_eligible_positive_pa_rows": 43726, "explicit_zero_pa_rows": 14, "identity_duplicates": 0, "feature_columns": 90, "lineage_columns": 1}:
        raise ValueError("feature-view population identity changed")
    if manifest.get("separation") != {"join_keys": IDENTITY, "feature_artifact_contains_labels": False, "feature_artifact_contains_actual_lineup_slot": False, "feature_artifact_contains_target_day_context": False, "target_artifact_contains_features": False}:
        raise ValueError("feature/target separation safety values changed")
    if manifest.get("limitations") != contract["limitations"] or manifest.get("full_game_boundary") != contract["full_game_boundary"]:
        raise ValueError("declared limitations or full-game blocker changed")


def feature_boundary_contract_sha() -> str:
    return "54355ea512df8d92ef16c68430079cfa54c88ed6315d017017527ff30a0da20c"


def validate(
    *, features: Path, targets: Path, manifest_path: Path, contract_path: Path,
) -> dict[str, Any]:
    features = safe_release_path(features, expected=FEATURE_OUTPUT_PATH, must_exist=True)
    targets = safe_release_path(targets, expected=TARGET_OUTPUT_PATH, must_exist=True)
    manifest_path = safe_release_path(manifest_path, expected=MANIFEST_OUTPUT_PATH, must_exist=True)
    contract_path = safe_release_path(contract_path, expected=CONTRACT_PATH, must_exist=True)
    assert_directory_entries(
        features.parent,
        allowed=(
            frozenset({Path(FEATURE_OUTPUT_PATH).name, Path(TARGET_OUTPUT_PATH).name, Path(MANIFEST_OUTPUT_PATH).name}),
            frozenset({Path(FEATURE_OUTPUT_PATH).name, Path(TARGET_OUTPUT_PATH).name, Path(MANIFEST_OUTPUT_PATH).name, Path(CERTIFICATE_OUTPUT_PATH).name}),
        ),
        context="feature-view artifact directory",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(manifest) != {"schema_version", "status", "research_only", "betting_authorized", "production_changed", "implementation_source_base_commit", "upstream", "contract", "implementation_sha256", "dependency_identity", "outputs", "population", "separation", "limitations", "full_game_boundary", "release_binding"}:
        raise ValueError("feature-view manifest top-level schema changed")
    if manifest.get("schema_version") != "direct-batter-pa-feature-view-manifest-v1" or manifest.get("status") != "LOCAL_UNBOUND_LABEL_FREE_2023_FEATURE_VIEW_RESEARCH_ONLY":
        raise ValueError("feature-view manifest schema/status changed")
    if manifest.get("research_only") is not True or manifest.get("betting_authorized") is not False or manifest.get("production_changed") is not False:
        raise ValueError("feature-view manifest immutable safety values changed")
    if manifest.get("implementation_source_base_commit") != BASE_COMMIT:
        raise ValueError("feature-view base commit identity changed")
    binding = manifest.get("release_binding")
    if binding != {
        "state": RELEASE_BINDING_STATE,
        "expected_external_release_digest": None,
        "source_panel_present_in_release_root": False,
        "release_certifiable": False,
        "reason": "the immutable upstream source panel is not delivered inside this worktree/release",
    }:
        raise ValueError("unbound release safety state changed")
    if (ROOT / UPSTREAM_PANEL_PATH).exists():
        raise ValueError("release claims source panel absent but a local release entry exists")
    contract = load_contract(contract_path)
    validate_manifest_envelope(manifest, contract)
    if manifest.get("contract") != {"path": CONTRACT_PATH, "sha256": sha256_file(contract_path)}:
        raise ValueError("feature-view contract hash mismatch")
    upstream = manifest.get("upstream", {})
    expected_upstream = {
        "registry_path": UPSTREAM_REGISTRY_PATH,
        "registry_sha256": UPSTREAM_REGISTRY_SHA256,
        "foundation_source_commit": UPSTREAM_SOURCE_COMMIT,
        "panel_logical_path": UPSTREAM_PANEL_PATH,
        "panel_sha256": UPSTREAM_PANEL_SHA256,
        "panel_manifest_sha256": UPSTREAM_MANIFEST_SHA256,
        "panel_certificate_sha256": UPSTREAM_CERTIFICATE_SHA256,
    }
    if upstream != expected_upstream:
        raise ValueError("upstream fixed identity binding changed")
    upstream_registry_path = safe_release_path(Path(UPSTREAM_REGISTRY_PATH), expected=UPSTREAM_REGISTRY_PATH, must_exist=True)
    if sha256_file(upstream_registry_path) != UPSTREAM_REGISTRY_SHA256:
        raise ValueError("upstream artifact registry differs from fixed digest")
    implementation = manifest.get("implementation_sha256")
    if not isinstance(implementation, dict) or tuple(implementation) != tuple(sorted(IMPLEMENTATION_FILES)):
        raise ValueError("implementation identity map is incomplete or has unexpected entries")
    for relative in IMPLEMENTATION_FILES:
        path = safe_release_path(Path(relative), expected=relative, must_exist=True)
        if sha256_file(path) != implementation[relative]:
            raise ValueError(f"implementation hash mismatch: {relative}")
    declarations = manifest.get("dependency_identity", {}).get("declaration_sha256", {})
    if tuple(declarations) != tuple(sorted(DEPENDENCY_FILES)) or manifest["dependency_identity"]["build_environment_observation"].get(
        "exact_environment_lock_claimed"
    ) is not False:
        raise ValueError("dependency map is incomplete or falsely claims an exact lock")
    for relative in DEPENDENCY_FILES:
        path = safe_release_path(Path(relative), expected=relative, must_exist=True)
        if sha256_file(path) != declarations[relative]:
            raise ValueError(f"dependency declaration hash mismatch: {relative}")
    for name, path in (("features", features), ("targets", targets)):
        declared = manifest.get("outputs", {}).get(name, {})
        expected_path = FEATURE_OUTPUT_PATH if name == "features" else TARGET_OUTPUT_PATH
        if declared.get("path") != expected_path or sha256_file(path) != declared.get("sha256"):
            raise ValueError(f"{name} artifact hash mismatch")
        if path.stat().st_size != declared.get("bytes"):
            raise ValueError(f"{name} artifact byte size mismatch")
    feature_frame = pd.read_csv(features, low_memory=False)
    target_frame = pd.read_csv(targets, low_memory=False)
    if list(feature_frame.columns) != [*IDENTITY, *LINEAGE, *contract["feature_columns"]]:
        raise ValueError("feature artifact schema/order differs from positive allowlist")
    validate_feature_frame(feature_frame, contract)
    target_frame = validate_target_frame(target_frame, feature_identity=feature_frame[IDENTITY])
    target_values = target_frame[TARGETS]
    if list(feature_frame.columns) != manifest["outputs"]["features"]["columns"]:
        raise ValueError("manifest feature schema differs from artifact")
    if list(target_frame.columns) != manifest["outputs"]["targets"]["columns"]:
        raise ValueError("manifest target schema differs from artifact")
    if len(feature_frame) != manifest["outputs"]["features"]["rows"]:
        raise ValueError("manifest feature row count differs from artifact")
    if len(target_frame) != manifest["outputs"]["targets"]["rows"]:
        raise ValueError("manifest target row count differs from artifact")
    if any(column.startswith(("out_", "target_", "lineup_")) for column in feature_frame.columns):
        raise ValueError("label or actual target-day lineup context entered feature artifact")
    return {
        "schema_version": "direct-batter-pa-feature-view-certificate-v1",
        "status": "LOCAL_VALIDATION_PASSED_RELEASE_UNBOUND_NOT_CERTIFIABLE",
        "manifest": {"path": MANIFEST_OUTPUT_PATH, "sha256": sha256_file(manifest_path)},
        "features": {"path": FEATURE_OUTPUT_PATH, "sha256": sha256_file(features), "rows": len(feature_frame)},
        "targets": {"path": TARGET_OUTPUT_PATH, "sha256": sha256_file(targets), "rows": len(target_frame)},
        "validation": {
            "positive_schema_exact": True,
            "labels_physically_separate": True,
            "identity_join_only": IDENTITY,
            "chronology_violations": 0,
            "identity_duplicates": 0,
            "doubleheader_prior_date_contract": True,
            "fit_eligible_positive_pa_rows": int(target_values.sum(axis=1).gt(0).sum()),
            "explicit_zero_pa_rows": int(target_values.sum(axis=1).eq(0).sum()),
        },
        "limitations": contract["limitations"],
        "full_game_boundary": contract["full_game_boundary"],
        "release_binding": binding,
        "implementation_sha256": implementation,
        "dependency_identity": manifest["dependency_identity"],
        "protected_invariants": {
            "selection_2024_opened": False,
            "spent_confirmation_2025_opened": False,
            "may_2026_opened": False,
            "economic_evidence_opened": False,
            "collectors_touched": False,
            "model_fitted": False,
            "betting_authorized": False,
        },
    }


def certify(
    *, features: Path, targets: Path, manifest_path: Path, contract_path: Path,
    certificate: Path, registry: Path,
) -> dict[str, Any]:
    certificate = safe_release_path(certificate, expected=CERTIFICATE_OUTPUT_PATH)
    registry = safe_release_path(registry, expected=REGISTRY_OUTPUT_PATH)
    result = validate(
        features=features, targets=targets, manifest_path=manifest_path,
        contract_path=contract_path,
    )
    atomic_json(certificate, result)
    try:
        registration = {
            "schema_version": "direct-batter-pa-feature-view-v1-artifact-registry-v1",
            "status": "LOCAL_HASH_BOUND_RESEARCH_INPUT_RELEASE_UNBOUND_NOT_CERTIFIABLE",
            "implementation_source_base_commit": json.loads(
                manifest_path.read_text(encoding="utf-8")
            )["implementation_source_base_commit"],
            "artifacts": {
                "features": {"path": FEATURE_OUTPUT_PATH, "sha256": sha256_file(features), "bytes": features.stat().st_size},
                "targets": {"path": TARGET_OUTPUT_PATH, "sha256": sha256_file(targets), "bytes": targets.stat().st_size},
                "manifest": {"path": MANIFEST_OUTPUT_PATH, "sha256": sha256_file(manifest_path), "bytes": manifest_path.stat().st_size},
                "certificate": {"path": CERTIFICATE_OUTPUT_PATH, "sha256": sha256_file(certificate), "bytes": certificate.stat().st_size},
            },
            "contract": {"path": CONTRACT_PATH, "sha256": sha256_file(contract_path)},
            "implementation_sha256": result["implementation_sha256"],
            "dependency_identity": result["dependency_identity"],
            "release_binding": result["release_binding"],
            "limitations": result["limitations"],
            "full_game_boundary": result["full_game_boundary"],
            "research_only": True,
            "model_fitted": False,
            "betting_authorized": False,
        }
        atomic_json(registry, registration)
        validate_written_package(certificate=certificate, registry=registry)
    except Exception:
        certificate.unlink(missing_ok=True)
        registry.unlink(missing_ok=True)
        raise
    return result


def validate_written_package(*, certificate: Path, registry: Path) -> None:
    certificate = safe_release_path(certificate, expected=CERTIFICATE_OUTPUT_PATH, must_exist=True)
    registry = safe_release_path(registry, expected=REGISTRY_OUTPUT_PATH, must_exist=True)
    assert_directory_entries(
        certificate.parent,
        allowed=(frozenset({
            Path(FEATURE_OUTPUT_PATH).name, Path(TARGET_OUTPUT_PATH).name,
            Path(MANIFEST_OUTPUT_PATH).name, Path(CERTIFICATE_OUTPUT_PATH).name,
        }),),
        context="certified artifact directory",
    )
    cert = json.loads(certificate.read_text(encoding="utf-8"))
    registered = json.loads(registry.read_text(encoding="utf-8"))
    validate_package_document_envelopes(cert, registered)
    if set(cert) != CERTIFICATE_KEYS:
        raise ValueError("certificate schema has missing or unexpected entries")
    if cert.get("schema_version") != "direct-batter-pa-feature-view-certificate-v1" or cert.get("status") != "LOCAL_VALIDATION_PASSED_RELEASE_UNBOUND_NOT_CERTIFIABLE":
        raise ValueError("certificate schema/status changed")
    protected = cert.get("protected_invariants")
    if protected != {"selection_2024_opened": False, "spent_confirmation_2025_opened": False, "may_2026_opened": False, "economic_evidence_opened": False, "collectors_touched": False, "model_fitted": False, "betting_authorized": False}:
        raise ValueError("certificate immutable safety values changed")
    if set(registered) != REGISTRY_KEYS:
        raise ValueError("artifact registry schema has missing or unexpected entries")
    if registered.get("schema_version") != "direct-batter-pa-feature-view-v1-artifact-registry-v1" or registered.get("status") != "LOCAL_HASH_BOUND_RESEARCH_INPUT_RELEASE_UNBOUND_NOT_CERTIFIABLE":
        raise ValueError("artifact registry schema/status changed")
    if registered.get("research_only") is not True or registered.get("model_fitted") is not False or registered.get("betting_authorized") is not False:
        raise ValueError("artifact registry immutable safety values changed")
    if registered.get("release_binding") != cert.get("release_binding"):
        raise ValueError("certificate and registry release binding disagree")
    if tuple(registered.get("implementation_sha256", {})) != tuple(sorted(IMPLEMENTATION_FILES)):
        raise ValueError("artifact registry implementation map changed")
    if tuple(registered.get("dependency_identity", {}).get("declaration_sha256", {})) != tuple(sorted(DEPENDENCY_FILES)):
        raise ValueError("artifact registry dependency map changed")


def validate_package_document_envelopes(cert: dict[str, Any], registered: dict[str, Any]) -> None:
    if set(cert) != CERTIFICATE_KEYS:
        raise ValueError("certificate schema has missing or unexpected entries")
    if cert.get("schema_version") != "direct-batter-pa-feature-view-certificate-v1" or cert.get("status") != "LOCAL_VALIDATION_PASSED_RELEASE_UNBOUND_NOT_CERTIFIABLE":
        raise ValueError("certificate schema/status changed")
    _exact_sha_map(cert.get("implementation_sha256"), expected_paths=IMPLEMENTATION_FILES, context="certificate implementation")
    dependency = cert.get("dependency_identity", {})
    _exact_sha_map(dependency.get("declaration_sha256"), expected_paths=DEPENDENCY_FILES, context="certificate dependency")
    if set(registered) != REGISTRY_KEYS:
        raise ValueError("artifact registry schema has missing or unexpected entries")
    if registered.get("schema_version") != "direct-batter-pa-feature-view-v1-artifact-registry-v1" or registered.get("status") != "LOCAL_HASH_BOUND_RESEARCH_INPUT_RELEASE_UNBOUND_NOT_CERTIFIABLE":
        raise ValueError("artifact registry schema/status changed")
    _exact_sha_map(registered.get("implementation_sha256"), expected_paths=IMPLEMENTATION_FILES, context="registry implementation")
    registry_dependency = registered.get("dependency_identity", {})
    _exact_sha_map(registry_dependency.get("declaration_sha256"), expected_paths=DEPENDENCY_FILES, context="registry dependency")
    if registered.get("release_binding") != cert.get("release_binding"):
        raise ValueError("certificate and registry release binding disagree")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--manifest", dest="manifest_path", type=Path, required=True)
    parser.add_argument("--contract", dest="contract_path", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    result = certify(**vars(parser.parse_args()))
    print(json.dumps({"status": result["status"], "validation": result["validation"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
