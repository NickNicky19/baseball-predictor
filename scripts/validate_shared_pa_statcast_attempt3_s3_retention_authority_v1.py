#!/usr/bin/env python3
"""Offline-only validator for the inactive attempt-3 S3 retention proposal.

This module intentionally has no AWS SDK, subprocess, socket, or write path.
It validates preparation files and synthetic provider observations only.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any


BASE_COMMIT = "b1cef71c16e7c9af51f57bf71f88d78c2b7c11d3"
AUTHORITY_PATH = "config/shared_pa_statcast_attempt3_s3_retention_authority_package_20260802_v1.json"
SCHEMA_PATH = "contracts/schemas/shared_pa_statcast_attempt3_s3_custody_manifest_v1.schema.json"
PLAN_PATH = "reports/shared_pa_statcast_attempt3_s3_retention_execution_plan_20260802_v1.json"
ARTIFACT_KEY = "mlb-source-evidence/statcast/attempt-03/run-30725195810/artifact-8826086488/shared-pa-statcast-attempt-03-30725195810.zip"
CUSTODY_KEY = "mlb-source-evidence/statcast/attempt-03/run-30725195810/artifact-8826086488/shared-pa-statcast-attempt-03-30725195810.custody-v1.json"
BUCKET = "mlb-statcast-evidence-lock-20260802-8826086488-v1"
RETAIN_UNTIL = "2033-08-02T00:00:00Z"
ZIP_BYTES = 977314
ZIP_SHA256 = "a2fb41e783e80e067b958b0c0666d9e53cb36fffc591e33e04a0238f9c1c8fe4"
RAW_BYTES = 2918703
RAW_SHA256 = "0d4c91cb2d0eabaeaeed726fcf1d4aabd7f9d888fb4c9f90789a7ba679e0a968"
FAILURE_BYTES = 2449
FAILURE_SHA256 = "a166ab2c08c49238be1e45986764a6a7a960360d405b721602f28679e2bebdd0"
AUDIT_PRINCIPAL = "arn:aws:iam::723322847536:user/mlb-retention-audit"
AUDIT_POLICY_NAME = "MlbRetentionAuditReadOnlyV1"
AUDIT_POLICY_SHA256 = "5076611dad2b6abd27e8331d0e0b665d0d65b5ce52862ad2f654be5dc724d150"
AUDIT_POLICY_BYTES = 4908
AUDIT_ACTIVATION = "2026-08-04T21:00:00Z"
AUDIT_EXPIRATION = "2026-08-05T21:00:00Z"
REQUIRED_PROHIBITED_SIMULATIONS = {
    "s3:GetObject", "s3:PutObject", "s3:CreateBucket", "s3:PutObjectRetention",
    "iam:CreatePolicy", "iam:AttachUserPolicy", "iam:PutUserPolicy", "iam:PassRole",
    "sts:AssumeRole", "kms:Decrypt", "kms:CreateKey", "cloudtrail:CreateTrail",
    "organizations:CreatePolicy",
}
FORBIDDEN_AUDIT_ACTIONS = REQUIRED_PROHIBITED_SIMULATIONS | {
    "s3:GetObjectVersion", "s3:DeleteObject", "iam:CreatePolicyVersion",
    "iam:DeletePolicy", "iam:DeletePolicyVersion", "iam:DetachUserPolicy",
    "iam:DeleteUserPolicy", "iam:AddUserToGroup", "iam:CreateAccessKey",
    "iam:UpdateAccessKey", "iam:DeleteAccessKey", "iam:ChangePassword",
    "iam:CreateVirtualMFADevice", "iam:EnableMFADevice", "iam:DeactivateMFADevice",
    "iam:DeleteVirtualMFADevice",
}
EXPECTED_REPOSITORY_HASHES = {
    "reports/shared_pa_statcast_attempt_3_incident_preservation_20260802_v1.json": "52c5d6c03be7a5683f74b5d6aac3d6358c1f868d0718c02f42ef9d57c4b0d797",
    "reports/shared_pa_statcast_attempt_3_offline_body_validation_20260802_v1.json": "21daea2df80c000f4abfeffde3c9c3f4ac44f4e0ea7e4e8727c869dc39372cdf",
    "reports/shared_pa_statcast_attempt_3_preservation_manifest_20260802_v1.json": "55e73f0f6292b3220796ee3d2524b36c29bd115d9626a6432ebc70ff7042f660",
    "reports/shared_pa_statcast_attempt3_durable_retention_proposal_20260802_v1.json": "eac1c63f86846816bc87f4a79681c6955ad35012da2daa0da5ccf7480f595b57",
    "config/shared_pa_statcast_source_contract_v1.json": "7078857cc1815e5c8dcecd667a9b6d69079ef31c1fc68057473d1fb1dc9ceebd",
    "config/shared_pa_statcast_source_contract_v2_proposal.json": "166c49a5ea66bf4491c9d04c7f233c7bc51c032683b9883b695236dabc637328",
    "config/shared_pa_statcast_sample_attempt_history_20260802_v3.json": "43815c024b7e138850001ecdd9f6cb5b7fcb928f8c518a1cbf4c7f6e4fe016b1",
    "config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json": "4f7aea30afdbff76fedd695ef3bd83bb23e5395f4db9496405552433695d9c56",
}


class RetentionValidationError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RetentionValidationError(f"JSON root is not an object: {path}")
    return value


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RetentionValidationError(message)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(parsed.tzinfo is not None, "timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _valid_bucket_name(value: str) -> bool:
    if not 3 <= len(value) <= 63 or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", value):
        return False
    if ".." in value or re.fullmatch(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}", value):
        return False
    if value.startswith(("xn--", "sthree-", "amzn-s3-demo-")):
        return False
    return not value.endswith(("-s3alias", "--ol-s3", ".mrap", "--x-s3", "--table-s3"))


def validate_read_only_audit_policy(authority: dict[str, Any]) -> dict[str, Any]:
    audit = authority.get("read_only_audit_policy", {})
    _require(audit.get("status") == "DRAFT_PREPARED_ACCESS_ANALYZER_VALIDATION_PENDING", "audit policy draft status differs")
    _require(audit.get("policy_name") == AUDIT_POLICY_NAME, "audit policy name differs")
    _require(audit.get("target_principal") == AUDIT_PRINCIPAL, "audit principal differs")
    _require(audit.get("form_decision", {}).get("recommended") == "CUSTOMER_MANAGED", "policy form differs")
    policy = audit.get("policy_document", {})
    canonical = canonical_json(policy)
    _require(len(canonical) == AUDIT_POLICY_BYTES, "canonical audit policy byte count differs")
    _require(sha256_bytes(canonical) == AUDIT_POLICY_SHA256, "canonical audit policy SHA-256 differs")
    _require(audit.get("canonical_policy_byte_count") == AUDIT_POLICY_BYTES, "recorded audit policy byte count differs")
    _require(audit.get("canonical_policy_sha256") == AUDIT_POLICY_SHA256, "recorded audit policy SHA-256 differs")
    _require(AUDIT_POLICY_BYTES > 2048 and AUDIT_POLICY_BYTES < 6144, "policy-form quota decision is not supported")
    boundary = audit.get("time_boundary", {})
    _require(boundary.get("activation_utc") == AUDIT_ACTIVATION, "audit activation differs")
    _require(boundary.get("expiration_utc") == AUDIT_EXPIRATION, "audit expiration differs")
    _require((_parse_utc(AUDIT_EXPIRATION) - _parse_utc(AUDIT_ACTIVATION)).total_seconds() == 86400, "audit window is not exactly 24 hours")
    statements = policy.get("Statement", [])
    _require(policy.get("Version") == "2012-10-17" and statements, "audit policy grammar differs")
    actions: list[str] = []
    for statement in statements:
        _require(statement.get("Effect") == "Allow", "non-Allow statement present")
        _require("NotAction" not in statement and "Principal" not in statement and "NotResource" not in statement, "unsafe policy element present")
        item = statement.get("Action")
        statement_actions = [item] if isinstance(item, str) else list(item or [])
        _require(statement_actions and all(isinstance(action, str) and action != "*" and not action.endswith(":*") for action in statement_actions), "wildcard or malformed action present")
        actions.extend(statement_actions)
        condition = statement.get("Condition", {})
        _require(condition.get("ArnEquals", {}).get("aws:PrincipalArn") == AUDIT_PRINCIPAL, "exact principal condition missing")
        _require(condition.get("StringEquals", {}).get("aws:PrincipalAccount") == "723322847536", "principal account condition missing")
        _require(condition.get("DateGreaterThanEquals", {}).get("aws:CurrentTime") == AUDIT_ACTIVATION, "activation condition missing")
        _require(condition.get("DateLessThan", {}).get("aws:CurrentTime") == AUDIT_EXPIRATION, "expiration condition missing")
    _require(len(actions) == len(set(actions)) == audit.get("action_count") == 61, "audit action set differs or contains duplicates")
    _require(FORBIDDEN_AUDIT_ACTIONS.isdisjoint(actions), "forbidden action present in audit policy")
    matrix = audit.get("allowed_action_matrix", [])
    _require(len(matrix) == 61 and {row.get("action") for row in matrix} == set(actions), "allowed-action matrix differs")
    for row in matrix:
        _require(row.get("access_level") in {"Read", "List"}, "non-read access level present")
        _require(row.get("is_write") is False and row.get("is_permission_management") is False, "write or permission-management classification present")
        _require(row.get("dependent_actions") == [], "unreviewed dependent action present")
        _require(row.get("read_only_validation") == "PASSED_OFFLINE_AGAINST_AWS_SERVICE_REFERENCE_V1_4", "offline action validation missing")
    validation = audit.get("access_analyzer_validation", {})
    _require(validation.get("status") == "BLOCKED_ACCESS_DENIED_CURRENT_TARGET_HAS_NO_VALIDATE_POLICY_PERMISSION", "Access Analyzer blocker differs")
    _require(validation.get("denied_action") == "access-analyzer:ValidatePolicy", "Access Analyzer denied action differs")
    _require(validation.get("policy_findings_returned") is False and validation.get("attachment_blocked_until_zero_errors") is True, "Access Analyzer failure did not block attachment")
    simulations = set(audit.get("simulation_plan", {}).get("required_denied_examples", []))
    _require(REQUIRED_PROHIBITED_SIMULATIONS <= simulations, "prohibited simulation coverage is incomplete")
    for field in ("preparation_is_attachment_authorization", "policy_creation_authorized", "policy_attachment_authorized", "cleanup_authorized"):
        _require(audit.get(field) is False, f"audit policy boundary must remain false: {field}")
    return {"status": "AWS_READ_ONLY_POLICY_DRAFT_PREPARED", "validation": "AWS_READ_ONLY_POLICY_NOT_VALIDATED", "action_count": len(actions)}


def validate_repository_state(repository: Path) -> None:
    for relative, expected in EXPECTED_REPOSITORY_HASHES.items():
        path = repository / relative
        _require(path.is_file() and not path.is_symlink(), f"required repository evidence missing or unsafe: {relative}")
        _require(sha256_file(path) == expected, f"repository evidence identity mismatch: {relative}")
    v2 = load_json(repository / "config/shared_pa_statcast_source_contract_v2_proposal.json")
    _require(v2.get("status") == "INACTIVE_PROPOSAL_ONLY", "source-contract v2 is not inactive")
    for field in ("activation_authorized", "capture_authorized", "source_qualification_authorized", "source_release_authorized"):
        _require(v2.get(field) is False, f"source-contract v2 boundary differs: {field}")
    confirmation = load_json(repository / "config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json")
    _require(confirmation.get("attempts") == [], "confirmation attempt has been recorded")
    _require(confirmation.get("next_attempt") == {"attempt_number": 1, "status": "UNUSED_UNAUTHORIZED", "reserved": False, "consumed": False}, "confirmation attempt state differs")
    _require(confirmation.get("global_real_external_statcast_request_count") == 3, "real Statcast request count differs")
    old = load_json(repository / "config/shared_pa_statcast_sample_attempt_history_20260802_v3.json")
    _require(old.get("attempt_4_authorized") is False, "old attempt 4 became authorized")
    _require(old.get("remaining_attempts") == [{"attempt_number": 4, "status": "UNUSED_UNAUTHORIZED"}], "old attempt 4 state differs")
    _require(old.get("total_external_statcast_requests") == 3, "old ledger request count differs")
    _require(not (repository / "data/source/shared_pa_statcast_v2_confirmation_2023-06-28_v1").exists(), "June 28 confirmation output unexpectedly exists")


def validate_preparation(authority: dict[str, Any], schema: dict[str, Any], plan: dict[str, Any], repository: Path) -> dict[str, Any]:
    validate_repository_state(repository)
    _require(authority.get("schema_version") == "shared-pa-statcast-attempt3-s3-retention-authority-package-v1", "authority schema differs")
    for field in ("preparation_is_aws_authorization", "aws_write_authorized", "upload_authorized", "retention_lock_authorized", "deletion_authorized"):
        _require(authority.get(field) is False, f"authority must be false: {field}")
    _require(authority.get("status") == "INACTIVE_PROPOSAL_BLOCKED_ON_READ_ONLY_AUDIT_POLICY_VALIDATION_AND_ATTACHMENT_AUTHORIZATION", "authority status differs")
    identity = authority.get("aws_identity", {})
    _require(identity.get("verification_status") == "AWS_RETENTION_READ_ONLY_IDENTITY_VERIFIED_SERVICE_AUDIT_BLOCKED_ON_POLICY", "AWS verification status differs")
    _require(identity.get("account_id") == "723322847536", "AWS account differs")
    _require(identity.get("caller_arn") == AUDIT_PRINCIPAL and identity.get("caller_is_root") is False, "AWS caller differs or is root")
    _require(identity.get("partition") == "aws" and identity.get("region") is None and identity.get("configured_region") is None, "AWS partition or unresolved region differs")
    _require(identity.get("profile") == "mlb-retention-audit" and identity.get("credentials_temporary") is True, "AWS profile or credential type differs")
    audit_result = validate_read_only_audit_policy(authority)
    bucket = authority.get("bucket", {})
    _require(bucket.get("name") == BUCKET and _valid_bucket_name(BUCKET), "bucket name differs or is syntactically invalid")
    _require(bucket.get("global_availability_verified") is False, "bucket availability was not verified")
    _require(bucket.get("dedicated_new_bucket_required") is True and bucket.get("existing_bucket_selected") is False, "dedicated bucket boundary differs")
    _require(bucket.get("versioning") == "Enabled" and bucket.get("object_lock") == "Enabled", "Versioning or Object Lock differs")
    _require(bucket.get("object_ownership") == "BucketOwnerEnforced", "Object Ownership differs")
    _require(bucket.get("default_retention_rule") is None, "bucket-wide default retention is prohibited")
    _require(all(bucket.get("block_public_access", {}).get(field) is True for field in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")), "Block Public Access differs")
    retention = authority.get("retention", {})
    _require(retention.get("mode") == "COMPLIANCE", "Governance or missing retention mode")
    _require(_parse_utc(retention.get("retain_until_utc", "1970-01-01T00:00:00Z")) >= _parse_utc(RETAIN_UNTIL), "retention date is shortened")
    _require(retention.get("object_level_explicit") is True, "retention is not explicit per object")
    encryption = authority.get("encryption", {})
    _require(encryption.get("recommendation") == "SSE-S3" and encryption.get("put_object_value") == "AES256" and encryption.get("kms_key_arn") is None, "encryption decision differs")
    objects = authority.get("objects", [])
    _require([item.get("key") for item in objects] == [ARTIFACT_KEY, CUSTODY_KEY], "authorized object set differs")
    _require(len(objects) == 2 and authority.get("maximum_upload_object_count") == 2, "an unapproved third object is possible")
    evidence = authority.get("evidence", {})
    _require(evidence.get("artifact_id") == 8826086488 and evidence.get("artifact_name") == "shared-pa-statcast-attempt-03-30725195810", "artifact identity differs")
    _require(evidence.get("zip_byte_count") == ZIP_BYTES and evidence.get("zip_sha256") == ZIP_SHA256, "ZIP identity differs")
    _require(evidence.get("raw_response_byte_count") == RAW_BYTES and evidence.get("raw_response_sha256") == RAW_SHA256, "raw response identity differs")
    _require(evidence.get("failure_package_byte_count") == FAILURE_BYTES and evidence.get("failure_package_sha256") == FAILURE_SHA256, "failure package identity differs")
    _require(schema.get("x-field-phases", {}).get("fabrication_prohibited") is True, "custody schema permits fabrication")
    _require(plan.get("status") == "NONEXECUTING_PROPOSAL_ONLY" and plan.get("execution_ready") is False, "execution plan became executable")
    _require(plan.get("fixed_values", {}).get("authorized_upload_object_count") == 2, "execution plan object count differs")
    _require(not any(step.get("aws_write") and step.get("executed") for step in plan.get("sequence", [])), "execution plan records an AWS write")
    custody = authority.get("custody_manifest", {})
    implementation = authority.get("implementation", {})
    _require(custody.get("schema_path") == SCHEMA_PATH and custody.get("schema_sha256") == sha256_file(repository / SCHEMA_PATH), "custody schema identity differs")
    _require(implementation.get("execution_plan_path") == PLAN_PATH and implementation.get("execution_plan_sha256") == sha256_file(repository / PLAN_PATH), "execution plan identity differs")
    _require(implementation.get("offline_validator_path") == "scripts/validate_shared_pa_statcast_attempt3_s3_retention_authority_v1.py", "offline validator path differs")
    _require(implementation.get("offline_validator_sha256") == sha256_file(repository / implementation["offline_validator_path"]), "offline validator identity differs")
    paths = sorted(authority.get("repository", {}).get("path_allowlist", []))
    path_payload = ("\n".join(paths) + "\n").encode("utf-8")
    _require(sha256_bytes(path_payload) == authority.get("repository", {}).get("path_allowlist_sha256"), "path allowlist identity differs")
    return {
        "status": "AWS_RETENTION_AUTHORITY_PACKAGE_PREPARED",
        "aws_identity": "AWS_RETENTION_READ_ONLY_IDENTITY_VERIFIED_SERVICE_AUDIT_BLOCKED_ON_POLICY",
        "audit_policy": audit_result,
        "execution": "DURABLE_RETENTION_EXECUTION_NOT_PERFORMED",
        "authorized_object_count": 2,
        "total_real_statcast_requests": 3,
    }


def validate_synthetic_execution(authority: dict[str, Any], observation: dict[str, Any], bindings: dict[str, str]) -> None:
    """Fail-closed evaluation of synthetic future execution evidence."""
    _require(observation.get("artifact_id") == 8826086488, "artifact ID mismatch")
    _require(observation.get("artifact_name") == "shared-pa-statcast-attempt-03-30725195810", "artifact name mismatch")
    _require(observation.get("zip_byte_count") == ZIP_BYTES and observation.get("zip_sha256") == ZIP_SHA256, "ZIP mismatch")
    _require(observation.get("raw_response_sha256") == RAW_SHA256, "raw-response hash mismatch")
    _require(observation.get("failure_package_sha256") == FAILURE_SHA256, "failure-package hash mismatch")
    _require(observation.get("account_id") == bindings["account_id"], "unexpected AWS account")
    _require(observation.get("partition") == bindings["partition"], "unexpected AWS partition")
    _require(observation.get("region") == bindings["region"], "unexpected AWS region")
    _require(observation.get("bucket") == BUCKET, "unexpected bucket")
    _require(observation.get("versioning") == "Enabled", "Versioning missing")
    _require(observation.get("object_lock_enabled") is True, "Object Lock missing")
    _require(observation.get("object_lock_mode") == "COMPLIANCE", "Governance or missing Object Lock mode")
    _require(_parse_utc(observation.get("retain_until_utc", "1970-01-01T00:00:00Z")) >= _parse_utc(RETAIN_UNTIL), "retention date shortened")
    _require(observation.get("encryption") == "AES256", "unexpected encryption")
    _require(observation.get("existing_versions") == [] and observation.get("existing_delete_markers") == [], "conflicting object exists")
    _require(observation.get("uploaded_keys") == [ARTIFACT_KEY, CUSTODY_KEY], "unexpected object key or third upload")
    _require(observation.get("artifact_version_id"), "artifact VersionId missing")
    _require(observation.get("custody_version_id"), "custody VersionId missing")
    _require(observation.get("artifact_read_back_byte_count") == ZIP_BYTES and observation.get("artifact_read_back_sha256") == ZIP_SHA256, "artifact read-back mismatch")
    _require(observation.get("custody_read_back_matches") is True, "custody read-back mismatch")
    _require(observation.get("artifact_retention_verified") is True and observation.get("custody_retention_verified") is True, "retention verification missing")
    _require(observation.get("provider_fields_source") == "ACTUAL_AWS_RESPONSES", "provider response fields were fabricated")


def finalize_custody_manifest(pre_upload: dict[str, Any], provider_fields: dict[str, Any], *, provider_fields_source: str) -> dict[str, Any]:
    _require(pre_upload.get("manifest_phase") == "PRE_UPLOAD_DETERMINISTIC", "manifest is not a pre-upload template")
    _require(provider_fields_source == "ACTUAL_AWS_RESPONSES", "provider fields cannot be invented")
    required = ("uploader_identity", "upload_utc_timestamp", "account_id", "partition", "region", "bucket", "artifact_version_id", "artifact_etag", "provider_request_ids", "authorization_identity", "execution_commit")
    for field in required:
        _require(provider_fields.get(field) not in (None, "", {}), f"actual provider field missing: {field}")
    result = copy.deepcopy(pre_upload)
    result["manifest_phase"] = "FINALIZED_FROM_ACTUAL_AWS_RESPONSES"
    result["immutable_result_status"] = "FINALIZED_VERIFIED_COMPLIANCE_LOCKED"
    aws = result["aws_custody"]
    for field in ("uploader_identity", "upload_utc_timestamp", "account_id", "partition", "region", "bucket", "provider_request_ids"):
        aws[field] = copy.deepcopy(provider_fields[field])
    aws["artifact_object"]["version_id"] = provider_fields["artifact_version_id"]
    aws["artifact_object"]["etag"] = provider_fields["artifact_etag"]
    result["authorization_identity"] = provider_fields["authorization_identity"]
    result["execution_commit"] = provider_fields["execution_commit"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--authority", type=Path, default=Path(AUTHORITY_PATH))
    parser.add_argument("--schema", type=Path, default=Path(SCHEMA_PATH))
    parser.add_argument("--execution-plan", type=Path, default=Path(PLAN_PATH))
    args = parser.parse_args()
    repository = args.repository.resolve()
    authority = load_json(repository / args.authority)
    schema = load_json(repository / args.schema)
    plan = load_json(repository / args.execution_plan)
    result = validate_preparation(authority, schema, plan, repository)
    print(canonical_json(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
