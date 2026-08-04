"""Fail-closed AWS Object Lock infrastructure executor.

The default mode is offline validation. Live mutation is unreachable unless a
separately merged, active, unused authorization record and its independently
supplied hashes pass every gate. This executor never uploads evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts/validate_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py"
SPEC = importlib.util.spec_from_file_location("june28_infrastructure_validator", VALIDATOR_PATH)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(VALIDATOR)

EXPECTED_ACCOUNT = "723322847536"
EXPECTED_ADMIN = "arn:aws:iam::723322847536:user/mlb-retention-admin"
EXPECTED_AUDIT = "arn:aws:iam::723322847536:user/mlb-retention-audit"
EXPECTED_REGION = "us-east-1"
EXPECTED_BUCKET = "mlb-statcast-evidence-lock-20260803-8868838408-v1"
EXPECTED_PROFILE = "mlb-retention-admin"
AUTHORITY_PATH = ROOT / VALIDATOR.AUTHORITY_PATH
PLAN_PATH = ROOT / VALIDATOR.PLAN_PATH
FUTURE_AUTHORIZATION_RELATIVE = "config/shared_pa_statcast_june28_aws_object_lock_infrastructure_execution_authorization_v1.json"
ROLE_NAMES = {
    "infrastructure": "MlbStatcastJune28RetentionInfrastructureV1",
    "writer": "MlbStatcastJune28RetentionWriterV1",
    "verifier": "MlbStatcastJune28RetentionVerifierV1",
}
POLICY_NAMES = {
    "infrastructure": "MlbStatcastJune28RetentionInfrastructureV1",
    "writer": "MlbStatcastJune28RetentionWriterV1",
    "verifier": "MlbStatcastJune28RetentionVerifierV1",
    "audit_user": "MlbStatcastJune28RetentionAuditUserV1",
}
EVENT_DATA_STORE_NAME = "mlb-statcast-june28-retention-audit-v1"
EVIDENCE_PREFIX = "mlb-source-evidence/statcast/v2-confirmation/date-2023-06-28/attempt-01/run-30846344146/artifact-8868838408/"
INFRASTRUCTURE_OPERATIONS = (
    "create_infrastructure_role",
    "put_infrastructure_role_policy",
    "create_writer_role",
    "put_writer_role_policy",
    "create_verifier_role",
    "put_verifier_role_policy",
    "put_audit_user_policy",
    "assume_infrastructure_role",
    "create_object_lock_bucket",
    "enable_versioning",
    "set_bucket_owner_enforced",
    "set_full_block_public_access",
    "set_aes256_default_encryption",
    "set_exact_bucket_policy",
    "create_cloudtrail_lake_event_data_store",
)
FORBIDDEN_OPERATION_TOKENS = ("put_object", "upload", "writer_assume", "verifier_assume", "delete_object")


class ExecutionBlocked(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExecutionBlocked(f"JSON object required: {path}")
    return value


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def git_head(repository: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        raise ExecutionBlocked(result.stderr.strip() or "git rev-parse failed")
    return result.stdout.strip()


@dataclass(frozen=True)
class ObservedState:
    account_id: str
    actor_arn: str
    actor_type: str
    administrator_mfa_registered: bool
    administrator_session_mfa: bool
    audit_mfa_registered: bool
    region: str
    bucket_exists: bool
    existing_roles: tuple[str, ...]
    existing_policies: tuple[str, ...]
    event_data_store_exists: bool
    evidence_objects_exist: bool
    partial_infrastructure_exists: bool
    used_authorization_ids: tuple[str, ...] = ()


def validate_execution_authorization(
    record: dict[str, Any], *, record_sha256: str, expected_record_sha256: str,
    human_authorization_sha256: str, authority_package_sha256: str,
    plan_sha256: str, repository_commit: str, now: datetime,
) -> list[str]:
    failures: list[str] = []
    expected = {
        "schema_version": "shared-pa-statcast-june28-aws-object-lock-infrastructure-execution-authorization-v1",
        "account_id": EXPECTED_ACCOUNT,
        "authorized_actor_arn": EXPECTED_ADMIN,
        "region": EXPECTED_REGION,
        "bucket": EXPECTED_BUCKET,
        "repository_commit": repository_commit,
        "authority_package_sha256": authority_package_sha256,
        "infrastructure_plan_sha256": plan_sha256,
        "human_authorization_sha256": human_authorization_sha256,
        "execution_mode": "INFRASTRUCTURE_ONLY",
        "aws_write_authorized": True,
        "infrastructure_execution_authorized": True,
        "evidence_upload_authorized": False,
        "writer_or_verifier_assumption_authorized": False,
        "authorization_used": False,
        "bucket_object_lock_irreversibility_acknowledged": True,
    }
    for key, value in expected.items():
        if record.get(key) != value:
            failures.append(f"execution authorization mismatch: {key}")
    if record_sha256 != expected_record_sha256:
        failures.append("execution authorization record SHA-256 mismatch")
    if not record.get("authorization_id"):
        failures.append("execution authorization ID missing")
    try:
        issued = parse_utc(record["issued_utc"])
        valid_from = parse_utc(record["valid_from_utc"])
        expires = parse_utc(record["expires_utc"])
        if not issued < valid_from < expires:
            failures.append("execution authorization time ordering invalid")
        if not valid_from <= now < expires:
            failures.append("execution authorization inactive or expired")
        if expires - valid_from > __import__("datetime").timedelta(hours=1):
            failures.append("execution authorization window exceeds one hour")
    except (KeyError, TypeError, ValueError):
        failures.append("execution authorization timestamps invalid")
    return failures


def validate_observed_state(state: ObservedState, authorization_id: str) -> list[str]:
    failures: list[str] = []
    if state.account_id != EXPECTED_ACCOUNT:
        failures.append("wrong account")
    if state.actor_arn == f"arn:aws:iam::{EXPECTED_ACCOUNT}:root" or state.actor_type == "Root":
        failures.append("root identity prohibited")
    if state.actor_arn != EXPECTED_ADMIN or state.actor_type != "IAMUser":
        failures.append("wrong administrator identity")
    if not state.administrator_mfa_registered:
        failures.append("administrator MFA device missing")
    if not state.administrator_session_mfa:
        failures.append("administrator MFA session missing")
    if not state.audit_mfa_registered:
        failures.append("audit-user MFA device missing")
    if state.region != EXPECTED_REGION:
        failures.append("wrong region")
    if state.bucket_exists:
        failures.append("bucket already exists")
    if state.existing_roles:
        failures.append("planned role already exists")
    if state.existing_policies:
        failures.append("planned policy already exists")
    if state.event_data_store_exists:
        failures.append("CloudTrail Lake store already exists")
    if state.evidence_objects_exist:
        failures.append("evidence object already exists")
    if state.partial_infrastructure_exists:
        failures.append("partial infrastructure exists")
    if authorization_id in state.used_authorization_ids:
        failures.append("execution authorization already used")
    return failures


class InfrastructureAdapter(Protocol):
    def apply(self, operation: str, plan: dict[str, Any]) -> None: ...


def execute_infrastructure(
    *, plan: dict[str, Any], record: dict[str, Any], state: ObservedState,
    record_sha256: str, expected_record_sha256: str, human_authorization_sha256: str,
    authority_package_sha256: str, repository_commit: str, now: datetime,
    adapter: InfrastructureAdapter,
) -> tuple[str, ...]:
    failures = validate_execution_authorization(
        record, record_sha256=record_sha256,
        expected_record_sha256=expected_record_sha256,
        human_authorization_sha256=human_authorization_sha256,
        authority_package_sha256=authority_package_sha256,
        plan_sha256=sha256_file(PLAN_PATH), repository_commit=repository_commit, now=now,
    )
    failures.extend(validate_observed_state(state, str(record.get("authorization_id", ""))))
    if any(token in operation for operation in INFRASTRUCTURE_OPERATIONS for token in FORBIDDEN_OPERATION_TOKENS):
        failures.append("infrastructure operation list contains evidence operation")
    if failures:
        raise ExecutionBlocked("; ".join(failures))
    for operation in INFRASTRUCTURE_OPERATIONS:
        adapter.apply(operation, plan)
    return INFRASTRUCTURE_OPERATIONS


class AwsCliInfrastructureAdapter:
    """Exact AWS CLI adapter. Instantiation alone performs no operation."""

    def __init__(self, profile: str, region: str) -> None:
        if profile != EXPECTED_PROFILE or region != EXPECTED_REGION:
            raise ExecutionBlocked("unexpected AWS profile or region")
        self.profile = profile
        self.region = region
        self.infrastructure_environment: dict[str, str] | None = None

    def _run(self, arguments: list[str], *, infrastructure: bool = False) -> dict[str, Any]:
        environment = os.environ.copy()
        if infrastructure:
            if self.infrastructure_environment is None:
                raise ExecutionBlocked("infrastructure role has not been assumed")
            environment.update(self.infrastructure_environment)
        command = ["aws", *arguments, "--region", self.region]
        if not infrastructure:
            command.extend(["--profile", self.profile])
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, check=False)
        if result.returncode != 0:
            raise ExecutionBlocked(result.stderr.strip() or f"AWS command failed: {arguments[:2]}")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def inspect(self) -> ObservedState:
        identity = self._run(["sts", "get-caller-identity"])
        actor_arn = str(identity.get("Arn", ""))
        if actor_arn.endswith(":root"):
            actor_type = "Root"
        elif ":user/" in actor_arn:
            actor_type = "IAMUser"
        elif ":assumed-role/" in actor_arn:
            actor_type = "AssumedRole"
        else:
            actor_type = "Unknown"
        admin_mfa = self._run(["iam", "list-mfa-devices", "--user-name", "mlb-retention-admin"]).get("MFADevices", [])
        audit_mfa = self._run(["iam", "list-mfa-devices", "--user-name", "mlb-retention-audit"]).get("MFADevices", [])
        events = self._run([
            "cloudtrail", "lookup-events", "--lookup-attributes",
            "AttributeKey=Username,AttributeValue=mlb-retention-admin", "--max-results", "20",
        ]).get("Events", [])
        session_mfa = False
        for event in events:
            try:
                detail = json.loads(event.get("CloudTrailEvent", "{}"))
                attributes = detail.get("userIdentity", {}).get("sessionContext", {}).get("attributes", {})
                if attributes.get("mfaAuthenticated") == "true":
                    session_mfa = True
                    break
            except (TypeError, json.JSONDecodeError):
                continue
        buckets = self._run(["s3api", "list-buckets"]).get("Buckets", [])
        bucket_exists = any(bucket.get("Name") == EXPECTED_BUCKET for bucket in buckets)
        roles = self._run(["iam", "list-roles"]).get("Roles", [])
        planned_role_names = set(ROLE_NAMES.values())
        existing_roles = tuple(sorted(role.get("RoleName") for role in roles if role.get("RoleName") in planned_role_names))
        policies = self._run(["iam", "list-policies", "--scope", "Local"]).get("Policies", [])
        planned_policy_names = set(POLICY_NAMES.values())
        existing_policies = tuple(sorted(policy.get("PolicyName") for policy in policies if policy.get("PolicyName") in planned_policy_names))
        stores = self._run(["cloudtrail", "list-event-data-stores"]).get("EventDataStores", [])
        event_store_exists = any(store.get("Name") == EVENT_DATA_STORE_NAME for store in stores)
        evidence_objects_exist = False
        if bucket_exists:
            versions = self._run(["s3api", "list-object-versions", "--bucket", EXPECTED_BUCKET, "--prefix", EVIDENCE_PREFIX])
            evidence_objects_exist = bool(versions.get("Versions") or versions.get("DeleteMarkers"))
        partial = bucket_exists or bool(existing_roles) or bool(existing_policies) or event_store_exists
        return ObservedState(
            account_id=str(identity.get("Account", "")), actor_arn=actor_arn, actor_type=actor_type,
            administrator_mfa_registered=bool(admin_mfa), administrator_session_mfa=session_mfa,
            audit_mfa_registered=bool(audit_mfa), region=self.region,
            bucket_exists=bucket_exists, existing_roles=existing_roles,
            existing_policies=existing_policies, event_data_store_exists=event_store_exists,
            evidence_objects_exist=evidence_objects_exist,
            partial_infrastructure_exists=partial, used_authorization_ids=(),
        )

    def apply(self, operation: str, plan: dict[str, Any]) -> None:
        trust = plan["trust_policies"]
        policies = plan["identity_policies"]
        mapping: dict[str, tuple[list[str], bool]] = {
            "create_infrastructure_role": (["iam", "create-role", "--role-name", ROLE_NAMES["infrastructure"], "--path", "/mlb-retention/", "--assume-role-policy-document", json.dumps(trust["infrastructure_role"], separators=(",", ":"))], False),
            "put_infrastructure_role_policy": (["iam", "put-role-policy", "--role-name", ROLE_NAMES["infrastructure"], "--policy-name", POLICY_NAMES["infrastructure"], "--policy-document", json.dumps(policies["infrastructure_role"], separators=(",", ":"))], False),
            "create_writer_role": (["iam", "create-role", "--role-name", ROLE_NAMES["writer"], "--path", "/mlb-retention/", "--assume-role-policy-document", json.dumps(trust["writer_role"], separators=(",", ":"))], False),
            "put_writer_role_policy": (["iam", "put-role-policy", "--role-name", ROLE_NAMES["writer"], "--policy-name", POLICY_NAMES["writer"], "--policy-document", json.dumps(policies["writer_role"], separators=(",", ":"))], False),
            "create_verifier_role": (["iam", "create-role", "--role-name", ROLE_NAMES["verifier"], "--path", "/mlb-retention/", "--assume-role-policy-document", json.dumps(trust["verifier_role"], separators=(",", ":"))], False),
            "put_verifier_role_policy": (["iam", "put-role-policy", "--role-name", ROLE_NAMES["verifier"], "--policy-name", POLICY_NAMES["verifier"], "--policy-document", json.dumps(policies["verifier_role"], separators=(",", ":"))], False),
            "put_audit_user_policy": (["iam", "put-user-policy", "--user-name", "mlb-retention-audit", "--policy-name", POLICY_NAMES["audit_user"], "--policy-document", json.dumps(policies["audit_user"], separators=(",", ":"))], False),
            "create_object_lock_bucket": (["s3api", "create-bucket", "--bucket", EXPECTED_BUCKET, "--object-lock-enabled-for-bucket"], True),
            "enable_versioning": (["s3api", "put-bucket-versioning", "--bucket", EXPECTED_BUCKET, "--versioning-configuration", json.dumps(plan["bucket_controls"]["versioning"], separators=(",", ":"))], True),
            "set_bucket_owner_enforced": (["s3api", "put-bucket-ownership-controls", "--bucket", EXPECTED_BUCKET, "--ownership-controls", json.dumps(plan["bucket_controls"]["ownership_controls"], separators=(",", ":"))], True),
            "set_full_block_public_access": (["s3api", "put-public-access-block", "--bucket", EXPECTED_BUCKET, "--public-access-block-configuration", json.dumps(plan["bucket_controls"]["public_access_block"], separators=(",", ":"))], True),
            "set_aes256_default_encryption": (["s3api", "put-bucket-encryption", "--bucket", EXPECTED_BUCKET, "--server-side-encryption-configuration", json.dumps(plan["bucket_controls"]["default_encryption"], separators=(",", ":"))], True),
            "set_exact_bucket_policy": (["s3api", "put-bucket-policy", "--bucket", EXPECTED_BUCKET, "--policy", json.dumps(plan["bucket_policy"], separators=(",", ":"))], True),
            "create_cloudtrail_lake_event_data_store": (["cloudtrail", "create-event-data-store", "--name", EVENT_DATA_STORE_NAME, "--retention-period", "2557", "--termination-protection-enabled", "--advanced-event-selectors", json.dumps(plan["cloudtrail_lake"]["advanced_event_selectors"], separators=(",", ":"))], True),
        }
        if operation == "assume_infrastructure_role":
            response = self._run(["sts", "assume-role", "--role-arn", VALIDATOR.EXPECTED_ROLES["infrastructure"], "--role-session-name", "june28-retention-infrastructure-v1"])
            credentials = response.get("Credentials", {})
            required = ("AccessKeyId", "SecretAccessKey", "SessionToken")
            if not all(credentials.get(key) for key in required):
                raise ExecutionBlocked("assume-role response missing temporary credentials")
            self.infrastructure_environment = {
                "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
                "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
                "AWS_SESSION_TOKEN": credentials["SessionToken"],
                "AWS_DEFAULT_REGION": self.region,
                "AWS_REGION": self.region,
            }
            return
        if operation not in mapping:
            raise ExecutionBlocked(f"unknown operation: {operation}")
        arguments, infrastructure = mapping[operation]
        self._run(arguments, infrastructure=infrastructure)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--profile", default=EXPECTED_PROFILE)
    parser.add_argument("--authorization-path", type=Path)
    parser.add_argument("--authorization-record-sha256")
    parser.add_argument("--human-authorization-sha256")
    args = parser.parse_args()
    package_result = VALIDATOR.validate(args.repository, require_base_head=False)
    if package_result["status"] != "PASS":
        raise ExecutionBlocked("offline package validation failed: " + "; ".join(package_result["failures"]))
    if not args.execute:
        print(json.dumps({
            "status": "VALIDATE_ONLY_NO_MUTATION",
            "aws_mutation_performed": False,
            "evidence_uploaded": False,
            "execution_authorization_present": (args.repository / FUTURE_AUTHORIZATION_RELATIVE).is_file(),
            "operations_if_separately_authorized": INFRASTRUCTURE_OPERATIONS,
        }, sort_keys=True))
        return 0
    authorization_path = args.authorization_path or (args.repository / FUTURE_AUTHORIZATION_RELATIVE)
    if not authorization_path.is_file() or not args.authorization_record_sha256 or not args.human_authorization_sha256:
        raise ExecutionBlocked("separate execution authorization and both independent hashes are required")
    record_sha256 = sha256_file(authorization_path)
    record = load_object(authorization_path)
    repository_commit = git_head(args.repository)
    adapter = AwsCliInfrastructureAdapter(args.profile, EXPECTED_REGION)
    state = adapter.inspect()
    operations = execute_infrastructure(
        plan=load_object(PLAN_PATH), record=record, state=state,
        record_sha256=record_sha256,
        expected_record_sha256=args.authorization_record_sha256,
        human_authorization_sha256=args.human_authorization_sha256,
        authority_package_sha256=sha256_file(AUTHORITY_PATH),
        repository_commit=repository_commit, now=datetime.now(timezone.utc), adapter=adapter,
    )
    print(json.dumps({
        "status": "INFRASTRUCTURE_CREATION_TERMINAL",
        "operations": operations,
        "evidence_uploaded": False,
        "writer_role_assumed": False,
        "verifier_role_assumed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
