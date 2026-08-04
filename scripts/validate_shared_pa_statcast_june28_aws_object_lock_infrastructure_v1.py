"""Offline fail-closed validator for the inactive June 28 AWS infrastructure package."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


BASE_COMMIT = "aa36ab090c563c53ea3dcf5781fedb9ff8652df7"
PLAN_PATH = "reports/shared_pa_statcast_june28_aws_object_lock_infrastructure_plan_20260804_v1.json"
AUTHORITY_PATH = "config/shared_pa_statcast_june28_aws_object_lock_infrastructure_execution_authority_v1.json"
PREPARATION_MANIFEST_PATH = "reports/shared_pa_statcast_june28_aws_object_lock_infrastructure_preparation_manifest_20260804_v1.json"
CHANGED_PATH_MANIFEST_PATH = "reports/shared_pa_statcast_june28_aws_object_lock_infrastructure_changed_path_manifest_20260804_v1.json"
EXECUTOR_PATH = "scripts/execute_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py"
VALIDATOR_PATH = "scripts/validate_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py"
TEST_PATH = "tests/test_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py"
EXPECTED_CHANGED_PATHS = sorted([
    AUTHORITY_PATH,
    CHANGED_PATH_MANIFEST_PATH,
    PLAN_PATH,
    PREPARATION_MANIFEST_PATH,
    EXECUTOR_PATH,
    VALIDATOR_PATH,
    TEST_PATH,
])
EXPECTED_PLAN_BYTES = 26523
EXPECTED_PLAN_SHA256 = "ed66e8fba03ec496a0cb05cd124553f37abb46f4f1868d5cacd0e3a7eb6e6724"
EXPECTED_SECTION_SHA256 = {
    "trust_policies": "c37cf48e6f6f22b3959dc106694d41396d76707cfcd4efa7183aa35cc4bb0fa5",
    "identity_policies": "00aaeaecc94778931bda64621e28275c2678565e26a6de1ab3397c65b7fb5644",
    "bucket_policy": "1c5930a431dab3207084f180c3a0674daa65f8d178a5d8d81a398898dc2594d4",
    "bucket_controls": "e1492e940e79bd661f7a10d42991b8f32cb810906d5c52991708533de67bc0b3",
    "cloudtrail_lake": "eeb5d57ead96102b89c13c98e59e6068b194006d12702e88bb04e5be7d7cfdc1",
    "create_only_upload_contract": "fb72bf97134b113cbad76f5f727a2d67a7b40268ccc95cf00eda30feef3867f8",
    "exact_version_verification_contract": "71f92514a4ef5bf272135672d67713c4a0a621c6b3d83195a23efe10b085d131",
    "custody_receipt_contract": "f8451a049c0d345512f53ad3ae7d2a9a566eeecb92605d9a864d712a1fcecca2",
}
EXPECTED_ACCOUNT = "723322847536"
EXPECTED_REGION = "us-east-1"
EXPECTED_BUCKET = "mlb-statcast-evidence-lock-20260803-8868838408-v1"
EXPECTED_ADMIN = "arn:aws:iam::723322847536:user/mlb-retention-admin"
EXPECTED_AUDIT = "arn:aws:iam::723322847536:user/mlb-retention-audit"
EXPECTED_ROLES = {
    "infrastructure": "arn:aws:iam::723322847536:role/mlb-retention/MlbStatcastJune28RetentionInfrastructureV1",
    "writer": "arn:aws:iam::723322847536:role/mlb-retention/MlbStatcastJune28RetentionWriterV1",
    "verifier": "arn:aws:iam::723322847536:role/mlb-retention/MlbStatcastJune28RetentionVerifierV1",
}


class InfrastructurePackageError(RuntimeError):
    pass


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256_bytes(payload)


def canonical_changed_path_list_sha256(paths: list[str]) -> str:
    return sha256_bytes(("\n".join(sorted(paths)) + "\n").encode("utf-8"))


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise InfrastructurePackageError(f"JSON object required: {path}")
    return value


def git_head(repository: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        raise InfrastructurePackageError(result.stderr.strip() or "git rev-parse failed")
    return result.stdout.strip()


def git_first_parent(repository: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD^1"], cwd=repository, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def validate_plan(plan: dict[str, Any], plan_path: Path) -> list[str]:
    failures: list[str] = []
    if plan_path.stat().st_size != EXPECTED_PLAN_BYTES:
        failures.append("plan byte count mismatch")
    if sha256_file(plan_path) != EXPECTED_PLAN_SHA256:
        failures.append("plan SHA-256 mismatch")
    if plan.get("status") != "PREPARED_READ_ONLY_NOT_AUTHORIZED_NOT_APPLIED":
        failures.append("plan status mismatch")
    for flag in (
        "preparation_is_aws_authorization", "aws_write_authorized", "upload_authorized",
        "retention_lock_authorized", "deletion_authorized", "repository_mutation_authorized",
    ):
        if plan.get(flag) is not False:
            failures.append(f"plan flag must be false: {flag}")
    for name, expected in EXPECTED_SECTION_SHA256.items():
        if canonical_json_sha256(plan.get(name)) != expected:
            failures.append(f"plan section SHA-256 mismatch: {name}")
    if plan.get("account") != EXPECTED_ACCOUNT or plan.get("region") != EXPECTED_REGION:
        failures.append("plan account or region mismatch")
    if plan.get("bucket") != EXPECTED_BUCKET:
        failures.append("plan bucket mismatch")
    principals = plan.get("principals", {})
    if principals.get("bootstrap_administrator") != EXPECTED_ADMIN:
        failures.append("plan administrator mismatch")
    if principals.get("audit_user") != EXPECTED_AUDIT:
        failures.append("plan audit user mismatch")
    for name, expected in EXPECTED_ROLES.items():
        if principals.get(f"{name}_role") != expected:
            failures.append(f"plan role mismatch: {name}")
    retention = plan.get("retention", {})
    if retention.get("mode") != "COMPLIANCE" or retention.get("retain_until_utc") != "2033-08-03T00:00:00Z":
        failures.append("plan retention mismatch")
    if retention.get("status") != "PROPOSAL_ONLY_REQUIRES_SEPARATE_EXPLICIT_HUMAN_AUTHORIZATION":
        failures.append("retention is not proposal-only")
    controls = plan.get("bucket_controls", {})
    if controls.get("versioning", {}).get("Status") != "Enabled":
        failures.append("Versioning is not enabled")
    if controls.get("object_lock", {}).get("ObjectLockEnabled") != "Enabled":
        failures.append("Object Lock is not enabled")
    if controls.get("ownership_controls", {}).get("Rules") != [{"ObjectOwnership": "BucketOwnerEnforced"}]:
        failures.append("ownership controls mismatch")
    if set(controls.get("public_access_block", {}).values()) != {True}:
        failures.append("Block Public Access is incomplete")
    if controls.get("default_encryption", {}).get("Rules", [{}])[0].get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm") != "AES256":
        failures.append("encryption mismatch")
    if controls.get("default_retention") is not None or controls.get("lifecycle_configuration") is not None:
        failures.append("unexpected default retention or lifecycle")
    if plan.get("cloudtrail_lake", {}).get("retention_period_days") != 2557:
        failures.append("CloudTrail retention mismatch")
    if plan.get("cloudtrail_lake", {}).get("termination_protection_enabled") is not True:
        failures.append("CloudTrail termination protection missing")
    return failures


def validate_authority(authority: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for flag in (
        "preparation_is_execution_authorization", "aws_write_authorized",
        "infrastructure_execution_authorized", "evidence_upload_authorized",
        "retention_application_authorized", "deletion_authorized", "self_authorization_permitted",
    ):
        if authority.get(flag) is not False:
            failures.append(f"authority flag must be false: {flag}")
    if authority.get("preparation_base_commit") != BASE_COMMIT:
        failures.append("authority base mismatch")
    if authority.get("account_id") != EXPECTED_ACCOUNT or authority.get("region") != EXPECTED_REGION:
        failures.append("authority account or region mismatch")
    if authority.get("bucket") != EXPECTED_BUCKET:
        failures.append("authority bucket mismatch")
    identities = authority.get("authenticated_identities", {})
    if identities.get("bootstrap_administrator") != EXPECTED_ADMIN or identities.get("audit_user") != EXPECTED_AUDIT:
        failures.append("authority identity mismatch")
    if identities.get("administrator_mfa_registered") is not True or identities.get("audit_user_mfa_registered") is not True:
        failures.append("authority MFA state mismatch")
    if authority.get("planned_roles") != EXPECTED_ROLES:
        failures.append("authority role binding mismatch")
    plan = authority.get("infrastructure_plan", {})
    if plan.get("path") != PLAN_PATH or plan.get("bytes") != EXPECTED_PLAN_BYTES or plan.get("sha256") != EXPECTED_PLAN_SHA256:
        failures.append("authority plan binding mismatch")
    if plan.get("section_sha256") != EXPECTED_SECTION_SHA256:
        failures.append("authority section binding mismatch")
    future = authority.get("future_execution_authorization", {})
    if not all(future.get(key) is True for key in (
        "must_be_separately_merged", "must_be_short_lived", "must_be_active", "must_be_unused",
        "must_bind_repository_commit", "must_bind_authority_package_sha256", "must_bind_plan_sha256",
        "must_bind_human_authorization_sha256", "must_acknowledge_bucket_object_lock_irreversibility",
    )):
        failures.append("future authorization requirements weakened")
    if future.get("may_authorize_evidence_upload") is not False or future.get("may_authorize_writer_or_verifier_assumption") is not False:
        failures.append("future authorization exceeds infrastructure boundary")
    evidence = authority.get("exact_evidence_boundary", {})
    if evidence.get("maximum_object_upload_count") != 0 or evidence.get("object_keys_may_be_created_by_this_executor") is not False:
        failures.append("infrastructure package can upload evidence")
    scientific = authority.get("scientific_state", {})
    expected_scientific = {
        "confirmation_attempt_1": "CONSUMED", "total_real_statcast_requests": 4,
        "source_contract_v2_promoted": False, "source_qualified": False,
        "non_promotable": True, "independent_reproduction_completed": False,
        "full_capture_authorized": False, "model_work_authorized": False,
    }
    if scientific != expected_scientific:
        failures.append("scientific state mismatch")
    prohibited = set(authority.get("prohibited_actions", []))
    for item in ("evidence object upload", "source request or workflow dispatch", "independent reproduction"):
        if item not in prohibited:
            failures.append(f"missing prohibition: {item}")
    return failures


def validate(repository: Path, require_base_head: bool = True) -> dict[str, Any]:
    repository = repository.resolve()
    plan_path = repository / PLAN_PATH
    authority_path = repository / AUTHORITY_PATH
    changed_path_path = repository / CHANGED_PATH_MANIFEST_PATH
    preparation_path = repository / PREPARATION_MANIFEST_PATH
    plan = load_object(plan_path)
    authority = load_object(authority_path)
    changed = load_object(changed_path_path)
    preparation = load_object(preparation_path)
    failures = validate_plan(plan, plan_path) + validate_authority(authority)
    expected_path_hash = canonical_changed_path_list_sha256(EXPECTED_CHANGED_PATHS)
    if changed.get("changed_paths") != EXPECTED_CHANGED_PATHS:
        failures.append("changed-path manifest scope mismatch")
    if changed.get("canonical_changed_path_list_sha256") != expected_path_hash:
        failures.append("changed-path manifest hash mismatch")
    if preparation.get("changed_paths") != EXPECTED_CHANGED_PATHS:
        failures.append("preparation manifest scope mismatch")
    if preparation.get("canonical_changed_path_list_sha256") != expected_path_hash:
        failures.append("preparation manifest path hash mismatch")
    non_self = preparation.get("non_self_file_sha256", {})
    if set(non_self) != set(EXPECTED_CHANGED_PATHS) - {PREPARATION_MANIFEST_PATH}:
        failures.append("preparation manifest identity map mismatch")
    for relative, expected in non_self.items():
        path = repository / relative
        if not path.is_file() or sha256_file(path) != expected:
            failures.append(f"file SHA-256 mismatch: {relative}")
    if require_base_head:
        head = git_head(repository)
        if head != BASE_COMMIT and git_first_parent(repository) != BASE_COMMIT:
            failures.append("repository base or first-parent mismatch")
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "base_commit": BASE_COMMIT,
        "plan_sha256": sha256_file(plan_path),
        "authority_sha256": sha256_file(authority_path),
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
