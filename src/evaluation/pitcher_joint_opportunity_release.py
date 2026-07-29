"""Content-addressed verifier for the pre-fit joint pitcher scaffold."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from src.evaluation.pitcher_joint_opportunity_v1 import (
    load_evidence_authority_contract,
    load_protocol,
)


SCHEMA_VERSION = "pitcher-joint-opportunity-predeclared-release-v1"
RELEASE_ID = "pitcher_joint_opportunity_v1_predeclared"
EXPECTED_BASE_COMMIT = "506ebb40203582d25ff01c0a8c2d1dec4c44ed7d"
DEFAULT_MANIFEST_PATH = Path("reports/pitcher_joint_opportunity_v1_manifest.json")
REQUIRED_CHANGED_FILES = {
    "config": {
        "config/pitcher_joint_opportunity_v1_protocol.json",
        "config/pitcher_joint_opportunity_v1_evidence_authority.json",
    },
    "source": {
        "src/evaluation/pitcher_joint_opportunity_v1.py",
        "src/evaluation/pitcher_joint_opportunity_release.py",
    },
    "tests": {
        "tests/test_pitcher_joint_opportunity_v1.py",
        "tests/test_pitcher_joint_opportunity_release.py",
    },
}
REQUIRED_DELTA = set().union(*REQUIRED_CHANGED_FILES.values()) | {
    "reports/pitcher_joint_opportunity_v1_predeclared.md",
    "reports/pitcher_joint_opportunity_v1_manifest.json",
}
FROZEN_BINDINGS = {
    "src/prediction/prop_engine.py": "189f371575337259e5f290c219e2678cbe252502633fe10fad83b0ba4454759f",
    "src/prediction/role_innings.py": "ba6c221389f0f01fe71e785cb221a67683fe6c90c3d448c17e861047db36e5ab",
    "config/config.json": "afcf0f969f9ee28fd00ca2644315f9e7d36aefb19b1f693143c2337a4a34754f",
    "config/config.kbb.json": "ff65d440b7571aa490300b26b1cf8a6271c5979e564c1a74d1d0988a0b743bad",
}
RUNTIME_DEPENDENCY_BINDINGS = {
    "src/evaluation/forward_pitcher_context.py": "cfe56c92fbfa0702f36cf226fabe9e77f0202d2172f70f7b4a2d51ee941378cb",
    "src/evaluation/forward_pitcher_context_ledger.py": "ff34f4c9b7dea869b5c66ace5d3769e03212646ddb7cf8c58e80f074dae685dd",
    "src/evaluation/forward_pitcher_context_v2.py": "68ca25dff40a041ccc20717252898a45dfd58b772c35755bce19f8f6a8fb2c9a",
    "src/evaluation/shadow_capture_plan.py": "491a73556cab02b8295eee695f0474345defbc15f5d9bd01aae011cdff2b17e0",
}
_SHA = re.compile(r"^[0-9a-f]{64}$")


class PitcherJointOpportunityReleaseError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _duplicate_safe(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PitcherJointOpportunityReleaseError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_duplicate_safe)
    except (OSError, json.JSONDecodeError) as exc:
        raise PitcherJointOpportunityReleaseError(f"manifest is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise PitcherJointOpportunityReleaseError("manifest must be an object")
    return value


def _path(root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if posix.is_absolute() or not posix.parts or ".." in posix.parts:
        raise PitcherJointOpportunityReleaseError(f"unsafe manifest path: {relative}")
    resolved = (root / Path(*posix.parts)).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise PitcherJointOpportunityReleaseError(f"manifest path escapes root: {relative}") from exc
    return resolved


def _verify_hash_map(root: Path, value: object, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise PitcherJointOpportunityReleaseError(f"{label} file set differs")
    for relative, expected_hash in value.items():
        if not isinstance(expected_hash, str) or _SHA.fullmatch(expected_hash) is None:
            raise PitcherJointOpportunityReleaseError(f"{label} hash is invalid: {relative}")
        path = _path(root, relative)
        if not path.is_file() or file_sha256(path) != expected_hash:
            raise PitcherJointOpportunityReleaseError(f"{label} hash mismatch: {relative}")


def verify_manifest_payload(root: Path, payload: dict[str, Any]) -> None:
    expected = {
        "schema_version", "release_id", "classification", "status", "base_commit",
        "changed_files", "protocol", "evidence_authority", "runtime_dependency_bindings",
        "external_authorization", "frozen_production_bindings", "report",
        "test_evidence", "read_boundaries", "delivered_artifacts", "blockers",
        "promotion", "single_highest_value_next_action",
    }
    if set(payload) != expected:
        raise PitcherJointOpportunityReleaseError("manifest top-level schema differs")
    if payload["schema_version"] != SCHEMA_VERSION or payload["release_id"] != RELEASE_ID:
        raise PitcherJointOpportunityReleaseError("manifest release identity differs")
    if payload["classification"] != "PREDECLARED_RESEARCH_SCAFFOLD_ONLY" or payload["status"] != "NO_FIT_NO_SELECTION_NO_PREDICTION":
        raise PitcherJointOpportunityReleaseError("release classification or status differs")
    if payload["base_commit"] != EXPECTED_BASE_COMMIT:
        raise PitcherJointOpportunityReleaseError("base commit differs")
    changed = payload["changed_files"]
    if not isinstance(changed, dict) or set(changed) != set(REQUIRED_CHANGED_FILES):
        raise PitcherJointOpportunityReleaseError("changed-file groups differ")
    for group, paths in REQUIRED_CHANGED_FILES.items():
        _verify_hash_map(root, changed[group], paths, group)

    protocol = payload["protocol"]
    if not isinstance(protocol, dict) or set(protocol) != {"path", "sha256", "bytes_frozen_before_2024_access"}:
        raise PitcherJointOpportunityReleaseError("protocol binding differs")
    if protocol["bytes_frozen_before_2024_access"] is not True:
        raise PitcherJointOpportunityReleaseError("protocol is not frozen before 2024")
    protocol_path = _path(root, protocol["path"])
    if file_sha256(protocol_path) != protocol["sha256"]:
        raise PitcherJointOpportunityReleaseError("protocol hash mismatch")
    loaded_protocol = load_protocol(protocol_path)

    authority = payload["evidence_authority"]
    if not isinstance(authority, dict) or set(authority) != {
        "path", "sha256", "status", "authorized_receipt_sha256"
    }:
        raise PitcherJointOpportunityReleaseError("evidence authority binding differs")
    if authority != {
        "path": "config/pitcher_joint_opportunity_v1_evidence_authority.json",
        "sha256": "9c29e53656f70ce30bf1db4a415c814ab825f32288b3f482965d6cd5bc452a18",
        "status": "UNBOUND_NO_APPROVED_ARCHIVE_ERA",
        "authorized_receipt_sha256": None,
    }:
        raise PitcherJointOpportunityReleaseError("evidence authority declaration differs")
    if file_sha256(_path(root, authority["path"])) != authority["sha256"]:
        raise PitcherJointOpportunityReleaseError("evidence authority hash mismatch")
    loaded_authority = load_evidence_authority_contract(
        protocol_path=protocol_path, protocol=loaded_protocol
    )
    if loaded_authority["status"] != authority["status"]:
        raise PitcherJointOpportunityReleaseError("evidence authority status differs")

    if payload["runtime_dependency_bindings"] != RUNTIME_DEPENDENCY_BINDINGS:
        raise PitcherJointOpportunityReleaseError("runtime dependency binding declaration differs")
    _verify_hash_map(
        root,
        payload["runtime_dependency_bindings"],
        set(RUNTIME_DEPENDENCY_BINDINGS),
        "runtime dependency",
    )
    if payload["external_authorization"] != {
        "evidence_authority_required_for_probability": True,
        "authorized_evidence_authority_receipt_sha256": None,
        "runtime_release_required_for_probability": True,
        "authorized_runtime_release_sha256": None,
        "artifact_release_required_for_probability": True,
        "authorized_artifact_release_sha256": None,
        "synthetic_self_promotion_allowed": False,
    }:
        raise PitcherJointOpportunityReleaseError("external authorization boundary differs")

    if payload["frozen_production_bindings"] != FROZEN_BINDINGS:
        raise PitcherJointOpportunityReleaseError("frozen production binding declaration differs")
    _verify_hash_map(root, payload["frozen_production_bindings"], set(FROZEN_BINDINGS), "frozen production")

    report = payload["report"]
    if not isinstance(report, dict) or set(report) != {"path", "sha256"}:
        raise PitcherJointOpportunityReleaseError("report binding differs")
    if file_sha256(_path(root, report["path"])) != report["sha256"]:
        raise PitcherJointOpportunityReleaseError("report hash mismatch")

    evidence = payload["test_evidence"]
    if not isinstance(evidence, list) or len(evidence) < 2:
        raise PitcherJointOpportunityReleaseError("focused and impacted test evidence are required")
    for row in evidence:
        if not isinstance(row, dict) or set(row) != {"suite", "command", "result", "python", "network"}:
            raise PitcherJointOpportunityReleaseError("test evidence schema differs")
        if row["network"] != "DISABLED" or not all(isinstance(value, str) and value for value in row.values()):
            raise PitcherJointOpportunityReleaseError("test evidence is incomplete or networked")

    expected_boundaries = {
        "may_2026_read": False,
        "2024_outcomes_read": False,
        "any_outcomes_read": False,
        "model_fit": False,
        "historical_or_prospective_receipts_backfilled": False,
        "prices_or_settlements_read": False,
        "aws_or_collector_runtime_touched": False,
    }
    if payload["read_boundaries"] != expected_boundaries:
        raise PitcherJointOpportunityReleaseError("read boundary declaration differs")
    if payload["delivered_artifacts"] != {
        "fitted_model": None,
        "training_data": None,
        "feature_panel": None,
        "2024_selection": None,
        "prediction_archive": None,
    }:
        raise PitcherJointOpportunityReleaseError("release falsely declares a delivered predictive artifact")
    if not isinstance(payload["blockers"], list) or len(payload["blockers"]) < 5 or not all(isinstance(item, str) and item for item in payload["blockers"]):
        raise PitcherJointOpportunityReleaseError("truthful blockers are incomplete")
    if payload["promotion"] != {
        "evidence_collection_authorized": False,
        "protocol_locking_authorized": True,
        "fitting_authorized": False,
        "shadow_use_authorized": False,
        "production_changed": False,
        "betting_authorized": False,
    }:
        raise PitcherJointOpportunityReleaseError("promotion boundary differs")
    if not isinstance(payload["single_highest_value_next_action"], str) or not payload["single_highest_value_next_action"]:
        raise PitcherJointOpportunityReleaseError("next action is missing")


def _git(root: Path, args: list[str]) -> str:
    completed = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise PitcherJointOpportunityReleaseError(completed.stderr.strip() or "Git verification failed")
    return completed.stdout


def _changed_files(root: Path) -> set[str]:
    tracked = _git(root, ["diff", "--name-only", EXPECTED_BASE_COMMIT, "--", "config", "src", "tests", "reports"])
    untracked = _git(root, ["ls-files", "--others", "--exclude-standard", "--", "config", "src", "tests", "reports"])
    return {line.strip().replace("\\", "/") for line in (tracked + "\n" + untracked).splitlines() if line.strip()}


def verify_release(root: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    payload = load_manifest(manifest_path or root / DEFAULT_MANIFEST_PATH)
    verify_manifest_payload(root, payload)
    _git(root, ["cat-file", "-e", f"{EXPECTED_BASE_COMMIT}^{{commit}}"])
    changed = _changed_files(root)
    if changed != REQUIRED_DELTA:
        raise PitcherJointOpportunityReleaseError(
            f"release delta differs; expected {sorted(REQUIRED_DELTA)}, got {sorted(changed)}"
        )
    return payload
