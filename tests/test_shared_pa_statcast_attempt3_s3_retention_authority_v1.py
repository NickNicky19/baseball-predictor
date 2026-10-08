from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_PATH = ROOT / "config/shared_pa_statcast_attempt3_s3_retention_authority_package_20260802_v1.json"
SCHEMA_PATH = ROOT / "contracts/schemas/shared_pa_statcast_attempt3_s3_custody_manifest_v1.schema.json"
PLAN_PATH = ROOT / "reports/shared_pa_statcast_attempt3_s3_retention_execution_plan_20260802_v1.json"
VALIDATOR_PATH = ROOT / "scripts/validate_shared_pa_statcast_attempt3_s3_retention_authority_v1.py"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_validator():
    spec = importlib.util.spec_from_file_location("retention_validator", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Attempt3S3RetentionAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()
        cls.authority = load_json(AUTHORITY_PATH)
        cls.schema = load_json(SCHEMA_PATH)
        cls.plan = load_json(PLAN_PATH)
        cls.bindings = {"account_id": "123456789012", "partition": "aws", "region": "us-east-2"}

    def observation(self):
        v = self.validator
        return {
            "artifact_id": 8826086488,
            "artifact_name": "shared-pa-statcast-attempt-03-30725195810",
            "zip_byte_count": v.ZIP_BYTES,
            "zip_sha256": v.ZIP_SHA256,
            "raw_response_sha256": v.RAW_SHA256,
            "failure_package_sha256": v.FAILURE_SHA256,
            "account_id": self.bindings["account_id"],
            "partition": self.bindings["partition"],
            "region": self.bindings["region"],
            "bucket": v.BUCKET,
            "versioning": "Enabled",
            "object_lock_enabled": True,
            "object_lock_mode": "COMPLIANCE",
            "retain_until_utc": v.RETAIN_UNTIL,
            "encryption": "AES256",
            "existing_versions": [],
            "existing_delete_markers": [],
            "uploaded_keys": [v.ARTIFACT_KEY, v.CUSTODY_KEY],
            "artifact_version_id": "synthetic-artifact-version",
            "custody_version_id": "synthetic-custody-version",
            "artifact_read_back_byte_count": v.ZIP_BYTES,
            "artifact_read_back_sha256": v.ZIP_SHA256,
            "custody_read_back_matches": True,
            "artifact_retention_verified": True,
            "custody_retention_verified": True,
            "provider_fields_source": "ACTUAL_AWS_RESPONSES",
        }

    def assert_rejected(self, observation, phrase):
        with self.assertRaisesRegex(self.validator.RetentionValidationError, phrase):
            self.validator.validate_synthetic_execution(self.authority, observation, self.bindings)

    def test_01_every_evidence_hash_and_preparation_boundary_is_bound(self):
        result = self.validator.validate_preparation(self.authority, self.schema, self.plan, ROOT)
        self.assertEqual(result["status"], "AWS_RETENTION_AUTHORITY_PACKAGE_PREPARED")
        self.assertEqual(result["authorized_object_count"], 2)

    def test_02_mismatched_zip_fails(self):
        observation = self.observation()
        observation["zip_sha256"] = "0" * 64
        self.assert_rejected(observation, "ZIP mismatch")

    def test_03_mismatched_raw_response_hash_fails(self):
        observation = self.observation()
        observation["raw_response_sha256"] = "0" * 64
        self.assert_rejected(observation, "raw-response hash mismatch")

    def test_04_mismatched_artifact_id_or_name_fails(self):
        for field, value, phrase in (("artifact_id", 1, "artifact ID mismatch"), ("artifact_name", "wrong", "artifact name mismatch")):
            with self.subTest(field=field):
                observation = self.observation()
                observation[field] = value
                self.assert_rejected(observation, phrase)

    def test_05_missing_object_lock_fails(self):
        observation = self.observation()
        observation["object_lock_enabled"] = False
        self.assert_rejected(observation, "Object Lock missing")

    def test_06_governance_mode_fails(self):
        observation = self.observation()
        observation["object_lock_mode"] = "GOVERNANCE"
        self.assert_rejected(observation, "Governance or missing Object Lock mode")

    def test_07_shortened_retention_date_fails(self):
        observation = self.observation()
        observation["retain_until_utc"] = "2033-08-01T23:59:59Z"
        self.assert_rejected(observation, "retention date shortened")

    def test_08_unexpected_bucket_or_region_fails(self):
        for field, value, phrase in (("bucket", "other-bucket", "unexpected bucket"), ("region", "us-west-1", "unexpected AWS region")):
            with self.subTest(field=field):
                observation = self.observation()
                observation[field] = value
                self.assert_rejected(observation, phrase)

    def test_09_unexpected_object_key_fails(self):
        observation = self.observation()
        observation["uploaded_keys"] = [self.validator.ARTIFACT_KEY, "unexpected"]
        self.assert_rejected(observation, "unexpected object key or third upload")

    def test_10_existing_conflicting_object_fails(self):
        observation = self.observation()
        observation["existing_versions"] = [{"Key": self.validator.ARTIFACT_KEY, "VersionId": "old"}]
        self.assert_rejected(observation, "conflicting object exists")

    def test_11_read_back_mismatch_fails(self):
        observation = self.observation()
        observation["artifact_read_back_sha256"] = "f" * 64
        self.assert_rejected(observation, "artifact read-back mismatch")

    def test_12_missing_version_id_fails(self):
        observation = self.observation()
        observation["artifact_version_id"] = None
        self.assert_rejected(observation, "artifact VersionId missing")

    def test_13_missing_retention_verification_fails(self):
        observation = self.observation()
        observation["custody_retention_verified"] = False
        self.assert_rejected(observation, "retention verification missing")

    def test_14_manifest_finalization_cannot_invent_provider_fields(self):
        pre_upload = {
            "manifest_phase": "PRE_UPLOAD_DETERMINISTIC",
            "immutable_result_status": "PREPARED_NOT_UPLOADED",
            "aws_custody": {
                "uploader_identity": None,
                "upload_utc_timestamp": None,
                "account_id": None,
                "partition": None,
                "region": None,
                "bucket": None,
                "artifact_object": {"version_id": None, "etag": None},
                "provider_request_ids": {},
            },
        }
        with self.assertRaisesRegex(self.validator.RetentionValidationError, "cannot be invented"):
            self.validator.finalize_custody_manifest(pre_upload, {}, provider_fields_source="SYNTHETIC")

    def test_15_no_unapproved_third_object_is_possible(self):
        self.assertEqual(self.authority["maximum_upload_object_count"], 2)
        self.assertEqual([item["key"] for item in self.authority["objects"]], [self.validator.ARTIFACT_KEY, self.validator.CUSTODY_KEY])
        observation = self.observation()
        observation["uploaded_keys"].append("third")
        self.assert_rejected(observation, "unexpected object key or third upload")

    def test_16_source_contract_v2_remains_inactive(self):
        v2 = load_json(ROOT / "config/shared_pa_statcast_source_contract_v2_proposal.json")
        self.assertEqual(v2["status"], "INACTIVE_PROPOSAL_ONLY")
        self.assertFalse(v2["activation_authorized"])
        self.assertFalse(v2["capture_authorized"])

    def test_17_june_28_confirmation_response_remains_unseen(self):
        ledger = load_json(ROOT / "config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json")
        self.assertEqual(ledger["attempts"], [])
        self.assertEqual(ledger["next_attempt"], {"attempt_number": 1, "status": "UNUSED_UNAUTHORIZED", "reserved": False, "consumed": False})
        self.assertFalse((ROOT / "data/source/shared_pa_statcast_v2_confirmation_2023-06-28_v1").exists())

    def test_18_no_statcast_request_path_exists_in_the_proposal(self):
        combined = AUTHORITY_PATH.read_text(encoding="utf-8") + PLAN_PATH.read_text(encoding="utf-8") + VALIDATOR_PATH.read_text(encoding="utf-8")
        self.assertNotIn("baseballsavant.mlb.com/statcast_search", combined)
        self.assertFalse(self.authority["upload_authorized"])

    def test_19_no_aws_write_or_network_occurs(self):
        source = VALIDATOR_PATH.read_text(encoding="utf-8")
        imports = {node.names[0].name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import)}
        imports.update(node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) and node.module)
        self.assertTrue({"boto3", "botocore", "requests", "subprocess"}.isdisjoint(imports))
        with mock.patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            result = self.validator.validate_preparation(self.authority, self.schema, self.plan, ROOT)
        self.assertEqual(result["execution"], "DURABLE_RETENTION_EXECUTION_NOT_PERFORMED")
        self.assertFalse(self.authority["aws_write_authorized"])

    def test_20_total_real_statcast_requests_remain_exactly_three(self):
        old = load_json(ROOT / "config/shared_pa_statcast_sample_attempt_history_20260802_v3.json")
        new = load_json(ROOT / "config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json")
        self.assertEqual(old["total_external_statcast_requests"], 3)
        self.assertEqual(new["global_real_external_statcast_request_count"], 3)

    def test_21_read_only_policy_canonical_identity_and_form(self):
        audit = self.authority["read_only_audit_policy"]
        payload = self.validator.canonical_json(audit["policy_document"])
        self.assertEqual(len(payload), 4908)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), "5076611dad2b6abd27e8331d0e0b665d0d65b5ce52862ad2f654be5dc724d150")
        self.assertGreater(len(payload), audit["form_decision"]["user_inline"]["aws_non_whitespace_character_quota"])
        self.assertLess(len(payload), audit["form_decision"]["customer_managed"]["aws_non_whitespace_character_quota"])
        self.assertEqual(audit["form_decision"]["recommended"], "CUSTOMER_MANAGED")

    def test_22_read_only_policy_has_exact_24_hour_window(self):
        audit = self.authority["read_only_audit_policy"]
        self.assertEqual(audit["time_boundary"]["activation_utc"], "2026-08-04T21:00:00Z")
        self.assertEqual(audit["time_boundary"]["expiration_utc"], "2026-08-05T21:00:00Z")
        self.assertEqual(audit["time_boundary"]["duration_seconds"], 86400)

    def test_23_every_allowed_action_is_offline_classified_read_only(self):
        audit = self.authority["read_only_audit_policy"]
        matrix = audit["allowed_action_matrix"]
        self.assertEqual(len(matrix), 61)
        self.assertTrue(all(row["access_level"] in {"Read", "List"} for row in matrix))
        self.assertTrue(all(not row["is_write"] and not row["is_permission_management"] for row in matrix))
        self.assertTrue(all(row["dependent_actions"] == [] for row in matrix))

    def test_24_forbidden_actions_and_wildcards_are_absent(self):
        audit = self.authority["read_only_audit_policy"]
        statements = audit["policy_document"]["Statement"]
        actions = {a for statement in statements for a in ([statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"])}
        self.assertTrue(self.validator.FORBIDDEN_AUDIT_ACTIONS.isdisjoint(actions))
        self.assertFalse(any(action == "*" or action.endswith(":*") for action in actions))
        self.assertTrue(all("NotAction" not in statement for statement in statements))

    def test_25_every_statement_is_bound_to_exact_principal_account_and_time(self):
        statements = self.authority["read_only_audit_policy"]["policy_document"]["Statement"]
        for statement in statements:
            condition = statement["Condition"]
            self.assertEqual(condition["ArnEquals"]["aws:PrincipalArn"], self.validator.AUDIT_PRINCIPAL)
            self.assertEqual(condition["StringEquals"]["aws:PrincipalAccount"], "723322847536")
            self.assertEqual(condition["DateGreaterThanEquals"]["aws:CurrentTime"], self.validator.AUDIT_ACTIVATION)
            self.assertEqual(condition["DateLessThan"]["aws:CurrentTime"], self.validator.AUDIT_EXPIRATION)

    def test_26_s3_object_contents_remain_unreadable(self):
        actions = {row["action"] for row in self.authority["read_only_audit_policy"]["allowed_action_matrix"]}
        self.assertNotIn("s3:GetObject", actions)
        self.assertNotIn("s3:GetObjectVersion", actions)
        self.assertNotIn("s3:PutObject", actions)
        self.assertIn("s3:ListBucketVersions", actions)

    def test_27_prohibited_simulation_matrix_is_complete(self):
        simulated = set(self.authority["read_only_audit_policy"]["simulation_plan"]["required_denied_examples"])
        self.assertTrue(self.validator.REQUIRED_PROHIBITED_SIMULATIONS <= simulated)

    def test_28_access_analyzer_denial_blocks_attachment(self):
        validation = self.authority["read_only_audit_policy"]["access_analyzer_validation"]
        self.assertEqual(validation["status"], "BLOCKED_ACCESS_DENIED_CURRENT_TARGET_HAS_NO_VALIDATE_POLICY_PERMISSION")
        self.assertFalse(validation["policy_findings_returned"])
        self.assertTrue(validation["attachment_blocked_until_zero_errors"])
        self.assertFalse(self.authority["read_only_audit_policy"]["policy_attachment_authorized"])

    def test_29_target_cannot_self_grant_or_escalate(self):
        audit = self.authority["read_only_audit_policy"]
        actions = {row["action"] for row in audit["allowed_action_matrix"]}
        self.assertTrue(set(audit["future_grantor_requirements"]["prohibited_for_target"]).isdisjoint(actions))
        self.assertTrue(audit["future_grantor_requirements"]["target_self_grant_prohibited"])

    def test_30_policy_preparation_has_no_aws_execution_path(self):
        source = VALIDATOR_PATH.read_text(encoding="utf-8")
        self.assertNotIn("create-policy", source)
        self.assertNotIn("attach-user-policy", source)
        audit = self.authority["read_only_audit_policy"]
        self.assertFalse(audit["policy_creation_authorized"])
        self.assertFalse(audit["policy_attachment_authorized"])
        self.assertFalse(audit["cleanup_authorized"])


if __name__ == "__main__":
    unittest.main()
