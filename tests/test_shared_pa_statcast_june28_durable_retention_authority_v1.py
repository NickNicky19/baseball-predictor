from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/validate_shared_pa_statcast_june28_durable_retention_authority_v1.py"
SPEC = importlib.util.spec_from_file_location("retention_validator", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
AUTHORITY = json.loads((ROOT / "config/shared_pa_statcast_june28_durable_retention_authority_package_v1.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "contracts/schemas/shared_pa_statcast_june28_custody_manifest_v1.schema.json").read_text(encoding="utf-8"))
PLAN = json.loads((ROOT / "reports/shared_pa_statcast_june28_durable_retention_execution_plan_v1.json").read_text(encoding="utf-8"))


def valid_custody() -> dict:
    source = AUTHORITY["source_artifact"]
    return {
        "schema_version": "shared-pa-statcast-june28-custody-manifest-v1",
        "evidence_purpose": "SEPARATE_DURABLE_RETENTION_ONLY", "attempt": 1,
        "workflow_run_id": 30846344146,
        "artifact": {
            "name": source["artifact_name"], "id": source["artifact_id"],
            "expiration_utc": source["expires_at_utc"], "zip_byte_count": source["zip_byte_count"],
            "zip_sha256": source["zip_sha256"], "raw_byte_count": source["raw_byte_count"],
            "raw_sha256": source["raw_sha256"], "entry_count": 19,
            "inventory": copy.deepcopy(AUTHORITY["exact_artifact_inventory"]),
        },
        "external_receipts": {"artifact_retrieval_sha256": "1" * 64, "post_terminal_run_sha256": "2" * 64},
        "scientific_disposition": {
            "technical_validation": "PASS", "durable_retention_verified_before_execution": False,
            "independent_reproduction_verified": False, "source_qualified": False, "promotable": False,
            "confirmation_attempt_1": "CONSUMED", "total_real_statcast_requests": 4,
        },
        "authorization": {"authorization_id": "future", "authorization_sha256": "3" * 64, "execution_commit": "4" * 40},
        "provider": {
            "account_id": "723322847536", "partition": "aws", "region": "us-east-2",
            "bucket": AUTHORITY["aws_target"]["proposed_dedicated_bucket"],
            "uploader_arn": "arn:aws:iam::723322847536:role/future-writer",
            "upload_utc": "2026-08-04T00:00:00Z", "encryption": "AES256",
            "object_lock_mode": "COMPLIANCE", "retain_until_utc": AUTHORITY["aws_target"]["retain_until_utc"],
            "audit_identity": "future-audit-receipt",
        },
        "artifact_object": {
            "key": MODULE.ARTIFACT_KEY, "version_id": "v1", "etag_metadata_only": "etag", "request_id": "request",
            "byte_count": source["zip_byte_count"], "sha256": source["zip_sha256"],
            "read_back_byte_count": source["zip_byte_count"], "read_back_sha256": source["zip_sha256"],
            "retention_verified": True,
        },
        "custody_manifest_intent": {
            "key": MODULE.CUSTODY_KEY, "create_only": True, "object_lock_mode": "COMPLIANCE",
            "retain_until_utc": AUTHORITY["aws_target"]["retain_until_utc"],
            "actual_provider_result_location": "SEPARATE_LOCAL_OR_REPOSITORY_EXECUTION_RESULT_NOT_A_THIRD_S3_OBJECT",
        },
        "chain_of_custody": [
            {"event": f"event-{i}", "observed_at_utc": "2026-08-04T00:00:00Z", "actor": "future", "evidence_sha256": "5" * 64}
            for i in range(4)
        ],
        "immutable_result_status": "ARTIFACT_OBJECT_VERIFIED_CUSTODY_MANIFEST_READY_FOR_CREATE_ONLY_UPLOAD",
    }


class RetentionAuthorityTests(unittest.TestCase):
    def test_offline_package_validation_passes(self) -> None:
        self.assertEqual(MODULE.validate(ROOT)["status"], "PASS")

    def test_package_cannot_authorize_itself_or_aws_writes(self) -> None:
        for key in ("preparation_is_aws_authorization", "aws_write_authorized", "upload_authorized", "retention_lock_authorized", "deletion_authorized", "source_qualification_authorized", "self_authorization_permitted"):
            self.assertIs(AUTHORITY[key], False)

    def test_github_actions_artifact_is_not_durable_retention(self) -> None:
        self.assertIs(AUTHORITY["retention_adjudication"]["github_actions_artifact_alone_is_durable_retention"], False)

    def test_exact_zip_and_evidence_hashes_are_bound(self) -> None:
        source = AUTHORITY["source_artifact"]
        self.assertEqual(source["zip_sha256"], "4c77f6d9572d8bc21ab79a06b0d8b77e2a04231375de000ee9e37dc7a2b530b5")
        self.assertEqual(source["raw_sha256"], "4b8a99e4a4130dcb7fb409f0637a25054f93d536bb57a02051b6d1003716f237")
        self.assertEqual(len(AUTHORITY["required_embedded_evidence"]), 9)
        self.assertEqual(len(AUTHORITY["exact_artifact_inventory"]), 19)
        self.assertEqual(len({item["path"] for item in AUTHORITY["exact_artifact_inventory"]}), 19)

    def test_provider_retrieval_and_post_terminal_receipts_are_required(self) -> None:
        self.assertEqual(
            set(AUTHORITY["required_external_receipts"]),
            {"artifact_retrieval_receipt", "post_terminal_run_receipt"},
        )

    def test_exact_zip_must_remain_unwrapped_and_byte_identical(self) -> None:
        decision = AUTHORITY["retention_adjudication"]
        self.assertIs(decision["original_zip_must_remain_byte_identical"], True)
        self.assertIs(decision["wrapping_original_zip_allowed"], False)
        self.assertIs(decision["separate_extracted_raw_object_required"], False)

    def test_two_create_only_keys_and_no_third_object(self) -> None:
        keys = AUTHORITY["object_keys"]
        self.assertEqual(keys["maximum_object_upload_count"], 2)
        self.assertIs(keys["create_only"], True)
        self.assertIs(keys["third_object_allowed"], False)
        self.assertNotEqual(keys["artifact_zip"], keys["custody_manifest"])

    def test_compliance_mode_and_minimum_retention_are_frozen(self) -> None:
        target = AUTHORITY["aws_target"]
        self.assertEqual(target["object_lock_mode"], "COMPLIANCE")
        self.assertEqual(target["retain_until_utc"], "2033-08-03T00:00:00Z")
        self.assertEqual(target["retain_until_status"], "PROPOSED_MINIMUM_FOR_SEPARATE_HUMAN_APPROVAL_NOT_A_FROZEN_SCIENTIFIC_REQUIREMENT")
        self.assertIsNone(target["bucket_default_retention"])

    def test_unresolved_aws_bindings_fail_closed(self) -> None:
        target = AUTHORITY["aws_target"]
        for key in ("region", "infrastructure_role_arn", "writer_role_arn", "verifier_role_arn", "audit_trail_or_event_data_store_arn", "bucket_policy_sha256"):
            self.assertIsNone(target[key])
        self.assertIs(target["unresolved_bindings_fail_closed"], True)

    def test_pr58_is_not_reused_as_june28_authority(self) -> None:
        assessment = AUTHORITY["pr58_assessment"]
        self.assertEqual(assessment["pull_request"], 58)
        self.assertEqual(assessment["disposition"], "ATTEMPT3_SPECIFIC_STALE_FOR_JUNE28_AND_INCOMPLETE_FOR_EXECUTION")
        self.assertIs(assessment["reusable_design_only"], True)

    def test_mutating_authorization_flag_fails_validator(self) -> None:
        changed = copy.deepcopy(AUTHORITY)
        changed["aws_write_authorized"] = True
        self.assertIn("authority flag must be false: aws_write_authorized", MODULE.validate_values(changed, SCHEMA, PLAN))

    def test_any_inventory_member_mutation_fails(self) -> None:
        changed = copy.deepcopy(AUTHORITY)
        changed["exact_artifact_inventory"][0]["sha256"] = "0" * 64
        self.assertIn("exact inventory content mismatch", MODULE.validate_values(changed, SCHEMA, PLAN))

    def test_object_lock_governance_and_shortened_retention_fail(self) -> None:
        for field, value, expected in (
            ("object_lock_mode", "GOVERNANCE", "Object Lock mode is not COMPLIANCE"),
            ("retain_until_utc", "2030-01-01T00:00:00Z", "retention timestamp mismatch"),
        ):
            changed = copy.deepcopy(AUTHORITY)
            changed["aws_target"][field] = value
            self.assertIn(expected, MODULE.validate_values(changed, SCHEMA, PLAN))

    def test_unexpected_bucket_region_key_and_third_object_fail(self) -> None:
        changed = copy.deepcopy(AUTHORITY)
        changed["aws_target"]["region"] = "us-west-2"
        changed["object_keys"]["artifact_zip"] = "wrong"
        changed["object_keys"]["third_object_allowed"] = True
        failures = MODULE.validate_values(changed, SCHEMA, PLAN)
        self.assertIn("unverified AWS binding was populated", failures)
        self.assertIn("exact object key mismatch", failures)
        self.assertIn("two-object boundary mismatch", failures)

    def test_custody_exact_inventory_and_provider_fields_fail_closed(self) -> None:
        candidate = valid_custody()
        self.assertEqual(MODULE.validate_custody(candidate, AUTHORITY), [])
        candidate["artifact"]["inventory"][1]["bytes"] += 1
        candidate["artifact_object"]["version_id"] = ""
        candidate["artifact_object"]["retention_verified"] = False
        failures = MODULE.validate_custody(candidate, AUTHORITY)
        self.assertIn("custody exact inventory", failures)
        self.assertIn("custody provider field:version_id", failures)
        self.assertIn("custody artifact object:retention_verified", failures)

    def test_read_back_and_exact_key_mismatch_fail(self) -> None:
        candidate = valid_custody()
        candidate["artifact_object"]["key"] = "wrong"
        candidate["artifact_object"]["read_back_sha256"] = "0" * 64
        failures = MODULE.validate_custody(candidate, AUTHORITY)
        self.assertIn("custody artifact object:key", failures)
        self.assertIn("custody artifact object:read_back_sha256", failures)

    def test_custody_manifest_is_non_circular(self) -> None:
        properties = SCHEMA["properties"]
        self.assertNotIn("objects", properties)
        self.assertIn("artifact_object", properties)
        self.assertIn("custody_manifest_intent", properties)
        self.assertIn("non_circular_manifest_rule", PLAN)

    def test_existing_object_version_and_missing_retention_are_fail_closed(self) -> None:
        conditions = set(AUTHORITY["fail_closed_conditions"])
        self.assertIn("pre-existing key, version or delete marker", conditions)
        self.assertIn("read-back byte, SHA-256, inventory, version or retention mismatch", conditions)

    def test_source_contract_and_promotion_remain_out_of_scope(self) -> None:
        prohibited = set(AUTHORITY["prohibited_actions"])
        self.assertIn("source-contract change or activation", prohibited)
        self.assertIn("source qualification or promotion", prohibited)
        self.assertIn("source request", prohibited)

    def test_scientific_and_repository_identity_mutations_fail(self) -> None:
        changed = copy.deepcopy(AUTHORITY)
        changed["governing_decision"]["source_qualified"] = True
        changed["governing_decision"]["total_real_statcast_requests"] = 999
        changed["immutable_scientific_and_execution_identities"]["source_contract_v1_sha256"] = "0" * 64
        failures = MODULE.validate_values(changed, SCHEMA, PLAN)
        self.assertIn("governing decision mismatch: source_qualified", failures)
        self.assertIn("governing decision mismatch: total_real_statcast_requests", failures)
        self.assertIn("immutable identity mismatch: source_contract_v1_sha256", failures)

    def test_terminal_and_complete_source_identity_mutations_fail(self) -> None:
        changed = copy.deepcopy(AUTHORITY)
        changed["governing_decision"]["execution_contract_sha256"] = "0" * 64
        changed["governing_decision"]["terminal_status"] = "QUALIFIED"
        for key, value in {
            "artifact_name": "wrong", "dispatch_commit": "0" * 40,
            "expires_at_utc": "2000-01-01T00:00:00Z", "raw_content_type": "text/html",
            "http_status": 500, "raw_member": "wrong.csv",
        }.items():
            changed["source_artifact"][key] = value
        failures = MODULE.validate_values(changed, SCHEMA, PLAN)
        self.assertIn("governing decision mismatch: execution_contract_sha256", failures)
        self.assertIn("governing decision mismatch: terminal_status", failures)
        for key in ("artifact_name", "dispatch_commit", "expires_at_utc", "raw_content_type", "http_status", "raw_member"):
            self.assertIn(f"source artifact mismatch: {key}", failures)

    def test_custody_top_level_scientific_and_provider_mutations_fail(self) -> None:
        candidate = valid_custody()
        candidate.pop("schema_version")
        candidate["scientific_disposition"]["source_qualified"] = True
        candidate["provider"]["account_id"] = "000000000000"
        candidate["custody_manifest_intent"]["object_lock_mode"] = "GOVERNANCE"
        failures = MODULE.validate_custody(candidate, AUTHORITY)
        self.assertIn("custody top-level:schema_version", failures)
        self.assertIn("custody top-level shape", failures)
        self.assertIn("custody scientific:source_qualified", failures)
        self.assertIn("custody provider:account_id", failures)
        self.assertIn("custody manifest intent:object_lock_mode", failures)

    def test_custody_rejects_malformed_hashes_dates_and_extra_provider_fields(self) -> None:
        candidate = valid_custody()
        candidate["external_receipts"]["artifact_retrieval_sha256"] = "not-a-hash"
        candidate["authorization"]["execution_commit"] = "short"
        candidate["provider"]["upload_utc"] = "not-a-date"
        candidate["provider"]["unexpected"] = "value"
        candidate["chain_of_custody"][0]["evidence_sha256"] = "bad"
        failures = MODULE.validate_custody(candidate, AUTHORITY)
        self.assertIn("custody external receipts", failures)
        self.assertIn("custody authorization:execution_commit", failures)
        self.assertIn("custody provider:upload_utc", failures)
        self.assertIn("custody provider shape", failures)
        self.assertIn("custody chain event", failures)

    def test_preparation_manifest_is_exact_and_hash_bound(self) -> None:
        manifest = json.loads(
            (ROOT / "reports/shared_pa_statcast_june28_durable_retention_preparation_manifest_v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["changed_paths"], MODULE.EXPECTED_CHANGED_PATHS)
        self.assertEqual(manifest["canonical_changed_path_list_sha256"], MODULE.EXPECTED_CHANGED_PATH_LIST_SHA256)
        self.assertEqual(
            set(manifest["non_self_file_sha256"]),
            set(MODULE.EXPECTED_CHANGED_PATHS) - {"reports/shared_pa_statcast_june28_durable_retention_preparation_manifest_v1.json"},
        )
        for relative, expected in manifest["non_self_file_sha256"].items():
            self.assertEqual(MODULE.sha256_file(ROOT / relative), expected)


if __name__ == "__main__":
    unittest.main()
