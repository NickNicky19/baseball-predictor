"""Append-only governance for shared-PA research experiments.

This module records metadata and byte identities only.  It never fits a model,
opens an outcome, evaluates a market, or reads a price.  Its purpose is to make
candidate predeclaration, repeated-attempt accounting, and selection-window
spend executable rather than documentary.

The local hash chain is tamper-evident, not an independent trust root.  An
authoritative replay must compare the returned checkpoint with a checkpoint
anchored outside the registry directory; otherwise deletion of a complete
suffix (entries, checkpoints, and HEAD together) cannot be detected locally.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence


POLICY_SCHEMA = "shared-pa-experiment-registry-policy-v1"
ENTRY_SCHEMA = "shared-pa-experiment-registry-entry-v1"
CHECKPOINT_SCHEMA = "shared-pa-experiment-registry-checkpoint-v1"
HEAD_SCHEMA = "shared-pa-experiment-registry-head-v1"
IDENTITY_SCHEMA = "shared-pa-experiment-registry-identity-v1"
TRUSTED_CHECKPOINT_SCHEMA = "shared-pa-experiment-registry-trusted-checkpoint-v1"
RELEASE_EXPECTATION_SCHEMA = "shared-pa-experiment-registry-release-expectation-v1"
APPEND_RECEIPT_SCHEMA = "shared-pa-experiment-registry-append-receipt-v1"
ZERO_HASH = "0" * 64
ROOT_PARENT = "frozen_shared_pa_foundation"
MARKETS = ("hits", "hr_over_0_5", "total_bases")
CANONICAL_EVIDENCE_WINDOWS = ("development_2023_only",)
UNBOUND_AUTHORITY = "UNBOUND_EXTERNAL_TRUST_REQUIRED"
BOUND_AUTHORITY = "BOUND_EXTERNAL_TRUST_VERIFIED"
EVALUATOR_STATE = "IMPLEMENTED_SYNTHETIC_ONLY_EXTERNALLY_UNBOUND"
EVALUATOR_EVIDENCE_CLASS = "SYNTHETIC_STRUCTURAL_MECHANICS_ONLY"
EVALUATOR_SOURCE_BASE_COMMIT = "9f86f6a34fc71151b76debf48059829aa4fa91da"
EVALUATOR_MANIFEST_PATHS = frozenset({
    "config/shared_pa_market_evaluation_contract_v1.json",
    "config/schemas/shared_pa_market_evaluation_contract_v1.schema.json",
    "config/schemas/shared_pa_market_evaluation_row_v1.schema.json",
    "config/schemas/shared_pa_market_evaluation_authority_v1.schema.json",
    "config/schemas/shared_pa_market_evaluation_report_v1.schema.json",
    "src/evaluation/shared_pa_market_evaluator.py",
    "tests/test_shared_pa_market_evaluator.py",
    "docs/research/SHARED_PA_MARKET_EVALUATION_ENGINE_V1.md",
    "scripts/check_shared_pa_market_evaluator_offline.py",
    "requirements-prospective-batter-opportunity-ci.lock",
})
ENTRY_TYPES = {
    "EXPERIMENT_PREDECLARED",
    "CANDIDATE_FROZEN",
    "SELECTION_TOKEN_ISSUED",
    "SELECTION_TOKEN_CONSUMED",
    "TERMINAL_DISPOSITION",
}
TERMINAL_DISPOSITIONS = {
    "REJECTED_INTEGRITY",
    "REJECTED_DEVELOPMENT",
    "REJECTED_SELECTION",
    "REJECTED_PROSPECTIVE",
    "RETAINED_RESEARCH_ONLY",
}
PROTECTED_BOUNDARIES = {
    "may_2026_sealed": True,
    "spent_2025_hr_confirmation_reusable": False,
    "historical_prices_executable": False,
    "missed_prospective_backfill_allowed": False,
    "market_pooling_allowed": False,
    "cross_market_rescue_allowed": False,
    "frozen_baselines_mutable": False,
    "betting_authorized": False,
}


class SharedPAExperimentRegistryError(ValueError):
    """The registry or a requested transition violated a locked boundary."""


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise SharedPAExperimentRegistryError("value is not canonical JSON") from exc


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SharedPAExperimentRegistryError(f"{label} must be a lowercase SHA-256")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SharedPAExperimentRegistryError(f"{label} must be a canonical nonempty string")
    return value


def _utc(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SharedPAExperimentRegistryError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SharedPAExperimentRegistryError(f"{label} is invalid") from exc
    if parsed.tzinfo != timezone.utc or parsed.microsecond != 0:
        raise SharedPAExperimentRegistryError(f"{label} must be second-resolution UTC")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise SharedPAExperimentRegistryError(f"{label} is not canonical UTC")
    return value


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SharedPAExperimentRegistryError(f"cannot inspect path identity: {path}") from exc
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


def _assert_no_links(path: Path, *, include_leaf: bool = True) -> None:
    absolute = path.absolute()
    parts = absolute.parts if include_leaf else absolute.parent.parts
    if not parts:
        raise SharedPAExperimentRegistryError("path has no inspectable components")
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        if current.exists() and _is_link_or_reparse(current):
            raise SharedPAExperimentRegistryError(f"symlink or reparse point is forbidden: {current}")


def _relative_path(value: Any, label: str) -> str:
    text = _nonempty(value, label).replace("\\", "/")
    pure = PurePosixPath(text)
    if pure.is_absolute() or text != pure.as_posix() or any(part in {"", ".", ".."} for part in pure.parts):
        raise SharedPAExperimentRegistryError(f"{label} must be a normalized relative path")
    return text


def _output_namespace(value: Any) -> str:
    text = _relative_path(value, "output_namespace")
    if text != text.casefold() or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-/" for character in text):
        raise SharedPAExperimentRegistryError("output namespace must be lowercase ASCII")
    if not text.startswith("experiments/"):
        raise SharedPAExperimentRegistryError("output namespace must remain under experiments/")
    return text


def _load_unique_json(path: Path, label: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise SharedPAExperimentRegistryError(f"{label} has duplicate JSON key: {key}")
            output[key] = value
        return output

    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=unique)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedPAExperimentRegistryError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise SharedPAExperimentRegistryError(f"{label} must be a JSON object")
    return value


def _canonical_file(path: Path, label: str) -> dict[str, Any]:
    value = _load_unique_json(path, label)
    if path.read_bytes() != canonical_bytes(value) + b"\n":
        raise SharedPAExperimentRegistryError(f"{label} bytes are not canonical")
    return value


def validate_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "registry_id",
        "status",
        "source_base_commit",
        "root_parent_candidate_id",
        "markets",
        "selection_authority",
        "protected_boundaries",
        "artifact_rules",
        "evaluator_binding",
        "external_checkpoint_rule",
        "canonical_evidence_window_ids",
        "authority_state",
    }
    if set(policy) != required or policy.get("schema_version") != POLICY_SCHEMA:
        raise SharedPAExperimentRegistryError("registry policy surface or schema changed")
    _nonempty(policy.get("registry_id"), "registry_id")
    if policy.get("status") != "LOCKED_RESEARCH_ONLY_REGISTRY_WITH_SYNTHETIC_EVALUATOR":
        raise SharedPAExperimentRegistryError("registry policy is not locked research-only")
    commit = policy.get("source_base_commit")
    if not isinstance(commit, str) or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise SharedPAExperimentRegistryError("source base commit is invalid")
    if policy.get("root_parent_candidate_id") != ROOT_PARENT:
        raise SharedPAExperimentRegistryError("root parent authority changed")
    if policy.get("markets") != {
        "names": list(MARKETS),
        "adjudicate_separately": True,
        "pooling_allowed": False,
        "cross_market_rescue_allowed": False,
    }:
        raise SharedPAExperimentRegistryError("market separation policy changed")
    authority = policy.get("selection_authority")
    if authority != {
        "authority_id": "shared_pa_2024_selection_authority_v1",
        "window_id": "2024_regular_season",
        "window_state": "INELIGIBLE_ALREADY_SPENT",
        "issuance_enabled": False,
        "initial_issuance_state": "UNISSUED",
        "initial_consumption_state": "UNCONSUMED",
        "maximum_issues": 1,
        "maximum_consumptions": 1,
        "human_approval_required": True,
    }:
        raise SharedPAExperimentRegistryError("2024 selection authority changed")
    if policy.get("protected_boundaries") != PROTECTED_BOUNDARIES:
        raise SharedPAExperimentRegistryError("protected research boundary changed")
    if policy.get("canonical_evidence_window_ids") != list(CANONICAL_EVIDENCE_WINDOWS):
        raise SharedPAExperimentRegistryError("canonical evidence-window allowlist changed")
    if policy.get("authority_state") != UNBOUND_AUTHORITY:
        raise SharedPAExperimentRegistryError("checked-in registry policy must remain externally unbound")
    if policy.get("artifact_rules") != {
        "all_paths_relative_to_artifact_root": True,
        "all_bytes_sha256_bound": True,
        "symlink_and_reparse_points_forbidden": True,
        "artifact_reverification_required_on_replay": True,
        "output_namespace_part_of_governance_not_equivalence": True,
        "output_namespace_globally_unique": True,
        "semantic_design_independent_of_json_python_reserialization": True,
        "external_authority_required_for_authoritative_append": True,
    }:
        raise SharedPAExperimentRegistryError("artifact identity rules changed")
    evaluator = policy.get("evaluator_binding")
    expected_evaluator = {
        "path": "config/shared_pa_market_evaluation_contract_v1.json",
        "sha256": "53673c56b90e48d593c4355e0fb2c6152e69dbf165c15125db065d9059b5b34d",
        "file_manifest_path": "config/shared_pa_market_evaluator_v1_file_manifest.json",
        "file_manifest_sha256": "83cf0ca50a67f7f3c905d2f2cf189045ba9983ad5fc4a5718682a0b79cd7eec1",
        "state": EVALUATOR_STATE,
        "evidence_class": EVALUATOR_EVIDENCE_CLASS,
        "external_authority_required_before_real_evaluation": True,
        "real_evaluation_authorized": False,
        "model_fitting_authorized": False,
        "promotion_authorized": False,
    }
    if not isinstance(evaluator, Mapping) or dict(evaluator) != expected_evaluator:
        raise SharedPAExperimentRegistryError("evaluator binding is malformed")
    _relative_path(evaluator.get("path"), "evaluator_binding.path")
    _sha256(evaluator.get("sha256"), "evaluator_binding.sha256")
    _relative_path(evaluator.get("file_manifest_path"), "evaluator_binding.file_manifest_path")
    _sha256(evaluator.get("file_manifest_sha256"), "evaluator_binding.file_manifest_sha256")
    if policy.get("external_checkpoint_rule") != {
        "authoritative_replay_requires_trusted_checkpoint": True,
        "local_chain_cannot_detect_wholesale_suffix_rollback": True,
        "fixed_canonical_path_required": True,
        "independent_expected_digest_required": True,
        "monotonic_sequence_required": True,
        "trusted_append_receipt_required": True,
    }:
        raise SharedPAExperimentRegistryError("external checkpoint limitation changed")
    return dict(policy)


def load_policy(path: Path) -> dict[str, Any]:
    return validate_policy(_load_unique_json(path, "registry policy"))


def _binding(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise SharedPAExperimentRegistryError(f"{label} binding is malformed")
    return {
        "path": _relative_path(value.get("path"), f"{label}.path"),
        "sha256": _sha256(value.get("sha256"), f"{label}.sha256"),
    }


def _policy_evaluator_bindings(policy: Mapping[str, Any]) -> list[dict[str, str]]:
    evaluator = policy["evaluator_binding"]
    return [
        {"path": evaluator["path"], "sha256": evaluator["sha256"]},
        {
            "path": evaluator["file_manifest_path"],
            "sha256": evaluator["file_manifest_sha256"],
        },
    ]


def _validate_policy_evaluator_artifacts(
    policy: Mapping[str, Any], artifact_root: Path
) -> None:
    bindings = _policy_evaluator_bindings(policy)
    _validate_artifact_bytes(bindings, artifact_root)
    evaluator = policy["evaluator_binding"]
    manifest_path = artifact_root.absolute().joinpath(
        *PurePosixPath(evaluator["file_manifest_path"]).parts
    )
    manifest = _load_unique_json(manifest_path, "market evaluator file manifest")
    if (
        set(manifest)
        != {
            "schema_version", "component_id", "state", "source_base_commit",
            "research_only", "betting_authorized", "files",
        }
        or manifest.get("schema_version")
        != "shared-pa-market-evaluator-file-manifest-v1"
        or manifest.get("component_id") != "shared_pa_market_evaluator_v1"
        or manifest.get("state") != "UNBOUND_EXTERNAL_AUTHORITIES_REQUIRED"
        or manifest.get("source_base_commit") != EVALUATOR_SOURCE_BASE_COMMIT
        or manifest.get("research_only") is not True
        or manifest.get("betting_authorized") is not False
    ):
        raise SharedPAExperimentRegistryError(
            "market evaluator file manifest authority changed"
        )
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise SharedPAExperimentRegistryError("market evaluator file manifest is malformed")
    rows_by_path: dict[str, dict[str, Any]] = {}
    file_bindings: list[dict[str, str]] = []
    for index, row in enumerate(files):
        if not isinstance(row, Mapping) or set(row) != {
            "path", "size", "sha256", "purpose",
        }:
            raise SharedPAExperimentRegistryError(
                "market evaluator file manifest row surface changed"
            )
        relative = _relative_path(
            row.get("path"), f"market evaluator file manifest files[{index}].path"
        )
        if relative in rows_by_path:
            raise SharedPAExperimentRegistryError(
                f"market evaluator file manifest has duplicate path: {relative}"
            )
        size = row.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise SharedPAExperimentRegistryError(
                f"market evaluator file manifest has invalid size: {relative}"
            )
        digest = _sha256(
            row.get("sha256"),
            f"market evaluator file manifest files[{index}].sha256",
        )
        _nonempty(
            row.get("purpose"),
            f"market evaluator file manifest files[{index}].purpose",
        )
        rows_by_path[relative] = dict(row)
        file_bindings.append({"path": relative, "sha256": digest})
    if frozenset(rows_by_path) != EVALUATOR_MANIFEST_PATHS:
        raise SharedPAExperimentRegistryError(
            "market evaluator file manifest exact file surface changed"
        )
    _validate_artifact_bytes(file_bindings, artifact_root)
    root = artifact_root.absolute()
    for relative, row in rows_by_path.items():
        target = root.joinpath(*PurePosixPath(relative).parts)
        if target.stat().st_size != row["size"]:
            raise SharedPAExperimentRegistryError(
                f"market evaluator file size changed: {relative}"
            )
    contract_row = rows_by_path.get(evaluator["path"])
    if (
        contract_row is None
        or contract_row.get("sha256") != evaluator["sha256"]
    ):
        raise SharedPAExperimentRegistryError(
            "market evaluator manifest does not bind the exact contract"
        )


def _binding_list(value: Any, label: str, *, nonempty: bool = True) -> list[dict[str, str]]:
    if not isinstance(value, list) or (nonempty and not value):
        raise SharedPAExperimentRegistryError(f"{label} must be a {'nonempty ' if nonempty else ''}list")
    bindings = [_binding(item, f"{label}[{index}]") for index, item in enumerate(value)]
    paths = [item["path"] for item in bindings]
    if len(paths) != len(set(paths)):
        raise SharedPAExperimentRegistryError(f"{label} has duplicate artifact paths")
    return bindings


def _validate_artifact_bytes(bindings: Sequence[Mapping[str, str]], artifact_root: Path) -> None:
    root = artifact_root.absolute()
    if not root.is_dir():
        raise SharedPAExperimentRegistryError("artifact root is not a directory")
    _assert_no_links(root)
    for binding in bindings:
        relative = _relative_path(binding.get("path"), "artifact.path")
        target = root.joinpath(*PurePosixPath(relative).parts)
        _assert_no_links(target)
        if not target.is_file() or not stat.S_ISREG(target.stat().st_mode):
            raise SharedPAExperimentRegistryError(f"bound artifact is missing or nonregular: {relative}")
        if sha256_file(target) != _sha256(binding.get("sha256"), "artifact.sha256"):
            raise SharedPAExperimentRegistryError(f"bound artifact bytes changed: {relative}")


def _all_predeclaration_bindings(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    output = [
        _binding(payload.get("feature_contract"), "feature_contract"),
        _binding(payload.get("grid_contract"), "grid_contract"),
        _binding(payload.get("fold_contract"), "fold_contract"),
        _binding(payload.get("evaluator_binding"), "evaluator_binding"),
    ]
    artifacts = payload.get("artifact_bindings")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {
        "code", "configuration", "data", "tests", "output_schema",
    }:
        raise SharedPAExperimentRegistryError("predeclaration artifact roles changed")
    for role in ("code", "configuration", "data", "tests", "output_schema"):
        output.extend(_binding_list(artifacts.get(role), f"artifact_bindings.{role}"))
    paths = [item["path"] for item in output]
    if len(paths) != len(set(paths)):
        raise SharedPAExperimentRegistryError("one artifact path occupies multiple predeclaration roles")
    return output


def _semantic_json(path: Path, label: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise SharedPAExperimentRegistryError(f"{label} has duplicate JSON key: {key}")
            output[key] = value
        return output

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedPAExperimentRegistryError(f"{label} is not valid semantic JSON") from exc


def _semantic_python(path: Path, label: str) -> str:
    try:
        parsed = ast.parse(path.read_text(encoding="utf-8"), filename=label)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise SharedPAExperimentRegistryError(f"{label} is not valid semantic Python") from exc
    return ast.dump(parsed, annotate_fields=True, include_attributes=False)


def semantic_experiment_spec(payload: Mapping[str, Any], artifact_root: Path) -> dict[str, Any]:
    """Return the outcome-affecting specification independent of JSON/Python storage bytes."""
    root = artifact_root.absolute()

    def target(binding: Mapping[str, Any], label: str) -> Path:
        checked = _binding(binding, label)
        path = root.joinpath(*PurePosixPath(checked["path"]).parts)
        _assert_no_links(path)
        return path

    artifacts = payload.get("artifact_bindings")
    if not isinstance(artifacts, Mapping):
        raise SharedPAExperimentRegistryError("predeclaration artifact roles changed")
    code = [
        _semantic_python(target(item, f"artifact_bindings.code[{index}]"), f"code[{index}]")
        for index, item in enumerate(artifacts.get("code", []))
    ]
    configuration = [
        _semantic_json(target(item, f"artifact_bindings.configuration[{index}]"), f"configuration[{index}]")
        for index, item in enumerate(artifacts.get("configuration", []))
    ]
    data = [
        _semantic_json(target(item, f"artifact_bindings.data[{index}]"), f"data[{index}]")
        for index, item in enumerate(artifacts.get("data", []))
    ]
    return {
        "parent_candidate_id": payload.get("parent_candidate_id"),
        "feature_contract": _semantic_json(target(payload["feature_contract"], "feature_contract"), "feature_contract"),
        "grid_contract": _semantic_json(target(payload["grid_contract"], "grid_contract"), "grid_contract"),
        "fold_contract": _semantic_json(target(payload["fold_contract"], "fold_contract"), "fold_contract"),
        "code": code,
        "configuration": configuration,
        "data": data,
        "allowed_evidence_window_ids": payload.get("allowed_evidence_window_ids"),
        "evaluator_contract": _semantic_json(target(payload["evaluator_binding"], "evaluator_binding"), "evaluator_binding"),
    }


def experiment_equivalence_fingerprint(payload: Mapping[str, Any], artifact_root: Path) -> str:
    """Fingerprint the canonical semantic design, excluding caller labels and storage hashes."""
    return sha256_bytes(canonical_bytes(semantic_experiment_spec(payload, artifact_root)))


def derived_multiplicity_family_id(payload: Mapping[str, Any], artifact_root: Path) -> str:
    return "family-" + experiment_equivalence_fingerprint(payload, artifact_root)


def _validate_predeclaration(
    payload: Mapping[str, Any], *, entries: Sequence[Mapping[str, Any]], artifact_root: Path,
) -> dict[str, Any]:
    required = {
        "candidate_id", "parent_candidate_id", "multiplicity_family_id", "hypothesis",
        "feature_contract", "grid_contract", "fold_contract", "artifact_bindings",
        "allowed_evidence_window_ids", "prior_attempt_entry_hashes", "output_namespace",
        "selection_authority", "untouched_forward_window", "protected_boundaries",
        "terminal_disposition", "evaluator_binding", "equivalence_fingerprint",
    }
    if set(payload) != required:
        raise SharedPAExperimentRegistryError("experiment predeclaration surface changed")
    candidate_id = _nonempty(payload.get("candidate_id"), "candidate_id")
    family_id = _nonempty(payload.get("multiplicity_family_id"), "multiplicity_family_id")
    parent_id = _nonempty(payload.get("parent_candidate_id"), "parent_candidate_id")
    _nonempty(payload.get("hypothesis"), "hypothesis")
    output_namespace = _output_namespace(payload.get("output_namespace"))
    if payload.get("selection_authority") != {
        "authority_id": "shared_pa_2024_selection_authority_v1",
        "required_before_2024_read": True,
        "token_entry_hash": None,
    }:
        raise SharedPAExperimentRegistryError("predeclaration selection authority changed")
    if payload.get("untouched_forward_window") != {
        "state": "UNASSIGNED",
        "window_id": None,
        "first_official_date": None,
        "last_official_date": None,
        "backfill_allowed": False,
    }:
        raise SharedPAExperimentRegistryError("untouched forward window must begin unassigned")
    if payload.get("protected_boundaries") != PROTECTED_BOUNDARIES:
        raise SharedPAExperimentRegistryError("predeclaration weakened protected boundaries")
    if payload.get("terminal_disposition") != "PENDING":
        raise SharedPAExperimentRegistryError("a new experiment must begin pending")
    windows = payload.get("allowed_evidence_window_ids")
    if not isinstance(windows, list) or not windows or windows != sorted(set(windows)):
        raise SharedPAExperimentRegistryError("allowed evidence IDs must be a nonempty sorted unique list")
    allowed = set(CANONICAL_EVIDENCE_WINDOWS)
    if any(not isinstance(window, str) or window not in allowed for window in windows):
        raise SharedPAExperimentRegistryError("evidence window is not a canonical allowlisted ID")

    prior_candidates = [
        entry for entry in entries
        if entry["entry_type"] == "EXPERIMENT_PREDECLARED"
    ]
    by_id = {entry["payload"]["candidate_id"]: entry for entry in prior_candidates}
    if candidate_id in by_id:
        raise SharedPAExperimentRegistryError("duplicate candidate ID")
    if any(entry["payload"]["output_namespace"] == output_namespace for entry in prior_candidates):
        raise SharedPAExperimentRegistryError("output namespace is already reserved globally")
    if parent_id != ROOT_PARENT and parent_id not in by_id:
        raise SharedPAExperimentRegistryError("candidate parent is missing from earlier registry state")
    bindings = _all_predeclaration_bindings(payload)
    _validate_artifact_bytes(bindings, artifact_root)
    expected_fingerprint = experiment_equivalence_fingerprint(payload, artifact_root)
    if payload.get("equivalence_fingerprint") != expected_fingerprint:
        raise SharedPAExperimentRegistryError("experiment equivalence fingerprint changed")
    if family_id != "family-" + expected_fingerprint:
        raise SharedPAExperimentRegistryError("multiplicity family must derive from canonical semantic design")
    expected_prior = [
        entry["entry_hash"] for entry in prior_candidates
        if entry["payload"]["multiplicity_family_id"] == family_id
    ]
    if payload.get("prior_attempt_entry_hashes") != expected_prior:
        raise SharedPAExperimentRegistryError("prior attempt list omits, adds, or reorders a family attempt")
    for entry in prior_candidates:
        if entry["payload"]["equivalence_fingerprint"] == expected_fingerprint:
            raise SharedPAExperimentRegistryError(
                "equivalent candidate already exists; rename or output path cannot create a new attempt"
            )
    return dict(payload)


def _candidate_state(entries: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for entry in entries:
        payload = entry["payload"]
        if entry["entry_type"] == "EXPERIMENT_PREDECLARED":
            if payload.get("candidate_id") in state:
                raise SharedPAExperimentRegistryError("duplicate candidate state during replay")
            state[payload["candidate_id"]] = {
                "predeclaration_hash": entry["entry_hash"],
                "frozen_hash": None,
                "terminal": None,
            }
        elif entry["entry_type"] == "CANDIDATE_FROZEN":
            if payload.get("candidate_id") not in state:
                raise SharedPAExperimentRegistryError("candidate freeze precedes predeclaration")
            state[payload["candidate_id"]]["frozen_hash"] = entry["entry_hash"]
        elif entry["entry_type"] == "TERMINAL_DISPOSITION":
            if payload.get("candidate_id") not in state:
                raise SharedPAExperimentRegistryError("terminal disposition precedes predeclaration")
            state[payload["candidate_id"]]["terminal"] = payload["disposition"]
    return state


def _validate_event_payload(
    entry_type: str,
    payload: Mapping[str, Any],
    *,
    entries: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    artifact_root: Path,
) -> dict[str, Any]:
    if entry_type == "EXPERIMENT_PREDECLARED":
        return _validate_predeclaration(payload, entries=entries, artifact_root=artifact_root)
    state = _candidate_state(entries)
    if entry_type == "CANDIDATE_FROZEN":
        if set(payload) != {"candidate_id", "predeclaration_entry_hash", "model_artifacts", "model_release_hash"}:
            raise SharedPAExperimentRegistryError("candidate freeze surface changed")
        candidate_id = _nonempty(payload.get("candidate_id"), "candidate_id")
        if candidate_id not in state or state[candidate_id]["terminal"] is not None:
            raise SharedPAExperimentRegistryError("candidate freeze lacks a live predeclaration")
        if state[candidate_id]["frozen_hash"] is not None:
            raise SharedPAExperimentRegistryError("candidate model was already frozen")
        if payload.get("predeclaration_entry_hash") != state[candidate_id]["predeclaration_hash"]:
            raise SharedPAExperimentRegistryError("candidate freeze does not bind its predeclaration")
        models = _binding_list(payload.get("model_artifacts"), "model_artifacts")
        _validate_artifact_bytes(models, artifact_root)
        expected = sha256_bytes(canonical_bytes({
            "predeclaration_entry_hash": payload["predeclaration_entry_hash"],
            "model_artifacts": models,
        }))
        if payload.get("model_release_hash") != expected:
            raise SharedPAExperimentRegistryError("model release hash changed")
        return dict(payload)
    if entry_type == "SELECTION_TOKEN_ISSUED":
        if set(payload) != {"authority_id", "candidate_id", "candidate_frozen_entry_hash", "window_id", "human_approval_sha256"}:
            raise SharedPAExperimentRegistryError("selection issue surface changed")
        authority = policy["selection_authority"]
        if authority["issuance_enabled"] is not True or authority["window_state"] != "ELIGIBLE_UNTOUCHED":
            raise SharedPAExperimentRegistryError("2024 selection token issuance is disabled or ineligible")
        if any(entry["entry_type"] == "SELECTION_TOKEN_ISSUED" for entry in entries):
            raise SharedPAExperimentRegistryError("selection token was already issued")
        candidate_id = _nonempty(payload.get("candidate_id"), "candidate_id")
        if candidate_id not in state or state[candidate_id]["frozen_hash"] is None or state[candidate_id]["terminal"] is not None:
            raise SharedPAExperimentRegistryError("selection token candidate is not frozen and live")
        if payload.get("candidate_frozen_entry_hash") != state[candidate_id]["frozen_hash"]:
            raise SharedPAExperimentRegistryError("selection token does not bind frozen candidate")
        if payload.get("authority_id") != authority["authority_id"] or payload.get("window_id") != authority["window_id"]:
            raise SharedPAExperimentRegistryError("selection token authority or window changed")
        _sha256(payload.get("human_approval_sha256"), "human_approval_sha256")
        return dict(payload)
    if entry_type == "SELECTION_TOKEN_CONSUMED":
        if set(payload) != {"authority_id", "candidate_id", "issue_entry_hash", "window_id", "selection_output_namespace"}:
            raise SharedPAExperimentRegistryError("selection consumption surface changed")
        issues = [entry for entry in entries if entry["entry_type"] == "SELECTION_TOKEN_ISSUED"]
        consumes = [entry for entry in entries if entry["entry_type"] == "SELECTION_TOKEN_CONSUMED"]
        if len(issues) != 1 or consumes:
            raise SharedPAExperimentRegistryError("selection token is absent or already consumed")
        issue = issues[0]
        if any(payload.get(key) != issue["payload"].get(key) for key in ("authority_id", "candidate_id", "window_id")):
            raise SharedPAExperimentRegistryError("selection consumption differs from issued authority")
        if payload.get("issue_entry_hash") != issue["entry_hash"]:
            raise SharedPAExperimentRegistryError("selection consumption does not bind issue entry")
        namespace = _relative_path(payload.get("selection_output_namespace"), "selection_output_namespace")
        predeclared = next(
            entry["payload"] for entry in entries
            if entry["entry_type"] == "EXPERIMENT_PREDECLARED" and entry["payload"]["candidate_id"] == payload["candidate_id"]
        )
        if namespace != predeclared["output_namespace"] + "/selection_2024":
            raise SharedPAExperimentRegistryError("selection output path bypasses predeclared namespace")
        return dict(payload)
    if entry_type == "TERMINAL_DISPOSITION":
        if set(payload) != {"candidate_id", "disposition", "reason_code", "evidence_bindings"}:
            raise SharedPAExperimentRegistryError("terminal disposition surface changed")
        candidate_id = _nonempty(payload.get("candidate_id"), "candidate_id")
        if candidate_id not in state or state[candidate_id]["terminal"] is not None:
            raise SharedPAExperimentRegistryError("candidate is missing or already terminal")
        if payload.get("disposition") not in TERMINAL_DISPOSITIONS:
            raise SharedPAExperimentRegistryError("terminal disposition is invalid")
        _nonempty(payload.get("reason_code"), "reason_code")
        bindings = _binding_list(payload.get("evidence_bindings"), "evidence_bindings")
        _validate_artifact_bytes(bindings, artifact_root)
        return dict(payload)
    raise SharedPAExperimentRegistryError(f"unknown entry type: {entry_type}")


def _entry_core(sequence: int, previous: str, entry_type: str, recorded_at_utc: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": ENTRY_SCHEMA,
        "sequence": sequence,
        "previous_entry_hash": previous,
        "entry_type": entry_type,
        "recorded_at_utc": _utc(recorded_at_utc, "recorded_at_utc"),
        "payload_sha256": sha256_bytes(canonical_bytes(payload)),
        "payload": dict(payload),
    }


def _checkpoint_core(registry_id: str, count: int, head_hash: str, previous: str) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_SCHEMA,
        "registry_id": registry_id,
        "entry_count": count,
        "head_entry_hash": head_hash,
        "previous_checkpoint_hash": previous,
    }


def _publish_once(path: Path, payload: bytes) -> None:
    _assert_no_links(path, include_leaf=False)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path, flags, 0o444)
    except FileExistsError as exc:
        raise SharedPAExperimentRegistryError(f"refusing to overwrite immutable file: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(path, stat.S_IREAD)
        except OSError:
            pass
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _replace_head(path: Path, payload: bytes) -> None:
    _assert_no_links(path, include_leaf=False)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".head.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


@contextmanager
def _exclusive_lock(root: Path) -> Iterator[None]:
    lock = root / ".append.lock"
    _assert_no_links(lock, include_leaf=False)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(lock, flags, 0o600)
    except FileExistsError as exc:
        raise SharedPAExperimentRegistryError("registry append lock already exists; fail closed for concurrency or crash") from exc
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class RegistryState:
    registry_id: str
    entry_count: int
    head_entry_hash: str
    checkpoint_hash: str
    entries: tuple[dict[str, Any], ...]
    authority_state: str = UNBOUND_AUTHORITY
    release_expectation_sha256: str | None = None

    def trusted_checkpoint(self) -> dict[str, Any]:
        return {
            "schema_version": TRUSTED_CHECKPOINT_SCHEMA,
            "registry_id": self.registry_id,
            "entry_count": self.entry_count,
            "head_entry_hash": self.head_entry_hash,
            "checkpoint_hash": self.checkpoint_hash,
        }


def _absolute_canonical_path(path: Path, label: str) -> str:
    if not path.is_absolute():
        raise SharedPAExperimentRegistryError(f"{label} must be an absolute fixed path")
    _assert_no_links(path, include_leaf=path.exists())
    return str(path.absolute())


def load_release_expectation(
    path: Path,
    *,
    expected_sha256: str,
    artifact_root: Path,
    policy_path: Path,
) -> dict[str, Any]:
    """Load an independently digested release expectation outside the registry."""
    _assert_no_links(path)
    expected = _sha256(expected_sha256, "release expectation expected_sha256")
    if sha256_file(path) != expected:
        raise SharedPAExperimentRegistryError("external release expectation digest changed")
    value = _canonical_file(path, "external release expectation")
    required = {
        "schema_version", "authority_id", "registry_id", "source_base_commit",
        "policy_binding", "evaluator_binding", "artifact_manifest_binding",
        "trusted_checkpoint_canonical_path", "append_not_before_utc",
    }
    if set(value) != required or value.get("schema_version") != RELEASE_EXPECTATION_SCHEMA:
        raise SharedPAExperimentRegistryError("external release expectation surface or schema changed")
    _nonempty(value.get("authority_id"), "release_expectation.authority_id")
    policy = load_policy(policy_path)
    if value.get("registry_id") != policy["registry_id"]:
        raise SharedPAExperimentRegistryError("release expectation registry identity changed")
    if value.get("source_base_commit") != policy["source_base_commit"]:
        raise SharedPAExperimentRegistryError("release expectation base commit changed")
    policy_binding = _binding(value.get("policy_binding"), "release_expectation.policy_binding")
    evaluator_binding = _binding(value.get("evaluator_binding"), "release_expectation.evaluator_binding")
    manifest_binding = _binding(value.get("artifact_manifest_binding"), "release_expectation.artifact_manifest_binding")
    actual_policy = policy_path.absolute()
    bound_policy = artifact_root.absolute().joinpath(*PurePosixPath(policy_binding["path"]).parts)
    if sha256_file(actual_policy) != policy_binding["sha256"] or sha256_file(bound_policy) != policy_binding["sha256"]:
        raise SharedPAExperimentRegistryError("release expectation does not bind the exact policy path and bytes")
    if evaluator_binding != {"path": policy["evaluator_binding"]["path"], "sha256": policy["evaluator_binding"]["sha256"]}:
        raise SharedPAExperimentRegistryError("release expectation evaluator binding differs from policy")
    _validate_artifact_bytes([policy_binding, evaluator_binding, manifest_binding], artifact_root)
    checkpoint_path = value.get("trusted_checkpoint_canonical_path")
    if not isinstance(checkpoint_path, str) or checkpoint_path != str(Path(checkpoint_path).absolute()):
        raise SharedPAExperimentRegistryError("trusted checkpoint path is not fixed canonical absolute path")
    _assert_no_links(Path(checkpoint_path), include_leaf=Path(checkpoint_path).exists())
    _utc(value.get("append_not_before_utc"), "release_expectation.append_not_before_utc")
    return value


def load_bound_trusted_checkpoint(
    path: Path,
    *,
    expected_sha256: str,
    release_expectation: Mapping[str, Any],
) -> dict[str, Any]:
    fixed = _absolute_canonical_path(path, "trusted checkpoint path")
    if fixed != release_expectation.get("trusted_checkpoint_canonical_path"):
        raise SharedPAExperimentRegistryError("trusted checkpoint path differs from external release expectation")
    expected = _sha256(expected_sha256, "trusted checkpoint expected_sha256")
    if sha256_file(path) != expected:
        raise SharedPAExperimentRegistryError("trusted checkpoint digest changed or was replaced")
    return load_trusted_checkpoint(path)


def load_append_receipt(
    path: Path,
    *,
    expected_sha256: str,
    release_expectation: Mapping[str, Any],
    state: RegistryState,
    entry_type: str,
    payload: Mapping[str, Any],
    artifact_root: Path,
) -> dict[str, Any]:
    _assert_no_links(path)
    expected = _sha256(expected_sha256, "append receipt expected_sha256")
    if sha256_file(path) != expected:
        raise SharedPAExperimentRegistryError("external append receipt digest changed")
    value = _canonical_file(path, "external append receipt")
    required = {
        "schema_version", "authority_id", "registry_id", "append_sequence",
        "expected_previous_checkpoint_hash", "entry_type", "payload_semantic_sha256",
        "payload_canonical_sha256",
        "trusted_recorded_at_utc",
    }
    if set(value) != required or value.get("schema_version") != APPEND_RECEIPT_SCHEMA:
        raise SharedPAExperimentRegistryError("external append receipt surface or schema changed")
    if value.get("authority_id") != release_expectation.get("authority_id"):
        raise SharedPAExperimentRegistryError("append receipt authority changed")
    if value.get("registry_id") != state.registry_id:
        raise SharedPAExperimentRegistryError("append receipt registry changed")
    if value.get("append_sequence") != state.entry_count + 1:
        raise SharedPAExperimentRegistryError("append receipt sequence is nonmonotonic")
    if value.get("expected_previous_checkpoint_hash") != state.checkpoint_hash:
        raise SharedPAExperimentRegistryError("append receipt does not bind the prior checkpoint")
    if value.get("entry_type") != entry_type:
        raise SharedPAExperimentRegistryError("append receipt entry type changed")
    semantic_hash = (
        experiment_equivalence_fingerprint(payload, artifact_root)
        if entry_type == "EXPERIMENT_PREDECLARED"
        else sha256_bytes(canonical_bytes(payload))
    )
    if value.get("payload_semantic_sha256") != semantic_hash:
        raise SharedPAExperimentRegistryError("append receipt does not bind the semantic payload")
    canonical_payload_hash = sha256_bytes(canonical_bytes(payload))
    if value.get("payload_canonical_sha256") != canonical_payload_hash:
        raise SharedPAExperimentRegistryError("append receipt does not bind the complete canonical payload")
    trusted_time = _utc(value.get("trusted_recorded_at_utc"), "append_receipt.trusted_recorded_at_utc")
    if trusted_time < release_expectation["append_not_before_utc"]:
        raise SharedPAExperimentRegistryError("append receipt timestamp predates external release authority")
    if state.entries and trusted_time <= state.entries[-1]["recorded_at_utc"]:
        raise SharedPAExperimentRegistryError("append receipt timestamp is backdated or nonmonotonic")
    return value


def initialize_registry(
    *,
    root: Path,
    policy_path: Path,
    artifact_root: Path,
    external_release_expectation_path: Path | None = None,
    expected_release_expectation_sha256: str | None = None,
) -> RegistryState:
    if root.exists():
        raise SharedPAExperimentRegistryError("registry root must not already exist")
    policy = load_policy(policy_path)
    _validate_policy_evaluator_artifacts(policy, artifact_root)
    if (external_release_expectation_path is None) != (expected_release_expectation_sha256 is None):
        raise SharedPAExperimentRegistryError("external release expectation path and digest are both required")
    release_expectation_sha256: str | None = None
    configured_authority = UNBOUND_AUTHORITY
    if external_release_expectation_path is not None and expected_release_expectation_sha256 is not None:
        load_release_expectation(
            external_release_expectation_path,
            expected_sha256=expected_release_expectation_sha256,
            artifact_root=artifact_root,
            policy_path=policy_path,
        )
        release_expectation_sha256 = expected_release_expectation_sha256
        configured_authority = "EXTERNAL_RELEASE_CONFIGURED_CHECKPOINT_REQUIRED"
    _assert_no_links(root, include_leaf=False)
    root.mkdir(parents=False)
    (root / "entries").mkdir()
    (root / "checkpoints").mkdir()
    _publish_once(root / "registry_policy.json", canonical_bytes(policy) + b"\n")
    identity = {
        "schema_version": IDENTITY_SCHEMA,
        "registry_id": policy["registry_id"],
        "configured_authority_state": configured_authority,
        "release_expectation_sha256": release_expectation_sha256,
    }
    _publish_once(root / "registry_identity.json", canonical_bytes(identity) + b"\n")
    checkpoint_core = _checkpoint_core(policy["registry_id"], 0, ZERO_HASH, ZERO_HASH)
    checkpoint_hash = sha256_bytes(canonical_bytes(checkpoint_core))
    checkpoint = {**checkpoint_core, "checkpoint_hash": checkpoint_hash}
    _publish_once(
        root / "checkpoints" / f"00000000-{checkpoint_hash}.json",
        canonical_bytes(checkpoint) + b"\n",
    )
    head = {
        "schema_version": HEAD_SCHEMA,
        "registry_id": policy["registry_id"],
        "entry_count": 0,
        "head_entry_hash": ZERO_HASH,
        "checkpoint_hash": checkpoint_hash,
        "token_issuance_state": policy["selection_authority"]["initial_issuance_state"],
        "token_consumption_state": policy["selection_authority"]["initial_consumption_state"],
        "configured_authority_state": configured_authority,
        "release_expectation_sha256": release_expectation_sha256,
    }
    _replace_head(root / "HEAD.json", canonical_bytes(head) + b"\n")
    return verify_registry(root=root, artifact_root=artifact_root)


def _validate_checkpoint(value: Mapping[str, Any], *, previous_hash: str, count: int, head_hash: str, registry_id: str) -> str:
    if set(value) != {
        "schema_version", "registry_id", "entry_count", "head_entry_hash",
        "previous_checkpoint_hash", "checkpoint_hash",
    } or value.get("schema_version") != CHECKPOINT_SCHEMA:
        raise SharedPAExperimentRegistryError("checkpoint surface or schema changed")
    if value.get("registry_id") != registry_id or value.get("entry_count") != count:
        raise SharedPAExperimentRegistryError("checkpoint registry or sequence changed")
    if value.get("head_entry_hash") != head_hash or value.get("previous_checkpoint_hash") != previous_hash:
        raise SharedPAExperimentRegistryError("checkpoint chain changed")
    core = {key: value[key] for key in value if key != "checkpoint_hash"}
    expected = sha256_bytes(canonical_bytes(core))
    if value.get("checkpoint_hash") != expected:
        raise SharedPAExperimentRegistryError("checkpoint hash changed")
    return expected


def verify_registry(
    *,
    root: Path,
    artifact_root: Path | None = None,
    trusted_checkpoint: Mapping[str, Any] | None = None,
    external_release_expectation_path: Path | None = None,
    expected_release_expectation_sha256: str | None = None,
    trusted_checkpoint_path: Path | None = None,
    expected_trusted_checkpoint_sha256: str | None = None,
    allow_lock: bool = False,
) -> RegistryState:
    if not root.is_dir():
        raise SharedPAExperimentRegistryError("registry root is missing")
    _assert_no_links(root)
    allowed = {"registry_policy.json", "registry_identity.json", "entries", "checkpoints", "HEAD.json"}
    if allow_lock:
        allowed.add(".append.lock")
    names = {path.name for path in root.iterdir()}
    if names != allowed:
        raise SharedPAExperimentRegistryError("registry root has missing or unexpected paths")
    for directory in (root / "entries", root / "checkpoints"):
        if not directory.is_dir() or _is_link_or_reparse(directory):
            raise SharedPAExperimentRegistryError("registry storage directory is missing or redirected")
    policy = validate_policy(_canonical_file(root / "registry_policy.json", "registry policy"))
    identity = _canonical_file(root / "registry_identity.json", "registry identity")
    if set(identity) != {
        "schema_version", "registry_id", "configured_authority_state", "release_expectation_sha256",
    } or identity.get("schema_version") != IDENTITY_SCHEMA or identity.get("registry_id") != policy["registry_id"]:
        raise SharedPAExperimentRegistryError("registry identity surface or registry binding changed")
    if identity.get("configured_authority_state") not in {
        UNBOUND_AUTHORITY, "EXTERNAL_RELEASE_CONFIGURED_CHECKPOINT_REQUIRED",
    }:
        raise SharedPAExperimentRegistryError("registry configured authority state changed")
    if artifact_root is not None:
        _validate_policy_evaluator_artifacts(policy, artifact_root)
    entries: list[dict[str, Any]] = []
    previous_entry_hash = ZERO_HASH
    for sequence, path in enumerate(sorted((root / "entries").iterdir()), start=1):
        if _is_link_or_reparse(path) or not path.is_file():
            raise SharedPAExperimentRegistryError("registry entry is redirected or nonregular")
        value = _canonical_file(path, f"entry {sequence}")
        required = {
            "schema_version", "sequence", "previous_entry_hash", "entry_type",
            "recorded_at_utc", "payload_sha256", "payload", "entry_hash",
        }
        if set(value) != required or value.get("schema_version") != ENTRY_SCHEMA:
            raise SharedPAExperimentRegistryError("entry surface or schema changed")
        if value.get("sequence") != sequence or value.get("previous_entry_hash") != previous_entry_hash:
            raise SharedPAExperimentRegistryError("entry deletion, truncation, or reordering detected")
        if value.get("entry_type") not in ENTRY_TYPES:
            raise SharedPAExperimentRegistryError("entry type is unknown")
        _utc(value.get("recorded_at_utc"), "entry.recorded_at_utc")
        payload = value.get("payload")
        if not isinstance(payload, Mapping) or value.get("payload_sha256") != sha256_bytes(canonical_bytes(payload)):
            raise SharedPAExperimentRegistryError("entry payload hash changed")
        core = {key: value[key] for key in value if key != "entry_hash"}
        expected_hash = sha256_bytes(canonical_bytes(core))
        if value.get("entry_hash") != expected_hash:
            raise SharedPAExperimentRegistryError("entry hash changed")
        expected_name = f"{sequence:08d}-{expected_hash}.json"
        if path.name != expected_name:
            raise SharedPAExperimentRegistryError("entry filename identity changed")
        if artifact_root is None and value["entry_type"] in {
            "EXPERIMENT_PREDECLARED", "CANDIDATE_FROZEN", "TERMINAL_DISPOSITION",
        }:
            raise SharedPAExperimentRegistryError("artifact root is required to reverify registered bytes")
        if artifact_root is not None:
            _validate_event_payload(
                value["entry_type"], payload, entries=entries, policy=policy, artifact_root=artifact_root,
            )
        entries.append(value)
        previous_entry_hash = expected_hash

    checkpoints = sorted((root / "checkpoints").iterdir())
    if len(checkpoints) != len(entries) + 1:
        raise SharedPAExperimentRegistryError("checkpoint deletion or truncation detected")
    previous_checkpoint_hash = ZERO_HASH
    for count, path in enumerate(checkpoints):
        if _is_link_or_reparse(path) or not path.is_file():
            raise SharedPAExperimentRegistryError("checkpoint is redirected or nonregular")
        value = _canonical_file(path, f"checkpoint {count}")
        head_hash = ZERO_HASH if count == 0 else entries[count - 1]["entry_hash"]
        checkpoint_hash = _validate_checkpoint(
            value,
            previous_hash=previous_checkpoint_hash,
            count=count,
            head_hash=head_hash,
            registry_id=policy["registry_id"],
        )
        if path.name != f"{count:08d}-{checkpoint_hash}.json":
            raise SharedPAExperimentRegistryError("checkpoint filename identity changed")
        previous_checkpoint_hash = checkpoint_hash

    issued = any(entry["entry_type"] == "SELECTION_TOKEN_ISSUED" for entry in entries)
    consumed = any(entry["entry_type"] == "SELECTION_TOKEN_CONSUMED" for entry in entries)
    head = _canonical_file(root / "HEAD.json", "registry HEAD")
    expected_head = {
        "schema_version": HEAD_SCHEMA,
        "registry_id": policy["registry_id"],
        "entry_count": len(entries),
        "head_entry_hash": previous_entry_hash,
        "checkpoint_hash": previous_checkpoint_hash,
        "token_issuance_state": "ISSUED" if issued else "UNISSUED",
        "token_consumption_state": "CONSUMED" if consumed else "UNCONSUMED",
        "configured_authority_state": identity["configured_authority_state"],
        "release_expectation_sha256": identity["release_expectation_sha256"],
    }
    if head != expected_head:
        raise SharedPAExperimentRegistryError("HEAD differs from replayed state")
    authority_state = UNBOUND_AUTHORITY
    release_expectation_sha256 = identity["release_expectation_sha256"]
    external_arguments = (
        external_release_expectation_path,
        expected_release_expectation_sha256,
        trusted_checkpoint_path,
        expected_trusted_checkpoint_sha256,
    )
    if any(value is not None for value in external_arguments):
        if any(value is None for value in external_arguments):
            raise SharedPAExperimentRegistryError("complete external release and checkpoint authority is required")
        if expected_release_expectation_sha256 != release_expectation_sha256:
            raise SharedPAExperimentRegistryError("external release expectation differs from initialized registry")
        if artifact_root is None:
            raise SharedPAExperimentRegistryError("artifact root is required for external authority verification")
        release_expectation = load_release_expectation(
            external_release_expectation_path,
            expected_sha256=expected_release_expectation_sha256,
            artifact_root=artifact_root,
            policy_path=root / "registry_policy.json",
        )
        checkpoint = load_bound_trusted_checkpoint(
            trusted_checkpoint_path,
            expected_sha256=expected_trusted_checkpoint_sha256,
            release_expectation=release_expectation,
        )
        if checkpoint != {
            "schema_version": TRUSTED_CHECKPOINT_SCHEMA,
            "registry_id": policy["registry_id"],
            "entry_count": len(entries),
            "head_entry_hash": previous_entry_hash,
            "checkpoint_hash": previous_checkpoint_hash,
        }:
            raise SharedPAExperimentRegistryError("external checkpoint is stale, replaced, or rolled back")
        authority_state = BOUND_AUTHORITY
    state = RegistryState(
        registry_id=policy["registry_id"],
        entry_count=len(entries),
        head_entry_hash=previous_entry_hash,
        checkpoint_hash=previous_checkpoint_hash,
        entries=tuple(entries),
        authority_state=authority_state,
        release_expectation_sha256=release_expectation_sha256,
    )
    if trusted_checkpoint is not None and dict(trusted_checkpoint) != state.trusted_checkpoint():
        raise SharedPAExperimentRegistryError("trusted checkpoint detects rollback or divergent registry")
    return state


def append_event(
    *,
    root: Path,
    artifact_root: Path,
    entry_type: str,
    payload: Mapping[str, Any],
    recorded_at_utc: str | None = None,
    trusted_checkpoint: Mapping[str, Any] | None = None,
    external_release_expectation_path: Path | None = None,
    expected_release_expectation_sha256: str | None = None,
    trusted_checkpoint_path: Path | None = None,
    expected_trusted_checkpoint_sha256: str | None = None,
    append_receipt_path: Path | None = None,
    expected_append_receipt_sha256: str | None = None,
    allow_unbound_test_only: bool = False,
) -> RegistryState:
    if entry_type not in ENTRY_TYPES:
        raise SharedPAExperimentRegistryError("unknown registry entry type")
    external_arguments = (
        external_release_expectation_path,
        expected_release_expectation_sha256,
        trusted_checkpoint_path,
        expected_trusted_checkpoint_sha256,
        append_receipt_path,
        expected_append_receipt_sha256,
    )
    authoritative = all(value is not None for value in external_arguments)
    if any(value is not None for value in external_arguments) and not authoritative:
        raise SharedPAExperimentRegistryError("complete external authority and append receipt are required")
    if not authoritative and not allow_unbound_test_only:
        raise SharedPAExperimentRegistryError("registry authority is UNBOUND; authoritative append refused")
    if not authoritative and trusted_checkpoint is None:
        raise SharedPAExperimentRegistryError("unbound test-only append still requires an exact local checkpoint")
    with _exclusive_lock(root):
        if authoritative:
            state = verify_registry(
                root=root,
                artifact_root=artifact_root,
                external_release_expectation_path=external_release_expectation_path,
                expected_release_expectation_sha256=expected_release_expectation_sha256,
                trusted_checkpoint_path=trusted_checkpoint_path,
                expected_trusted_checkpoint_sha256=expected_trusted_checkpoint_sha256,
                allow_lock=True,
            )
        else:
            state = verify_registry(
                root=root,
                artifact_root=artifact_root,
                trusted_checkpoint=trusted_checkpoint,
                allow_lock=True,
            )
        policy = validate_policy(_canonical_file(root / "registry_policy.json", "registry policy"))
        checked_payload = _validate_event_payload(
            entry_type,
            payload,
            entries=state.entries,
            policy=policy,
            artifact_root=artifact_root,
        )
        sequence = state.entry_count + 1
        trusted_time = recorded_at_utc or _now_utc()
        if authoritative:
            release_expectation = load_release_expectation(
                external_release_expectation_path,
                expected_sha256=expected_release_expectation_sha256,
                artifact_root=artifact_root,
                policy_path=root / "registry_policy.json",
            )
            receipt = load_append_receipt(
                append_receipt_path,
                expected_sha256=expected_append_receipt_sha256,
                release_expectation=release_expectation,
                state=state,
                entry_type=entry_type,
                payload=checked_payload,
                artifact_root=artifact_root,
            )
            trusted_time = receipt["trusted_recorded_at_utc"]
            if recorded_at_utc is not None and recorded_at_utc != trusted_time:
                raise SharedPAExperimentRegistryError("caller timestamp cannot override trusted append receipt")
        core = _entry_core(
            sequence,
            state.head_entry_hash,
            entry_type,
            trusted_time,
            checked_payload,
        )
        entry_hash = sha256_bytes(canonical_bytes(core))
        entry = {**core, "entry_hash": entry_hash}
        _publish_once(
            root / "entries" / f"{sequence:08d}-{entry_hash}.json",
            canonical_bytes(entry) + b"\n",
        )
        checkpoint_core = _checkpoint_core(
            state.registry_id,
            sequence,
            entry_hash,
            state.checkpoint_hash,
        )
        checkpoint_hash = sha256_bytes(canonical_bytes(checkpoint_core))
        checkpoint = {**checkpoint_core, "checkpoint_hash": checkpoint_hash}
        _publish_once(
            root / "checkpoints" / f"{sequence:08d}-{checkpoint_hash}.json",
            canonical_bytes(checkpoint) + b"\n",
        )
        issued = entry_type == "SELECTION_TOKEN_ISSUED" or any(
            item["entry_type"] == "SELECTION_TOKEN_ISSUED" for item in state.entries
        )
        consumed = entry_type == "SELECTION_TOKEN_CONSUMED" or any(
            item["entry_type"] == "SELECTION_TOKEN_CONSUMED" for item in state.entries
        )
        head = {
            "schema_version": HEAD_SCHEMA,
            "registry_id": state.registry_id,
            "entry_count": sequence,
            "head_entry_hash": entry_hash,
            "checkpoint_hash": checkpoint_hash,
            "token_issuance_state": "ISSUED" if issued else "UNISSUED",
            "token_consumption_state": "CONSUMED" if consumed else "UNCONSUMED",
            "configured_authority_state": (
                "EXTERNAL_RELEASE_CONFIGURED_CHECKPOINT_REQUIRED"
                if state.release_expectation_sha256 is not None else UNBOUND_AUTHORITY
            ),
            "release_expectation_sha256": state.release_expectation_sha256,
        }
        _replace_head(root / "HEAD.json", canonical_bytes(head) + b"\n")
        return verify_registry(root=root, artifact_root=artifact_root, allow_lock=True)


def write_trusted_checkpoint_once(path: Path, state: RegistryState) -> None:
    """Publish a checkpoint receipt outside the registry; never overwrite it."""
    _publish_once(path, canonical_bytes(state.trusted_checkpoint()) + b"\n")


def load_trusted_checkpoint(path: Path) -> dict[str, Any]:
    value = _canonical_file(path, "trusted checkpoint")
    if set(value) != {
        "schema_version", "registry_id", "entry_count", "head_entry_hash", "checkpoint_hash",
    } or value.get("schema_version") != TRUSTED_CHECKPOINT_SCHEMA:
        raise SharedPAExperimentRegistryError("trusted checkpoint surface or schema changed")
    _nonempty(value.get("registry_id"), "trusted_checkpoint.registry_id")
    if not isinstance(value.get("entry_count"), int) or isinstance(value.get("entry_count"), bool) or value["entry_count"] < 0:
        raise SharedPAExperimentRegistryError("trusted checkpoint count is invalid")
    _sha256(value.get("head_entry_hash"), "trusted_checkpoint.head_entry_hash")
    _sha256(value.get("checkpoint_hash"), "trusted_checkpoint.checkpoint_hash")
    return value
