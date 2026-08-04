"""Fail-closed June 28 AWS Object Lock infrastructure executor.

Default execution is offline validation.  Live execution requires a separately
merged short-lived authorization record, an independently supplied protected
merge commit M, and independent record/human-text hashes.  The first live AWS
mutation is create-role and is the provider-backed atomic authorization claim.
This program never uploads evidence or assumes the writer/verifier evidence
roles.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
EXECUTOR_RELATIVE = "scripts/execute_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py"
VALIDATOR_RELATIVE = "scripts/validate_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py"
AUTHORITY_RELATIVE = "config/shared_pa_statcast_june28_aws_object_lock_infrastructure_execution_authority_v1.json"
PLAN_RELATIVE = "reports/shared_pa_statcast_june28_aws_object_lock_infrastructure_plan_20260804_v1.json"
SCHEMA_RELATIVE = "contracts/schemas/shared_pa_statcast_june28_aws_object_lock_infrastructure_execution_authorization_v1.schema.json"
FUTURE_AUTHORIZATION_RELATIVE = "config/shared_pa_statcast_june28_aws_object_lock_infrastructure_execution_authorization_v1.json"

VALIDATOR_PATH = ROOT / VALIDATOR_RELATIVE
SPEC = importlib.util.spec_from_file_location("june28_infrastructure_validator", VALIDATOR_PATH)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = VALIDATOR
SPEC.loader.exec_module(VALIDATOR)

EXPECTED_REPOSITORY = "NickNicky19/baseball-predictor"
EXPECTED_ACCOUNT = "723322847536"
EXPECTED_ADMIN = "arn:aws:iam::723322847536:user/mlb-retention-admin"
EXPECTED_AUDIT = "arn:aws:iam::723322847536:user/mlb-retention-audit"
EXPECTED_REGION = "us-east-1"
EXPECTED_EVIDENCE_BUCKET = "mlb-statcast-evidence-lock-20260803-8868838408-v1"
EXPECTED_AUDIT_BUCKET = "mlb-statcast-audit-lock-723322847536-us-east-1-v1"
EXPECTED_PROFILE = "mlb-retention-admin"
EXPECTED_AUDIT_ATTACHED_POLICIES = (
    "arn:aws:iam::aws:policy/IAMUserChangePassword",
    "arn:aws:iam::aws:policy/SignInLocalDevelopmentAccess",
)
EXPECTED_AWS_CLI = Path(r"C:\Users\nicho\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe")
EXPECTED_AWS_CLI_VERSION = "aws-cli/2.36.14"
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
TRAIL_NAME = "mlb-statcast-june28-retention-audit-v1"
EVIDENCE_PREFIX = "mlb-source-evidence/statcast/v2-confirmation/date-2023-06-28/attempt-01/run-30846344146/artifact-8868838408/"

INFRASTRUCTURE_OPERATIONS = (
    "consume_authorization_create_infrastructure_role",
    "put_infrastructure_role_policy",
    "create_writer_role",
    "put_writer_role_policy",
    "create_verifier_role",
    "put_verifier_role_policy",
    "put_audit_user_policy",
    "assume_infrastructure_role",
    "create_evidence_object_lock_bucket",
    "enable_evidence_versioning",
    "set_evidence_bucket_owner_enforced",
    "set_evidence_full_block_public_access",
    "set_evidence_aes256_default_encryption",
    "set_exact_evidence_bucket_policy",
    "create_audit_object_lock_bucket",
    "enable_audit_versioning",
    "set_audit_bucket_owner_enforced",
    "set_audit_full_block_public_access",
    "set_audit_aes256_default_encryption",
    "set_audit_default_compliance_retention_2557_days",
    "set_exact_audit_bucket_policy",
    "create_standard_cloudtrail_trail",
    "set_exact_cloudtrail_event_selectors",
    "start_cloudtrail_logging",
    "verify_terminal_infrastructure",
)
FORBIDDEN_OPERATION_TOKENS = (
    "put_evidence_object", "multipart", "apply_evidence_retention",
    "legal_hold", "assume_writer", "assume_verifier", "delete_object",
)
TERMINAL_STATUSES = {
    "INFRASTRUCTURE_CREATED_AND_VERIFIED",
    "PARTIAL_INFRASTRUCTURE_PRESERVED",
    "AUTHORIZATION_CONSUMED_NO_FURTHER_MUTATION",
    "PRE_MUTATION_FAILURE",
}


class ExecutionBlocked(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ExecutionBlocked(f"JSON object required: {path}")
    return value


def git(repository: Path, *args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *args], cwd=repository, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=not binary, check=False,
    )
    if result.returncode:
        stderr = result.stderr.decode("utf-8", "replace") if binary else result.stderr
        raise ExecutionBlocked((stderr or "git command failed").strip())
    return result.stdout


def _contains_identity(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return any(_contains_identity(item, forbidden) for item in value.values())
    if isinstance(value, list):
        return any(_contains_identity(item, forbidden) for item in value)
    return isinstance(value, str) and any(identity in value for identity in forbidden)


def verify_non_circular_merge(
    repository: Path, record_path: Path, record: dict[str, Any], *,
    expected_execution_commit_sha: str, expected_record_sha256: str,
    observed_default_branch_head: str | None = None,
) -> dict[str, str]:
    """Verify the protected B/H/M graph without embedding H or M in the record."""
    if not re.fullmatch(r"[0-9a-f]{40}", expected_execution_commit_sha):
        raise ExecutionBlocked("expected execution commit is malformed")
    checked_out = str(git(repository, "rev-parse", "HEAD")).strip()
    if str(git(repository, "status", "--porcelain")).strip():
        raise ExecutionBlocked("execution worktree is not clean")
    if checked_out != expected_execution_commit_sha:
        raise ExecutionBlocked("checked-out HEAD differs from expected execution commit M")
    if observed_default_branch_head is None:
        remote = str(git(repository, "ls-remote", "--exit-code", "origin", "refs/heads/main")).strip().split()
        if len(remote) != 2:
            raise ExecutionBlocked("default-branch head could not be observed")
        observed_default_branch_head = remote[0]
    if observed_default_branch_head != expected_execution_commit_sha:
        raise ExecutionBlocked("current default-branch head differs from expected execution commit M")
    parents = str(git(repository, "show", "-s", "--format=%P", checked_out)).strip().split()
    if len(parents) != 2:
        raise ExecutionBlocked("execution commit M is not an exact two-parent merge commit")
    base, head = parents
    if record.get("authorization_base_main_commit") != base:
        raise ExecutionBlocked("record authorization base B differs from merge first parent")
    tree = str(git(repository, "show", "-s", "--format=%T", checked_out)).strip()
    blob = str(git(repository, "rev-parse", f"{checked_out}:{FUTURE_AUTHORIZATION_RELATIVE}")).strip()
    if _contains_identity(record, {head, checked_out, tree, blob, expected_record_sha256}):
        raise ExecutionBlocked("authorization record embeds H, M, tree, blob, or its own hash")
    raw = str(git(
        repository, "diff-tree", "--no-commit-id", "--raw", "-r", "--no-renames",
        base, checked_out,
    )).splitlines()
    raw = [line for line in raw if line.strip()]
    if len(raw) != 1:
        raise ExecutionBlocked("first-parent delta does not contain exactly one path")
    match = re.fullmatch(r":([0-7]{6}) ([0-7]{6}) ([0-9a-f]+) ([0-9a-f]+) ([A-Z][0-9]*)\t(.+)", raw[0])
    if not match:
        raise ExecutionBlocked("first-parent raw delta is malformed")
    old_mode, new_mode, old_oid, _new_oid, status, changed_path = match.groups()
    if (changed_path, status, old_mode, new_mode) != (FUTURE_AUTHORIZATION_RELATIVE, "A", "000000", "100644"):
        raise ExecutionBlocked("first-parent delta is not the exact regular authorization-record addition")
    if set(old_oid) != {"0"}:
        raise ExecutionBlocked("authorization-record path existed in base B")
    expected_path = (repository / FUTURE_AUTHORIZATION_RELATIVE).resolve()
    if record_path.resolve() != expected_path or not record_path.is_file() or record_path.is_symlink():
        raise ExecutionBlocked("authorization record path or file type differs")
    merged_bytes = git(repository, "show", f"{checked_out}:{FUTURE_AUTHORIZATION_RELATIVE}", binary=True)
    assert isinstance(merged_bytes, bytes)
    if merged_bytes != record_path.read_bytes() or sha256_bytes(merged_bytes) != expected_record_sha256:
        raise ExecutionBlocked("authorization record bytes or independent SHA-256 differ")
    return {"base": base, "head": head, "merge": checked_out, "tree": tree, "record_blob": blob}


def validate_authorization(
    record: dict[str, Any], *, expected_record_sha256: str,
    expected_human_sha256: str, repository: Path, now: datetime,
) -> list[str]:
    failures = VALIDATOR.validate_authorization_record(record, repository)
    expected = {
        "repository": EXPECTED_REPOSITORY,
        "aws_account_id": EXPECTED_ACCOUNT,
        "authorized_administrator_arn": EXPECTED_ADMIN,
        "administrator_mfa_required": True,
        "audit_user_mfa_required": True,
        "region": EXPECTED_REGION,
        "evidence_bucket": EXPECTED_EVIDENCE_BUCKET,
        "audit_log_bucket": EXPECTED_AUDIT_BUCKET,
        "infrastructure_role_arn": VALIDATOR.EXPECTED_ROLES["infrastructure"],
        "writer_role_arn": VALIDATOR.EXPECTED_ROLES["writer"],
        "verifier_role_arn": VALIDATOR.EXPECTED_ROLES["verifier"],
        "audit_identity_arn": EXPECTED_AUDIT,
        "executor_path": EXECUTOR_RELATIVE,
        "executor_sha256": sha256_file(repository / EXECUTOR_RELATIVE),
        "validator_sha256": sha256_file(repository / VALIDATOR_RELATIVE),
        "infrastructure_plan_sha256": sha256_file(repository / PLAN_RELATIVE),
        "authority_package_sha256": sha256_file(repository / AUTHORITY_RELATIVE),
        "execution_mode": "INFRASTRUCTURE_ONLY",
        "evidence_upload_authorized": False,
        "writer_or_verifier_evidence_role_assumption_authorized": False,
        "canonical_human_authorization_text_sha256": expected_human_sha256,
        "single_use_required": True,
        "bucket_object_lock_irreversibility_acknowledged": True,
        "evidence_retention_timestamp_applied_during_infrastructure_mode": False,
        "infrastructure_execution_authorized": True,
        "authorization_previously_used": False,
    }
    plan = load_object(repository / PLAN_RELATIVE)
    for key, section in VALIDATOR.RECORD_SECTION_BINDINGS.items():
        expected[key] = sha256_bytes(canonical_json(plan[section]))
    for key, value in expected.items():
        if record.get(key) != value:
            failures.append(f"execution authorization mismatch: {key}")
    active_record = repository / FUTURE_AUTHORIZATION_RELATIVE
    if not active_record.is_file() or sha256_file(active_record) != expected_record_sha256:
        failures.append("execution authorization record absent or SHA-256 mismatch")
    try:
        issued = parse_utc(record["issued_utc"])
        valid_from = parse_utc(record["valid_from_utc"])
        expires = parse_utc(record["expires_utc"])
        if not issued < valid_from < expires:
            failures.append("authorization timestamp ordering invalid")
        if expires - valid_from > timedelta(hours=1):
            failures.append("authorization active duration exceeds one hour")
        if not valid_from <= now < expires:
            failures.append("authorization is not currently active")
    except (KeyError, TypeError, ValueError):
        failures.append("authorization timestamps are invalid")
    return failures


@dataclass(frozen=True)
class ObservedState:
    account_id: str
    actor_arn: str
    actor_type: str
    administrator_mfa_registered: bool
    administrator_session_mfa: bool
    audit_mfa_registered: bool
    region: str
    evidence_bucket_exists: bool
    audit_bucket_exists: bool
    existing_roles: tuple[str, ...]
    existing_policies: tuple[str, ...]
    audit_inline_policies: tuple[str, ...]
    audit_attached_policies: tuple[str, ...]
    trail_exists: bool
    evidence_objects_exist: bool
    partial_infrastructure_exists: bool


def validate_observed_state(state: ObservedState) -> list[str]:
    failures: list[str] = []
    if state.account_id != EXPECTED_ACCOUNT:
        failures.append("wrong account")
    if state.actor_arn.endswith(":root") or state.actor_type == "Root":
        failures.append("root actor prohibited")
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
    if state.evidence_bucket_exists:
        failures.append("evidence bucket already exists")
    if state.audit_bucket_exists:
        failures.append("audit bucket already exists")
    if state.existing_roles:
        failures.append("planned role already exists; authorization is consumed or infrastructure is partial")
    if state.existing_policies:
        failures.append("planned policy already exists")
    if state.audit_inline_policies:
        failures.append("audit user has preexisting inline policy")
    if state.audit_attached_policies != EXPECTED_AUDIT_ATTACHED_POLICIES:
        failures.append("audit user attached-policy state differs from the exact login-only baseline")
    if state.trail_exists:
        failures.append("planned CloudTrail trail already exists")
    if state.evidence_objects_exist:
        failures.append("evidence object already exists")
    if state.partial_infrastructure_exists:
        failures.append("partial infrastructure exists")
    return failures


@dataclass
class ProviderReceipt:
    sequence_number: int
    operation: str
    utc_start: str
    utc_end: str
    success: bool
    response_payload: dict[str, Any] | list[Any] | None
    aws_request_id: str | None
    aws_extended_request_id: str | None
    http_status: int | None
    created_identity: str | None
    authorization_id: str
    created_arn: str | None = None
    created_name: str | None = None
    created_id: str | None = None
    role_id: str | None = None
    policy_name: str | None = None
    policy_sha256: str | None = None
    bucket_region: str | None = None
    trail_arn: str | None = None
    tags: list[dict[str, str]] = field(default_factory=list)
    postcondition_evidence: list[dict[str, Any]] = field(default_factory=list)
    exception_type: str | None = None
    exception_message: str | None = None
    last_confirmed_postcondition: str | None = None


class InfrastructureAdapter(Protocol):
    receipts: list[ProviderReceipt]
    authorization_consumed: bool
    mutation_count: int

    def inspect(self) -> ObservedState: ...
    def apply(self, operation: str, plan: dict[str, Any], record: dict[str, Any], sequence: int) -> None: ...
    def verify_operation(self, operation: str, plan: dict[str, Any], record: dict[str, Any]) -> str: ...
    def verify_terminal(self, plan: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]: ...


def execute_infrastructure(
    *, plan: dict[str, Any], record: dict[str, Any], state: ObservedState,
    adapter: InfrastructureAdapter,
) -> dict[str, Any]:
    failures = validate_observed_state(state)
    if any(token in op for op in INFRASTRUCTURE_OPERATIONS for token in FORBIDDEN_OPERATION_TOKENS):
        failures.append("operation list contains an evidence or destructive action")
    if failures:
        return {
            "status": "PRE_MUTATION_FAILURE", "failures": failures,
            "authorization_consumed": False, "receipts": [],
            "aws_mutation_performed": False, "evidence_uploaded": False,
        }
    last_postcondition: str | None = None
    for sequence, operation in enumerate(INFRASTRUCTURE_OPERATIONS, start=1):
        try:
            if operation == "verify_terminal_infrastructure":
                terminal = adapter.verify_terminal(plan, record)
                return {
                    "status": "INFRASTRUCTURE_CREATED_AND_VERIFIED",
                    "authorization_consumed": adapter.authorization_consumed,
                    "receipts": [asdict(item) for item in adapter.receipts],
                    "terminal_verification": terminal,
                    "aws_mutation_performed": True,
                    "evidence_uploaded": False,
                    "writer_role_assumed": False,
                    "verifier_role_assumed": False,
                }
            adapter.apply(operation, plan, record, sequence)
            last_postcondition = adapter.verify_operation(operation, plan, record)
            if adapter.receipts:
                adapter.receipts[-1].last_confirmed_postcondition = last_postcondition
        except Exception as exc:  # terminal preservation is mandatory
            consumed = adapter.authorization_consumed
            status = "AUTHORIZATION_CONSUMED_NO_FURTHER_MUTATION" if consumed and adapter.mutation_count <= 1 else (
                "PARTIAL_INFRASTRUCTURE_PRESERVED" if consumed else "PRE_MUTATION_FAILURE"
            )
            return {
                "status": status,
                "authorization_consumed": consumed,
                "failure": {"type": type(exc).__name__, "message": str(exc)},
                "last_confirmed_postcondition": last_postcondition,
                "receipts": [asdict(item) for item in adapter.receipts],
                "aws_mutation_performed": adapter.mutation_count > 0,
                "evidence_uploaded": False,
                "automatic_cleanup_performed": False,
                "automatic_rerun_permitted": False,
            }
    raise AssertionError("terminal verification operation missing")


def _safe_error(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if "An error occurred" in line or line.strip().startswith("aws:")]
    return (lines[-1] if lines else "AWS command failed")[:1000]


def parse_provider_response_metadata(stderr: str) -> tuple[str | None, str | None, int | None]:
    request_ids = re.findall(r"(?:x-amz-request-id|x-amzn-requestid)['\"\s:=]+([A-Za-z0-9-]+)", stderr, re.I)
    extended = re.findall(r"x-amz-id-2['\"\s:=]+([A-Za-z0-9+/=]+)", stderr, re.I)
    statuses = re.findall(r"(?:HTTP/1\.1[\"']?\s+|status code\s*[:=]?\s*)(\d{3})", stderr, re.I)
    return (
        request_ids[-1] if request_ids else None,
        extended[-1] if extended else None,
        int(statuses[-1]) if statuses else None,
    )


class AwsCliInfrastructureAdapter:
    """Pinned AWS CLI adapter retaining only safe response metadata from debug output."""

    def __init__(self, profile: str, region: str, authorization_id: str, receipt_journal: Path) -> None:
        if profile != EXPECTED_PROFILE or region != EXPECTED_REGION:
            raise ExecutionBlocked("unexpected AWS profile or region")
        if not EXPECTED_AWS_CLI.is_file():
            raise ExecutionBlocked("pinned AWS CLI executable is absent")
        version = subprocess.run([str(EXPECTED_AWS_CLI), "--version"], capture_output=True, text=True, check=False)
        rendered = (version.stdout + version.stderr).strip()
        if version.returncode or not rendered.startswith(EXPECTED_AWS_CLI_VERSION):
            raise ExecutionBlocked("pinned AWS CLI version mismatch")
        self.profile = profile
        self.region = region
        self.authorization_id = authorization_id
        self.infrastructure_environment: dict[str, str] | None = None
        self.receipts: list[ProviderReceipt] = []
        self.authorization_consumed = False
        self.mutation_count = 0
        if not receipt_journal.is_absolute():
            raise ExecutionBlocked("receipt journal path must be absolute")
        self.receipt_journal = receipt_journal.resolve()
        try:
            self.receipt_journal.relative_to(ROOT.resolve())
        except ValueError:
            pass
        else:
            raise ExecutionBlocked("receipt journal must remain outside the repository worktree")
        if not self.receipt_journal.parent.is_dir():
            raise ExecutionBlocked("receipt journal must be an absolute path in an existing directory")
        try:
            with self.receipt_journal.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps({"schema_version": "june28-aws-infrastructure-receipt-journal-v1", "authorization_id": authorization_id, "created_utc": utc_text(utc_now())}, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise ExecutionBlocked("receipt journal already exists; overwrite prohibited") from exc

    def _journal(self, event: dict[str, Any]) -> None:
        with self.receipt_journal.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def preserve_terminal(self, outcome: dict[str, Any]) -> None:
        self._journal({"event": "terminal", "utc": utc_text(utc_now()), "outcome": outcome})

    def _run(
        self, arguments: list[str], *, infrastructure: bool = False,
        operation: str | None = None, sequence: int = 0,
    ) -> dict[str, Any]:
        environment = os.environ.copy()
        if infrastructure:
            if self.infrastructure_environment is None:
                raise ExecutionBlocked("infrastructure role has not been assumed")
            environment.update(self.infrastructure_environment)
        command = [str(EXPECTED_AWS_CLI), *arguments, "--region", self.region, "--output", "json", "--no-cli-pager"]
        if not infrastructure:
            command.extend(["--profile", self.profile])
        if operation:
            command.append("--debug")
        started = utc_now()
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, check=False)
        ended = utc_now()
        parse_error: str | None = None
        try:
            payload: dict[str, Any] = json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError as exc:
            payload = {}
            parse_error = f"malformed AWS JSON response: {exc.msg}"
        if operation:
            request_id, extended_request_id, http_status = parse_provider_response_metadata(result.stderr)
            if result.returncode == 0 and (request_id is None or http_status is None):
                parse_error = parse_error or "required AWS request ID or HTTP status absent from provider response metadata"
            role_payload = payload.get("Role", {}) if isinstance(payload.get("Role"), dict) else {}
            created_arn = role_payload.get("Arn") or payload.get("TrailARN")
            created_name = role_payload.get("RoleName") or payload.get("Name")
            created_id = role_payload.get("RoleId")
            policy_name = arguments[arguments.index("--policy-name") + 1] if "--policy-name" in arguments else None
            policy_sha = None
            if "--policy-document" in arguments:
                policy_value = json.loads(arguments[arguments.index("--policy-document") + 1])
                policy_sha = sha256_bytes(canonical_json(policy_value))
            tag_values = json.loads(arguments[arguments.index("--tags") + 1]) if "--tags" in arguments else []
            bucket_region = self.region if arguments[:2] == ["s3api", "create-bucket"] else None
            created = str(created_arn or created_name or payload.get("Location")) if (created_arn or created_name or payload.get("Location")) else None
            receipt_payload = payload
            if arguments[:2] == ["sts", "assume-role"]:
                receipt_payload = {"AssumedRoleUser": payload.get("AssumedRoleUser")}
            receipt = ProviderReceipt(
                sequence_number=sequence, operation=operation, utc_start=utc_text(started), utc_end=utc_text(ended),
                success=result.returncode == 0 and parse_error is None, response_payload=receipt_payload or None,
                aws_request_id=request_id,
                aws_extended_request_id=extended_request_id,
                http_status=http_status, created_identity=created,
                authorization_id=self.authorization_id,
                created_arn=str(created_arn) if created_arn else None,
                created_name=str(created_name) if created_name else None,
                created_id=str(created_id) if created_id else None,
                role_id=str(created_id) if created_id else None,
                policy_name=policy_name, policy_sha256=policy_sha,
                bucket_region=bucket_region,
                trail_arn=str(payload.get("TrailARN")) if payload.get("TrailARN") else None,
                tags=tag_values,
                exception_type=("AwsJsonDecodeError" if parse_error else (None if result.returncode == 0 else "AwsCliError")),
                exception_message=parse_error or (None if result.returncode == 0 else _safe_error(result.stderr)),
            )
            self.receipts.append(receipt)
            self._journal({"event": "provider_response", "receipt": asdict(receipt)})
        if result.returncode:
            raise ExecutionBlocked(_safe_error(result.stderr))
        if parse_error:
            raise ExecutionBlocked(parse_error)
        return payload

    def _readback(self, arguments: list[str]) -> dict[str, Any]:
        payload = self._run(arguments)
        if self.receipts:
            self.receipts[-1].postcondition_evidence.append({
                "api_operation": " ".join(arguments[:2]),
                "response_payload": payload,
            })
            self._journal({"event": "postcondition_readback", "operation": " ".join(arguments[:2]), "response_payload": payload})
        return payload

    def _exists(self, arguments: list[str]) -> bool:
        try:
            self._run(arguments)
            return True
        except ExecutionBlocked:
            return False

    def _bucket_name_is_absent(self, bucket: str) -> bool:
        try:
            self._run(["s3api", "head-bucket", "--bucket", bucket])
            return False
        except ExecutionBlocked as exc:
            if "(404)" in str(exc) or "Not Found" in str(exc):
                return True
            raise ExecutionBlocked(f"global availability of bucket {bucket} is unresolved") from exc

    def _role_is_absent(self, role_name: str) -> bool:
        try:
            self._run(["iam", "get-role", "--role-name", role_name])
            return False
        except ExecutionBlocked as exc:
            if "NoSuchEntity" in str(exc):
                return True
            raise ExecutionBlocked(f"absence of role {role_name} is unresolved") from exc

    def _current_access_key_id(self) -> str:
        """Resolve only the current temporary access-key identifier; never retain secrets."""
        command = [str(EXPECTED_AWS_CLI), "configure", "export-credentials", "--profile", self.profile, "--format", "process"]
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode:
            raise ExecutionBlocked("current AWS Login credential identity could not be resolved")
        try:
            credentials = json.loads(result.stdout)
            access_key_id = str(credentials.get("AccessKeyId", ""))
        except json.JSONDecodeError as exc:
            raise ExecutionBlocked("current AWS Login credential identity response malformed") from exc
        finally:
            result.stdout = ""  # do not retain the credential response
        if not access_key_id or not credentials.get("SessionToken") or not credentials.get("Expiration"):
            raise ExecutionBlocked("current credentials are not a temporary AWS Login session")
        credentials.clear()
        return access_key_id

    def inspect(self) -> ObservedState:
        identity = self._run(["sts", "get-caller-identity"])
        actor_arn = str(identity.get("Arn", ""))
        actor_type = "Root" if actor_arn.endswith(":root") else ("IAMUser" if ":user/" in actor_arn else "Unknown")
        admin_mfa = self._run(["iam", "list-mfa-devices", "--user-name", "mlb-retention-admin"]).get("MFADevices", [])
        audit_mfa = self._run(["iam", "list-mfa-devices", "--user-name", "mlb-retention-audit"]).get("MFADevices", [])
        current_access_key_id = self._current_access_key_id()
        events = self._run(["cloudtrail", "lookup-events", "--lookup-attributes", "AttributeKey=Username,AttributeValue=mlb-retention-admin", "--max-results", "50"]).get("Events", [])
        session_mfa = False
        for event in events:
            try:
                detail = json.loads(event.get("CloudTrailEvent", "{}"))
                event_identity = detail.get("userIdentity", {})
                if event_identity.get("accessKeyId") == current_access_key_id:
                    session_mfa |= event_identity.get("sessionContext", {}).get("attributes", {}).get("mfaAuthenticated") == "true"
            except (TypeError, json.JSONDecodeError):
                continue
        buckets = {item.get("Name") for item in self._run(["s3api", "list-buckets"]).get("Buckets", [])}
        evidence_exists = EXPECTED_EVIDENCE_BUCKET in buckets or not self._bucket_name_is_absent(EXPECTED_EVIDENCE_BUCKET)
        audit_exists = EXPECTED_AUDIT_BUCKET in buckets or not self._bucket_name_is_absent(EXPECTED_AUDIT_BUCKET)
        existing_roles = tuple(sorted(name for name in ROLE_NAMES.values() if not self._role_is_absent(name)))
        policies = self._run(["iam", "list-policies", "--scope", "Local"]).get("Policies", [])
        existing_policies = tuple(sorted(item.get("PolicyName") for item in policies if item.get("PolicyName") in POLICY_NAMES.values()))
        audit_inline = tuple(sorted(self._run(["iam", "list-user-policies", "--user-name", "mlb-retention-audit"]).get("PolicyNames", [])))
        audit_attached = tuple(sorted(item.get("PolicyArn") for item in self._run(["iam", "list-attached-user-policies", "--user-name", "mlb-retention-audit"]).get("AttachedPolicies", [])))
        trails = self._run(["cloudtrail", "list-trails"]).get("Trails", [])
        trail_exists = any(item.get("Name") == TRAIL_NAME for item in trails)
        evidence_objects = False
        if evidence_exists:
            versions = self._run(["s3api", "list-object-versions", "--bucket", EXPECTED_EVIDENCE_BUCKET, "--prefix", EVIDENCE_PREFIX])
            uploads = self._run(["s3api", "list-multipart-uploads", "--bucket", EXPECTED_EVIDENCE_BUCKET, "--prefix", EVIDENCE_PREFIX])
            evidence_objects = bool(versions.get("Versions") or versions.get("DeleteMarkers") or uploads.get("Uploads"))
        partial = evidence_exists or audit_exists or bool(existing_roles) or bool(existing_policies) or trail_exists
        return ObservedState(
            account_id=str(identity.get("Account", "")), actor_arn=actor_arn, actor_type=actor_type,
            administrator_mfa_registered=bool(admin_mfa), administrator_session_mfa=session_mfa,
            audit_mfa_registered=bool(audit_mfa), region=self.region,
            evidence_bucket_exists=evidence_exists, audit_bucket_exists=audit_exists,
            existing_roles=existing_roles, existing_policies=existing_policies,
            audit_inline_policies=audit_inline, audit_attached_policies=audit_attached,
            trail_exists=trail_exists, evidence_objects_exist=evidence_objects,
            partial_infrastructure_exists=partial,
        )

    def _tags(self, record: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {"Key": "AuthorizationId", "Value": record["authorization_id"]},
            {"Key": "AuthorizationRecordSha256", "Value": record["__record_sha256"]},
            {"Key": "HumanAuthorizationSha256", "Value": record["canonical_human_authorization_text_sha256"]},
            {"Key": "ExecutionCommit", "Value": record["__execution_commit"]},
            {"Key": "InfrastructurePlanSha256", "Value": record["infrastructure_plan_sha256"]},
            {"Key": "ExecutorSha256", "Value": record["executor_sha256"]},
        ]

    def apply(self, operation: str, plan: dict[str, Any], record: dict[str, Any], sequence: int) -> None:
        trust = plan["trust_policies"]
        policies = plan["identity_policies"]
        controls = plan["bucket_controls"]
        tags = self._tags(record)
        mappings: dict[str, tuple[list[str], bool]] = {
            "consume_authorization_create_infrastructure_role": (["iam", "create-role", "--role-name", ROLE_NAMES["infrastructure"], "--path", "/mlb-retention/", "--assume-role-policy-document", json.dumps(trust["infrastructure_role"], separators=(",", ":")), "--tags", json.dumps(tags, separators=(",", ":"))], False),
            "put_infrastructure_role_policy": (["iam", "put-role-policy", "--role-name", ROLE_NAMES["infrastructure"], "--policy-name", POLICY_NAMES["infrastructure"], "--policy-document", json.dumps(policies["infrastructure_role"], separators=(",", ":"))], False),
            "create_writer_role": (["iam", "create-role", "--role-name", ROLE_NAMES["writer"], "--path", "/mlb-retention/", "--assume-role-policy-document", json.dumps(trust["writer_role"], separators=(",", ":")), "--tags", json.dumps(tags, separators=(",", ":"))], False),
            "put_writer_role_policy": (["iam", "put-role-policy", "--role-name", ROLE_NAMES["writer"], "--policy-name", POLICY_NAMES["writer"], "--policy-document", json.dumps(policies["writer_role"], separators=(",", ":"))], False),
            "create_verifier_role": (["iam", "create-role", "--role-name", ROLE_NAMES["verifier"], "--path", "/mlb-retention/", "--assume-role-policy-document", json.dumps(trust["verifier_role"], separators=(",", ":")), "--tags", json.dumps(tags, separators=(",", ":"))], False),
            "put_verifier_role_policy": (["iam", "put-role-policy", "--role-name", ROLE_NAMES["verifier"], "--policy-name", POLICY_NAMES["verifier"], "--policy-document", json.dumps(policies["verifier_role"], separators=(",", ":"))], False),
            "put_audit_user_policy": (["iam", "put-user-policy", "--user-name", "mlb-retention-audit", "--policy-name", POLICY_NAMES["audit_user"], "--policy-document", json.dumps(policies["audit_user"], separators=(",", ":"))], False),
            "create_evidence_object_lock_bucket": (["s3api", "create-bucket", "--bucket", EXPECTED_EVIDENCE_BUCKET, "--object-lock-enabled-for-bucket"], True),
            "enable_evidence_versioning": (["s3api", "put-bucket-versioning", "--bucket", EXPECTED_EVIDENCE_BUCKET, "--versioning-configuration", json.dumps(controls["evidence"]["versioning"], separators=(",", ":"))], True),
            "set_evidence_bucket_owner_enforced": (["s3api", "put-bucket-ownership-controls", "--bucket", EXPECTED_EVIDENCE_BUCKET, "--ownership-controls", json.dumps(controls["evidence"]["ownership_controls"], separators=(",", ":"))], True),
            "set_evidence_full_block_public_access": (["s3api", "put-public-access-block", "--bucket", EXPECTED_EVIDENCE_BUCKET, "--public-access-block-configuration", json.dumps(controls["evidence"]["public_access_block"], separators=(",", ":"))], True),
            "set_evidence_aes256_default_encryption": (["s3api", "put-bucket-encryption", "--bucket", EXPECTED_EVIDENCE_BUCKET, "--server-side-encryption-configuration", json.dumps(controls["evidence"]["default_encryption"], separators=(",", ":"))], True),
            "set_exact_evidence_bucket_policy": (["s3api", "put-bucket-policy", "--bucket", EXPECTED_EVIDENCE_BUCKET, "--policy", json.dumps(plan["evidence_bucket_policy"], separators=(",", ":"))], True),
            "create_audit_object_lock_bucket": (["s3api", "create-bucket", "--bucket", EXPECTED_AUDIT_BUCKET, "--object-lock-enabled-for-bucket"], True),
            "enable_audit_versioning": (["s3api", "put-bucket-versioning", "--bucket", EXPECTED_AUDIT_BUCKET, "--versioning-configuration", json.dumps(controls["audit"]["versioning"], separators=(",", ":"))], True),
            "set_audit_bucket_owner_enforced": (["s3api", "put-bucket-ownership-controls", "--bucket", EXPECTED_AUDIT_BUCKET, "--ownership-controls", json.dumps(controls["audit"]["ownership_controls"], separators=(",", ":"))], True),
            "set_audit_full_block_public_access": (["s3api", "put-public-access-block", "--bucket", EXPECTED_AUDIT_BUCKET, "--public-access-block-configuration", json.dumps(controls["audit"]["public_access_block"], separators=(",", ":"))], True),
            "set_audit_aes256_default_encryption": (["s3api", "put-bucket-encryption", "--bucket", EXPECTED_AUDIT_BUCKET, "--server-side-encryption-configuration", json.dumps(controls["audit"]["default_encryption"], separators=(",", ":"))], True),
            "set_audit_default_compliance_retention_2557_days": (["s3api", "put-object-lock-configuration", "--bucket", EXPECTED_AUDIT_BUCKET, "--object-lock-configuration", json.dumps(controls["audit"]["object_lock_configuration"], separators=(",", ":"))], True),
            "set_exact_audit_bucket_policy": (["s3api", "put-bucket-policy", "--bucket", EXPECTED_AUDIT_BUCKET, "--policy", json.dumps(plan["audit_bucket_policy"], separators=(",", ":"))], True),
            "create_standard_cloudtrail_trail": (["cloudtrail", "create-trail", "--name", TRAIL_NAME, "--s3-bucket-name", EXPECTED_AUDIT_BUCKET, "--s3-key-prefix", plan["cloudtrail_trail"]["s3_key_prefix"], "--include-global-service-events", "--no-is-multi-region-trail", "--enable-log-file-validation"], True),
            "set_exact_cloudtrail_event_selectors": (["cloudtrail", "put-event-selectors", "--trail-name", TRAIL_NAME, "--event-selectors", json.dumps(plan["cloudtrail_trail"]["event_selectors"], separators=(",", ":"))], True),
            "start_cloudtrail_logging": (["cloudtrail", "start-logging", "--name", TRAIL_NAME], True),
        }
        if operation == "assume_infrastructure_role":
            response = self._run(["sts", "assume-role", "--role-arn", VALIDATOR.EXPECTED_ROLES["infrastructure"], "--role-session-name", "june28-retention-infrastructure-v2"], operation=operation, sequence=sequence)
            credentials = response.get("Credentials", {})
            if not all(credentials.get(key) for key in ("AccessKeyId", "SecretAccessKey", "SessionToken")):
                raise ExecutionBlocked("assume-role response missing temporary credentials")
            self.infrastructure_environment = {
                "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"], "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
                "AWS_SESSION_TOKEN": credentials["SessionToken"], "AWS_DEFAULT_REGION": self.region, "AWS_REGION": self.region,
            }
            return
        if operation not in mappings:
            raise ExecutionBlocked(f"unknown infrastructure operation: {operation}")
        arguments, infrastructure = mappings[operation]
        self.mutation_count += 1  # conservative: the provider call is about to be submitted
        try:
            self._run(arguments, infrastructure=infrastructure, operation=operation, sequence=sequence)
        except Exception:
            if operation == "consume_authorization_create_infrastructure_role":
                # Any ambiguous provider/transport/parse failure after submitting
                # CreateRole is conservatively treated as consumed.  A read-only
                # recovery assessment must determine whether the role exists.
                self.authorization_consumed = True
            raise
        if operation == "consume_authorization_create_infrastructure_role":
            self.authorization_consumed = True

    def _canonical_equal(self, left: Any, right: Any) -> bool:
        return canonical_json(left) == canonical_json(right)

    def _role_state(self, kind: str, plan: dict[str, Any], record: dict[str, Any], *, expect_policy: bool = True) -> dict[str, Any]:
        name = ROLE_NAMES[kind]
        role = self._readback(["iam", "get-role", "--role-name", name])["Role"]
        tags = {item["Key"]: item["Value"] for item in role.get("Tags", [])}
        expected_tags = {item["Key"]: item["Value"] for item in self._tags(record)}
        if role.get("Arn") != VALIDATOR.EXPECTED_ROLES[kind] or role.get("Path") != "/mlb-retention/" or tags != expected_tags:
            raise ExecutionBlocked(f"{kind} role identity/path/tags postcondition mismatch")
        trust = role.get("AssumeRolePolicyDocument")
        if isinstance(trust, str):
            trust = json.loads(unquote(trust))
        if not self._canonical_equal(trust, plan["trust_policies"][f"{kind}_role"]):
            raise ExecutionBlocked(f"{kind} trust policy postcondition mismatch")
        attached = self._readback(["iam", "list-attached-role-policies", "--role-name", name]).get("AttachedPolicies", [])
        inline = self._readback(["iam", "list-role-policies", "--role-name", name]).get("PolicyNames", [])
        expected_inline = [POLICY_NAMES[kind]] if expect_policy else []
        if attached or sorted(inline) != sorted(expected_inline):
            raise ExecutionBlocked(f"{kind} unexpected attached or inline policy")
        if expected_inline:
            policy = self._readback(["iam", "get-role-policy", "--role-name", name, "--policy-name", POLICY_NAMES[kind]])["PolicyDocument"]
            if isinstance(policy, str):
                policy = json.loads(unquote(policy))
            if not self._canonical_equal(policy, plan["identity_policies"][f"{kind}_role"]):
                raise ExecutionBlocked(f"{kind} inline policy postcondition mismatch")
        return {"arn": role["Arn"], "role_id": role["RoleId"], "path": role["Path"], "tags": tags}

    def _bucket_state(self, bucket: str, expected: dict[str, Any], policy: dict[str, Any], *, audit: bool) -> dict[str, Any]:
        location = self._readback(["s3api", "get-bucket-location", "--bucket", bucket]).get("LocationConstraint") or "us-east-1"
        versioning = self._readback(["s3api", "get-bucket-versioning", "--bucket", bucket])
        lock = self._readback(["s3api", "get-object-lock-configuration", "--bucket", bucket]).get("ObjectLockConfiguration", {})
        ownership = self._readback(["s3api", "get-bucket-ownership-controls", "--bucket", bucket]).get("OwnershipControls", {})
        public = self._readback(["s3api", "get-public-access-block", "--bucket", bucket]).get("PublicAccessBlockConfiguration", {})
        encryption = self._readback(["s3api", "get-bucket-encryption", "--bucket", bucket]).get("ServerSideEncryptionConfiguration", {})
        observed_policy = json.loads(self._readback(["s3api", "get-bucket-policy", "--bucket", bucket])["Policy"])
        if location != EXPECTED_REGION or versioning.get("Status") != "Enabled":
            raise ExecutionBlocked(f"{bucket} region/versioning mismatch")
        if not self._canonical_equal(ownership, expected["ownership_controls"]):
            raise ExecutionBlocked(f"{bucket} ownership controls mismatch")
        if not self._canonical_equal(public, expected["public_access_block"]):
            raise ExecutionBlocked(f"{bucket} public access block mismatch")
        if not self._canonical_equal(encryption, expected["default_encryption"]):
            raise ExecutionBlocked(f"{bucket} encryption mismatch")
        if not self._canonical_equal(observed_policy, policy):
            raise ExecutionBlocked(f"{bucket} bucket policy mismatch")
        expected_lock = expected["object_lock_configuration"]
        if not self._canonical_equal(lock, expected_lock):
            raise ExecutionBlocked(f"{bucket} Object Lock/default retention mismatch")
        try:
            lifecycle = self._readback(["s3api", "get-bucket-lifecycle-configuration", "--bucket", bucket])
        except ExecutionBlocked as exc:
            if "NoSuchLifecycleConfiguration" not in str(exc):
                raise
        else:
            if lifecycle.get("Rules"):
                raise ExecutionBlocked(f"{bucket} has unexpected lifecycle configuration")
        list_args = ["s3api", "list-object-versions", "--bucket", bucket]
        upload_args = ["s3api", "list-multipart-uploads", "--bucket", bucket]
        versions = self._readback(list_args)
        uploads = self._readback(upload_args)
        if not audit and (versions.get("Versions") or versions.get("DeleteMarkers") or uploads.get("Uploads")):
            raise ExecutionBlocked("evidence bucket contains object, delete marker, or multipart upload")
        if audit:
            expected_audit_prefix = f"mlb-statcast-retention-audit/v1/AWSLogs/{EXPECTED_ACCOUNT}/"
            observed_versions = versions.get("Versions", [])
            if versions.get("DeleteMarkers") or uploads.get("Uploads"):
                raise ExecutionBlocked("audit bucket contains delete marker or multipart upload")
            if any(not str(item.get("Key", "")).startswith(expected_audit_prefix) for item in observed_versions):
                raise ExecutionBlocked("audit bucket contains object outside the exact trail prefix")
            minimum_retention = utc_now() + timedelta(days=2556)
            for item in observed_versions:
                retention = self._readback(["s3api", "get-object-retention", "--bucket", bucket, "--key", item["Key"], "--version-id", item["VersionId"]]).get("Retention", {})
                try:
                    retain_until = parse_utc(str(retention.get("RetainUntilDate", "")))
                except ValueError as exc:
                    raise ExecutionBlocked("audit object retention timestamp malformed") from exc
                if retention.get("Mode") != "COMPLIANCE" or retain_until < minimum_retention:
                    raise ExecutionBlocked("audit object version lacks required COMPLIANCE horizon")
        return {"bucket": bucket, "region": location, "versioning": "Enabled", "object_lock": lock, "lifecycle": None, "observed_version_count": len(versions.get("Versions", []))}

    def _trail_state(self, plan: dict[str, Any]) -> dict[str, Any]:
        trail = self._readback(["cloudtrail", "get-trail", "--name", TRAIL_NAME])["Trail"]
        status = self._readback(["cloudtrail", "get-trail-status", "--name", TRAIL_NAME])
        selectors = self._readback(["cloudtrail", "get-event-selectors", "--trail-name", TRAIL_NAME]).get("EventSelectors", [])
        expected = plan["cloudtrail_trail"]
        comparisons = {
            "Name": TRAIL_NAME, "TrailARN": expected["arn"], "S3BucketName": EXPECTED_AUDIT_BUCKET,
            "S3KeyPrefix": expected["s3_key_prefix"], "IncludeGlobalServiceEvents": True,
            "IsMultiRegionTrail": False, "LogFileValidationEnabled": True,
        }
        if any(trail.get(key) != value for key, value in comparisons.items()):
            raise ExecutionBlocked("standard CloudTrail identity/configuration mismatch")
        if not self._canonical_equal(selectors, expected["event_selectors"]):
            raise ExecutionBlocked("CloudTrail event selector mismatch")
        if status.get("IsLogging") is not True:
            raise ExecutionBlocked("CloudTrail logging is not active")
        return {"trail_arn": trail.get("TrailARN"), "is_logging": True, "selectors": selectors}

    def verify_operation(self, operation: str, plan: dict[str, Any], record: dict[str, Any]) -> str:
        if operation == "consume_authorization_create_infrastructure_role":
            self._role_state("infrastructure", plan, record, expect_policy=False)
            return "authorization atomically consumed by exact tagged infrastructure role"
        if operation.startswith("put_") and "role_policy" in operation:
            kind = operation.split("_")[1]
            self._role_state(kind, plan, record)
            return f"{kind} role and inline policy verified"
        if operation.startswith("create_") and operation.endswith("_role"):
            self._role_state(operation.split("_")[1], plan, record, expect_policy=False)
            return f"{operation} exact identity, trust, tags, and empty policy state verified"
        if operation == "put_audit_user_policy":
            names = self._readback(["iam", "list-user-policies", "--user-name", "mlb-retention-audit"]).get("PolicyNames", [])
            if names != [POLICY_NAMES["audit_user"]]:
                raise ExecutionBlocked("audit user policy set mismatch")
            return "audit-user exact inline policy verified"
        if operation == "assume_infrastructure_role":
            return "infrastructure role assumed with temporary credentials"
        bucket_kind = "evidence" if "evidence" in operation else "audit"
        bucket = EXPECTED_EVIDENCE_BUCKET if bucket_kind == "evidence" else EXPECTED_AUDIT_BUCKET
        controls = plan["bucket_controls"][bucket_kind]
        staged_bucket_checks = {
            f"create_{bucket_kind}_object_lock_bucket": ("get-object-lock-configuration", "ObjectLockConfiguration", {"ObjectLockEnabled": "Enabled"}),
            f"enable_{bucket_kind}_versioning": ("get-bucket-versioning", None, controls["versioning"]),
            f"set_{bucket_kind}_bucket_owner_enforced": ("get-bucket-ownership-controls", "OwnershipControls", controls["ownership_controls"]),
            f"set_{bucket_kind}_full_block_public_access": ("get-public-access-block", "PublicAccessBlockConfiguration", controls["public_access_block"]),
            f"set_{bucket_kind}_aes256_default_encryption": ("get-bucket-encryption", "ServerSideEncryptionConfiguration", controls["default_encryption"]),
            f"set_{bucket_kind}_default_compliance_retention_2557_days": ("get-object-lock-configuration", "ObjectLockConfiguration", controls["object_lock_configuration"]),
        }
        if operation in staged_bucket_checks:
            call, wrapper, expected = staged_bucket_checks[operation]
            observed = self._readback(["s3api", call, "--bucket", bucket])
            observed = observed.get(wrapper, {}) if wrapper else observed
            if not self._canonical_equal(observed, expected):
                raise ExecutionBlocked(f"{operation} immediate postcondition mismatch")
            location = self._readback(["s3api", "get-bucket-location", "--bucket", bucket]).get("LocationConstraint") or "us-east-1"
            if location != EXPECTED_REGION:
                raise ExecutionBlocked(f"{operation} bucket region mismatch")
            return f"{operation} exact provider state read back and compared"
        if operation in {"set_exact_evidence_bucket_policy", "set_exact_audit_bucket_policy"}:
            expected_policy = plan["evidence_bucket_policy" if bucket_kind == "evidence" else "audit_bucket_policy"]
            observed_policy = json.loads(self._readback(["s3api", "get-bucket-policy", "--bucket", bucket])["Policy"])
            if not self._canonical_equal(observed_policy, expected_policy):
                raise ExecutionBlocked(f"{operation} immediate bucket-policy mismatch")
            return f"{operation} exact policy read back and compared"
        if operation == "create_standard_cloudtrail_trail":
            observed = self._readback(["cloudtrail", "get-trail", "--name", TRAIL_NAME])["Trail"]
            expected = plan["cloudtrail_trail"]
            exact = {
                "Name": TRAIL_NAME, "TrailARN": expected["arn"],
                "S3BucketName": EXPECTED_AUDIT_BUCKET, "S3KeyPrefix": expected["s3_key_prefix"],
                "IncludeGlobalServiceEvents": True, "IsMultiRegionTrail": False,
                "LogFileValidationEnabled": True,
            }
            if any(observed.get(key) != value for key, value in exact.items()):
                raise ExecutionBlocked("created standard trail immediate identity mismatch")
            return "created standard trail exact identity read back"
        if operation == "set_exact_cloudtrail_event_selectors":
            observed = self._readback(["cloudtrail", "get-event-selectors", "--trail-name", TRAIL_NAME]).get("EventSelectors", [])
            if not self._canonical_equal(observed, plan["cloudtrail_trail"]["event_selectors"]):
                raise ExecutionBlocked("CloudTrail selector postcondition mismatch")
            return "exact standard-trail selectors read back"
        if operation == "start_cloudtrail_logging":
            if self._readback(["cloudtrail", "get-trail-status", "--name", TRAIL_NAME]).get("IsLogging") is not True:
                raise ExecutionBlocked("CloudTrail logging did not start")
            return "standard trail logging read back active"
        raise ExecutionBlocked(f"no postcondition verifier for {operation}")

    def verify_terminal(self, plan: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        roles = {kind: self._role_state(kind, plan, record) for kind in ROLE_NAMES}
        audit_policy = self._readback(["iam", "get-user-policy", "--user-name", "mlb-retention-audit", "--policy-name", POLICY_NAMES["audit_user"]])["PolicyDocument"]
        if isinstance(audit_policy, str):
            audit_policy = json.loads(unquote(audit_policy))
        if not self._canonical_equal(audit_policy, plan["identity_policies"]["audit_user"]):
            raise ExecutionBlocked("audit user policy terminal mismatch")
        attached = tuple(sorted(item.get("PolicyArn") for item in self._readback(["iam", "list-attached-user-policies", "--user-name", "mlb-retention-audit"]).get("AttachedPolicies", [])))
        if attached != EXPECTED_AUDIT_ATTACHED_POLICIES:
            raise ExecutionBlocked("audit user attached-policy baseline mismatch")
        if self._readback(["iam", "list-user-policies", "--user-name", "mlb-retention-audit"]).get("PolicyNames", []) != [POLICY_NAMES["audit_user"]]:
            raise ExecutionBlocked("audit user has unexpected inline policy")
        evidence = self._bucket_state(EXPECTED_EVIDENCE_BUCKET, plan["bucket_controls"]["evidence"], plan["evidence_bucket_policy"], audit=False)
        audit = self._bucket_state(EXPECTED_AUDIT_BUCKET, plan["bucket_controls"]["audit"], plan["audit_bucket_policy"], audit=True)
        trail = self._trail_state(plan)
        return {"roles": roles, "evidence_bucket": evidence, "audit_bucket": audit, "trail": trail, "evidence_object_count": 0}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--profile", default=EXPECTED_PROFILE)
    parser.add_argument("--authorization-path", type=Path)
    parser.add_argument("--authorization-record-sha256")
    parser.add_argument("--human-authorization-sha256")
    parser.add_argument("--expected-execution-commit-sha")
    parser.add_argument("--receipt-journal", type=Path)
    args = parser.parse_args()
    args.repository = args.repository.resolve()
    if args.repository != ROOT.resolve():
        raise ExecutionBlocked("--repository must be the exact worktree containing this executor")
    package = VALIDATOR.validate(
        args.repository, require_base_head=False,
        allow_active_authorization=args.execute,
    )
    if package["status"] != "PASS":
        raise ExecutionBlocked("offline package validation failed: " + "; ".join(package["failures"]))
    if not args.execute:
        print(json.dumps({
            "status": "VALIDATE_ONLY_NO_MUTATION", "aws_mutation_performed": False,
            "evidence_uploaded": False,
            "execution_authorization_present": (args.repository / FUTURE_AUTHORIZATION_RELATIVE).is_file(),
            "operations_if_separately_authorized": INFRASTRUCTURE_OPERATIONS,
            "execution_client": {"path": str(EXPECTED_AWS_CLI), "version": EXPECTED_AWS_CLI_VERSION},
        }, sort_keys=True))
        return 0
    required = (
        args.authorization_record_sha256, args.human_authorization_sha256,
        args.expected_execution_commit_sha, args.receipt_journal,
    )
    authorization_path = args.authorization_path or (args.repository / FUTURE_AUTHORIZATION_RELATIVE)
    if not authorization_path.is_file() or not all(required):
        raise ExecutionBlocked("separate authorization and all independent B/H/M/hash inputs are required")
    record = load_object(authorization_path)
    failures = validate_authorization(
        record, expected_record_sha256=args.authorization_record_sha256,
        expected_human_sha256=args.human_authorization_sha256,
        repository=args.repository, now=utc_now(),
    )
    if failures:
        raise ExecutionBlocked("; ".join(failures))
    graph = verify_non_circular_merge(
        args.repository, authorization_path, record,
        expected_execution_commit_sha=args.expected_execution_commit_sha,
        expected_record_sha256=args.authorization_record_sha256,
    )
    record["__record_sha256"] = args.authorization_record_sha256
    record["__execution_commit"] = graph["merge"]
    adapter = AwsCliInfrastructureAdapter(args.profile, EXPECTED_REGION, record["authorization_id"], args.receipt_journal)
    try:
        state = adapter.inspect()
        outcome = execute_infrastructure(plan=load_object(args.repository / PLAN_RELATIVE), record=record, state=state, adapter=adapter)
    except Exception as exc:
        outcome = {
            "status": "PRE_MUTATION_FAILURE",
            "failure": {"type": type(exc).__name__, "message": str(exc)},
            "authorization_consumed": adapter.authorization_consumed,
            "aws_mutation_performed": adapter.mutation_count > 0,
            "evidence_uploaded": False,
            "receipts": [asdict(item) for item in adapter.receipts],
        }
    if outcome["status"] not in TERMINAL_STATUSES:
        raise AssertionError("unknown terminal status")
    adapter.preserve_terminal(outcome)
    print(json.dumps(outcome, sort_keys=True))
    return 0 if outcome["status"] == "INFRASTRUCTURE_CREATED_AND_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
