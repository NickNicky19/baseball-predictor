"""Focused source-authority mutations for a future projected-opportunity v3."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.capture_pa_volume_official_source_v1 as capture
from src.evaluation import pa_volume_historical_source_access_v1 as historical_access
from src.evaluation.pa_volume_source_truth_v2 import (
    build_pa_volume_artifact,
    canonical_json_bytes,
)
from src.evaluation.shared_pa_pa_volume_source_authority_v1 import (
    AUTHORITY_SCHEMA,
    COMPLETE_DECISION,
    PAVolumeSourceAuthorityError,
    REBUILD_COMPLETE_DECISION,
    REBUILD_SCHEMA,
    RECEIPT_SCHEMA,
    VerifiedPAVolumeSourceAuthority,
    invoke_after_pa_volume_source_authority,
)


PROTECTED = {
    "may_2026_accessed": False,
    "selection_2024_accessed": False,
    "spent_hr_confirmation_2025_accessed": False,
    "prices_accessed": False,
    "prospective_evidence_accessed": False,
    "prospective_backfill_performed": False,
}
REQUIRED = {
    "raw_transport_request_and_response_receipts": True,
    "independent_official_source_receipts": True,
    "exact_reproducible_dependency_lock": True,
    "external_expected_release_digest": True,
}


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def capture_manifest_digest(value: dict[str, object]) -> str:
    unsigned = dict(value)
    unsigned["observed_capture_digest"] = None
    return hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()


def retained_receipt(
    request: dict[str, object], *, requested: str, observed: str
) -> dict[str, object]:
    started = datetime.fromisoformat(requested.replace("Z", "+00:00"))
    deadline = (started + timedelta(hours=1)).astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    return {
        "schema_version": capture.RECEIPT_SCHEMA,
        "authorization": capture.AUTHORIZATION,
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "model_fitting_performed": False,
        "probabilities_generated": False,
        "protected_data": PROTECTED,
        "request": request,
        "response": {
            "status": 200,
            "final_url": request["full_url"],
            "requested_at_utc": requested,
            "observed_at_utc": observed,
            "headers": {"content-type": "application/json"},
            "body_path": "response.json",
            "body_bytes": 2,
            "body_sha256": hashlib.sha256(b"{}").hexdigest(),
        },
        "runtime_attestation_sha256": "a" * 64,
        "source_access_authorization_id": "official-2023-source-access-v1",
        "source_access_authorization_sha256": "",  # filled by fixture
        "source_bundle_sha256": capture.capture_source_bundle_sha256(),
        "request_policy": {
            "minimum_request_interval_seconds": capture.MIN_REQUEST_INTERVAL_SECONDS,
            "maximum_attempts": 1,
            "retry_base_seconds": capture.RETRY_BASE_SECONDS,
            "retry_max_seconds": capture.RETRY_MAX_SECONDS,
            "overall_timeout_seconds": 3600.0,
        },
        "attempts": [{
            "attempt": 1,
            "requested_at_utc": requested,
            "observed_at_utc": observed,
            "outcome": "SUCCESS",
            "status": 200,
            "error_kind": None,
            "retry_after_header": None,
            "retry_after_seconds": None,
            "backoff_seconds": 0.0,
            "jitter_seconds": 0.0,
        }],
        "capture_window": {
            "capture_started_at_utc": requested,
            "deadline_at_utc": deadline,
            "authorization_valid_from_utc": "2026-07-29T09:00:00.000000Z",
            "authorization_expires_at_utc": "2026-07-30T09:00:00.000000Z",
        },
        "capture_context_sha256": "",
        "attempt_journal": {},
    }


def bind_receipt_support(
    root: Path,
    receipt: dict[str, object],
    *,
    context_path: Path,
    journal_path: Path,
    original_journal_path: str,
    plan_sha256: str,
) -> list[Path]:
    context = {
        "schema_version": capture.CAPTURE_CONTEXT_SCHEMA,
        "status": "ACTIVE_OR_COMPLETE_IMMUTABLE_CAPTURE_CONTEXT",
        "plan_sha256": plan_sha256,
        "runtime_attestation_sha256": receipt["runtime_attestation_sha256"],
        "source_access_authorization_id": receipt[
            "source_access_authorization_id"
        ],
        "source_access_authorization_sha256": receipt[
            "source_access_authorization_sha256"
        ],
        "source_bundle_sha256": receipt["source_bundle_sha256"],
        "request_policy": receipt["request_policy"],
        **receipt["capture_window"],
    }
    write_json(context_path, context)
    receipt["capture_context_sha256"] = digest(context_path)
    request_context = capture._request_context(
        run_context_sha256=digest(context_path), request=receipt["request"]
    )
    context_record = journal_path / "context.json"
    write_json(context_record, request_context)
    attempt = receipt["attempts"][0]
    reservation = {
        "schema_version": capture.ATTEMPT_RESERVATION_SCHEMA,
        "context_sha256": digest(context_record),
        "attempt": 1,
        "reserved_at_utc": attempt["requested_at_utc"],
    }
    reservation_path = journal_path / "reservation-0001.json"
    write_json(reservation_path, reservation)
    result = {
        "schema_version": capture.ATTEMPT_RESULT_SCHEMA,
        "context_sha256": digest(context_record),
        "reservation_sha256": digest(reservation_path),
        "attempt": attempt,
    }
    result_path = journal_path / "result-0001.json"
    write_json(result_path, result)
    receipt["attempt_journal"] = {
        "path": original_journal_path,
        "context_sha256": digest(context_record),
        "reservation_files": [{
            "path": reservation_path.name,
            "sha256": digest(reservation_path),
        }],
        "result_files": [{
            "path": result_path.name,
            "sha256": digest(result_path),
        }],
    }
    return [context_path, context_record, reservation_path, result_path]


def pa_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for side, team_id, player_base in (("away", 10, 1000), ("home", 20, 2000)):
        for slot in range(1, 10):
            rows.append(
                {
                    "game_pk": 1,
                    "official_date": "2023-03-30",
                    "side": side,
                    "team_id": team_id,
                    "player_id": player_base + slot,
                    "lineup_slot": slot,
                    "out_pa": 5 if slot <= 4 else 4,
                }
            )
    return rows


def fixture(
    root: Path,
    *,
    blocked: bool = False,
    authorization_expires_at_utc: str = "2026-07-30T09:00:00.000000Z",
) -> dict[str, object]:
    source_root = root / "source_release"
    source = source_root / "source_manifest.json"
    schedule = source_root / "schedule_index.json"
    projection_path = source_root / "projection.json"
    source_verification = root / "source_release_external_verification.json"
    lock = root / "locks" / "requirements.lock"
    pa = root / "artifacts" / "pa_volume.json"
    source.parent.mkdir(parents=True)
    lock.parent.mkdir(parents=True)
    pa.parent.mkdir(parents=True)
    repo = Path(__file__).resolve().parents[1]
    lock.write_bytes(
        (repo / "requirements-direct-batter-pa-source-authority.lock").read_bytes()
    )
    runtime_policy = root / "source_access" / "runtime_policy.json"
    runtime_policy.parent.mkdir(parents=True)
    runtime_policy.write_bytes(
        (
            repo / "config/direct_batter_pa_source_runtime_authority_v1.json"
        ).read_bytes()
    )
    authorization = root / "source_access" / "authorization.json"
    authorization_unsigned = {
        "schema_version": historical_access.SCHEMA,
        "authorization_id": "official-2023-source-access-v1",
        "status": historical_access.STATUS,
        "authorized_at_utc": "2026-07-29T09:00:00.000000Z",
        "valid_from_utc": "2026-07-29T09:00:00.000000Z",
        "expires_at_utc": authorization_expires_at_utc,
        "research_only": True,
        "betting_authorized": False,
        "network_fetch_authorized": True,
        "source_scope": historical_access.SOURCE_SCOPE,
        "authorized_actions": historical_access.AUTHORIZED_ACTIONS,
        "protected_boundaries": historical_access.PROTECTED_BOUNDARIES,
        "runtime_policy_sha256": digest(runtime_policy),
        "source_bundle_sha256": capture.capture_source_bundle_sha256(),
    }
    write_json(
        authorization,
        {
            **authorization_unsigned,
            "authorization_sha256": (
                historical_access.authorization_semantic_sha256(
                    authorization_unsigned
                )
            ),
        },
    )
    schedule_request = capture._request(
        "official-mlb-2023-regular-schedule",
        capture.SCHEDULE_FULL_URL,
        {"season": 2023, "game_type": "R"},
    )
    feed_request = capture._request(
        "game-1",
        "https://statsapi.mlb.com/api/v1.1/game/1/feed/live?fields=test",
        {"game_pk": 1, "away_team_id": 10, "home_team_id": 20},
    )
    schedule_receipt = retained_receipt(
        schedule_request,
        requested="2026-07-29T09:30:00.000000Z",
        observed="2026-07-29T09:31:00.000000Z",
    )
    feed_receipt = retained_receipt(
        feed_request,
        requested="2026-07-29T09:58:00.000000Z",
        observed="2026-07-29T09:59:00.000000Z",
    )
    for retained in (schedule_receipt, feed_receipt):
        retained["source_access_authorization_sha256"] = digest(authorization)
        retained["capture_window"]["authorization_expires_at_utc"] = (
            authorization_expires_at_utc
        )
    schedule_support = bind_receipt_support(
        root,
        schedule_receipt,
        context_path=root / "source_receipts/schedule/capture_context.json",
        journal_path=root / "source_receipts/schedule/attempts",
        original_journal_path="attempts",
        plan_sha256=hashlib.sha256(canonical_json_bytes(schedule_request)).hexdigest(),
    )
    feed_support = bind_receipt_support(
        root,
        feed_receipt,
        context_path=root / "source_receipts/feeds/capture_context.json",
        journal_path=root / "source_receipts/feeds/game-1/attempts",
        original_journal_path="feeds/game-1/attempts",
        plan_sha256="b" * 64,
    )
    schedule_receipt_path = root / "source_receipts/schedule/receipt.json"
    feed_receipt_path = root / "source_receipts/feeds/game-1/receipt.json"
    write_json(schedule_receipt_path, schedule_receipt)
    write_json(feed_receipt_path, feed_receipt)
    schedule_manifest = {
        "schema_version": capture.SCHEDULE_SCHEMA,
        "status": "COMPLETE_IMMUTABLE_OFFICIAL_SCHEDULE_CAPTURE",
        "authorization": capture.AUTHORIZATION,
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "protected_data": PROTECTED,
        "runtime_attestation_sha256": "a" * 64,
        "source_access_authorization_id": "official-2023-source-access-v1",
        "source_access_authorization_sha256": digest(authorization),
        "source_bundle_sha256": capture.capture_source_bundle_sha256(),
        "request": schedule_request,
        "body_sha256": hashlib.sha256(b"{}").hexdigest(),
        "receipt_sha256": digest(schedule_receipt_path),
        "observed_capture_digest": None,
    }
    schedule_manifest["observed_capture_digest"] = capture_manifest_digest(
        schedule_manifest
    )
    schedule_manifest_path = root / "source_receipts/schedule/manifest.json"
    write_json(schedule_manifest_path, schedule_manifest)
    feed_plan = {
        "schema_version": capture.PLAN_SCHEMA,
        "status": "LOCKED_OFFICIAL_2023_FINAL_FEED_CAPTURE_PLAN",
        "authorization": capture.AUTHORIZATION,
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "protected_data": PROTECTED,
        "schedule_capture_digest": schedule_manifest["observed_capture_digest"],
        "feed_fields": capture.FEED_FIELDS,
        "requests": [feed_request],
    }
    feed_plan_path = root / "source_receipts/feeds/plan.json"
    write_json(feed_plan_path, feed_plan)
    feed_manifest = {
        "schema_version": capture.FEED_SCHEMA,
        "status": "COMPLETE_IMMUTABLE_OFFICIAL_2023_FEED_CAPTURE",
        "authorization": capture.AUTHORIZATION,
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "protected_data": PROTECTED,
        "schedule_capture_digest": schedule_manifest["observed_capture_digest"],
        "plan_sha256": hashlib.sha256(canonical_json_bytes(feed_plan)).hexdigest(),
        "runtime_attestation_sha256": "a" * 64,
        "source_access_authorization_id": "official-2023-source-access-v1",
        "source_access_authorization_sha256": digest(authorization),
        "source_bundle_sha256": capture.capture_source_bundle_sha256(),
        "game_count": 1,
        "entries": [{
            "request_id": "game-1",
            "response_sha256": feed_receipt["response"]["body_sha256"],
            "receipt_sha256": digest(feed_receipt_path),
        }],
        "observed_capture_digest": None,
    }
    feed_manifest["observed_capture_digest"] = capture_manifest_digest(feed_manifest)
    feed_manifest_path = root / "source_receipts/feeds/manifest.json"
    write_json(feed_manifest_path, feed_manifest)
    evidence_entries = sorted(
        [
            {"role": "schedule_manifest", "request_id": "schedule-2023", "path": "source_receipts/schedule/manifest.json", "sha256": digest(schedule_manifest_path)},
            {"role": "schedule_receipt", "request_id": schedule_request["request_id"], "path": "source_receipts/schedule/receipt.json", "sha256": digest(schedule_receipt_path)},
            {"role": "feed_manifest", "request_id": "feed-2023", "path": "source_receipts/feeds/manifest.json", "sha256": digest(feed_manifest_path)},
            {"role": "feed_plan", "request_id": "feed-2023", "path": "source_receipts/feeds/plan.json", "sha256": digest(feed_plan_path)},
            {"role": "feed_receipt", "request_id": "game-1", "path": "source_receipts/feeds/game-1/receipt.json", "sha256": digest(feed_receipt_path)},
        ] + [
            {
                "role": "schedule_support",
                "request_id": "schedule-2023",
                "path": path.relative_to(root).as_posix(),
                "sha256": digest(path),
            }
            for path in schedule_support
        ] + [
            {
                "role": "feed_support",
                "request_id": "game-1",
                "path": path.relative_to(root).as_posix(),
                "sha256": digest(path),
            }
            for path in feed_support
        ],
        key=lambda row: row["path"],
    )
    evidence_manifest = root / "source_receipts/manifest.json"
    write_json(evidence_manifest, {
        "schema_version": "pa-volume-qualified-capture-receipt-evidence-v1",
        "source_access_authorization_id": "official-2023-source-access-v1",
        "source_access_authorization_sha256": digest(authorization),
        "source_bundle_sha256": capture.capture_source_bundle_sha256(),
        "schedule_capture_observed_digest": schedule_manifest["observed_capture_digest"],
        "feed_capture_observed_digest": feed_manifest["observed_capture_digest"],
        "entries": evidence_entries,
    })
    rows = pa_rows()
    schedule_value = {
        "schema_version": "pa-volume-2023-schedule-index-v1",
        "season": 2023,
        "fields": [
            "game_pk", "official_date", "game_type", "away_team_id", "home_team_id",
        ],
        "games": [{
            "game_pk": 1,
            "official_date": "2023-03-30",
            "game_type": "R",
            "away_team_id": 10,
            "home_team_id": 20,
        }],
    }
    write_json(schedule, schedule_value)
    reviewed_paths = [
        "scripts/capture_direct_batter_pa_source_transport_v2.py",
        "scripts/capture_pa_volume_official_source_v1.py",
        "scripts/build_pa_volume_official_source_release_v1.py",
        "src/evaluation/pa_volume_official_feed_projection_v1.py",
        "src/evaluation/pa_volume_source_truth_v2.py",
    ]
    reviewed = [
        {"path": relative, "sha256": digest(repo / relative)}
        for relative in reviewed_paths
    ]
    projection_rows = {
        "schema_version": "pa-volume-official-starter-projection-v2",
        "season": 2023,
        "fields": [
            "game_pk", "official_date", "side", "team_id", "player_id",
            "lineup_slot", "out_pa",
        ],
        "rows": rows,
    }
    projection = {
        "schema_version": "pa-volume-official-feed-projection-v1",
        "status": "COMPLETE_OFFICIAL_2023_FINAL_FEED_PROJECTION",
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "prospective_evidence_claimed": False,
        "protected_data": PROTECTED,
        "bindings": {
            "schedule_capture_manifest_sha256": digest(schedule_manifest_path),
            "feed_capture_manifest_sha256": digest(feed_manifest_path),
            "parser_source_sha256": reviewed[3]["sha256"],
        },
        "game_count": 1,
        "row_count": 18,
        "feed_hashes": [{"game_pk": 1, "sha256": "3" * 64}],
        "projection_sha256": hashlib.sha256(
            canonical_json_bytes(projection_rows)
        ).hexdigest(),
        "projection": projection_rows,
    }
    write_json(projection_path, projection)
    source_value = {
        "schema_version": "pa-volume-official-source-release-v1",
        "status": "AWAITING_INDEPENDENT_EXTERNAL_VERIFICATION",
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "model_fitting_performed": False,
        "probabilities_generated": False,
        "protected_data": PROTECTED,
        "source_access_authorization_id": "official-2023-source-access-v1",
        "source_access_authorization_sha256": digest(authorization),
        "schedule_capture_observed_digest": schedule_manifest[
            "observed_capture_digest"
        ],
        "schedule_capture_manifest_sha256": digest(schedule_manifest_path),
        "feed_capture_observed_digest": feed_manifest["observed_capture_digest"],
        "feed_capture_manifest_sha256": digest(feed_manifest_path),
        "schedule_index_sha256": digest(schedule),
        "projection_sha256": digest(projection_path),
        "parser_source_sha256": reviewed[3]["sha256"],
        "reviewed_source_files": reviewed,
        "reviewed_source_bundle_sha256": hashlib.sha256(
            canonical_json_bytes(reviewed)
        ).hexdigest(),
        "dependency_lock_sha256": digest(lock),
        "game_count": 1,
        "row_count": 18,
    }
    write_json(source, source_value)
    verification = {
        "schema_version": "pa-volume-official-source-external-verification-v1",
        "status": "INDEPENDENT_SOURCE_RELEASE_VERIFIED",
        "source_access_authorization_id": source_value["source_access_authorization_id"],
        "source_access_authorization_sha256": source_value["source_access_authorization_sha256"],
        "source_manifest_sha256": digest(source),
        "projection_sha256": digest(projection_path),
        "schedule_capture_observed_digest": source_value["schedule_capture_observed_digest"],
        "feed_capture_observed_digest": source_value["feed_capture_observed_digest"],
        "dependency_lock_sha256": digest(lock),
        "reviewed_source_bundle_sha256": source_value["reviewed_source_bundle_sha256"],
        "protected_data": PROTECTED,
    }
    write_json(source_verification, verification)
    builder_source = (
        repo / "src/evaluation/pa_volume_source_truth_v2.py"
    )
    pa.write_bytes(
        canonical_json_bytes(
            build_pa_volume_artifact(
                rows=rows,
                source_release_manifest_sha256=digest(source),
                source_release_external_verification_sha256=digest(source_verification),
                dependency_lock_sha256=digest(lock),
                builder_source_sha256=digest(builder_source),
            )
        )
    )

    rebuild = {
        "schema_version": REBUILD_SCHEMA,
        "decision": REBUILD_COMPLETE_DECISION,
        "source_season": 2023,
        "fit_seasons": [2023],
        "source_release_manifest_sha256": digest(source),
        "source_release_external_verification_sha256": digest(source_verification),
        "pa_volume_artifact_path": "artifacts/pa_volume.json",
        "pa_volume_artifact_sha256": digest(pa),
        "dependency_lock_sha256": digest(lock),
        "manual_coefficients": False,
        "protected_data": PROTECTED,
    }
    rebuild_path = root / "rebuild" / "manifest.json"
    write_json(rebuild_path, rebuild)
    authority = {
        "schema_version": AUTHORITY_SCHEMA,
        "authority_id": "synthetic-receipt-complete-2023-v1",
        "decision": (
            "BLOCKED_INCOMPLETE_SOURCE_AND_DEPENDENCY_AUTHORITY"
            if blocked
            else COMPLETE_DECISION
        ),
        "research_only": True,
        "betting_authorized": False,
        "eligible_for_model_consumption": not blocked,
        "blockers": ["RAW_TRANSPORT_RECEIPTS_MISSING"] if blocked else [],
        "source_season": 2023,
        "qualified_at_utc": "2026-07-29T10:00:00Z",
        "required_authority": REQUIRED,
        "observed_authority": (
            {**REQUIRED, "raw_transport_request_and_response_receipts": False}
            if blocked
            else REQUIRED
        ),
        "protected_data": PROTECTED,
        "source_release_manifest": {
            "path": "source_release/source_manifest.json",
            "sha256": digest(source),
        },
        "source_release_external_verification": {
            "path": "source_release_external_verification.json",
            "sha256": digest(source_verification),
        },
        "rebuild_manifest": {
            "path": "rebuild/manifest.json",
            "sha256": digest(rebuild_path),
        },
        "pa_volume_artifact": {
            "path": "artifacts/pa_volume.json",
            "sha256": digest(pa),
        },
        "exact_dependency_lock": {
            "path": "locks/requirements.lock",
            "sha256": digest(lock),
        },
        "source_access_authorization": {
            "path": "source_access/authorization.json",
            "sha256": digest(authorization),
        },
        "runtime_policy": {
            "path": "source_access/runtime_policy.json",
            "sha256": digest(runtime_policy),
        },
        "source_capture_receipt_evidence": {
            "path": "source_receipts/manifest.json",
            "sha256": digest(evidence_manifest),
        },
        "source_capture_window": {
            "first_request_at_utc": "2026-07-29T09:30:00.000000Z",
            "latest_observation_at_utc": "2026-07-29T09:59:00.000000Z",
        },
    }
    authority_path = root / "authority" / "manifest.json"
    write_json(authority_path, authority)
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "authority_id": authority["authority_id"],
        "authority_manifest_path": "authority/manifest.json",
        "authority_manifest_sha256": digest(authority_path),
        "source_release_manifest_sha256": digest(source),
        "source_release_external_verification_sha256": digest(source_verification),
        "rebuild_manifest_sha256": digest(rebuild_path),
        "pa_volume_artifact_sha256": digest(pa),
        "dependency_lock_sha256": digest(lock),
        "source_access_authorization_sha256": digest(authorization),
        "runtime_policy_sha256": digest(runtime_policy),
        "capture_receipt_evidence_sha256": digest(evidence_manifest),
        "first_source_request_at_utc": authority["source_capture_window"][
            "first_request_at_utc"
        ],
        "latest_source_observation_at_utc": authority[
            "source_capture_window"
        ]["latest_observation_at_utc"],
        "observed_at_utc": "2026-07-29T10:01:00Z",
    }
    receipt["receipt_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
    receipt_path = root / "external" / "receipt.json"
    write_json(receipt_path, receipt)
    return {
        "authority_root": root,
        "authority_manifest_relative": "authority/manifest.json",
        "external_receipt_relative": "external/receipt.json",
        "expected_external_receipt_sha256": digest(receipt_path),
        "expected_pa_volume_artifact_sha256": digest(pa),
        "decision_time_utc": "2026-07-29T10:02:00Z",
        "paths": {
            "authority": authority_path,
            "receipt": receipt_path,
            "rebuild": rebuild_path,
            "pa": pa,
            "source": source,
            "source_verification": source_verification,
            "schedule": schedule,
            "projection": projection_path,
            "lock": lock,
            "authorization": authorization,
            "runtime_policy": runtime_policy,
            "capture_evidence": evidence_manifest,
        },
    }


def guarded_call(arguments: dict[str, object]) -> tuple[list[object], object]:
    called: list[object] = []

    def parent(authority: VerifiedPAVolumeSourceAuthority) -> dict[str, object]:
        called.append(authority)
        return authority.binding()

    return called, lambda: invoke_after_pa_volume_source_authority(parent, **{
        key: value for key, value in arguments.items() if key != "paths"
    })


def reanchor_all_outer_hashes(
    arguments: dict[str, object], *, rebuild_source_chain: bool = False
) -> None:
    """Model a malicious but consistently rehashed outer authority envelope."""
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    authority_path = paths["authority"]
    receipt_path = paths["receipt"]
    rebuild_path = paths["rebuild"]
    pa_path = paths["pa"]
    source_path = paths["source"]
    verification_path = paths["source_verification"]
    lock_path = paths["lock"]
    authorization_path = paths["authorization"]
    runtime_policy_path = paths["runtime_policy"]
    capture_evidence_path = paths["capture_evidence"]
    assert all(
        isinstance(path, Path)
        for path in (
            authority_path, receipt_path, rebuild_path, pa_path, source_path,
            verification_path, lock_path, authorization_path, runtime_policy_path,
            capture_evidence_path,
        )
    )
    if rebuild_source_chain:
        source = json.loads(source_path.read_bytes())
        source["source_access_authorization_sha256"] = digest(
            authorization_path
        )
        write_json(source_path, source)
        verification = json.loads(verification_path.read_bytes())
        verification["source_access_authorization_sha256"] = digest(
            authorization_path
        )
        verification["source_manifest_sha256"] = digest(source_path)
        write_json(verification_path, verification)
        builder_path = (
            Path(__file__).resolve().parents[1]
            / "src/evaluation/pa_volume_source_truth_v2.py"
        )
        write_json(
            pa_path,
            build_pa_volume_artifact(
                rows=pa_rows(),
                source_release_manifest_sha256=digest(source_path),
                source_release_external_verification_sha256=digest(
                    verification_path
                ),
                dependency_lock_sha256=digest(lock_path),
                builder_source_sha256=digest(builder_path),
            ),
        )
    rebuild = json.loads(rebuild_path.read_bytes())
    rebuild["source_release_manifest_sha256"] = digest(source_path)
    rebuild["source_release_external_verification_sha256"] = digest(
        verification_path
    )
    rebuild["pa_volume_artifact_sha256"] = digest(pa_path)
    rebuild["dependency_lock_sha256"] = digest(lock_path)
    write_json(rebuild_path, rebuild)
    authority = json.loads(authority_path.read_bytes())
    authority["source_release_manifest"]["sha256"] = digest(source_path)
    authority["source_release_external_verification"]["sha256"] = digest(
        verification_path
    )
    authority["rebuild_manifest"]["sha256"] = digest(rebuild_path)
    authority["pa_volume_artifact"]["sha256"] = digest(pa_path)
    authority["exact_dependency_lock"]["sha256"] = digest(lock_path)
    authority["source_access_authorization"]["sha256"] = digest(
        authorization_path
    )
    authority["runtime_policy"]["sha256"] = digest(runtime_policy_path)
    authority["source_capture_receipt_evidence"]["sha256"] = digest(
        capture_evidence_path
    )
    write_json(authority_path, authority)
    receipt = json.loads(receipt_path.read_bytes())
    receipt["authority_manifest_sha256"] = digest(authority_path)
    receipt["source_release_manifest_sha256"] = digest(source_path)
    receipt["source_release_external_verification_sha256"] = digest(
        verification_path
    )
    receipt["rebuild_manifest_sha256"] = digest(rebuild_path)
    receipt["pa_volume_artifact_sha256"] = digest(pa_path)
    receipt["dependency_lock_sha256"] = digest(lock_path)
    receipt["source_access_authorization_sha256"] = digest(
        authorization_path
    )
    receipt["runtime_policy_sha256"] = digest(runtime_policy_path)
    receipt["capture_receipt_evidence_sha256"] = digest(capture_evidence_path)
    receipt.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
    write_json(receipt_path, receipt)
    arguments["expected_external_receipt_sha256"] = digest(receipt_path)
    arguments["expected_pa_volume_artifact_sha256"] = digest(pa_path)


def test_matching_pa_hash_but_blocked_authority_prevents_parent_probability(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path, blocked=True)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="blocked or ineligible"):
        run()
    assert called == []


@pytest.mark.parametrize("missing", ["authority", "receipt"])
def test_missing_authority_evidence_prevents_parent_probability(
    tmp_path: Path, missing: str
) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    path = paths[missing]
    assert isinstance(path, Path)
    path.unlink()
    called, run = guarded_call(arguments)
    with pytest.raises(
        PAVolumeSourceAuthorityError, match="missing|exact file set"
    ):
        run()
    assert called == []


def test_unanchored_rehashed_authority_prevents_parent_probability(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    authority_path = paths["authority"]
    assert isinstance(authority_path, Path)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority["authority_id"] = "locally-rehashed-without-external-anchor"
    write_json(authority_path, authority)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="authority manifest differs"):
        run()
    assert called == []


def test_wrong_external_expected_digest_prevents_parent_probability(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    arguments["expected_external_receipt_sha256"] = "0" * 64
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="independent expected digest"):
        run()
    assert called == []


def test_rebuild_or_artifact_drift_prevents_parent_probability(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    pa_path = paths["pa"]
    assert isinstance(pa_path, Path)
    pa_path.write_bytes(pa_path.read_bytes() + b"mutation")
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="PA-volume artifact bytes differ"):
        run()
    assert called == []


def test_late_authority_observation_prevents_parent_probability(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    arguments["decision_time_utc"] = "2026-07-29T10:00:30Z"
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="chronology is invalid"):
        run()
    assert called == []


def test_protected_boundary_mutation_prevents_parent_probability(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    authority_path = paths["authority"]
    receipt_path = paths["receipt"]
    assert isinstance(authority_path, Path) and isinstance(receipt_path, Path)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority["protected_data"]["may_2026_accessed"] = True
    write_json(authority_path, authority)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["authority_manifest_sha256"] = digest(authority_path)
    receipt.pop("receipt_sha256")
    receipt["receipt_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
    write_json(receipt_path, receipt)
    arguments["expected_external_receipt_sha256"] = digest(receipt_path)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="protected-data boundary"):
        run()
    assert called == []


def test_well_hashed_arbitrary_source_manifest_cannot_pass(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict) and isinstance(paths["source"], Path)
    write_json(paths["source"], {"schema_version": "arbitrary-source-v1"})
    reanchor_all_outer_hashes(arguments)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="source manifest schema"):
        run()
    assert called == []


def test_well_hashed_arbitrary_external_verification_cannot_pass(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict) and isinstance(
        paths["source_verification"], Path
    )
    write_json(
        paths["source_verification"],
        {"schema_version": "arbitrary-external-verification-v1"},
    )
    reanchor_all_outer_hashes(arguments)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="not semantically bound"):
        run()
    assert called == []


def test_well_hashed_arbitrary_dependency_lock_cannot_pass(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict) and isinstance(paths["lock"], Path)
    paths["lock"].write_bytes(
        b"arbitrary==1.0 --hash=sha256:" + b"a" * 64 + b"\n"
    )
    reanchor_all_outer_hashes(arguments)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="exact audited lock"):
        run()
    assert called == []


def test_reanchored_widened_source_access_cannot_reach_parent(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict) and isinstance(paths["authorization"], Path)
    authorization = json.loads(paths["authorization"].read_bytes())
    authorization["authorized_actions"]["feature_construction"] = True
    authorization["authorization_sha256"] = (
        historical_access.authorization_semantic_sha256(authorization)
    )
    write_json(paths["authorization"], authorization)
    reanchor_all_outer_hashes(arguments, rebuild_source_chain=True)
    called, run = guarded_call(arguments)
    with pytest.raises(
        PAVolumeSourceAuthorityError,
        match="source-access authorization semantic validation failed",
    ):
        run()
    assert called == []


def test_reanchored_runtime_policy_mutation_cannot_reach_parent(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    assert isinstance(paths["runtime_policy"], Path)
    assert isinstance(paths["authorization"], Path)
    policy = json.loads(paths["runtime_policy"].read_bytes())
    policy["network_fetch_authorized"] = True
    write_json(paths["runtime_policy"], policy)
    authorization = json.loads(paths["authorization"].read_bytes())
    authorization["runtime_policy_sha256"] = digest(paths["runtime_policy"])
    authorization["authorization_sha256"] = (
        historical_access.authorization_semantic_sha256(authorization)
    )
    write_json(paths["authorization"], authorization)
    reanchor_all_outer_hashes(arguments, rebuild_source_chain=True)
    called, run = guarded_call(arguments)
    with pytest.raises(
        PAVolumeSourceAuthorityError,
        match="runtime-policy semantic validation failed",
    ):
        run()
    assert called == []


def test_fully_reanchored_claimed_capture_window_cannot_reach_parent(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict) and isinstance(paths["authority"], Path)
    authority = json.loads(paths["authority"].read_bytes())
    authority["source_capture_window"] = {
        "first_request_at_utc": "2026-07-29T09:00:00.000000Z",
        "latest_observation_at_utc": "2026-07-29T10:30:00.000000Z",
    }
    write_json(paths["authority"], authority)
    receipt_path = paths["receipt"]
    assert isinstance(receipt_path, Path)
    receipt = json.loads(receipt_path.read_bytes())
    receipt["first_source_request_at_utc"] = authority[
        "source_capture_window"
    ]["first_request_at_utc"]
    receipt["latest_source_observation_at_utc"] = authority[
        "source_capture_window"
    ]["latest_observation_at_utc"]
    write_json(receipt_path, receipt)
    reanchor_all_outer_hashes(arguments)
    called, run = guarded_call(arguments)
    with pytest.raises(
        PAVolumeSourceAuthorityError,
        match="differs from retained receipt bytes",
    ):
        run()
    assert called == []


def test_latest_retained_observation_must_precede_authorization_expiry(
    tmp_path: Path,
) -> None:
    arguments = fixture(
        tmp_path,
        authorization_expires_at_utc="2026-07-29T09:59:00.000000Z",
    )
    called, run = guarded_call(arguments)
    with pytest.raises(
        PAVolumeSourceAuthorityError,
        match="outside source authorization validity|deterministic attempt history differs",
    ):
        run()
    assert called == []


def test_fully_rehashed_copied_retry_metadata_cannot_reach_parent(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict) and isinstance(paths["capture_evidence"], Path)
    evidence = json.loads(paths["capture_evidence"].read_bytes())
    entry = next(
        row for row in evidence["entries"]
        if row["role"] == "schedule_support"
        and row["path"].endswith("result-0001.json")
    )
    result_path = tmp_path / entry["path"]
    result = json.loads(result_path.read_bytes())
    result["attempt"]["backoff_seconds"] = 1.0
    write_json(result_path, result)
    entry["sha256"] = digest(result_path)
    write_json(paths["capture_evidence"], evidence)
    reanchor_all_outer_hashes(arguments)
    called, run = guarded_call(arguments)
    with pytest.raises(
        PAVolumeSourceAuthorityError,
        match="result differs|journal attempts differ",
    ):
        run()
    assert called == []


def test_extra_unbound_file_cannot_reach_parent(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    (tmp_path / "unbound.json").write_text("{}\n", encoding="utf-8")
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="exact file set"):
        run()
    assert called == []


def test_well_hashed_arbitrary_builder_binding_cannot_pass(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict) and isinstance(paths["pa"], Path)
    artifact = json.loads(paths["pa"].read_bytes())
    artifact["source"]["bindings"]["builder_source_sha256"] = "e" * 64
    write_json(paths["pa"], artifact)
    reanchor_all_outer_hashes(arguments)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="deterministic source rebuild"):
        run()
    assert called == []


def test_valid_authority_calls_parent_once_and_binds_exact_artifact_and_rebuild(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    called, run = guarded_call(arguments)
    binding = run()
    assert len(called) == 1
    authority = called[0]
    assert isinstance(authority, VerifiedPAVolumeSourceAuthority)
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    assert binding["pa_volume_artifact_sha256"] == digest(paths["pa"])
    assert binding["pa_volume_rebuild_manifest_sha256"] == digest(paths["rebuild"])
    assert binding["pa_volume_source_release_manifest_sha256"] == digest(paths["source"])
    assert binding["pa_volume_source_release_external_verification_sha256"] == digest(paths["source_verification"])
    assert binding["pa_volume_dependency_lock_sha256"] == digest(paths["lock"])
    assert binding["pa_volume_source_access_authorization_sha256"] == digest(
        paths["authorization"]
    )
    assert binding["pa_volume_runtime_policy_sha256"] == digest(
        paths["runtime_policy"]
    )
    assert binding["pa_volume_capture_receipt_evidence_sha256"] == digest(
        paths["capture_evidence"]
    )
