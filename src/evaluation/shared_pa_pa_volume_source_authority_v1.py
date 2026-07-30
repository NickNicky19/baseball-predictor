"""Fail-closed source authority for a historical PA-volume artifact.

Artifact byte identity is necessary but is not source authority.  This module
requires an independently digest-anchored authority receipt, a semantically
qualified source manifest, and a rebuild manifest that binds the exact PA
artifact before a parent probability builder may run.

It deliberately does not import or modify any projected-opportunity candidate.
A future candidate must opt in explicitly by calling
``invoke_after_pa_volume_source_authority``.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from scripts import capture_pa_volume_official_source_v1 as capture
from scripts import verify_direct_batter_pa_source_runtime_authority as runtime_authority
from src.evaluation import pa_volume_historical_source_access_v1 as historical_access
from src.evaluation.pa_volume_source_truth_v2 import (
    PAVolumeSourceTruthError,
    build_pa_volume_artifact,
    canonical_json_bytes,
    validate_pa_volume_artifact,
)


class PAVolumeSourceAuthorityError(ValueError):
    """PA-volume source authority is missing, blocked, late, or contradictory."""


AUTHORITY_SCHEMA = "shared-pa-pa-volume-source-authority-v3"
REBUILD_SCHEMA = "shared-pa-pa-volume-qualified-rebuild-v1"
RECEIPT_SCHEMA = "shared-pa-pa-volume-source-authority-runtime-receipt-v3"
CAPTURE_EVIDENCE_SCHEMA = "pa-volume-qualified-capture-receipt-evidence-v1"
COMPLETE_DECISION = "SOURCE_AND_DEPENDENCY_AUTHORITY_COMPLETE"
REBUILD_COMPLETE_DECISION = "QUALIFIED_SOURCE_REBUILD_COMPLETE"
SOURCE_RELEASE_SCHEMA = "pa-volume-official-source-release-v1"
SOURCE_EXTERNAL_VERIFICATION_SCHEMA = (
    "pa-volume-official-source-external-verification-v1"
)
OFFICIAL_PROJECTION_SCHEMA = "pa-volume-official-feed-projection-v1"
OFFICIAL_ROW_SCHEMA = "pa-volume-official-starter-projection-v2"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_T = TypeVar("_T")

_SOURCE_RELEASE_PATHS = {
    "manifest": "source_release/source_manifest.json",
    "schedule": "source_release/schedule_index.json",
    "projection": "source_release/projection.json",
    "verification": "source_release_external_verification.json",
    "rebuild": "rebuild/manifest.json",
    "artifact": "artifacts/pa_volume.json",
    "lock": "locks/requirements.lock",
    "source_access": "source_access/authorization.json",
    "runtime_policy": "source_access/runtime_policy.json",
    "capture_evidence": "source_receipts/manifest.json",
}
_REVIEWED_SOURCE_PATHS = [
    "scripts/capture_direct_batter_pa_source_transport_v2.py",
    "scripts/capture_pa_volume_official_source_v1.py",
    "scripts/build_pa_volume_official_source_release_v1.py",
    "src/evaluation/pa_volume_official_feed_projection_v1.py",
    "src/evaluation/pa_volume_source_truth_v2.py",
]

_REQUIRED_AUTHORITY = {
    "raw_transport_request_and_response_receipts": True,
    "independent_official_source_receipts": True,
    "exact_reproducible_dependency_lock": True,
    "external_expected_release_digest": True,
}
_PROTECTED = {
    "may_2026_accessed": False,
    "selection_2024_accessed": False,
    "spent_hr_confirmation_2025_accessed": False,
    "prices_accessed": False,
    "prospective_evidence_accessed": False,
    "prospective_backfill_performed": False,
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _exact_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        if _is_reparse(path):
            raise PAVolumeSourceAuthorityError(
                "qualified authority contains a symlink or reparse point"
            )
        if path.is_file():
            files.add(path.relative_to(root).as_posix())
    return files


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PAVolumeSourceAuthorityError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise PAVolumeSourceAuthorityError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PAVolumeSourceAuthorityError(
            f"{label} is not a valid ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PAVolumeSourceAuthorityError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _is_reparse(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _safe_file(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise PAVolumeSourceAuthorityError(f"{label} path must be non-empty")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise PAVolumeSourceAuthorityError(f"{label} path is unsafe")
    candidate = root / rel
    if not candidate.is_file():
        raise PAVolumeSourceAuthorityError(f"{label} is missing")
    root_resolved = root.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PAVolumeSourceAuthorityError(f"{label} escapes authority root") from exc
    current = candidate
    while True:
        if _is_reparse(current):
            raise PAVolumeSourceAuthorityError(
                f"{label} traverses a symlink, junction, or reparse point"
            )
        if current.resolve(strict=True) == root_resolved:
            break
        current = current.parent
    return candidate


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise PAVolumeSourceAuthorityError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise PAVolumeSourceAuthorityError(f"{label} must be an object")
    return value, raw


def _binding(
    value: Any, *, root: Path, label: str
) -> tuple[Path, str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise PAVolumeSourceAuthorityError(f"{label} binding changed")
    relative = value.get("path")
    expected = _sha(value.get("sha256"), f"{label}.sha256")
    path = _safe_file(root, relative, label)
    actual = _sha256_bytes(path.read_bytes())
    if actual != expected:
        raise PAVolumeSourceAuthorityError(f"{label} bytes differ")
    return path, expected, str(relative)


def _canonical_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    value, raw = _read_json(path, label)
    if raw != canonical_json_bytes(value):
        raise PAVolumeSourceAuthorityError(f"{label} is not canonical JSON")
    return value, raw


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PAVolumeSourceAuthorityError(f"{label} must be a positive integer")
    return value


def _date_2023(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise PAVolumeSourceAuthorityError(f"{label} must be a canonical 2023 date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PAVolumeSourceAuthorityError(
            f"{label} must be a canonical 2023 date"
        ) from exc
    if parsed.year != 2023 or parsed.isoformat() != value:
        raise PAVolumeSourceAuthorityError(f"{label} must be a canonical 2023 date")
    return value


def _active_reviewed_source_files() -> list[dict[str, str]]:
    repo = Path(__file__).resolve().parents[2]
    rows: list[dict[str, str]] = []
    for relative in _REVIEWED_SOURCE_PATHS:
        path = repo / relative
        if not path.is_file() or _is_reparse(path):
            raise PAVolumeSourceAuthorityError(
                f"reviewed source is missing or unsafe: {relative}"
            )
        rows.append({"path": relative, "sha256": _sha256_bytes(path.read_bytes())})
    return rows


def _active_capture_source_bundle_sha256() -> str:
    repo = Path(__file__).resolve().parents[2]
    rows = [
        {
            "path": relative,
            "sha256": _sha256_bytes((repo / relative).read_bytes()),
        }
        for relative in _REVIEWED_SOURCE_PATHS[:2]
    ]
    return _sha256_bytes(canonical_json_bytes(rows))


def _verify_runtime_policy(
    *, policy_path: Path, policy_sha: str, lock_sha: str
) -> None:
    if _sha256_bytes(policy_path.read_bytes()) != policy_sha:
        raise PAVolumeSourceAuthorityError("runtime-policy bytes differ")
    try:
        policy = runtime_authority.load_policy(policy_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PAVolumeSourceAuthorityError(
            "runtime-policy semantic validation failed"
        ) from exc
    dependency_lock = policy.get("dependency_lock")
    if (
        not isinstance(dependency_lock, Mapping)
        or dependency_lock.get("path")
        != "requirements-direct-batter-pa-source-authority.lock"
        or dependency_lock.get("sha256") != lock_sha
    ):
        raise PAVolumeSourceAuthorityError(
            "runtime policy binds a different dependency lock"
        )
    audited = policy.get("audited_python_sha256")
    if not isinstance(audited, Mapping):
        raise PAVolumeSourceAuthorityError("runtime-policy audited source is invalid")
    repo = Path(__file__).resolve().parents[2]
    for relative, expected in audited.items():
        if not isinstance(relative, str):
            raise PAVolumeSourceAuthorityError(
                "runtime-policy audited source path is invalid"
            )
        path = _safe_file(repo, relative, "runtime-policy audited source")
        if _sha256_bytes(path.read_bytes()) != _sha(
            expected, "runtime-policy audited source digest"
        ):
            raise PAVolumeSourceAuthorityError(
                "runtime-policy audited source differs from active bytes"
            )


def _capture_manifest_digest(value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned["observed_capture_digest"] = None
    return _sha256_bytes(canonical_json_bytes(unsigned))


def _receipt_window(
    receipt: Mapping[str, Any], label: str
) -> tuple[datetime, datetime]:
    response = receipt.get("response")
    attempts = receipt.get("attempts")
    if not isinstance(response, Mapping) or not isinstance(attempts, list) or not attempts:
        raise PAVolumeSourceAuthorityError(f"{label} lacks retained request timing")
    requests = [_utc(response.get("requested_at_utc"), f"{label} request")]
    observations = [_utc(response.get("observed_at_utc"), f"{label} observation")]
    for index, attempt in enumerate(attempts, 1):
        if not isinstance(attempt, Mapping):
            raise PAVolumeSourceAuthorityError(f"{label} attempt is malformed")
        requested = _utc(
            attempt.get("requested_at_utc"), f"{label} attempt {index} request"
        )
        observed = _utc(
            attempt.get("observed_at_utc"),
            f"{label} attempt {index} observation",
        )
        if observed < requested:
            raise PAVolumeSourceAuthorityError(f"{label} attempt timing is reversed")
        requests.append(requested)
        observations.append(observed)
    if (
        attempts[-1].get("requested_at_utc") != response.get("requested_at_utc")
        or attempts[-1].get("observed_at_utc") != response.get("observed_at_utc")
    ):
        raise PAVolumeSourceAuthorityError(
            f"{label} final attempt and response timing differ"
        )
    return min(requests), max(observations)


def verify_copied_capture_receipt_semantics(
    *, root: Path, receipt: Mapping[str, Any], expected_request: Mapping[str, Any],
    context_relative: str, journal_relative: str, original_journal_relative: str,
    support_hashes: Mapping[str, str], label: str,
) -> tuple[datetime, datetime, set[str]]:
    """Revalidate copied receipt metadata when raw response bodies are not retained."""
    if set(receipt) != {
        "schema_version", "authorization", "season", "research_only",
        "betting_authorized", "model_fitting_performed", "probabilities_generated",
        "protected_data", "request", "response", "runtime_attestation_sha256",
        "source_access_authorization_id", "source_access_authorization_sha256",
        "source_bundle_sha256", "request_policy", "attempts", "capture_window",
        "capture_context_sha256", "attempt_journal",
    }:
        raise PAVolumeSourceAuthorityError(f"{label} positive schema differs")
    policy = receipt.get("request_policy")
    window = receipt.get("capture_window")
    if not isinstance(policy, Mapping) or set(policy) != {
        "minimum_request_interval_seconds", "maximum_attempts", "retry_base_seconds",
        "retry_max_seconds", "overall_timeout_seconds",
    } or policy.get("retry_base_seconds") != capture.RETRY_BASE_SECONDS or policy.get(
        "retry_max_seconds"
    ) != capture.RETRY_MAX_SECONDS:
        raise PAVolumeSourceAuthorityError(f"{label} request policy differs")
    try:
        capture._validate_operational_policy(
            timeout_seconds=30.0,
            minimum_request_interval_seconds=policy["minimum_request_interval_seconds"],
            maximum_attempts=policy["maximum_attempts"],
            overall_timeout_seconds=policy["overall_timeout_seconds"],
        )
        capture._validate_attempt_history(
            attempts=receipt.get("attempts"), policy=policy,
            capture_window=window, require_success=True,
        )
    except capture.OfficialSourceCaptureError as exc:
        raise PAVolumeSourceAuthorityError(
            f"{label} deterministic attempt history differs"
        ) from exc
    consumed: set[str] = set()

    def document(relative: str, kind: str) -> dict[str, Any]:
        expected = support_hashes.get(relative)
        if expected is None:
            raise PAVolumeSourceAuthorityError(f"{label} support evidence is incomplete")
        value, raw = _canonical_json(_safe_file(root, relative, kind), kind)
        if _sha256_bytes(raw) != expected:
            raise PAVolumeSourceAuthorityError(f"{label} support bytes differ")
        consumed.add(relative)
        return value

    context = document(context_relative, f"{label} capture context")
    if not isinstance(window, Mapping) or set(window) != {
        "capture_started_at_utc", "deadline_at_utc", "authorization_valid_from_utc",
        "authorization_expires_at_utc",
    } or set(context) != {
        "schema_version", "status", "plan_sha256", "runtime_attestation_sha256",
        "source_access_authorization_id", "source_access_authorization_sha256",
        "source_bundle_sha256", "request_policy", "capture_started_at_utc",
        "deadline_at_utc", "authorization_valid_from_utc", "authorization_expires_at_utc",
    } or context.get("schema_version") != capture.CAPTURE_CONTEXT_SCHEMA or context.get(
        "status"
    ) != "ACTIVE_OR_COMPLETE_IMMUTABLE_CAPTURE_CONTEXT" or support_hashes[
        context_relative
    ] != receipt.get("capture_context_sha256") or context.get(
        "runtime_attestation_sha256"
    ) != receipt.get("runtime_attestation_sha256") or context.get(
        "source_access_authorization_id"
    ) != receipt.get("source_access_authorization_id") or context.get(
        "source_access_authorization_sha256"
    ) != receipt.get("source_access_authorization_sha256") or context.get(
        "source_bundle_sha256"
    ) != receipt.get("source_bundle_sha256") or context.get(
        "request_policy"
    ) != policy or {key: context.get(key) for key in window} != dict(window):
        raise PAVolumeSourceAuthorityError(f"{label} capture context binding differs")
    started = _utc(context["capture_started_at_utc"], f"{label} start")
    deadline = _utc(context["deadline_at_utc"], f"{label} deadline")
    if deadline != started + timedelta(seconds=float(policy["overall_timeout_seconds"])):
        raise PAVolumeSourceAuthorityError(f"{label} capture deadline differs")

    journal = receipt.get("attempt_journal")
    if not isinstance(journal, Mapping) or set(journal) != {
        "path", "context_sha256", "reservation_files", "result_files"
    } or journal.get("path") != original_journal_relative:
        raise PAVolumeSourceAuthorityError(f"{label} journal binding differs")
    request_context_relative = f"{journal_relative}/context.json"
    request_context = document(request_context_relative, f"{label} request context")
    expected_context = capture._request_context(
        run_context_sha256=str(receipt.get("capture_context_sha256")),
        request=expected_request,
    )
    if request_context != expected_context or support_hashes[
        request_context_relative
    ] != journal.get("context_sha256"):
        raise PAVolumeSourceAuthorityError(f"{label} request context differs")
    attempts = receipt.get("attempts")
    reservations = journal.get("reservation_files")
    results = journal.get("result_files")
    if not isinstance(attempts, list) or not isinstance(reservations, list) or not isinstance(
        results, list
    ) or len(attempts) != len(reservations) or len(attempts) != len(results):
        raise PAVolumeSourceAuthorityError(f"{label} journal closure differs")
    context_sha = _sha256_bytes(canonical_json_bytes(expected_context))
    reservation_paths: list[str] = []
    reservation_times: list[datetime] = []
    for index, binding in enumerate(reservations, 1):
        name = f"reservation-{index:04d}.json"
        relative = f"{journal_relative}/{name}"
        reservation = document(relative, f"{label} reservation")
        if not isinstance(binding, Mapping) or binding != {
            "path": name, "sha256": support_hashes[relative]
        } or set(reservation) != {
            "schema_version", "context_sha256", "attempt", "reserved_at_utc"
        } or reservation.get("schema_version") != capture.ATTEMPT_RESERVATION_SCHEMA or reservation.get(
            "context_sha256"
        ) != context_sha or reservation.get("attempt") != index:
            raise PAVolumeSourceAuthorityError(f"{label} reservation differs")
        reserved = _utc(reservation["reserved_at_utc"], f"{label} reservation")
        requested = _utc(attempts[index - 1]["requested_at_utc"], f"{label} request")
        if not (started <= reserved <= requested and reserved < deadline):
            raise PAVolumeSourceAuthorityError(f"{label} reservation chronology differs")
        if index > 1:
            prior = attempts[index - 2]
            if reserved < _utc(prior["requested_at_utc"], "prior request") + timedelta(
                seconds=float(policy["minimum_request_interval_seconds"])
            ) or reserved < _utc(prior["observed_at_utc"], "prior observation") + timedelta(
                seconds=float(prior["backoff_seconds"])
            ):
                raise PAVolumeSourceAuthorityError(f"{label} reservation pacing differs")
        reservation_paths.append(relative)
        reservation_times.append(reserved)
    journal_attempts: list[dict[str, Any]] = []
    for index, binding in enumerate(results, 1):
        name = f"result-{index:04d}.json"
        relative = f"{journal_relative}/{name}"
        result = document(relative, f"{label} result")
        if not isinstance(binding, Mapping) or binding != {
            "path": name, "sha256": support_hashes[relative]
        } or set(result) != {
            "schema_version", "context_sha256", "reservation_sha256", "attempt"
        } or result.get("schema_version") != capture.ATTEMPT_RESULT_SCHEMA or result.get(
            "context_sha256"
        ) != context_sha or result.get("reservation_sha256") != support_hashes[
            reservation_paths[index - 1]
        ] or not isinstance(result.get("attempt"), Mapping) or result[
            "attempt"
        ].get("attempt") != index or reservation_times[index - 1] > _utc(
            result["attempt"]["requested_at_utc"], f"{label} request"
        ):
            raise PAVolumeSourceAuthorityError(f"{label} result differs")
        journal_attempts.append(dict(result["attempt"]))
    if journal_attempts != attempts:
        raise PAVolumeSourceAuthorityError(f"{label} journal attempts differ")
    response = receipt.get("response")
    if not isinstance(response, Mapping) or set(response) != {
        "status", "final_url", "requested_at_utc", "observed_at_utc", "headers",
        "body_path", "body_bytes", "body_sha256",
    } or response.get("status") != 200 or response.get("final_url") != expected_request.get(
        "full_url"
    ) or not isinstance(response.get("headers"), Mapping):
        raise PAVolumeSourceAuthorityError(f"{label} response semantics differ")
    _sha(response.get("body_sha256"), f"{label} response body")
    if attempts[-1].get("requested_at_utc") != response.get(
        "requested_at_utc"
    ) or attempts[-1].get("observed_at_utc") != response.get("observed_at_utc"):
        raise PAVolumeSourceAuthorityError(f"{label} final response differs")
    first, latest = _receipt_window(receipt, label)
    return first, latest, consumed


def _capture_evidence_entries(
    root: Path,
) -> tuple[dict[str, Any], bytes, list[Mapping[str, Any]]]:
    path = _safe_file(root, _SOURCE_RELEASE_PATHS["capture_evidence"], "capture receipt evidence")
    value, raw = _canonical_json(path, "capture receipt evidence")
    if set(value) != {
        "schema_version", "source_access_authorization_id",
        "source_access_authorization_sha256", "source_bundle_sha256",
        "schedule_capture_observed_digest", "feed_capture_observed_digest",
        "entries",
    } or value.get("schema_version") != CAPTURE_EVIDENCE_SCHEMA:
        raise PAVolumeSourceAuthorityError(
            "capture receipt evidence positive schema differs"
        )
    entries = value.get("entries")
    if not isinstance(entries, list) or not entries:
        raise PAVolumeSourceAuthorityError("capture receipt evidence is empty")
    paths: list[str] = []
    roles: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {
            "role", "request_id", "path", "sha256"
        }:
            raise PAVolumeSourceAuthorityError(
                "capture receipt evidence entry schema differs"
            )
        role = entry.get("role")
        request_id = entry.get("request_id")
        relative = entry.get("path")
        if (
            role not in {
                "schedule_manifest", "schedule_receipt", "feed_manifest",
                "feed_plan", "feed_receipt", "schedule_support", "feed_support",
            }
            or not isinstance(request_id, str)
            or not request_id
            or not isinstance(relative, str)
        ):
            raise PAVolumeSourceAuthorityError(
                "capture receipt evidence identity differs"
            )
        expected_relative = {
            "schedule_manifest": "source_receipts/schedule/manifest.json",
            "schedule_receipt": "source_receipts/schedule/receipt.json",
            "feed_manifest": "source_receipts/feeds/manifest.json",
            "feed_plan": "source_receipts/feeds/plan.json",
        }.get(str(role), f"source_receipts/feeds/{request_id}/receipt.json")
        support_prefix = {
            "schedule_support": "source_receipts/schedule/",
            "feed_support": "source_receipts/feeds/",
        }.get(str(role))
        if (support_prefix is None and relative != expected_relative) or (
            support_prefix is not None and not relative.startswith(support_prefix)
        ):
            raise PAVolumeSourceAuthorityError(
                "capture receipt evidence path differs from its role"
            )
        retained = _safe_file(root, relative, "retained capture evidence")
        if _sha256_bytes(retained.read_bytes()) != _sha(
            entry.get("sha256"), "capture receipt evidence digest"
        ):
            raise PAVolumeSourceAuthorityError(
                "retained capture receipt bytes differ"
            )
        paths.append(relative)
        roles.append(str(role))
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise PAVolumeSourceAuthorityError(
            "capture receipt evidence paths are not unique and sorted"
        )
    if any(roles.count(role) != 1 for role in (
        "schedule_manifest", "schedule_receipt", "feed_manifest", "feed_plan"
    )) or roles.count("feed_receipt") < 1 or roles.count("schedule_support") < 3 or roles.count(
        "feed_support"
    ) < 3:
        raise PAVolumeSourceAuthorityError(
            "capture receipt evidence role coverage differs"
        )
    return value, raw, entries


def _verify_capture_evidence_window(
    *,
    root: Path,
    evidence: Mapping[str, Any],
    entries: list[Mapping[str, Any]],
    source_manifest: Mapping[str, Any],
    authorization_path: Path,
    authorization_sha: str,
    policy_sha: str,
) -> tuple[datetime, datetime]:
    singleton = {
        str(entry["role"]): entry
        for entry in entries
        if entry["role"] != "feed_receipt"
    }
    feed_entries = {
        str(entry["request_id"]): entry
        for entry in entries
        if entry["role"] == "feed_receipt"
    }
    support_hashes = {
        str(entry["path"]): str(entry["sha256"])
        for entry in entries
        if entry["role"] in {"schedule_support", "feed_support"}
    }
    consumed_support: set[str] = set()

    def document(entry: Mapping[str, Any], label: str) -> dict[str, Any]:
        value, _ = _canonical_json(_safe_file(root, entry["path"], label), label)
        return value

    schedule_manifest = document(singleton["schedule_manifest"], "schedule manifest")
    feed_manifest = document(singleton["feed_manifest"], "feed manifest")
    plan = document(singleton["feed_plan"], "feed plan")
    schedule_manifest_path = _safe_file(
        root, singleton["schedule_manifest"]["path"], "schedule manifest"
    )
    feed_manifest_path = _safe_file(
        root, singleton["feed_manifest"]["path"], "feed manifest"
    )
    if (
        _sha256_bytes(schedule_manifest_path.read_bytes())
        != source_manifest["schedule_capture_manifest_sha256"]
        or _sha256_bytes(feed_manifest_path.read_bytes())
        != source_manifest["feed_capture_manifest_sha256"]
        or _capture_manifest_digest(schedule_manifest)
        != source_manifest["schedule_capture_observed_digest"]
        or _capture_manifest_digest(feed_manifest)
        != source_manifest["feed_capture_observed_digest"]
    ):
        raise PAVolumeSourceAuthorityError(
            "capture receipt evidence differs from source manifest"
        )
    active_source_bundle = _active_capture_source_bundle_sha256()
    if (
        evidence.get("source_access_authorization_id")
        != source_manifest["source_access_authorization_id"]
        or evidence.get("source_access_authorization_sha256") != authorization_sha
        or evidence.get("source_bundle_sha256") != active_source_bundle
        or evidence.get("schedule_capture_observed_digest")
        != source_manifest["schedule_capture_observed_digest"]
        or evidence.get("feed_capture_observed_digest")
        != source_manifest["feed_capture_observed_digest"]
    ):
        raise PAVolumeSourceAuthorityError(
            "capture receipt evidence authority differs"
        )
    requests = plan.get("requests")
    if (
        plan.get("schema_version") != capture.PLAN_SCHEMA
        or plan.get("status") != "LOCKED_OFFICIAL_2023_FINAL_FEED_CAPTURE_PLAN"
        or plan.get("authorization") != capture.AUTHORIZATION
        or plan.get("season") != 2023
        or plan.get("research_only") is not True
        or plan.get("betting_authorized") is not False
        or plan.get("protected_data") != _PROTECTED
        or not isinstance(requests, list)
        or len(requests) != source_manifest["game_count"]
    ):
        raise PAVolumeSourceAuthorityError("retained feed plan is invalid")
    request_ids = [
        request.get("request_id") if isinstance(request, Mapping) else None
        for request in requests
    ]
    if (
        any(not isinstance(request_id, str) or not request_id for request_id in request_ids)
        or len(set(request_ids)) != len(request_ids)
        or len(feed_entries) != len(requests)
        or feed_manifest.get("plan_sha256")
        != _sha256_bytes(canonical_json_bytes(plan))
        or feed_manifest.get("schedule_capture_digest")
        != schedule_manifest.get("observed_capture_digest")
    ):
        raise PAVolumeSourceAuthorityError("retained feed authority differs")
    schedule_entry = singleton["schedule_receipt"]
    schedule_receipt = document(schedule_entry, "schedule receipt")
    if (
        schedule_receipt.get("request") != schedule_manifest.get("request")
        or schedule_entry["sha256"] != schedule_manifest.get("receipt_sha256")
    ):
        raise PAVolumeSourceAuthorityError("schedule receipt identity differs")
    pairs: list[tuple[Mapping[str, Any], Mapping[str, Any], str]] = [
        (schedule_receipt, schedule_manifest.get("request") or {}, "schedule receipt")
    ]
    expected_feed_manifest_entries = []
    for request in requests:
        if not isinstance(request, Mapping):
            raise PAVolumeSourceAuthorityError("feed request is malformed")
        request_id = str(request["request_id"])
        entry = feed_entries.get(request_id)
        if entry is None:
            raise PAVolumeSourceAuthorityError("feed receipt evidence is incomplete")
        receipt = document(entry, f"feed receipt {request_id}")
        if receipt.get("request") != request:
            raise PAVolumeSourceAuthorityError("feed receipt identity differs")
        expected_feed_manifest_entries.append(
            {
                "request_id": request_id,
                "response_sha256": (receipt.get("response") or {}).get("body_sha256"),
                "receipt_sha256": entry["sha256"],
            }
        )
        pairs.append((receipt, request, f"feed receipt {request_id}"))
    if feed_manifest.get("entries") != expected_feed_manifest_entries:
        raise PAVolumeSourceAuthorityError("feed receipt digest binding differs")
    firsts: list[datetime] = []
    lasts: list[datetime] = []
    prior_feed_request: datetime | None = None
    for receipt, expected_request, label in pairs:
        if (
            receipt.get("schema_version") != capture.RECEIPT_SCHEMA
            or receipt.get("authorization") != capture.AUTHORIZATION
            or receipt.get("season") != 2023
            or receipt.get("research_only") is not True
            or receipt.get("betting_authorized") is not False
            or receipt.get("model_fitting_performed") is not False
            or receipt.get("probabilities_generated") is not False
            or receipt.get("protected_data") != _PROTECTED
            or receipt.get("request") != expected_request
            or receipt.get("source_access_authorization_id")
            != source_manifest["source_access_authorization_id"]
            or receipt.get("source_access_authorization_sha256") != authorization_sha
            or receipt.get("source_bundle_sha256") != active_source_bundle
        ):
            raise PAVolumeSourceAuthorityError(f"{label} authority differs")
        request_id = str(expected_request.get("request_id"))
        is_schedule = label == "schedule receipt"
        first, last, consumed = verify_copied_capture_receipt_semantics(
            root=root,
            receipt=receipt,
            expected_request=expected_request,
            context_relative=(
                "source_receipts/schedule/capture_context.json"
                if is_schedule else "source_receipts/feeds/capture_context.json"
            ),
            journal_relative=(
                "source_receipts/schedule/attempts"
                if is_schedule
                else f"source_receipts/feeds/{request_id}/attempts"
            ),
            original_journal_relative=(
                "attempts" if is_schedule else f"feeds/{request_id}/attempts"
            ),
            support_hashes=support_hashes,
            label=label,
        )
        consumed_support.update(consumed)
        if not is_schedule:
            first_attempt = _utc(
                receipt["attempts"][0]["requested_at_utc"],
                "feed global pacing",
            )
            if prior_feed_request is not None and first_attempt < prior_feed_request + timedelta(
                seconds=float(receipt["request_policy"]["minimum_request_interval_seconds"])
            ):
                raise PAVolumeSourceAuthorityError(
                    "retained feed requests violate global pacing"
                )
            prior_feed_request = _utc(
                receipt["attempts"][-1]["requested_at_utc"],
                "feed global pacing",
            )
        firsts.append(first)
        lasts.append(last)
    if consumed_support != set(support_hashes):
        raise PAVolumeSourceAuthorityError(
            "capture receipt support evidence exact file set differs"
        )
    first_request = min(firsts)
    latest_observation = max(lasts)
    for access_time in (first_request, latest_observation):
        try:
            historical_access.verify_historical_source_access_authorization(
                authorization_path=authorization_path,
                expected_authorization_sha256=authorization_sha,
                expected_runtime_policy_sha256=policy_sha,
                expected_source_bundle_sha256=active_source_bundle,
                access_time_utc=access_time.isoformat(timespec="microseconds").replace(
                    "+00:00", "Z"
                ),
            )
        except historical_access.HistoricalSourceAccessError as exc:
            raise PAVolumeSourceAuthorityError(
                "capture receipt timing is outside source authorization validity"
            ) from exc
    return first_request, latest_observation


def _verify_exact_dependency_lock(lock_path: Path, lock_sha: str) -> None:
    """Require the release lock to be the exact audited source-runtime lock."""
    repo_lock = Path(__file__).resolve().parents[2] / (
        "requirements-direct-batter-pa-source-authority.lock"
    )
    if not repo_lock.is_file() or _is_reparse(repo_lock):
        raise PAVolumeSourceAuthorityError("audited dependency lock is unavailable")
    expected = repo_lock.read_bytes()
    observed = lock_path.read_bytes()
    if not observed or observed != expected or _sha256_bytes(observed) != lock_sha:
        raise PAVolumeSourceAuthorityError(
            "qualified dependency lock differs from the exact audited lock"
        )


def _verify_schedule_index(value: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    fields = [
        "game_pk",
        "official_date",
        "game_type",
        "away_team_id",
        "home_team_id",
    ]
    if set(value) != {"schema_version", "season", "fields", "games"} or (
        value.get("schema_version") != "pa-volume-2023-schedule-index-v1"
        or value.get("season") != 2023
        or value.get("fields") != fields
        or not isinstance(value.get("games"), list)
        or not value["games"]
    ):
        raise PAVolumeSourceAuthorityError("copied schedule index schema differs")
    games: dict[int, dict[str, Any]] = {}
    canonical: list[dict[str, Any]] = []
    for raw in value["games"]:
        if not isinstance(raw, Mapping) or set(raw) != set(fields):
            raise PAVolumeSourceAuthorityError("copied schedule game schema differs")
        game_pk = _positive_int(raw.get("game_pk"), "schedule game_pk")
        if game_pk in games:
            raise PAVolumeSourceAuthorityError("copied schedule game is duplicated")
        game = {
            "game_pk": game_pk,
            "official_date": _date_2023(raw.get("official_date"), "official_date"),
            "game_type": raw.get("game_type"),
            "away_team_id": _positive_int(raw.get("away_team_id"), "away team"),
            "home_team_id": _positive_int(raw.get("home_team_id"), "home team"),
        }
        if game["game_type"] != "R" or game["away_team_id"] == game["home_team_id"]:
            raise PAVolumeSourceAuthorityError("copied schedule identity differs")
        games[game_pk] = game
        canonical.append(game)
    if canonical != sorted(canonical, key=lambda row: (row["official_date"], row["game_pk"])):
        raise PAVolumeSourceAuthorityError("copied schedule index is not sorted")
    return games


def _verify_projection(
    value: Mapping[str, Any], schedule: Mapping[int, Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    keys = {
        "schema_version",
        "status",
        "season",
        "research_only",
        "betting_authorized",
        "prospective_evidence_claimed",
        "protected_data",
        "bindings",
        "game_count",
        "row_count",
        "feed_hashes",
        "projection_sha256",
        "projection",
    }
    if set(value) != keys or (
        value.get("schema_version") != OFFICIAL_PROJECTION_SCHEMA
        or value.get("status") != "COMPLETE_OFFICIAL_2023_FINAL_FEED_PROJECTION"
        or value.get("season") != 2023
        or value.get("research_only") is not True
        or value.get("betting_authorized") is not False
        or value.get("prospective_evidence_claimed") is not False
        or value.get("protected_data") != _PROTECTED
    ):
        raise PAVolumeSourceAuthorityError("copied official projection schema differs")
    bindings = value.get("bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != {
        "schedule_capture_manifest_sha256",
        "feed_capture_manifest_sha256",
        "parser_source_sha256",
    }:
        raise PAVolumeSourceAuthorityError("copied projection bindings differ")
    for key in bindings:
        _sha(bindings[key], f"projection binding {key}")
    projection = value.get("projection")
    row_fields = [
        "game_pk",
        "official_date",
        "side",
        "team_id",
        "player_id",
        "lineup_slot",
        "out_pa",
    ]
    if not isinstance(projection, Mapping) or set(projection) != {
        "schema_version", "season", "fields", "rows"
    } or (
        projection.get("schema_version") != OFFICIAL_ROW_SCHEMA
        or projection.get("season") != 2023
        or projection.get("fields") != row_fields
        or not isinstance(projection.get("rows"), list)
    ):
        raise PAVolumeSourceAuthorityError("copied projection row schema differs")
    if _sha256_bytes(canonical_json_bytes(projection)) != _sha(
        value.get("projection_sha256"), "projection semantic digest"
    ):
        raise PAVolumeSourceAuthorityError("copied projection semantic digest differs")
    rows = projection["rows"]
    if (
        value.get("game_count") != len(schedule)
        or value.get("row_count") != len(rows)
        or len(rows) != len(schedule) * 18
    ):
        raise PAVolumeSourceAuthorityError("copied projection coverage differs")
    feed_hashes = value.get("feed_hashes")
    if not isinstance(feed_hashes, list) or len(feed_hashes) != len(schedule):
        raise PAVolumeSourceAuthorityError("copied projection feed hashes differ")
    observed_feed_ids: list[int] = []
    for row in feed_hashes:
        if not isinstance(row, Mapping) or set(row) != {"game_pk", "sha256"}:
            raise PAVolumeSourceAuthorityError("copied feed-hash schema differs")
        observed_feed_ids.append(_positive_int(row.get("game_pk"), "feed game_pk"))
        _sha(row.get("sha256"), "feed hash")
    if observed_feed_ids != list(schedule):
        raise PAVolumeSourceAuthorityError("copied feed-hash identity differs")
    for row in rows:
        if not isinstance(row, Mapping):
            raise PAVolumeSourceAuthorityError("copied projection row is malformed")
        game_pk = row.get("game_pk")
        game = schedule.get(game_pk)
        if game is None or row.get("official_date") != game["official_date"]:
            raise PAVolumeSourceAuthorityError("projection and schedule identity differ")
        side = row.get("side")
        expected_team = game.get(f"{side}_team_id") if side in {"away", "home"} else None
        if row.get("team_id") != expected_team:
            raise PAVolumeSourceAuthorityError("projection team and schedule differ")
    return rows


def _verify_source_semantics(
    *,
    root: Path,
    source_path: Path,
    source_sha: str,
    source_verification_path: Path,
    source_verification_sha: str,
    lock_path: Path,
    lock_sha: str,
    pa_raw: bytes,
) -> dict[str, Any]:
    source, source_raw = _canonical_json(source_path, "qualified source manifest")
    source_keys = {
        "schema_version", "status", "season", "research_only",
        "betting_authorized", "model_fitting_performed", "probabilities_generated",
        "protected_data", "source_access_authorization_id",
        "source_access_authorization_sha256", "schedule_capture_observed_digest",
        "schedule_capture_manifest_sha256", "feed_capture_observed_digest",
        "feed_capture_manifest_sha256", "schedule_index_sha256",
        "projection_sha256", "parser_source_sha256", "reviewed_source_files",
        "reviewed_source_bundle_sha256", "dependency_lock_sha256", "game_count",
        "row_count",
    }
    if set(source) != source_keys or (
        source.get("schema_version") != SOURCE_RELEASE_SCHEMA
        or source.get("status") != "AWAITING_INDEPENDENT_EXTERNAL_VERIFICATION"
        or source.get("season") != 2023
        or source.get("research_only") is not True
        or source.get("betting_authorized") is not False
        or source.get("model_fitting_performed") is not False
        or source.get("probabilities_generated") is not False
        or source.get("protected_data") != _PROTECTED
    ):
        raise PAVolumeSourceAuthorityError("qualified source manifest schema differs")
    if _sha256_bytes(source_raw) != source_sha:
        raise PAVolumeSourceAuthorityError("qualified source manifest digest differs")
    source_access_id = source.get("source_access_authorization_id")
    if not isinstance(source_access_id, str) or not source_access_id:
        raise PAVolumeSourceAuthorityError("source-access authorization ID is invalid")
    for field in (
        "source_access_authorization_sha256", "schedule_capture_observed_digest",
        "schedule_capture_manifest_sha256", "feed_capture_observed_digest",
        "feed_capture_manifest_sha256", "schedule_index_sha256", "projection_sha256",
        "parser_source_sha256", "reviewed_source_bundle_sha256",
        "dependency_lock_sha256",
    ):
        _sha(source.get(field), f"source manifest {field}")
    active_reviewed = _active_reviewed_source_files()
    if source.get("reviewed_source_files") != active_reviewed or source.get(
        "reviewed_source_bundle_sha256"
    ) != _sha256_bytes(canonical_json_bytes(active_reviewed)):
        raise PAVolumeSourceAuthorityError("reviewed source bundle differs from active bytes")
    if source.get("parser_source_sha256") != active_reviewed[3]["sha256"]:
        raise PAVolumeSourceAuthorityError("projection parser binding differs")
    _verify_exact_dependency_lock(lock_path, lock_sha)
    if source.get("dependency_lock_sha256") != lock_sha:
        raise PAVolumeSourceAuthorityError("source manifest lock binding differs")

    schedule_path = _safe_file(
        root, _SOURCE_RELEASE_PATHS["schedule"], "copied schedule index"
    )
    projection_path = _safe_file(
        root, _SOURCE_RELEASE_PATHS["projection"], "copied official projection"
    )
    schedule, schedule_raw = _canonical_json(schedule_path, "copied schedule index")
    projection, projection_raw = _canonical_json(
        projection_path, "copied official projection"
    )
    if _sha256_bytes(schedule_raw) != source.get("schedule_index_sha256") or (
        _sha256_bytes(projection_raw) != source.get("projection_sha256")
    ):
        raise PAVolumeSourceAuthorityError("copied source-release bytes differ from manifest")
    schedule_games = _verify_schedule_index(schedule)
    rows = _verify_projection(projection, schedule_games)
    if source.get("game_count") != len(schedule_games) or source.get("row_count") != len(rows):
        raise PAVolumeSourceAuthorityError("source manifest coverage differs")
    projection_bindings = projection["bindings"]
    if (
        projection_bindings["schedule_capture_manifest_sha256"]
        != source["schedule_capture_manifest_sha256"]
        or projection_bindings["feed_capture_manifest_sha256"]
        != source["feed_capture_manifest_sha256"]
        or projection_bindings["parser_source_sha256"] != source["parser_source_sha256"]
    ):
        raise PAVolumeSourceAuthorityError("projection and source-manifest bindings differ")

    verification, verification_raw = _canonical_json(
        source_verification_path, "source-release external verification"
    )
    expected_verification = {
        "schema_version": SOURCE_EXTERNAL_VERIFICATION_SCHEMA,
        "status": "INDEPENDENT_SOURCE_RELEASE_VERIFIED",
        "source_access_authorization_id": source_access_id,
        "source_access_authorization_sha256": source["source_access_authorization_sha256"],
        "source_manifest_sha256": source_sha,
        "projection_sha256": _sha256_bytes(projection_raw),
        "schedule_capture_observed_digest": source["schedule_capture_observed_digest"],
        "feed_capture_observed_digest": source["feed_capture_observed_digest"],
        "dependency_lock_sha256": lock_sha,
        "reviewed_source_bundle_sha256": source["reviewed_source_bundle_sha256"],
        "protected_data": _PROTECTED,
    }
    if _sha256_bytes(verification_raw) != source_verification_sha or verification != expected_verification:
        raise PAVolumeSourceAuthorityError(
            "source-release external verification is not semantically bound"
        )

    builder_path = Path(__file__).resolve().parent / "pa_volume_source_truth_v2.py"
    builder_sha = _sha256_bytes(builder_path.read_bytes())
    rebuilt = build_pa_volume_artifact(
        rows=rows,
        source_release_manifest_sha256=source_sha,
        source_release_external_verification_sha256=source_verification_sha,
        dependency_lock_sha256=lock_sha,
        builder_source_sha256=builder_sha,
    )
    if canonical_json_bytes(rebuilt) != pa_raw:
        raise PAVolumeSourceAuthorityError(
            "PA-volume artifact differs from deterministic source rebuild"
        )
    return rebuilt


@dataclass(frozen=True)
class VerifiedPAVolumeSourceAuthority:
    authority_id: str
    authority_manifest_path: Path
    authority_manifest_sha256: str
    external_receipt_path: Path
    external_receipt_sha256: str
    source_release_manifest_path: Path
    source_release_manifest_sha256: str
    source_release_external_verification_path: Path
    source_release_external_verification_sha256: str
    rebuild_manifest_path: Path
    rebuild_manifest_sha256: str
    pa_volume_artifact_path: Path
    pa_volume_artifact_sha256: str
    dependency_lock_path: Path
    dependency_lock_sha256: str
    qualified_at_utc: str
    observed_at_utc: str
    # Optional only for compatibility with older isolated numerical tests that
    # construct the value object directly.  The authority verifier always
    # supplies all six values, and only verifier-created bindings are eligible
    # for the v2 source-authority protocol.
    source_access_authorization_path: Path | None = None
    source_access_authorization_sha256: str = ""
    runtime_policy_path: Path | None = None
    runtime_policy_sha256: str = ""
    first_source_request_at_utc: str = ""
    latest_source_observation_at_utc: str = ""
    capture_receipt_evidence_path: Path | None = None
    capture_receipt_evidence_sha256: str = ""

    def binding(self) -> dict[str, str]:
        """Return the exact identities a downstream candidate must serialize."""
        binding = {
            "pa_volume_source_authority_id": self.authority_id,
            "pa_volume_source_authority_manifest_sha256": (
                self.authority_manifest_sha256
            ),
            "pa_volume_source_authority_receipt_sha256": (
                self.external_receipt_sha256
            ),
            "pa_volume_source_release_manifest_sha256": (
                self.source_release_manifest_sha256
            ),
            "pa_volume_source_release_external_verification_sha256": (
                self.source_release_external_verification_sha256
            ),
            "pa_volume_rebuild_manifest_sha256": self.rebuild_manifest_sha256,
            "pa_volume_artifact_sha256": self.pa_volume_artifact_sha256,
            "pa_volume_dependency_lock_sha256": self.dependency_lock_sha256,
        }
        if self.source_access_authorization_sha256:
            binding["pa_volume_source_access_authorization_sha256"] = (
                self.source_access_authorization_sha256
            )
        if self.runtime_policy_sha256:
            binding["pa_volume_runtime_policy_sha256"] = self.runtime_policy_sha256
        if self.first_source_request_at_utc:
            binding["pa_volume_first_source_request_at_utc"] = (
                self.first_source_request_at_utc
            )
        if self.latest_source_observation_at_utc:
            binding["pa_volume_latest_source_observation_at_utc"] = (
                self.latest_source_observation_at_utc
            )
        if self.capture_receipt_evidence_sha256:
            binding["pa_volume_capture_receipt_evidence_sha256"] = (
                self.capture_receipt_evidence_sha256
            )
        return binding


def verify_pa_volume_source_authority(
    *,
    authority_root: str | Path,
    authority_manifest_relative: str,
    external_receipt_relative: str,
    expected_external_receipt_sha256: str,
    expected_pa_volume_artifact_sha256: str,
    decision_time_utc: str,
) -> VerifiedPAVolumeSourceAuthority:
    """Verify complete source authority before any probability may be built."""
    root = Path(authority_root).resolve(strict=True)
    expected_receipt = _sha(
        expected_external_receipt_sha256, "external authority receipt"
    )
    expected_pa = _sha(
        expected_pa_volume_artifact_sha256, "expected PA-volume artifact"
    )
    decision_time = _utc(decision_time_utc, "decision_time_utc")

    receipt_relative = Path(external_receipt_relative).as_posix()
    evidence, evidence_raw, evidence_entries = _capture_evidence_entries(root)
    expected_files = set(_SOURCE_RELEASE_PATHS.values()) | {
        "authority/manifest.json",
        receipt_relative,
    } | {str(entry["path"]) for entry in evidence_entries}
    if _exact_files(root) != expected_files:
        raise PAVolumeSourceAuthorityError(
            "qualified authority exact file set differs"
        )

    authority_path = _safe_file(
        root, authority_manifest_relative, "source-authority manifest"
    )
    receipt_path = _safe_file(
        root, external_receipt_relative, "external authority receipt"
    )
    receipt, receipt_raw = _read_json(receipt_path, "external authority receipt")
    if _sha256_bytes(receipt_raw) != expected_receipt:
        raise PAVolumeSourceAuthorityError(
            "external authority receipt differs from independent expected digest"
        )
    receipt_keys = {
        "schema_version",
        "authority_id",
        "authority_manifest_path",
        "authority_manifest_sha256",
        "source_release_manifest_sha256",
        "source_release_external_verification_sha256",
        "rebuild_manifest_sha256",
        "pa_volume_artifact_sha256",
        "dependency_lock_sha256",
        "source_access_authorization_sha256",
        "runtime_policy_sha256",
        "capture_receipt_evidence_sha256",
        "first_source_request_at_utc",
        "latest_source_observation_at_utc",
        "observed_at_utc",
        "receipt_sha256",
    }
    if set(receipt) != receipt_keys or receipt.get("schema_version") != RECEIPT_SCHEMA:
        raise PAVolumeSourceAuthorityError("external authority receipt schema changed")
    unsigned_receipt = dict(receipt)
    supplied_receipt_hash = _sha(
        unsigned_receipt.pop("receipt_sha256", None), "receipt.receipt_sha256"
    )
    if _sha256_bytes(_canonical_bytes(unsigned_receipt)) != supplied_receipt_hash:
        raise PAVolumeSourceAuthorityError("external authority receipt self-hash differs")
    if receipt.get("authority_manifest_path") != authority_manifest_relative:
        raise PAVolumeSourceAuthorityError(
            "external receipt names a different authority manifest"
        )

    authority, authority_raw = _read_json(
        authority_path, "source-authority manifest"
    )
    authority_sha = _sha256_bytes(authority_raw)
    if _sha(receipt.get("authority_manifest_sha256"), "receipt authority digest") != authority_sha:
        raise PAVolumeSourceAuthorityError(
            "authority manifest differs from external receipt"
        )
    authority_keys = {
        "schema_version",
        "authority_id",
        "decision",
        "research_only",
        "betting_authorized",
        "eligible_for_model_consumption",
        "blockers",
        "source_season",
        "qualified_at_utc",
        "required_authority",
        "observed_authority",
        "protected_data",
        "source_release_manifest",
        "source_release_external_verification",
        "rebuild_manifest",
        "pa_volume_artifact",
        "exact_dependency_lock",
        "source_access_authorization",
        "runtime_policy",
        "source_capture_receipt_evidence",
        "source_capture_window",
    }
    if set(authority) != authority_keys or authority.get("schema_version") != AUTHORITY_SCHEMA:
        raise PAVolumeSourceAuthorityError("source-authority manifest schema changed")
    authority_id = authority.get("authority_id")
    if not isinstance(authority_id, str) or not authority_id:
        raise PAVolumeSourceAuthorityError("source-authority ID is invalid")
    if authority_id != receipt.get("authority_id"):
        raise PAVolumeSourceAuthorityError("source-authority identity differs")
    if (
        authority.get("decision") != COMPLETE_DECISION
        or authority.get("eligible_for_model_consumption") is not True
        or authority.get("blockers") != []
    ):
        raise PAVolumeSourceAuthorityError(
            "PA-volume source authority is blocked or ineligible for model consumption"
        )
    if (
        authority.get("research_only") is not True
        or authority.get("betting_authorized") is not False
        or authority.get("source_season") != 2023
        or authority.get("required_authority") != _REQUIRED_AUTHORITY
        or authority.get("observed_authority") != _REQUIRED_AUTHORITY
        or authority.get("protected_data") != _PROTECTED
    ):
        raise PAVolumeSourceAuthorityError(
            "source authority, chronology, or protected-data boundary changed"
        )

    source_path, source_sha, source_relative = _binding(
        authority.get("source_release_manifest"),
        root=root,
        label="qualified source-release manifest",
    )
    source_verification_path, source_verification_sha, verification_relative = _binding(
        authority.get("source_release_external_verification"),
        root=root,
        label="source-release external verification",
    )
    rebuild_path, rebuild_sha, rebuild_relative = _binding(
        authority.get("rebuild_manifest"), root=root, label="PA-volume rebuild manifest"
    )
    pa_path, pa_sha, pa_relative = _binding(
        authority.get("pa_volume_artifact"), root=root, label="PA-volume artifact"
    )
    lock_path, lock_sha, lock_relative = _binding(
        authority.get("exact_dependency_lock"), root=root, label="exact dependency lock"
    )
    source_access_path, source_access_sha, source_access_relative = _binding(
        authority.get("source_access_authorization"),
        root=root,
        label="source-access authorization",
    )
    runtime_policy_path, runtime_policy_sha, runtime_policy_relative = _binding(
        authority.get("runtime_policy"), root=root, label="runtime policy"
    )
    capture_evidence_path, capture_evidence_sha, capture_evidence_relative = _binding(
        authority.get("source_capture_receipt_evidence"),
        root=root,
        label="source capture receipt evidence",
    )
    if {
        "manifest": source_relative,
        "verification": verification_relative,
        "rebuild": rebuild_relative,
        "artifact": pa_relative,
        "lock": lock_relative,
        "source_access": source_access_relative,
        "runtime_policy": runtime_policy_relative,
        "capture_evidence": capture_evidence_relative,
    } != {
        key: _SOURCE_RELEASE_PATHS[key]
        for key in (
            "manifest", "verification", "rebuild", "artifact", "lock",
            "source_access", "runtime_policy",
            "capture_evidence",
        )
    }:
        raise PAVolumeSourceAuthorityError("qualified authority binding paths differ")
    if pa_sha != expected_pa:
        raise PAVolumeSourceAuthorityError(
            "qualified PA-volume artifact differs from candidate expected digest"
        )

    source_window = authority.get("source_capture_window")
    if not isinstance(source_window, Mapping) or set(source_window) != {
        "first_request_at_utc",
        "latest_observation_at_utc",
    }:
        raise PAVolumeSourceAuthorityError("source capture window schema changed")
    claimed_first_request = _utc(
        source_window.get("first_request_at_utc"), "first source request"
    )
    claimed_latest_observation = _utc(
        source_window.get("latest_observation_at_utc"),
        "latest source observation",
    )
    if claimed_latest_observation < claimed_first_request:
        raise PAVolumeSourceAuthorityError("source capture chronology is invalid")

    pa_value, pa_raw = _canonical_json(pa_path, "PA-volume artifact")
    rebuilt = _verify_source_semantics(
        root=root,
        source_path=source_path,
        source_sha=source_sha,
        source_verification_path=source_verification_path,
        source_verification_sha=source_verification_sha,
        lock_path=lock_path,
        lock_sha=lock_sha,
        pa_raw=pa_raw,
    )

    _verify_runtime_policy(
        policy_path=runtime_policy_path,
        policy_sha=runtime_policy_sha,
        lock_sha=lock_sha,
    )
    try:
        verified_access = (
            historical_access.verify_historical_source_access_authorization(
                authorization_path=source_access_path,
                expected_authorization_sha256=source_access_sha,
                expected_runtime_policy_sha256=runtime_policy_sha,
                expected_source_bundle_sha256=(
                    _active_capture_source_bundle_sha256()
                ),
                access_time_utc=source_window["first_request_at_utc"],
            )
        )
    except historical_access.HistoricalSourceAccessError as exc:
        raise PAVolumeSourceAuthorityError(
            "source-access authorization semantic validation failed"
        ) from exc
    source_manifest, _ = _canonical_json(
        source_path, "qualified source manifest"
    )
    if (
        verified_access.authorization_id
        != source_manifest.get("source_access_authorization_id")
        or verified_access.authorization_file_sha256 != source_access_sha
        or source_manifest.get("source_access_authorization_sha256")
        != source_access_sha
    ):
        raise PAVolumeSourceAuthorityError(
            "source-access authorization binding differs"
        )
    if (
        capture_evidence_path.read_bytes() != evidence_raw
        or capture_evidence_sha != _sha256_bytes(evidence_raw)
    ):
        raise PAVolumeSourceAuthorityError(
            "source capture receipt evidence binding differs"
        )
    first_request, latest_observation = _verify_capture_evidence_window(
        root=root,
        evidence=evidence,
        entries=evidence_entries,
        source_manifest=source_manifest,
        authorization_path=source_access_path,
        authorization_sha=source_access_sha,
        policy_sha=runtime_policy_sha,
    )
    if (
        first_request.isoformat(timespec="microseconds").replace("+00:00", "Z")
        != source_window["first_request_at_utc"]
        or latest_observation.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ) != source_window["latest_observation_at_utc"]
    ):
        raise PAVolumeSourceAuthorityError(
            "source capture window differs from retained receipt bytes"
        )
    if rebuilt != pa_value:
        raise PAVolumeSourceAuthorityError(
            "PA-volume artifact semantic rebuild differs"
        )
    try:
        validate_pa_volume_artifact(pa_value)
    except PAVolumeSourceTruthError as exc:
        raise PAVolumeSourceAuthorityError(
            "qualified PA-volume artifact fails source-truth validation"
        ) from exc
    pa_source = pa_value.get("source")
    pa_bindings = pa_source.get("bindings") if isinstance(pa_source, Mapping) else None
    if (
        not isinstance(pa_bindings, Mapping)
        or pa_bindings.get("source_release_manifest_sha256") != source_sha
        or pa_bindings.get("source_release_external_verification_sha256")
        != source_verification_sha
        or pa_bindings.get("dependency_lock_sha256") != lock_sha
    ):
        raise PAVolumeSourceAuthorityError(
            "PA-volume artifact does not bind the qualified source release and lock"
        )

    receipt_digests = {
        "source_release_manifest_sha256": source_sha,
        "source_release_external_verification_sha256": source_verification_sha,
        "rebuild_manifest_sha256": rebuild_sha,
        "pa_volume_artifact_sha256": pa_sha,
        "dependency_lock_sha256": lock_sha,
        "source_access_authorization_sha256": source_access_sha,
        "runtime_policy_sha256": runtime_policy_sha,
        "capture_receipt_evidence_sha256": capture_evidence_sha,
    }
    if any(
        _sha(receipt.get(key), f"receipt.{key}") != expected
        for key, expected in receipt_digests.items()
    ):
        raise PAVolumeSourceAuthorityError(
            "external receipt does not bind the exact source, rebuild, artifact, and lock"
        )
    if (
        receipt.get("first_source_request_at_utc")
        != source_window["first_request_at_utc"]
        or receipt.get("latest_source_observation_at_utc")
        != source_window["latest_observation_at_utc"]
    ):
        raise PAVolumeSourceAuthorityError(
            "external receipt does not bind the exact source capture window"
        )

    rebuild, _ = _read_json(rebuild_path, "PA-volume rebuild manifest")
    expected_rebuild = {
        "schema_version": REBUILD_SCHEMA,
        "decision": REBUILD_COMPLETE_DECISION,
        "source_season": 2023,
        "fit_seasons": [2023],
        "source_release_manifest_sha256": source_sha,
        "source_release_external_verification_sha256": source_verification_sha,
        "pa_volume_artifact_path": pa_relative,
        "pa_volume_artifact_sha256": pa_sha,
        "dependency_lock_sha256": lock_sha,
        "manual_coefficients": False,
        "protected_data": _PROTECTED,
    }
    if rebuild != expected_rebuild:
        raise PAVolumeSourceAuthorityError(
            "PA-volume rebuild does not bind the exact qualified source and artifact"
        )

    qualified_time = _utc(authority.get("qualified_at_utc"), "qualified_at_utc")
    observed_time = _utc(receipt.get("observed_at_utc"), "observed_at_utc")
    if (
        latest_observation > qualified_time
        or qualified_time > observed_time
        or observed_time > decision_time
    ):
        raise PAVolumeSourceAuthorityError(
            "source qualification, external observation, and decision chronology is invalid"
        )

    return VerifiedPAVolumeSourceAuthority(
        authority_id=authority_id,
        authority_manifest_path=authority_path,
        authority_manifest_sha256=authority_sha,
        external_receipt_path=receipt_path,
        external_receipt_sha256=expected_receipt,
        source_release_manifest_path=source_path,
        source_release_manifest_sha256=source_sha,
        source_release_external_verification_path=source_verification_path,
        source_release_external_verification_sha256=source_verification_sha,
        rebuild_manifest_path=rebuild_path,
        rebuild_manifest_sha256=rebuild_sha,
        pa_volume_artifact_path=pa_path,
        pa_volume_artifact_sha256=pa_sha,
        dependency_lock_path=lock_path,
        dependency_lock_sha256=lock_sha,
        source_access_authorization_path=source_access_path,
        source_access_authorization_sha256=source_access_sha,
        runtime_policy_path=runtime_policy_path,
        runtime_policy_sha256=runtime_policy_sha,
        capture_receipt_evidence_path=capture_evidence_path,
        capture_receipt_evidence_sha256=capture_evidence_sha,
        first_source_request_at_utc=str(source_window["first_request_at_utc"]),
        latest_source_observation_at_utc=str(
            source_window["latest_observation_at_utc"]
        ),
        qualified_at_utc=str(authority["qualified_at_utc"]),
        observed_at_utc=str(receipt["observed_at_utc"]),
    )


def invoke_after_pa_volume_source_authority(
    parent_probability_builder: Callable[[VerifiedPAVolumeSourceAuthority], _T],
    **authority_arguments: Any,
) -> _T:
    """Invoke ``parent_probability_builder`` only after complete authority passes."""
    authority = verify_pa_volume_source_authority(**authority_arguments)
    return parent_probability_builder(authority)
