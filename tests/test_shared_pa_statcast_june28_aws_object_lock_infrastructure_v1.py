from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_module(
    "june28_infrastructure_validator_test",
    ROOT / "scripts/validate_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py",
)
EXECUTOR = load_module(
    "june28_infrastructure_executor_test",
    ROOT / "scripts/execute_shared_pa_statcast_june28_aws_object_lock_infrastructure_v1.py",
)
PLAN = json.loads((ROOT / VALIDATOR.PLAN_PATH).read_text(encoding="utf-8"))
AUTHORITY = json.loads((ROOT / VALIDATOR.AUTHORITY_PATH).read_text(encoding="utf-8"))


def valid_state(**updates):
    values = {
        "account_id": EXECUTOR.EXPECTED_ACCOUNT,
        "actor_arn": EXECUTOR.EXPECTED_ADMIN,
        "actor_type": "IAMUser",
        "administrator_mfa_registered": True,
        "administrator_session_mfa": True,
        "audit_mfa_registered": True,
        "region": EXECUTOR.EXPECTED_REGION,
        "bucket_exists": False,
        "existing_roles": (),
        "existing_policies": (),
        "event_data_store_exists": False,
        "evidence_objects_exist": False,
        "partial_infrastructure_exists": False,
        "used_authorization_ids": (),
    }
    values.update(updates)
    return EXECUTOR.ObservedState(**values)


def valid_record(**updates):
    values = {
        "schema_version": "shared-pa-statcast-june28-aws-object-lock-infrastructure-execution-authorization-v1",
        "authorization_id": "future-infrastructure-auth-unique-v1",
        "issued_utc": "2026-08-04T02:00:00Z",
        "valid_from_utc": "2026-08-04T02:15:00Z",
        "expires_utc": "2026-08-04T02:45:00Z",
        "account_id": EXECUTOR.EXPECTED_ACCOUNT,
        "authorized_actor_arn": EXECUTOR.EXPECTED_ADMIN,
        "region": EXECUTOR.EXPECTED_REGION,
        "bucket": EXECUTOR.EXPECTED_BUCKET,
        "repository_commit": "1" * 40,
        "authority_package_sha256": VALIDATOR.sha256_file(ROOT / VALIDATOR.AUTHORITY_PATH),
        "infrastructure_plan_sha256": VALIDATOR.EXPECTED_PLAN_SHA256,
        "human_authorization_sha256": "2" * 64,
        "execution_mode": "INFRASTRUCTURE_ONLY",
        "aws_write_authorized": True,
        "infrastructure_execution_authorized": True,
        "evidence_upload_authorized": False,
        "writer_or_verifier_assumption_authorized": False,
        "authorization_used": False,
        "bucket_object_lock_irreversibility_acknowledged": True,
    }
    values.update(updates)
    return values


class FakeAdapter:
    def __init__(self):
        self.operations = []

    def apply(self, operation, plan):
        self.operations.append(operation)


def auth_failures(record=None, **kwargs):
    record = valid_record() if record is None else record
    parameters = {
        "record_sha256": "a" * 64,
        "expected_record_sha256": "a" * 64,
        "human_authorization_sha256": "2" * 64,
        "authority_package_sha256": VALIDATOR.sha256_file(ROOT / VALIDATOR.AUTHORITY_PATH),
        "plan_sha256": VALIDATOR.EXPECTED_PLAN_SHA256,
        "repository_commit": "1" * 40,
        "now": datetime(2026, 8, 4, 2, 30, tzinfo=timezone.utc),
    }
    parameters.update(kwargs)
    return EXECUTOR.validate_execution_authorization(record, **parameters)


