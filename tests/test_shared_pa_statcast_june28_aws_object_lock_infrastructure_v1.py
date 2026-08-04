from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validator = load_module("june28_correction_validator_tests", "scripts/validate_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py")
executor = load_module("june28_correction_executor_tests", "scripts/execute_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py")
PLAN = json.loads((ROOT / validator.PLAN_PATH).read_text(encoding="utf-8"))


def clean_state(**changes):
    state = executor.ObservedState(
        account_id=executor.EXPECTED_ACCOUNT,
        actor_arn=executor.EXPECTED_ADMIN,
        actor_type="IAMUser",
        administrator_mfa_registered=True,
        administrator_session_mfa=True,
        audit_mfa_registered=True,
        region=executor.EXPECTED_REGION,
        evidence_bucket_exists=False,
        audit_bucket_exists=False,
        existing_roles=(),
        existing_policies=(),
        audit_inline_policies=(),
        audit_attached_policies=executor.EXPECTED_AUDIT_ATTACHED_POLICIES,
        trail_exists=False,
        evidence_objects_exist=False,
        partial_infrastructure_exists=False,
    )
    return replace(state, **changes)


def future_record(now: datetime | None = None):
    now = now or datetime(2026, 8, 4, 15, 0, tzinfo=timezone.utc)
    record = {
        "schema_version": "shared-pa-statcast-june28-aws-object-lock-infrastructure-execution-authorization-v1",
        "authorization_id": "june28-infrastructure-correction-fresh-00000001",
        "repository": executor.EXPECTED_REPOSITORY,
        "authorization_base_main_commit": "1" * 40,
        "aws_account_id": executor.EXPECTED_ACCOUNT,
        "authorized_administrator_arn": executor.EXPECTED_ADMIN,
        "administrator_mfa_required": True,
        "audit_user_mfa_required": True,
        "region": executor.EXPECTED_REGION,
        "evidence_bucket": executor.EXPECTED_EVIDENCE_BUCKET,
        "audit_log_bucket": executor.EXPECTED_AUDIT_BUCKET,
        "infrastructure_role_arn": validator.EXPECTED_ROLES["infrastructure"],
        "writer_role_arn": validator.EXPECTED_ROLES["writer"],
        "verifier_role_arn": validator.EXPECTED_ROLES["verifier"],
        "audit_identity_arn": executor.EXPECTED_AUDIT,
        "executor_path": executor.EXECUTOR_RELATIVE,
        "executor_sha256": executor.sha256_file(ROOT / executor.EXECUTOR_RELATIVE),
        "validator_sha256": executor.sha256_file(ROOT / executor.VALIDATOR_RELATIVE),
        "infrastructure_plan_sha256": executor.sha256_file(ROOT / executor.PLAN_RELATIVE),
        "authority_package_sha256": executor.sha256_file(ROOT / executor.AUTHORITY_RELATIVE),
        "execution_mode": "INFRASTRUCTURE_ONLY",
        "evidence_upload_authorized": False,
        "writer_or_verifier_evidence_role_assumption_authorized": False,
        "issued_utc": (now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        "valid_from_utc": (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
        "expires_utc": (now + timedelta(minutes=25)).isoformat().replace("+00:00", "Z"),
        "canonical_human_authorization_text_sha256": "a" * 64,
        "single_use_required": True,
        "bucket_object_lock_irreversibility_acknowledged": True,
        "evidence_retention_timestamp_applied_during_infrastructure_mode": False,
        "infrastructure_execution_authorized": True,
        "authorization_previously_used": False,
    }
    for field, section in validator.RECORD_SECTION_BINDINGS.items():
        record[field] = validator.canonical_json_sha256(PLAN[section])
    return record


class FakeAdapter:
    def __init__(self, fail_at: str | None = None, already_consumed: bool = False):
        self.fail_at = fail_at
        self.authorization_consumed = already_consumed
        self.mutation_count = 0
        self.receipts = []
        self.applied = []
        self.verified = []

    def inspect(self):
        return clean_state()

    def apply(self, operation, plan, record, sequence):
        if operation == "consume_authorization_create_infrastructure_role" and self.authorization_consumed:
            self.mutation_count = 1
            raise executor.ExecutionBlocked("EntityAlreadyExists")
        if operation == self.fail_at:
            raise executor.ExecutionBlocked("synthetic failure")
        self.applied.append(operation)
        self.mutation_count += 1
        if operation == "consume_authorization_create_infrastructure_role":
            self.authorization_consumed = True
        self.receipts.append(executor.ProviderReceipt(
            sequence_number=sequence, operation=operation, utc_start="2026-08-04T15:00:00Z",
            utc_end="2026-08-04T15:00:01Z", success=True, response_payload={"ok": True},
            aws_request_id=f"request-{sequence}", aws_extended_request_id=None, http_status=200,
            created_identity=operation, authorization_id=record["authorization_id"],
        ))

    def verify_operation(self, operation, plan, record):
        self.verified.append(operation)
        return f"read-back:{operation}"

    def verify_terminal(self, plan, record):
        self.verified.append("verify_terminal_infrastructure")
        return {"all_created_resources_read_back": True, "evidence_object_count": 0}


def git(repo: Path, *args: str):
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def graph(tmp_path: Path, *, extra=False, modify=False, mode=None, merge=True, wrong_parent=False):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Synthetic")
    git(repo, "config", "user.email", "synthetic@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "seed.txt").write_text("seed\n")
    git(repo, "add", "seed.txt"); git(repo, "commit", "-m", "B")
    base = git(repo, "rev-parse", "HEAD")
    path = repo / executor.FUTURE_AUTHORIZATION_RELATIVE
    if modify:
        path.parent.mkdir(parents=True); path.write_text("old\n"); git(repo, "add", str(path)); git(repo, "commit", "-m", "preexisting")
        base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "authorization")
    record = future_record(); record["authorization_base_main_commit"] = base
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    if mode in {"symlink", "submodule"}:
        path.write_text("seed.txt", encoding="utf-8")
        blob = git(repo, "hash-object", "-w", str(path)) if mode == "symlink" else git(repo, "rev-parse", "HEAD")
        index_mode = "120000" if mode == "symlink" else "160000"
        git(repo, "update-index", "--add", "--cacheinfo", f"{index_mode},{blob},{executor.FUTURE_AUTHORIZATION_RELATIVE}")
    else:
        git(repo, "add", "-A")
    git(repo, "commit", "-m", "H")
    if mode == "executable":
        git(repo, "update-index", "--chmod=+x", executor.FUTURE_AUTHORIZATION_RELATIVE); git(repo, "commit", "-m", "mode")
    if extra:
        (repo / "extra.txt").write_text("extra\n"); git(repo, "add", "extra.txt"); git(repo, "commit", "-m", "extra")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    if mode == "submodule" and path.exists():
        path.unlink()
    if wrong_parent:
        (repo / "advance.txt").write_text("advance\n"); git(repo, "add", "advance.txt"); git(repo, "commit", "-m", "advance")
    if merge:
        git(repo, "merge", "--no-ff", "authorization", "-m", "M")
    else:
        git(repo, "merge", "--ff-only", "authorization")
    merge_sha = git(repo, "rev-parse", "HEAD")
    return repo, path, record, base, head, merge_sha


def verify_graph(parts):
    repo, path, record, _base, _head, merge_sha = parts
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, PermissionError):
        digest = "0" * 64
    return executor.verify_non_circular_merge(repo, path, record, expected_execution_commit_sha=merge_sha, expected_record_sha256=digest, observed_default_branch_head=merge_sha)


def test_01_offline_package_validator_passes():
    assert validator.validate(ROOT, require_base_head=False)["status"] == "PASS"


def test_02_schema_is_strict_and_record_passes():
    schema = json.loads((ROOT / validator.SCHEMA_PATH).read_text())
    assert schema["additionalProperties"] is False
    assert validator.validate_authorization_record(future_record(), ROOT) == []


@pytest.mark.parametrize("change", [
    {"authorization_previously_used": True}, {"evidence_upload_authorized": True},
    {"execution_mode": "UPLOAD"}, {"audit_log_bucket": "wrong"},
])
def test_03_schema_rejects_weakened_or_wrong_record(change):
    record = future_record(); record.update(change)
    assert validator.validate_authorization_record(record, ROOT)


def test_04_stale_future_expired_and_overlong_authorizations_fail():
    now = datetime(2026, 8, 4, 15, 0, tzinfo=timezone.utc)
    for issued, valid, expires in [
        (now - timedelta(hours=2), now - timedelta(hours=2), now - timedelta(hours=1)),
        (now, now + timedelta(minutes=1), now + timedelta(minutes=31)),
        (now - timedelta(minutes=1), now - timedelta(minutes=1), now + timedelta(hours=2)),
    ]:
        record = future_record(now); record["issued_utc"] = issued.isoformat().replace("+00:00", "Z"); record["valid_from_utc"] = valid.isoformat().replace("+00:00", "Z"); record["expires_utc"] = expires.isoformat().replace("+00:00", "Z")
        assert executor.validate_authorization(record, expected_record_sha256="0" * 64, expected_human_sha256="a" * 64, repository=ROOT, now=now)


def test_05_graph_B_H_M_valid(tmp_path):
    result = verify_graph(graph(tmp_path))
    assert result["base"] and result["head"] and result["merge"]


def test_06_record_embeds_only_B(tmp_path):
    parts = graph(tmp_path); result = verify_graph(parts)
    assert parts[2]["authorization_base_main_commit"] == result["base"]
    assert result["head"] not in json.dumps(parts[2]) and result["merge"] not in json.dumps(parts[2])


def test_07_wrong_expected_M_fails(tmp_path):
    parts = graph(tmp_path)
    with pytest.raises(executor.ExecutionBlocked):
        executor.verify_non_circular_merge(parts[0], parts[1], parts[2], expected_execution_commit_sha="f" * 40, expected_record_sha256=hashlib.sha256(parts[1].read_bytes()).hexdigest(), observed_default_branch_head=parts[5])


def test_08_one_parent_commit_fails(tmp_path):
    parts = graph(tmp_path, merge=False)
    with pytest.raises(executor.ExecutionBlocked): verify_graph(parts)


def test_09_wrong_first_parent_fails(tmp_path):
    parts = graph(tmp_path, wrong_parent=True)
    with pytest.raises(executor.ExecutionBlocked): verify_graph(parts)


def test_10_extra_path_delta_fails(tmp_path):
    parts = graph(tmp_path, extra=True)
    with pytest.raises(executor.ExecutionBlocked): verify_graph(parts)


def test_11_modification_instead_of_addition_fails(tmp_path):
    parts = graph(tmp_path, modify=True)
    with pytest.raises(executor.ExecutionBlocked): verify_graph(parts)


@pytest.mark.parametrize("mode", ["symlink", "submodule", "executable"])
def test_12_nonregular_or_executable_mode_fails(tmp_path, mode):
    parts = graph(tmp_path, mode=mode)
    with pytest.raises(executor.ExecutionBlocked): verify_graph(parts)


def test_12b_deletion_operation_fails(tmp_path):
    parts = graph(tmp_path)
    repo, path, record = parts[0], parts[1], parts[2]
    previous = parts[5]
    git(repo, "checkout", "-b", "delete-auth")
    path.unlink(); git(repo, "add", "-A"); git(repo, "commit", "-m", "delete")
    git(repo, "checkout", "main"); git(repo, "merge", "--no-ff", "delete-auth", "-m", "delete merge")
    merge_sha = git(repo, "rev-parse", "HEAD")
    with pytest.raises(executor.ExecutionBlocked):
        executor.verify_non_circular_merge(repo, path, record, expected_execution_commit_sha=merge_sha, expected_record_sha256="0" * 64, observed_default_branch_head=merge_sha)
    assert previous != merge_sha


def test_12c_rename_operation_fails(tmp_path):
    parts = graph(tmp_path)
    repo, path, record = parts[0], parts[1], parts[2]
    git(repo, "checkout", "-b", "rename-auth")
    renamed = repo / "renamed.json"
    git(repo, "mv", executor.FUTURE_AUTHORIZATION_RELATIVE, "renamed.json"); git(repo, "commit", "-m", "rename")
    git(repo, "checkout", "main"); git(repo, "merge", "--no-ff", "rename-auth", "-m", "rename merge")
    merge_sha = git(repo, "rev-parse", "HEAD")
    with pytest.raises(executor.ExecutionBlocked):
        executor.verify_non_circular_merge(repo, renamed, record, expected_execution_commit_sha=merge_sha, expected_record_sha256=hashlib.sha256(renamed.read_bytes()).hexdigest(), observed_default_branch_head=merge_sha)


def test_13_record_hash_mismatch_fails(tmp_path):
    parts = graph(tmp_path)
    with pytest.raises(executor.ExecutionBlocked):
        executor.verify_non_circular_merge(parts[0], parts[1], parts[2], expected_execution_commit_sha=parts[5], expected_record_sha256="0" * 64, observed_default_branch_head=parts[5])


def test_14_canonical_text_mismatch_fails():
    now = datetime(2026, 8, 4, 15, 0, tzinfo=timezone.utc)
    assert any("canonical_human" in failure for failure in executor.validate_authorization(future_record(now), expected_record_sha256="0" * 64, expected_human_sha256="b" * 64, repository=ROOT, now=now))


@pytest.mark.parametrize("changes,needle", [
    ({"account_id": "000000000000"}, "wrong account"),
    ({"actor_arn": "arn:aws:iam::723322847536:root", "actor_type": "Root"}, "root actor"),
    ({"actor_arn": "arn:aws:iam::723322847536:user/wrong"}, "wrong administrator"),
    ({"administrator_mfa_registered": False}, "administrator MFA"),
    ({"administrator_session_mfa": False}, "administrator MFA session"),
    ({"audit_mfa_registered": False}, "audit-user MFA"),
    ({"region": "us-east-2"}, "wrong region"),
    ({"evidence_bucket_exists": True}, "evidence bucket"),
    ({"audit_bucket_exists": True}, "audit bucket"),
    ({"existing_roles": ("MlbStatcastJune28RetentionInfrastructureV1",)}, "planned role"),
    ({"existing_policies": ("MlbStatcastJune28RetentionInfrastructureV1",)}, "planned policy"),
    ({"audit_inline_policies": ("unexpected",)}, "preexisting inline"),
    ({"audit_attached_policies": ()}, "attached-policy state"),
    ({"trail_exists": True}, "CloudTrail"),
    ({"evidence_objects_exist": True}, "evidence object"),
    ({"partial_infrastructure_exists": True}, "partial infrastructure"),
])
def test_15_observed_state_fails_closed(changes, needle):
    assert any(needle in item for item in executor.validate_observed_state(clean_state(**changes)))


def test_16_first_role_creation_consumes_and_all_steps_read_back():
    adapter = FakeAdapter()
    result = executor.execute_infrastructure(plan=PLAN, record=future_record(), state=clean_state(), adapter=adapter)
    assert result["status"] == "INFRASTRUCTURE_CREATED_AND_VERIFIED"
    assert adapter.applied[0] == "consume_authorization_create_infrastructure_role"
    assert adapter.verified == list(executor.INFRASTRUCTURE_OPERATIONS)
    assert result["authorization_consumed"] is True
    assert result["evidence_uploaded"] is False


def test_17_concurrent_or_reused_authorization_cannot_cross_first_mutation():
    adapter = FakeAdapter(already_consumed=True)
    result = executor.execute_infrastructure(plan=PLAN, record=future_record(), state=clean_state(), adapter=adapter)
    assert result["status"] == "AUTHORIZATION_CONSUMED_NO_FURTHER_MUTATION"
    assert adapter.applied == []
    assert result["aws_mutation_performed"] is True  # conservative ambiguity classification


def test_18_failure_after_consumption_is_preserved_and_not_rerun():
    adapter = FakeAdapter(fail_at="create_writer_role")
    result = executor.execute_infrastructure(plan=PLAN, record=future_record(), state=clean_state(), adapter=adapter)
    assert result["status"] == "PARTIAL_INFRASTRUCTURE_PRESERVED"
    assert result["automatic_cleanup_performed"] is False
    assert result["automatic_rerun_permitted"] is False


def test_19_receipts_preserve_provider_metadata_and_postcondition():
    result = executor.execute_infrastructure(plan=PLAN, record=future_record(), state=clean_state(), adapter=FakeAdapter())
    assert all(item["aws_request_id"] and item["http_status"] == 200 and item["last_confirmed_postcondition"] for item in result["receipts"])
    required = {"created_arn", "created_name", "created_id", "role_id", "policy_name", "policy_sha256", "bucket_region", "trail_arn", "tags", "postcondition_evidence"}
    assert required <= set(result["receipts"][0])


def test_20_infrastructure_mode_has_no_evidence_operation():
    operations = json.dumps(executor.INFRASTRUCTURE_OPERATIONS).lower()
    assert "put_evidence" not in operations and "multipart" not in operations and "apply_evidence_retention" not in operations
    boundary = PLAN["infrastructure_mode_boundary"]
    assert boundary["maximum_evidence_object_upload_count"] == 0
    assert not any(value for key, value in boundary.items() if key.startswith("may_"))


def test_21_role_separation_is_fail_closed():
    policies = PLAN["identity_policies"]
    assert "s3:PutObject" in json.dumps(policies["infrastructure_role"])
    assert any(statement["Effect"] == "Deny" and "s3:PutObject" in statement["Action"] for statement in policies["infrastructure_role"]["Statement"])
    assert "DenyAllMutation" in json.dumps(policies["verifier_role"])
    assert "DenyMutation" in json.dumps(policies["audit_user"])
    assert "DenyInfrastructureReadAndDestruction" in json.dumps(policies["writer_role"])


def test_22_standard_cloudtrail_replaces_lake_and_selectors_are_exact():
    text = json.dumps(PLAN)
    assert "CreateEventDataStore" not in text and "BillingMode" not in text
    trail = PLAN["cloudtrail_trail"]
    assert trail["cloudtrail_lake_used"] is False
    assert trail["log_file_validation_enabled"] is True
    assert trail["event_selectors"] == [{"ReadWriteType": "All", "IncludeManagementEvents": True, "DataResources": [{"Type": "AWS::S3::Object", "Values": [f"arn:aws:s3:::{executor.EXPECTED_EVIDENCE_BUCKET}/{executor.EVIDENCE_PREFIX}"]}], "ExcludeManagementEventSources": []}]


def test_23_audit_retention_satisfies_frozen_horizon():
    audit = PLAN["bucket_controls"]["audit"]
    assert audit["object_lock_configuration"]["Rule"]["DefaultRetention"] == {"Mode": "COMPLIANCE", "Days": 2557}
    assert PLAN["cloudtrail_trail"]["audit_retention_days"] == 2557


def test_24_evidence_bucket_has_no_default_retention_and_raw_retention_unapplied():
    assert PLAN["bucket_controls"]["evidence"]["default_retention"] is None
    assert PLAN["proposed_evidence_retention"]["applied_by_infrastructure_mode"] is False


def test_25_pinned_execution_client_has_no_path_lookup():
    client = PLAN["execution_client"]
    assert Path(client["absolute_path"]).is_absolute()
    assert client["version_prefix"] == "aws-cli/2.36.14"
    assert "PATH" not in client["provider_metadata_capture"]


def test_26_default_executor_mode_is_no_mutation(capsys):
    # Validate the constant boundary without invoking AWS.
    assert "--execute" in (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    assert executor.FUTURE_AUTHORIZATION_RELATIVE not in validator.EXPECTED_CHANGED_PATHS


def test_27_no_live_aws_write_or_source_request_in_tests():
    assert PLAN["scientific_state"]["total_real_statcast_requests"] == 4
    combined = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8") + json.dumps(PLAN)
    assert "baseballsavant" not in combined.lower()
    assert PLAN["aws_write_authorized"] is False and PLAN["upload_authorized"] is False


def test_28_terminal_statuses_are_exact():
    assert executor.TERMINAL_STATUSES == {"INFRASTRUCTURE_CREATED_AND_VERIFIED", "PARTIAL_INFRASTRUCTURE_PRESERVED", "AUTHORIZATION_CONSUMED_NO_FURTHER_MUTATION", "PRE_MUTATION_FAILURE"}


def test_29_all_required_section_hashes_are_bound():
    authority = json.loads((ROOT / validator.AUTHORITY_PATH).read_text())
    observed = {field: validator.canonical_json_sha256(PLAN[section]) for field, section in validator.RECORD_SECTION_BINDINGS.items()}
    assert authority["corrected_bindings"]["section_sha256"] == observed


def test_30_no_short_lived_authorization_record_created():
    assert not (ROOT / executor.FUTURE_AUTHORIZATION_RELATIVE).exists()


def test_31_scientific_state_remains_frozen():
    assert PLAN["scientific_state"] == {
        "confirmation_attempt_1": "CONSUMED", "total_real_statcast_requests": 4,
        "source_contract_v2_promoted": False, "source_qualified": False,
        "non_promotable": True, "independent_reproduction_completed": False,
        "full_capture_authorized": False, "model_work_authorized": False,
    }


def test_32_future_one_file_authorization_is_allowed_only_for_live_package_validation(monkeypatch):
    future = "config/shared_pa_statcast_june28_aws_object_lock_infrastructure_execution_authorization_v1.json"
    monkeypatch.setattr(validator, "git_changed_paths", lambda _root: sorted(validator.EXPECTED_CHANGED_PATHS + [future]))
    assert validator.validate(ROOT, require_base_head=False, allow_active_authorization=True)["status"] == "PASS"
    assert validator.validate(ROOT, require_base_head=False, allow_active_authorization=False)["status"] == "FAIL"


def test_33_executor_rejects_cross_worktree_repository_argument():
    source = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    assert 'args.repository != ROOT.resolve()' in source
    assert 'load_object(args.repository / PLAN_RELATIVE)' in source


def test_34_current_mfa_proof_is_bound_to_current_temporary_access_key():
    source = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    assert 'identity.get("accessKeyId") == current_access_key_id' in source
    assert PLAN["current_session_mfa_proof"]["historical_username_only_match_allowed"] is False


def test_35_receipt_journal_is_create_only_fsynced_and_terminal():
    source = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    assert '.open("x"' in source and "os.fsync" in source
    assert "adapter.preserve_terminal(outcome)" in source
    receipt = PLAN["provider_receipt_contract"]
    assert receipt["journal_overwrite_allowed"] is False
    assert receipt["fsync_after_each_provider_response"] is True


def test_36_assume_role_receipt_redacts_credentials_before_persistence():
    source = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    redaction = source.index('receipt_payload = {"AssumedRoleUser"')
    persistence = source.index('self._journal({"event": "provider_response"')
    assert redaction < persistence


def test_37_immediate_bucket_readbacks_compare_exact_values():
    source = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    assert 'raise ExecutionBlocked(f"{operation} immediate postcondition mismatch")' in source
    assert 'raise ExecutionBlocked(f"{operation} immediate bucket-policy mismatch")' in source


def test_38_terminal_trail_verifies_exact_arn_and_audit_policy_baseline():
    source = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    assert '"TrailARN": expected["arn"]' in source
    assert PLAN["audit_user_preexisting_policy_baseline"]["attached_policy_arns"] == list(executor.EXPECTED_AUDIT_ATTACHED_POLICIES)


def test_39_sts_identity_is_not_shadowed_by_cloudtrail_event_identity():
    source = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    assert 'event_identity = detail.get("userIdentity", {})' in source
    assert 'account_id=str(identity.get("Account", ""))' in source


def test_40_receipt_journal_cannot_modify_repository():
    source = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    assert 'self.receipt_journal.relative_to(ROOT.resolve())' in source
    assert 'receipt journal must remain outside the repository worktree' in source


def test_41_bucket_names_require_authoritative_404_not_generic_access_failure():
    source = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    assert 'def _bucket_name_is_absent' in source
    assert 'global availability of bucket {bucket} is unresolved' in source


def test_42_receipt_field_names_match_frozen_contract():
    required = set(PLAN["provider_receipt_contract"]["per_operation"])
    fields = set(executor.ProviderReceipt.__dataclass_fields__)
    assert required <= fields


def test_43_infrastructure_policy_uses_valid_multipart_iam_actions():
    policy = json.dumps(PLAN["identity_policies"]["infrastructure_role"])
    assert "s3:CreateMultipartUpload" not in policy
    assert "s3:UploadPart" not in policy
    assert "s3:CompleteMultipartUpload" not in policy
    assert "s3:PutObject" in policy and "s3:AbortMultipartUpload" in policy


def test_44_audit_versions_are_prefix_and_compliance_retention_checked():
    source = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    assert "audit bucket contains object outside the exact trail prefix" in source
    assert "audit object version lacks required COMPLIANCE horizon" in source


def test_45_journal_failure_after_first_provider_submission_is_consumed():
    adapter = object.__new__(executor.AwsCliInfrastructureAdapter)
    adapter.authorization_consumed = False
    adapter.mutation_count = 0
    adapter.receipts = []
    adapter.infrastructure_environment = None
    adapter.authorization_id = "synthetic"
    adapter._run = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("synthetic journal failure"))
    record = future_record()
    record["__record_sha256"] = "b" * 64
    record["__execution_commit"] = "c" * 40
    with pytest.raises(OSError):
        adapter.apply("consume_authorization_create_infrastructure_role", PLAN, record, 1)
    assert adapter.authorization_consumed is True
    assert adapter.mutation_count == 1


def test_46_role_absence_uses_exact_get_role_and_only_nosuchentity_passes():
    source = (ROOT / executor.EXECUTOR_RELATIVE).read_text(encoding="utf-8")
    assert 'def _role_is_absent' in source
    assert 'if "NoSuchEntity" in str(exc)' in source
    assert 'absence of role {role_name} is unresolved' in source


def test_47_pinned_cli_debug_metadata_parser_handles_urllib3_format():
    debug = "https://iam.amazonaws.com:443 \"POST / HTTP/1.1\" 200 431 x-amzn-requestid: request-123 x-amz-id-2: extended+/="
    request_id, extended, status = executor.parse_provider_response_metadata(debug)
    assert (request_id, extended, status) == ("request-123", "extended+/=", 200)


def test_48_dirty_live_execution_worktree_fails(tmp_path):
    parts = graph(tmp_path)
    (parts[0] / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(executor.ExecutionBlocked, match="worktree is not clean"):
        verify_graph(parts)
