"""Fail-closed verifier for the pitcher source-truth repair-only release."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = "pitcher-source-truth-repair-release-v1"
RELEASE_ID = "pitcher_source_truth_repair_v1"
EXPECTED_BASE_COMMIT = "9f9d839187550f7a7a6da3b4bfdb25b2dff3e794"
DEFAULT_MANIFEST_PATH = Path("reports/pitcher_source_truth_repair_v1_manifest.json")

REQUIRED_SOURCE_FILES = frozenset(
    {
        "src/data/mlb_api.py",
        "src/data/pitching_source_truth.py",
        "src/data/point_in_time.py",
        "src/evaluation/pitcher_source_truth_release.py",
        "src/features/feature_factory.py",
        "src/features/matchup_intelligence.py",
        "src/learning/training_set_builder.py",
        "src/prediction/daily_predictor.py",
        "src/prediction/prop_engine.py",
        "src/prediction/role_innings.py",
    }
)
REQUIRED_TEST_FILES = frozenset(
    {
        "tests/test_pitcher_projection.py",
        "tests/test_pitcher_source_truth_release.py",
        "tests/test_pitching_source_truth.py",
    }
)
REQUIRED_NOT_APPLICABLE_IDENTITIES = frozenset(
    {"configuration", "data", "feature_artifacts", "model", "market_policy"}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class PitcherSourceTruthReleaseError(ValueError):
    """The release manifest or one of its bound files is invalid."""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PitcherSourceTruthReleaseError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise PitcherSourceTruthReleaseError(f"cannot read release manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise PitcherSourceTruthReleaseError("release manifest must be a JSON object")
    return payload


def _safe_bound_path(root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if posix.is_absolute() or ".." in posix.parts or not posix.parts:
        raise PitcherSourceTruthReleaseError(f"unsafe bound path: {relative!r}")
    path = (root / Path(*posix.parts)).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise PitcherSourceTruthReleaseError(
            f"bound path escapes repository root: {relative!r}"
        ) from exc
    return path


def _validate_file_group(
    root: Path,
    supplied: Any,
    *,
    label: str,
    required: frozenset[str],
) -> None:
    if not isinstance(supplied, dict) or set(supplied) != set(required):
        raise PitcherSourceTruthReleaseError(
            f"{label} file set mismatch; expected {sorted(required)}"
        )
    for relative, expected_sha in supplied.items():
        if not isinstance(expected_sha, str) or _SHA256_RE.fullmatch(expected_sha) is None:
            raise PitcherSourceTruthReleaseError(
                f"invalid SHA-256 for {label} file {relative}"
            )
        path = _safe_bound_path(root, relative)
        if not path.is_file():
            raise PitcherSourceTruthReleaseError(f"bound file missing: {relative}")
        actual_sha = file_sha256(path)
        if actual_sha != expected_sha:
            raise PitcherSourceTruthReleaseError(
                f"hash mismatch for {relative}: expected {expected_sha}, got {actual_sha}"
            )


def verify_manifest_payload(root: Path, payload: dict[str, Any]) -> None:
    """Validate manifest semantics and every content hash, excluding Git state."""

    expected_top_level = {
        "schema_version",
        "release_id",
        "classification",
        "status",
        "base_commit",
        "changed_files",
        "artifact_identities",
        "test_evidence",
        "promotion",
        "report",
        "limitations",
    }
    if set(payload) != expected_top_level:
        raise PitcherSourceTruthReleaseError("release manifest top-level fields mismatch")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise PitcherSourceTruthReleaseError("unexpected release schema_version")
    if payload["release_id"] != RELEASE_ID:
        raise PitcherSourceTruthReleaseError("unexpected release_id")
    if payload["classification"] != "REPAIR_ONLY_NO_PREDICTIVE_PROMOTION":
        raise PitcherSourceTruthReleaseError("release classification is not repair-only")
    if payload["status"] != "SOURCE_TRUTH_REPAIR_VERIFIED_NOT_ACTIVE":
        raise PitcherSourceTruthReleaseError("unexpected release status")
    base_commit = payload["base_commit"]
    if (
        not isinstance(base_commit, str)
        or _COMMIT_RE.fullmatch(base_commit) is None
        or base_commit != EXPECTED_BASE_COMMIT
    ):
        raise PitcherSourceTruthReleaseError("base_commit mismatch")

    changed = payload["changed_files"]
    if not isinstance(changed, dict) or set(changed) != {"source", "tests"}:
        raise PitcherSourceTruthReleaseError("changed_files must contain source and tests")
    _validate_file_group(
        root, changed["source"], label="source", required=REQUIRED_SOURCE_FILES
    )
    _validate_file_group(
        root, changed["tests"], label="test", required=REQUIRED_TEST_FILES
    )

    identities = payload["artifact_identities"]
    if not isinstance(identities, dict) or set(identities) != set(
        REQUIRED_NOT_APPLICABLE_IDENTITIES
    ):
        raise PitcherSourceTruthReleaseError("artifact identity categories mismatch")
    for category, identity in identities.items():
        if not isinstance(identity, dict) or set(identity) != {"status", "reason"}:
            raise PitcherSourceTruthReleaseError(
                f"invalid artifact identity declaration for {category}"
            )
        if identity["status"] != "NOT_APPLICABLE" or not isinstance(
            identity["reason"], str
        ) or not identity["reason"].strip():
            raise PitcherSourceTruthReleaseError(
                f"artifact identity {category} must have an explicit NOT_APPLICABLE reason"
            )

    test_evidence = payload["test_evidence"]
    if not isinstance(test_evidence, list) or len(test_evidence) < 2:
        raise PitcherSourceTruthReleaseError("at least two test-evidence records are required")
    for index, record in enumerate(test_evidence):
        required_fields = {"suite", "command", "python", "result", "network"}
        if not isinstance(record, dict) or set(record) != required_fields:
            raise PitcherSourceTruthReleaseError(
                f"test-evidence record {index} fields mismatch"
            )
        if not all(isinstance(record[field], str) and record[field].strip() for field in required_fields):
            raise PitcherSourceTruthReleaseError(
                f"test-evidence record {index} contains an empty field"
            )
        if record["network"] != "DISABLED":
            raise PitcherSourceTruthReleaseError("release tests must be network-disabled")

    promotion = payload["promotion"]
    expected_promotion = {
        "model_fit": False,
        "outcomes_scored": False,
        "production_changed": False,
        "betting_authorized": False,
    }
    if promotion != expected_promotion:
        raise PitcherSourceTruthReleaseError("repair-only promotion boundary mismatch")

    report = payload["report"]
    if not isinstance(report, dict) or set(report) != {"path", "sha256"}:
        raise PitcherSourceTruthReleaseError("report binding fields mismatch")
    if not isinstance(report["sha256"], str) or _SHA256_RE.fullmatch(report["sha256"]) is None:
        raise PitcherSourceTruthReleaseError("invalid report SHA-256")
    report_path = _safe_bound_path(root, report["path"])
    if not report_path.is_file() or file_sha256(report_path) != report["sha256"]:
        raise PitcherSourceTruthReleaseError("report hash mismatch")

    limitations = payload["limitations"]
    if not isinstance(limitations, list) or not limitations or not all(
        isinstance(item, str) and item.strip() for item in limitations
    ):
        raise PitcherSourceTruthReleaseError("release limitations must be explicit")


def _run_git(root: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PitcherSourceTruthReleaseError(
            f"Git verification failed for {' '.join(args)}: {completed.stderr.strip()}"
        )
    return completed.stdout


def _changed_source_test_files(root: Path, base_commit: str) -> set[str]:
    tracked = _run_git(
        root,
        ["diff", "--name-only", base_commit, "--", "src", "tests"],
    )
    untracked = _run_git(
        root,
        ["ls-files", "--others", "--exclude-standard", "--", "src", "tests"],
    )
    return {
        line.strip().replace("\\", "/")
        for line in (tracked + "\n" + untracked).splitlines()
        if line.strip()
    }


def verify_release(
    root: Path,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Verify hashes, the exact source/test delta, and the bound base commit."""

    root = root.resolve()
    manifest_path = manifest_path or (root / DEFAULT_MANIFEST_PATH)
    payload = load_manifest(manifest_path)
    verify_manifest_payload(root, payload)

    base_commit = payload["base_commit"]
    _run_git(root, ["cat-file", "-e", f"{base_commit}^{{commit}}"])
    actual_changed = _changed_source_test_files(root, base_commit)
    expected_changed = set(REQUIRED_SOURCE_FILES) | set(REQUIRED_TEST_FILES)
    if actual_changed != expected_changed:
        raise PitcherSourceTruthReleaseError(
            "source/test delta mismatch; "
            f"expected {sorted(expected_changed)}, got {sorted(actual_changed)}"
        )
    return payload