class ObjectLockInfrastructureTests(unittest.TestCase):
    def test_01_byte_exact_plan_and_all_section_hashes(self):
        path = ROOT / VALIDATOR.PLAN_PATH
        self.assertEqual(path.stat().st_size, 26523)
        self.assertEqual(VALIDATOR.sha256_file(path), VALIDATOR.EXPECTED_PLAN_SHA256)
        self.assertEqual(VALIDATOR.validate_plan(PLAN, path), [])

    def test_02_inactive_package_flags_and_scientific_state(self):
        self.assertEqual(VALIDATOR.validate_authority(AUTHORITY), [])
        self.assertFalse(AUTHORITY["aws_write_authorized"])
        self.assertFalse(AUTHORITY["evidence_upload_authorized"])
        self.assertEqual(AUTHORITY["scientific_state"]["total_real_statcast_requests"], 4)

    def test_03_wrong_account_fails(self):
        self.assertIn("wrong account", EXECUTOR.validate_observed_state(valid_state(account_id="000000000000"), "id"))

    def test_04_root_identity_fails(self):
        state = valid_state(actor_arn=f"arn:aws:iam::{EXECUTOR.EXPECTED_ACCOUNT}:root", actor_type="Root")
        self.assertIn("root identity prohibited", EXECUTOR.validate_observed_state(state, "id"))

    def test_05_audit_identity_as_administrator_fails(self):
        failures = EXECUTOR.validate_observed_state(valid_state(actor_arn=EXECUTOR.EXPECTED_AUDIT), "id")
        self.assertIn("wrong administrator identity", failures)

    def test_06_missing_administrator_mfa_fails(self):
        failures = EXECUTOR.validate_observed_state(valid_state(administrator_session_mfa=False), "id")
        self.assertIn("administrator MFA session missing", failures)

    def test_07_missing_audit_user_mfa_fails(self):
        failures = EXECUTOR.validate_observed_state(valid_state(audit_mfa_registered=False), "id")
        self.assertIn("audit-user MFA device missing", failures)

    def test_08_wrong_region_fails(self):
        self.assertIn("wrong region", EXECUTOR.validate_observed_state(valid_state(region="us-east-2"), "id"))

    def test_09_wrong_bucket_fails_through_authorization(self):
        self.assertIn("execution authorization mismatch: bucket", auth_failures(valid_record(bucket="wrong")))

    def test_10_existing_bucket_fails(self):
        self.assertIn("bucket already exists", EXECUTOR.validate_observed_state(valid_state(bucket_exists=True), "id"))

    def test_11_existing_role_or_policy_fails(self):
        failures = EXECUTOR.validate_observed_state(valid_state(existing_roles=("writer",), existing_policies=("policy",)), "id")
        self.assertIn("planned role already exists", failures)
        self.assertIn("planned policy already exists", failures)

    def test_12_existing_evidence_or_partial_infrastructure_fails(self):
        failures = EXECUTOR.validate_observed_state(valid_state(evidence_objects_exist=True, partial_infrastructure_exists=True), "id")
        self.assertIn("evidence object already exists", failures)
        self.assertIn("partial infrastructure exists", failures)

    def test_13_plan_hash_and_section_hash_mutations_fail(self):
        changed = copy.deepcopy(PLAN)
        changed["bucket_policy"]["Statement"][0]["Effect"] = "Allow"
        failures = VALIDATOR.validate_plan(changed, ROOT / VALIDATOR.PLAN_PATH)
        self.assertIn("plan section SHA-256 mismatch: bucket_policy", failures)
        record = valid_record(infrastructure_plan_sha256="0" * 64)
        self.assertIn("execution authorization mismatch: infrastructure_plan_sha256", auth_failures(record))

    def test_14_repository_commit_mismatch_fails(self):
        record = valid_record(repository_commit="0" * 40)
        self.assertIn("execution authorization mismatch: repository_commit", auth_failures(record))

    def test_15_missing_execution_authorization_fails(self):
        failures = auth_failures({})
        self.assertIn("execution authorization mismatch: schema_version", failures)
        self.assertIn("execution authorization ID missing", failures)

    def test_16_expired_authorization_fails(self):
        failures = auth_failures(now=datetime(2026, 8, 4, 3, 0, tzinfo=timezone.utc))
        self.assertIn("execution authorization inactive or expired", failures)

    def test_17_reused_authorization_fails(self):
        state = valid_state(used_authorization_ids=("future-infrastructure-auth-unique-v1",))
        self.assertIn("execution authorization already used", EXECUTOR.validate_observed_state(state, "future-infrastructure-auth-unique-v1"))

    def test_18_infrastructure_mode_cannot_upload_evidence(self):
        self.assertEqual(AUTHORITY["exact_evidence_boundary"]["maximum_object_upload_count"], 0)
        self.assertFalse(AUTHORITY["exact_evidence_boundary"]["object_keys_may_be_created_by_this_executor"])
        for operation in EXECUTOR.INFRASTRUCTURE_OPERATIONS:
            self.assertFalse(any(token in operation for token in EXECUTOR.FORBIDDEN_OPERATION_TOKENS))

    def test_19_writer_cannot_configure_infrastructure(self):
        policy = PLAN["identity_policies"]["writer_role"]
        denied = set(next(statement["Action"] for statement in policy["Statement"] if statement["Sid"] == "DenyReadBackMutationAndRetentionBypass"))
        self.assertIn("s3:PutBucketPolicy", denied)
        self.assertIn("s3:PutBucketObjectLockConfiguration", denied)

    def test_20_verifier_and_audit_identity_cannot_mutate(self):
        verifier = PLAN["identity_policies"]["verifier_role"]
        audit = PLAN["identity_policies"]["audit_user"]
        self.assertTrue(any(statement["Sid"] == "DenyAllMutation" for statement in verifier["Statement"]))
        self.assertTrue(any(statement["Sid"] == "DenyAllMutation" for statement in audit["Statement"]))

    def test_21_synthetic_valid_input_reaches_mock_boundary_only(self):
        adapter = FakeAdapter()
        record = valid_record()
        operations = EXECUTOR.execute_infrastructure(
            plan=PLAN, record=record, state=valid_state(),
            record_sha256="a" * 64, expected_record_sha256="a" * 64,
            human_authorization_sha256="2" * 64,
            authority_package_sha256=VALIDATOR.sha256_file(ROOT / VALIDATOR.AUTHORITY_PATH),
            repository_commit="1" * 40,
            now=datetime(2026, 8, 4, 2, 30, tzinfo=timezone.utc), adapter=adapter,
        )
        self.assertEqual(tuple(adapter.operations), EXECUTOR.INFRASTRUCTURE_OPERATIONS)
        self.assertEqual(operations, EXECUTOR.INFRASTRUCTURE_OPERATIONS)

    def test_22_no_source_request_or_evidence_upload_operation_exists(self):
        rendered = json.dumps({"operations": EXECUTOR.INFRASTRUCTURE_OPERATIONS, "authority": AUTHORITY})
        self.assertNotIn("baseballsavant", rendered.lower())
        self.assertNotIn("statcast_search", rendered.lower())
        self.assertNotIn("s3:PutObject\"", rendered)

    def test_23_exact_scope_manifest_and_offline_validator_pass(self):
        result = VALIDATOR.validate(ROOT, require_base_head=True)
        self.assertEqual(result["status"], "PASS", result["failures"])
        self.assertEqual(result["total_real_statcast_requests"], 4)


if __name__ == "__main__":
    unittest.main()
