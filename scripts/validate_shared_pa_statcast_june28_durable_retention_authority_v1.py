"""Offline fail-closed validation for the inactive June 28 retention package."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import re


EXPECTED_INVENTORY_SHA256 = "422d0ee833cf9ad80103af3d1d3150fd29794320c5dcfa22f89005385267d34b"
ARTIFACT_KEY = "mlb-source-evidence/statcast/v2-confirmation/date-2023-06-28/attempt-01/run-30846344146/artifact-8868838408/shared-pa-statcast-v2-confirmation-2023-06-28-attempt-01.zip"
CUSTODY_KEY = "mlb-source-evidence/statcast/v2-confirmation/date-2023-06-28/attempt-01/run-30846344146/artifact-8868838408/shared-pa-statcast-v2-confirmation-2023-06-28-attempt-01.custody-v1.json"
EXPECTED_IDENTITIES = {
    "workflow_sha256": "4367dc0bb530a3664aa30155a5a73654d13465acb3bcd924fce8d7d0abb98480",
    "carrier_commit": "a8ebc64eddf8ace3a48e25af57ee0d6658d0663a",
    "runtime_authority_sha256": "f348f1baf664fa12c821eb488b4ff4c8fb293b3e243d713600efcd0da96fbf2a",
    "authority_package_sha256": "31181de53446951a4d48c5740e4687c36fb042005e1466ff09a9ac9e1cf3ef0d",
    "source_bundle_sha256": "81861595f8eb4b83772220bdad2fb556fc06ec3640a1a4f2084255e8f5183033",
    "source_contract_v1_sha256": "7078857cc1815e5c8dcecd667a9b6d69079ef31c1fc68057473d1fb1dc9ceebd",
    "source_contract_v2_proposal_sha256": "166c49a5ea66bf4491c9d04c7f233c7bc51c032683b9883b695236dabc637328",
    "eof_policy_sha256": "b8d8dbcdee611c81029a51db74f9c005115360fbbcd3f76078857d1f6d978293",
    "team_identity_policy_sha256": "c6422eb1cfdd494e958d2623a2acdc911a1bf47f93f6b8f27186241988696b55",
    "request_plan_sha256": "0bcc3c9482ea67189e414502b9b58e6445cd66efeb27c309c5bcff4864108402",
    "decision_contract_sha256": "77754f074a874dfee6f1aabd3a058b1a3d33f5ed7726f17f75caf2d1db339c5c",
}
EXPECTED_CHANGED_PATHS = [
    "config/shared_pa_statcast_june28_durable_retention_authority_package_v1.json",
    "contracts/schemas/shared_pa_statcast_june28_custody_manifest_v1.schema.json",
    "reports/shared_pa_statcast_june28_durable_retention_execution_plan_v1.json",
    "reports/shared_pa_statcast_june28_durable_retention_preparation_manifest_v1.json",
    "scripts/validate_shared_pa_statcast_june28_durable_retention_authority_v1.py",
    "tests/test_shared_pa_statcast_june28_durable_retention_authority_v1.py",
]
EXPECTED_CHANGED_PATH_LIST_SHA256 = "d6ecb774bd699c640174b5f75e14ad45168fc70af22e9f6a4c30b493dfa621ff"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")


class RetentionPackageError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RetentionPackageError(f"JSON object required: {path}")
    return value


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_values(authority: dict, schema: dict, plan: dict) -> list[str]:
    failures: list[str] = []
    false_flags = (
        "preparation_is_aws_authorization", "aws_write_authorized", "upload_authorized",
        "retention_lock_authorized", "deletion_authorized", "source_qualification_authorized",
        "self_authorization_permitted",
    )
    for field in false_flags:
        if authority.get(field) is not False:
            failures.append(f"authority flag must be false: {field}")
    governing = authority.get("governing_decision", {})
    governing_expected = {
        "decision_contract_path": "config/shared_pa_statcast_confirmation_decision_contract_v1.json",
        "decision_contract_sha256": "77754f074a874dfee6f1aabd3a058b1a3d33f5ed7726f17f75caf2d1db339c5c",
        "execution_contract_path": "config/shared_pa_statcast_confirmation_execution_contract_v1.json",
        "execution_contract_sha256": "851e02beab8732b8f6d1a242ea4609d30ab1f3b462804134ae8194b3d42dc510",
        "terminal_status": "CONFIRMATION_PENDING_SEPARATE_DURABLE_RETENTION_AND_INDEPENDENT_REPRODUCTION",
        "technical_validation_status": "PASS", "durable_retention_verified": False,
        "independent_reproduction_verified": False, "source_qualified": False,
        "promotable": False, "confirmation_attempt_1": "CONSUMED",
        "old_july_attempt_4": "UNUSED_UNAUTHORIZED", "total_real_statcast_requests": 4,
    }
    for key, expected in governing_expected.items():
        if governing.get(key) != expected: failures.append(f"governing decision mismatch: {key}")
    identities = authority.get("immutable_scientific_and_execution_identities", {})
    for key, expected in EXPECTED_IDENTITIES.items():
        if identities.get(key) != expected: failures.append(f"immutable identity mismatch: {key}")
    source = authority.get("source_artifact", {})
    expected_source = {
        "workflow_run_id": 30846344146, "run_attempt": 1, "artifact_id": 8868838408,
        "dispatch_commit": "8c24b7362e5be52635f4909a74fe5c4832100938",
        "artifact_name": "shared-pa-statcast-v2-confirmation-2023-06-28-attempt-01-auth-june28-a1-209b664-b96dbba2067789d13c4033af438e87a1",
        "expires_at_utc": "2026-11-01T19:34:15Z",
        "zip_byte_count": 1005357,
        "zip_sha256": "4c77f6d9572d8bc21ab79a06b0d8b77e2a04231375de000ee9e37dc7a2b530b5",
        "entry_count": 19, "raw_byte_count": 2928702,
        "raw_member": "june28-confirmation-carrier/data/source/shared_pa_statcast_v2_confirmation_2023-06-28_v1/statcast-v2-confirmation-2023-06-28/response-attempt-01.csv",
        "raw_content_type": "application/download; charset=utf-8", "http_status": 200,
        "raw_sha256": "4b8a99e4a4130dcb7fb409f0637a25054f93d536bb57a02051b6d1003716f237",
    }
    for key, expected in expected_source.items():
        if source.get(key) != expected:
            failures.append(f"source artifact mismatch: {key}")
    inventory = authority.get("exact_artifact_inventory", [])
    if len(inventory) != 19 or len({item.get("path") for item in inventory}) != 19:
        failures.append("exact 19-member inventory is not bound")
    if canonical_sha256(inventory) != EXPECTED_INVENTORY_SHA256 or authority.get("exact_artifact_inventory_canonical_sha256") != EXPECTED_INVENTORY_SHA256:
        failures.append("exact inventory content mismatch")
    raw = [item for item in inventory if item.get("path") == source.get("raw_member")]
    if len(raw) != 1 or raw[0].get("bytes") != source.get("raw_byte_count") or raw[0].get("sha256") != source.get("raw_sha256"):
        failures.append("raw member does not reconcile with inventory")
    receipts = authority.get("required_external_receipts", {})
    if set(receipts) != {"artifact_retrieval_receipt", "post_terminal_run_receipt"}:
        failures.append("external provider receipts are not exactly required")
    adjudication = authority.get("retention_adjudication", {})
    if adjudication.get("github_actions_artifact_alone_is_durable_retention") is not False:
        failures.append("Actions artifact incorrectly treated as durable")
    if adjudication.get("original_zip_must_remain_byte_identical") is not True:
        failures.append("byte-identical ZIP not required")
    if adjudication.get("detached_custody_manifest_required") is not True:
        failures.append("detached custody manifest not required")
    aws = authority.get("aws_target", {})
    if aws.get("object_lock_mode") != "COMPLIANCE":
        failures.append("Object Lock mode is not COMPLIANCE")
    if aws.get("retain_until_utc") != "2033-08-03T00:00:00Z":
        failures.append("retention timestamp mismatch")
    if aws.get("retain_until_status") != "PROPOSED_MINIMUM_FOR_SEPARATE_HUMAN_APPROVAL_NOT_A_FROZEN_SCIENTIFIC_REQUIREMENT":
        failures.append("retention proposal status mismatch")
    if aws.get("bucket_default_retention") is not None:
        failures.append("unexpected bucket-wide default retention")
    unresolved = ("region", "infrastructure_role_arn", "writer_role_arn", "verifier_role_arn", "audit_trail_or_event_data_store_arn", "bucket_policy_sha256")
    if any(aws.get(field) is not None for field in unresolved):
        failures.append("unverified AWS binding was populated")
    keys = authority.get("object_keys", {})
    if keys.get("maximum_object_upload_count") != 2 or keys.get("third_object_allowed") is not False:
        failures.append("two-object boundary mismatch")
    if len({keys.get("artifact_zip"), keys.get("custody_manifest")}) != 2:
        failures.append("object keys are not distinct")
    if keys.get("artifact_zip") != ARTIFACT_KEY or keys.get("custody_manifest") != CUSTODY_KEY:
        failures.append("exact object key mismatch")
    properties = schema.get("properties", {})
    if properties.get("provider", {}).get("properties", {}).get("object_lock_mode", {}).get("const") != "COMPLIANCE":
        failures.append("custody schema does not require COMPLIANCE")
    if properties.get("artifact_object", {}).get("properties", {}).get("key", {}).get("const") != ARTIFACT_KEY:
        failures.append("custody schema artifact key mismatch")
    if properties.get("custody_manifest_intent", {}).get("properties", {}).get("key", {}).get("const") != CUSTODY_KEY:
        failures.append("custody schema manifest key mismatch")
    if "objects" in properties:
        failures.append("circular generic objects array remains")
    if plan.get("maximum_provider_object_writes") != 2 or plan.get("aws_mutation_performed") is not False:
        failures.append("execution plan mutation boundary mismatch")
    if "non_circular_manifest_rule" not in plan:
        failures.append("non-circular manifest rule missing")
    return failures


def validate_custody(candidate: dict, authority: dict) -> list[str]:
    failures: list[str] = []
    top_expected = {
        "schema_version": "shared-pa-statcast-june28-custody-manifest-v1",
        "evidence_purpose": "SEPARATE_DURABLE_RETENTION_ONLY", "attempt": 1,
        "workflow_run_id": 30846344146,
        "immutable_result_status": "ARTIFACT_OBJECT_VERIFIED_CUSTODY_MANIFEST_READY_FOR_CREATE_ONLY_UPLOAD",
    }
    for key, expected in top_expected.items():
        if candidate.get(key) != expected: failures.append(f"custody top-level:{key}")
    required_top = set(("schema_version","evidence_purpose","attempt","workflow_run_id","artifact","external_receipts","scientific_disposition","authorization","provider","artifact_object","custody_manifest_intent","chain_of_custody","immutable_result_status"))
    if set(candidate) != required_top:
        failures.append("custody top-level shape")
    artifact = candidate.get("artifact", {})
    if set(artifact) != {"name","id","expiration_utc","zip_byte_count","zip_sha256","raw_byte_count","raw_sha256","entry_count","inventory"}:
        failures.append("custody artifact shape")
    expected_source = authority["source_artifact"]
    expected_fields = {
        "name": expected_source["artifact_name"], "id": expected_source["artifact_id"],
        "expiration_utc": expected_source["expires_at_utc"], "zip_byte_count": expected_source["zip_byte_count"],
        "zip_sha256": expected_source["zip_sha256"], "raw_byte_count": expected_source["raw_byte_count"],
        "raw_sha256": expected_source["raw_sha256"], "entry_count": 19,
    }
    for key, expected in expected_fields.items():
        if artifact.get(key) != expected: failures.append(f"custody artifact:{key}")
    if artifact.get("inventory") != authority["exact_artifact_inventory"]:
        failures.append("custody exact inventory")
    provider = candidate.get("provider", {})
    if set(provider) != {"account_id","partition","region","bucket","uploader_arn","upload_utc","encryption","object_lock_mode","retain_until_utc","audit_identity"}:
        failures.append("custody provider shape")
    provider_expected = {
        "account_id": "723322847536", "partition": "aws",
        "bucket": authority["aws_target"]["proposed_dedicated_bucket"], "encryption": "AES256",
        "object_lock_mode": "COMPLIANCE", "retain_until_utc": authority["aws_target"]["retain_until_utc"],
    }
    for key, expected in provider_expected.items():
        if provider.get(key) != expected: failures.append(f"custody provider:{key}")
    for key in ("region", "uploader_arn", "upload_utc", "audit_identity"):
        if not provider.get(key): failures.append(f"custody provider:{key}")
    if provider.get("uploader_arn") and not provider["uploader_arn"].startswith("arn:aws:iam::723322847536:role/"):
        failures.append("custody provider:uploader_arn")
    if provider.get("upload_utc"):
        try: datetime.fromisoformat(provider["upload_utc"].replace("Z", "+00:00"))
        except (TypeError, ValueError): failures.append("custody provider:upload_utc")
    artifact_object = candidate.get("artifact_object", {})
    if set(artifact_object) != {"key","version_id","etag_metadata_only","request_id","byte_count","sha256","read_back_byte_count","read_back_sha256","retention_verified"}:
        failures.append("custody artifact object shape")
    expected_object = {
        "key": ARTIFACT_KEY, "byte_count": expected_source["zip_byte_count"],
        "sha256": expected_source["zip_sha256"], "read_back_byte_count": expected_source["zip_byte_count"],
        "read_back_sha256": expected_source["zip_sha256"], "retention_verified": True,
    }
    for key, expected in expected_object.items():
        if artifact_object.get(key) != expected: failures.append(f"custody artifact object:{key}")
    for key in ("version_id", "etag_metadata_only", "request_id"):
        if not artifact_object.get(key): failures.append(f"custody provider field:{key}")
    intent = candidate.get("custody_manifest_intent", {})
    if set(intent) != {"key","create_only","object_lock_mode","retain_until_utc","actual_provider_result_location"}:
        failures.append("custody manifest intent shape")
    intent_expected = {
        "key": CUSTODY_KEY, "create_only": True, "object_lock_mode": "COMPLIANCE",
        "retain_until_utc": authority["aws_target"]["retain_until_utc"],
        "actual_provider_result_location": "SEPARATE_LOCAL_OR_REPOSITORY_EXECUTION_RESULT_NOT_A_THIRD_S3_OBJECT",
    }
    for key, expected in intent_expected.items():
        if intent.get(key) != expected: failures.append(f"custody manifest intent:{key}")
    if "objects" in candidate:
        failures.append("custody circular objects")
    external = candidate.get("external_receipts", {})
    if set(external) != {"artifact_retrieval_sha256", "post_terminal_run_sha256"} or any(not HEX64.fullmatch(str(external.get(key, ""))) for key in external):
        failures.append("custody external receipts")
    scientific = candidate.get("scientific_disposition", {})
    scientific_expected = {
        "technical_validation": "PASS", "durable_retention_verified_before_execution": False,
        "independent_reproduction_verified": False, "source_qualified": False,
        "promotable": False, "confirmation_attempt_1": "CONSUMED", "total_real_statcast_requests": 4,
    }
    for key, expected in scientific_expected.items():
        if scientific.get(key) != expected: failures.append(f"custody scientific:{key}")
    authorization = candidate.get("authorization", {})
    if set(authorization) != {"authorization_id","authorization_sha256","execution_commit"}:
        failures.append("custody authorization shape")
    if not authorization.get("authorization_id"): failures.append("custody authorization:authorization_id")
    if not HEX64.fullmatch(str(authorization.get("authorization_sha256", ""))): failures.append("custody authorization:authorization_sha256")
    if not HEX40.fullmatch(str(authorization.get("execution_commit", ""))): failures.append("custody authorization:execution_commit")
    events = candidate.get("chain_of_custody", [])
    if not isinstance(events, list) or len(events) < 4:
        failures.append("custody chain of custody")
    else:
        for event in events:
            if set(event) != {"event","observed_at_utc","actor","evidence_sha256"} or not event.get("event") or not event.get("actor") or not HEX64.fullmatch(str(event.get("evidence_sha256", ""))):
                failures.append("custody chain event")
                break
            try: datetime.fromisoformat(str(event.get("observed_at_utc", "")).replace("Z", "+00:00"))
            except ValueError:
                failures.append("custody chain event")
                break
    return failures


def validate(repository: Path) -> dict:
    authority_path = repository / "config/shared_pa_statcast_june28_durable_retention_authority_package_v1.json"
    schema_path = repository / "contracts/schemas/shared_pa_statcast_june28_custody_manifest_v1.schema.json"
    plan_path = repository / "reports/shared_pa_statcast_june28_durable_retention_execution_plan_v1.json"
    manifest_path = repository / "reports/shared_pa_statcast_june28_durable_retention_preparation_manifest_v1.json"
    authority, schema, plan, manifest = load_object(authority_path), load_object(schema_path), load_object(plan_path), load_object(manifest_path)
    failures = validate_values(authority, schema, plan)
    if manifest.get("changed_paths") != EXPECTED_CHANGED_PATHS or manifest.get("canonical_changed_path_list_sha256") != EXPECTED_CHANGED_PATH_LIST_SHA256:
        failures.append("preparation manifest path scope")
    expected_non_self = set(EXPECTED_CHANGED_PATHS) - {"reports/shared_pa_statcast_june28_durable_retention_preparation_manifest_v1.json"}
    non_self = manifest.get("non_self_file_sha256", {})
    if set(non_self) != expected_non_self:
        failures.append("preparation manifest identity map")
    for relative, expected in non_self.items():
        path = repository / relative
        if not path.is_file() or sha256_file(path) != expected:
            failures.append(f"preparation manifest identity mismatch: {relative}")
    identity_paths = {
        "workflow_sha256": ".github/workflows/shared-pa-statcast-v2-confirmation-execution-v1.yml",
        "runtime_authority_sha256": "config/shared_pa_statcast_confirmation_runtime_authority_v1.json",
        "authority_package_sha256": "config/shared_pa_statcast_confirmation_attempt_01_authority_package_20260802_v1.json",
        "source_contract_v1_sha256": "config/shared_pa_statcast_source_contract_v1.json",
        "source_contract_v2_proposal_sha256": "config/shared_pa_statcast_source_contract_v2_proposal.json",
        "eof_policy_sha256": "config/shared_pa_statcast_csv_eof_completeness_policy_v1.json",
        "team_identity_policy_sha256": "config/shared_pa_statcast_canonical_team_identity_policy_v1.json",
        "request_plan_sha256": "config/shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json",
        "decision_contract_sha256": "config/shared_pa_statcast_confirmation_decision_contract_v1.json",
    }
    for identity, relative in identity_paths.items():
        if sha256_file(repository / relative) != EXPECTED_IDENTITIES[identity]:
            failures.append(f"repository identity mismatch: {identity}")
    if sha256_file(repository / "config/shared_pa_statcast_confirmation_execution_contract_v1.json") != "851e02beab8732b8f6d1a242ea4609d30ab1f3b462804134ae8194b3d42dc510":
        failures.append("repository identity mismatch: execution_contract_sha256")
    runtime = load_object(repository / "config/shared_pa_statcast_confirmation_runtime_authority_v1.json")
    if runtime.get("source_bundle", {}).get("canonical_path_hash_map_sha256") != EXPECTED_IDENTITIES["source_bundle_sha256"]:
        failures.append("repository identity mismatch: source_bundle_sha256")
    result = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "authority_sha256": sha256_file(authority_path),
        "custody_schema_sha256": sha256_file(schema_path),
        "execution_plan_sha256": sha256_file(plan_path),
        "aws_write_authorized": False,
        "execution_blocked_on_unresolved_aws_bindings": True,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = validate(args.repository.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
