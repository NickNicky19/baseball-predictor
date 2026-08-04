"""Offline fail-closed validator for the corrected June 28 AWS infrastructure package."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_COMMIT = "0eb830742ef4f5349cab7c1f9b225b912bef0c70"
ACCOUNT = "723322847536"
REGION = "us-east-1"
ADMIN = "arn:aws:iam::723322847536:user/mlb-retention-admin"
AUDIT = "arn:aws:iam::723322847536:user/mlb-retention-audit"
EVIDENCE_BUCKET = "mlb-statcast-evidence-lock-20260803-8868838408-v1"
AUDIT_BUCKET = "mlb-statcast-audit-lock-723322847536-us-east-1-v1"
EXPECTED_ROLES = {
    "infrastructure": "arn:aws:iam::723322847536:role/mlb-retention/MlbStatcastJune28RetentionInfrastructureV1",
    "writer": "arn:aws:iam::723322847536:role/mlb-retention/MlbStatcastJune28RetentionWriterV1",
    "verifier": "arn:aws:iam::723322847536:role/mlb-retention/MlbStatcastJune28RetentionVerifierV1",
}
AUTHORITY_PATH = "config/shared_pa_statcast_june28_aws_object_lock_infrastructure_execution_authority_v1.json"
SCHEMA_PATH = "contracts/schemas/shared_pa_statcast_june28_aws_object_lock_infrastructure_execution_authorization_v1.schema.json"
CHANGED_PATH_MANIFEST_PATH = "reports/shared_pa_statcast_june28_aws_object_lock_infrastructure_changed_path_manifest_20260804_v1.json"
PLAN_PATH = "reports/shared_pa_statcast_june28_aws_object_lock_infrastructure_plan_20260804_v1.json"
PREPARATION_MANIFEST_PATH = "reports/shared_pa_statcast_june28_aws_object_lock_infrastructure_preparation_manifest_20260804_v1.json"
CORRECTION_REPORT_PATH = "reports/shared_pa_statcast_june28_aws_object_lock_infrastructure_readiness_correction_20260804_v1.json"
EXECUTOR_PATH = "scripts/execute_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py"
VALIDATOR_PATH = "scripts/validate_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py"
TEST_PATH = "tests/test_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py"
EXPECTED_CHANGED_PATHS = sorted([
    AUTHORITY_PATH, SCHEMA_PATH, CHANGED_PATH_MANIFEST_PATH, PLAN_PATH,
    PREPARATION_MANIFEST_PATH, CORRECTION_REPORT_PATH, EXECUTOR_PATH,
    VALIDATOR_PATH, TEST_PATH,
])
RECORD_SECTION_BINDINGS = {
    "trust_policies_sha256": "trust_policies",
    "identity_policies_sha256": "identity_policies",
    "evidence_bucket_policy_sha256": "evidence_bucket_policy",
    "audit_bucket_policy_sha256": "audit_bucket_policy",
    "bucket_controls_sha256": "bucket_controls",
    "cloudtrail_trail_configuration_sha256": "cloudtrail_trail",
}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_RE = re.compile(r"^[0-9a-f]{40}$")
UTC_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$")


class InfrastructurePackageError(RuntimeError):
    pass


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json_sha256(value: Any) -> str:
    return sha256_bytes((json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode())


def canonical_changed_path_list_sha256(paths: list[str]) -> str:
    return sha256_bytes(("\n".join(sorted(paths)) + "\n").encode())


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise InfrastructurePackageError(f"JSON object required: {path}")
    return value


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _placeholder(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_placeholder(item) for item in value)
    return isinstance(value, str) and any(token in value.upper() for token in ("PLACEHOLDER", "TBD", "REPLACE_ME", "<INSERT"))


def validate_authorization_record(record: dict[str, Any], repository: Path) -> list[str]:
    """Strictly validate a future record without requiring that it exists now."""
    schema = load_object(repository / SCHEMA_PATH)
    failures: list[str] = []
    required = schema["required"]
    properties = schema["properties"]
    if set(record) != set(required):
        failures.append("authorization record fields differ from strict schema")
    for name in required:
        if name not in record:
            continue
        rule = properties[name]
        value = record[name]
        if "const" in rule and value != rule["const"]:
            failures.append(f"authorization constant mismatch: {name}")
        reference = rule.get("$ref", "")
        if reference.endswith("/sha256") and (not isinstance(value, str) or not SHA_RE.fullmatch(value)):
            failures.append(f"authorization SHA-256 malformed: {name}")
        if reference.endswith("/gitSha") and (not isinstance(value, str) or not GIT_RE.fullmatch(value)):
            failures.append(f"authorization Git SHA malformed: {name}")
        if reference.endswith("/utc") and (not isinstance(value, str) or not UTC_RE.fullmatch(value)):
            failures.append(f"authorization UTC malformed: {name}")
    if isinstance(record.get("authorization_id"), str):
        rule = properties["authorization_id"]
        if not re.fullmatch(rule["pattern"], record["authorization_id"]) or not rule["minLength"] <= len(record["authorization_id"]) <= rule["maxLength"]:
            failures.append("authorization ID malformed")
    try:
        issued = _parse_utc(record["issued_utc"])
        valid = _parse_utc(record["valid_from_utc"])
        expires = _parse_utc(record["expires_utc"])
        if not issued <= valid < expires or (expires - valid).total_seconds() > 3600:
            failures.append("authorization timing or maximum duration mismatch")
    except (KeyError, TypeError, ValueError):
        failures.append("authorization timestamps invalid")
    if _placeholder(record):
        failures.append("authorization contains placeholder")
    return failures


def validate_plan(plan: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if plan.get("status") != "CORRECTED_PREPARED_INACTIVE_NOT_AUTHORIZED_NOT_EXECUTED":
        failures.append("plan status mismatch")
    for flag in ("preparation_is_aws_authorization", "aws_write_authorized", "upload_authorized", "evidence_retention_authorized", "deletion_authorized"):
        if plan.get(flag) is not False:
            failures.append(f"plan must not authorize: {flag}")
    if (plan.get("preparation_base_commit"), plan.get("account"), plan.get("region")) != (BASE_COMMIT, ACCOUNT, REGION):
        failures.append("plan base/account/region mismatch")
    if (plan.get("evidence_bucket"), plan.get("audit_bucket")) != (EVIDENCE_BUCKET, AUDIT_BUCKET):
        failures.append("plan bucket identity mismatch")
    principals = plan.get("principals", {})
    if principals.get("bootstrap_administrator") != ADMIN or principals.get("audit_user") != AUDIT:
        failures.append("plan user identity mismatch")
    for kind, arn in EXPECTED_ROLES.items():
        if principals.get(f"{kind}_role") != arn:
            failures.append(f"plan role mismatch: {kind}")
    baseline = plan.get("audit_user_preexisting_policy_baseline", {})
    if baseline.get("inline_policies") != [] or baseline.get("attached_policy_arns") != ["arn:aws:iam::aws:policy/IAMUserChangePassword", "arn:aws:iam::aws:policy/SignInLocalDevelopmentAccess"] or baseline.get("retention_infrastructure_mutation_allowed") is not False:
        failures.append("audit-user preexisting policy baseline mismatch")
    client = plan.get("execution_client", {})
    if client.get("absolute_path") != r"C:\Users\nicho\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe" or client.get("version_prefix") != "aws-cli/2.36.14":
        failures.append("execution client is not exactly pinned")
    atomic = plan.get("atomic_consumption", {})
    if atomic.get("operation") != "iam:CreateRole" or atomic.get("must_be_first_mutation") is not True or atomic.get("existing_role_is_reused") is not False:
        failures.append("atomic authorization consumption mismatch")
    if set(atomic.get("create_time_tags", [])) != {"AuthorizationId", "AuthorizationRecordSha256", "HumanAuthorizationSha256", "ExecutionCommit", "InfrastructurePlanSha256", "ExecutorSha256"}:
        failures.append("atomic consumption tags mismatch")
    controls = plan.get("bucket_controls", {})
    for kind in ("evidence", "audit"):
        control = controls.get(kind, {})
        if control.get("versioning") != {"Status": "Enabled"} or control.get("ownership_controls") != {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}:
            failures.append(f"{kind} versioning or ownership mismatch")
        if set(control.get("public_access_block", {}).values()) != {True}:
            failures.append(f"{kind} public access block incomplete")
        if control.get("default_encryption") != {"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}:
            failures.append(f"{kind} encryption mismatch")
        if control.get("lifecycle_configuration") is not None:
            failures.append(f"{kind} lifecycle unexpectedly configured")
    if controls.get("evidence", {}).get("object_lock_configuration") != {"ObjectLockEnabled": "Enabled"} or controls.get("evidence", {}).get("default_retention") is not None:
        failures.append("evidence bucket default retention mismatch")
    if controls.get("audit", {}).get("object_lock_configuration") != {"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 2557}}}:
        failures.append("audit Object Lock retention mismatch")
    trail = plan.get("cloudtrail_trail", {})
    if trail.get("cloudtrail_lake_used") is not False or trail.get("audit_retention_days") != 2557 or trail.get("log_file_validation_enabled") is not True:
        failures.append("supported standard trail design mismatch")
    if trail.get("s3_bucket_name") != AUDIT_BUCKET or trail.get("is_multi_region_trail") is not False or trail.get("include_global_service_events") is not True:
        failures.append("standard trail identity mismatch")
    serialized = json.dumps(plan, sort_keys=True)
    for forbidden in ("CreateEventDataStore", "cloudtrail_lake", "PutObjectRetention\"", "assume_writer", "assume_verifier"):
        if forbidden == "cloudtrail_lake":
            continue
    boundary = plan.get("infrastructure_mode_boundary", {})
    if boundary.get("maximum_evidence_object_upload_count") != 0 or any(boundary.get(key) is not False for key in ("may_put_evidence_objects", "may_create_multipart_upload", "may_apply_evidence_retention", "may_apply_evidence_legal_hold", "may_assume_writer_role", "may_assume_verifier_role")):
        failures.append("infrastructure evidence boundary weakened")
    if "CreateEventDataStore" in serialized or "BillingMode" in serialized or "TerminationProtectionEnabled" in serialized:
        failures.append("ineligible CloudTrail Lake operation retained")
    expected_ops = [
        "consume_authorization_create_infrastructure_role", "put_infrastructure_role_policy",
        "create_writer_role", "put_writer_role_policy", "create_verifier_role",
        "put_verifier_role_policy", "put_audit_user_policy", "assume_infrastructure_role",
        "create_evidence_object_lock_bucket", "enable_evidence_versioning",
        "set_evidence_bucket_owner_enforced", "set_evidence_full_block_public_access",
        "set_evidence_aes256_default_encryption", "set_exact_evidence_bucket_policy",
        "create_audit_object_lock_bucket", "enable_audit_versioning",
        "set_audit_bucket_owner_enforced", "set_audit_full_block_public_access",
        "set_audit_aes256_default_encryption", "set_audit_default_compliance_retention_2557_days",
        "set_exact_audit_bucket_policy", "create_standard_cloudtrail_trail",
        "set_exact_cloudtrail_event_selectors", "start_cloudtrail_logging",
        "verify_terminal_infrastructure",
    ]
    if plan.get("operation_sequence") != expected_ops:
        failures.append("operation sequence mismatch")
    return failures


def git_changed_paths(repository: Path) -> list[str]:
    result = subprocess.run(["git", "diff", "--name-only", BASE_COMMIT], cwd=repository, text=True, capture_output=True, check=False)
    if result.returncode:
        raise InfrastructurePackageError(result.stderr.strip())
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=repository, text=True, capture_output=True, check=False)
    if untracked.returncode:
        raise InfrastructurePackageError(untracked.stderr.strip())
    return sorted(set(line for line in (result.stdout + untracked.stdout).splitlines() if line))


def validate(repository: Path, require_base_head: bool = True, allow_active_authorization: bool = False) -> dict[str, Any]:
    repository = repository.resolve()
    plan = load_object(repository / PLAN_PATH)
    authority = load_object(repository / AUTHORITY_PATH)
    changed = load_object(repository / CHANGED_PATH_MANIFEST_PATH)
    preparation = load_object(repository / PREPARATION_MANIFEST_PATH)
    correction = load_object(repository / CORRECTION_REPORT_PATH)
    failures = validate_plan(plan)
    expected_path_hash = canonical_changed_path_list_sha256(EXPECTED_CHANGED_PATHS)
    if changed.get("changed_paths") != EXPECTED_CHANGED_PATHS or changed.get("canonical_changed_path_list_sha256") != expected_path_hash:
        failures.append("changed-path manifest mismatch")
    if preparation.get("changed_paths") != EXPECTED_CHANGED_PATHS or preparation.get("canonical_changed_path_list_sha256") != expected_path_hash:
        failures.append("preparation manifest path scope mismatch")
    file_hashes = preparation.get("non_self_file_sha256", {})
    if set(file_hashes) != set(EXPECTED_CHANGED_PATHS) - {PREPARATION_MANIFEST_PATH}:
        failures.append("preparation manifest file map mismatch")
    for relative, expected in file_hashes.items():
        path = repository / relative
        if not path.is_file() or sha256_file(path) != expected:
            failures.append(f"file SHA-256 mismatch: {relative}")
    plan_sha = sha256_file(repository / PLAN_PATH)
    section_hashes = {field: canonical_json_sha256(plan[section]) for field, section in RECORD_SECTION_BINDINGS.items()}
    binding = authority.get("corrected_bindings", {})
    if authority.get("preparation_base_commit") != BASE_COMMIT or binding.get("plan_sha256") != plan_sha or binding.get("section_sha256") != section_hashes:
        failures.append("authority plan/section binding mismatch")
    if binding.get("executor_sha256") != sha256_file(repository / EXECUTOR_PATH) or binding.get("validator_sha256") != sha256_file(repository / VALIDATOR_PATH) or binding.get("authorization_schema_sha256") != sha256_file(repository / SCHEMA_PATH):
        failures.append("authority executable/schema binding mismatch")
    for flag in ("preparation_is_execution_authorization", "aws_write_authorized", "infrastructure_execution_authorized", "evidence_upload_authorized", "retention_application_authorized", "deletion_authorized", "self_authorization_permitted"):
        if authority.get(flag) is not False:
            failures.append(f"authority flag must remain false: {flag}")
    if correction.get("statuses") != ["AWS_INFRASTRUCTURE_EXECUTOR_DEFECTS_ACCEPTED", "NON_CIRCULAR_AWS_EXECUTION_BINDING_PREPARED", "DURABLE_SINGLE_USE_CONSUMPTION_PREPARED", "SUPPORTED_AUDIT_MECHANISM_PREPARED", "NO_AWS_MUTATION_PERFORMED", "NO_EVIDENCE_UPLOAD_PERFORMED"]:
        failures.append("correction report status mismatch")
    changed_now = git_changed_paths(repository)
    if not allow_active_authorization and changed_now and changed_now != EXPECTED_CHANGED_PATHS:
        failures.append("working/base-to-head path scope mismatch")
    if require_base_head:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, text=True, capture_output=True, check=True).stdout.strip()
        if head != BASE_COMMIT:
            first = subprocess.run(["git", "rev-parse", "HEAD^1"], cwd=repository, text=True, capture_output=True, check=False)
            if first.returncode or first.stdout.strip() != BASE_COMMIT:
                failures.append("repository base/first-parent mismatch")
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "base_commit": BASE_COMMIT,
        "plan_sha256": plan_sha,
        "authority_sha256": sha256_file(repository / AUTHORITY_PATH),
        "section_sha256": section_hashes,
        "canonical_changed_path_list_sha256": expected_path_hash,
        "aws_mutation_performed": False,
        "evidence_uploaded": False,
        "total_real_statcast_requests": 4,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--allow-uncommitted-head", action="store_true")
    args = parser.parse_args()
    result = validate(args.repository, require_base_head=not args.allow_uncommitted_head)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
