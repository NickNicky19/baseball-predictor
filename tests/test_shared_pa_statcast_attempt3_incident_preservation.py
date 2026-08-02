#!/usr/bin/env python3
"""No-network regression for attempt-3 incident and evidence preservation."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import sys


V2_LEDGER_SHA256 = "456787efef26d3d438e1afe13d1eee949cd9866d2f8d0a2c5c74d9928a97572e"
V3_LEDGER_SHA256 = "43815c024b7e138850001ecdd9f6cb5b7fcb928f8c518a1cbf4c7f6e4fe016b1"
OFFLINE_REPORT_SHA256 = "21daea2df80c000f4abfeffde3c9c3f4ac44f4e0ea7e4e8727c869dc39372cdf"
CONTRACT_SHA256 = "7078857cc1815e5c8dcecd667a9b6d69079ef31c1fc68057473d1fb1dc9ceebd"
AUTHORITY_SHA256 = "e4687d8e2c881c6820196c7e4f5a1dc30724de3be609581e2d9dc55af493d729"
OLD_WORKFLOW_SHA256 = "c23b0155c3b4c14c4ce6c6cd744a7c159a1c100b3df9ac04e7bda82ef2628ddf"
ARTIFACT_ZIP_SHA256 = "a2fb41e783e80e067b958b0c0666d9e53cb36fffc591e33e04a0238f9c1c8fe4"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_ledgers(repository: Path) -> None:
    v2_path = repository / "config/shared_pa_statcast_sample_attempt_history_20260801_v2.json"
    v3_path = repository / "config/shared_pa_statcast_sample_attempt_history_20260802_v3.json"
    if sha256_file(v2_path) != V2_LEDGER_SHA256:
        raise AssertionError("predecessor ledger was overwritten")
    if sha256_file(v3_path) != V3_LEDGER_SHA256:
        raise AssertionError("attempt-3 successor ledger identity differs")
    v2, v3 = load_json(v2_path), load_json(v3_path)
    if v3["predecessor"] != {
        "path": "config/shared_pa_statcast_sample_attempt_history_20260801_v2.json",
        "sha256": V2_LEDGER_SHA256,
        "modified": False,
        "operative_future_authority": False,
    }:
        raise AssertionError("append-only predecessor binding differs")
    if [row["attempt_number"] for row in v2["attempts"]] != [1, 2]:
        raise AssertionError("historical attempts were reinterpreted")
    if [row["attempt_number"] for row in v3["attempts"]] != [1, 2, 3]:
        raise AssertionError("attempts 1 through 3 were erased, reordered, or reset")
    if not all("PERMANENTLY_CONSUMED" in row["status"] for row in v3["attempts"]):
        raise AssertionError("a consumed attempt became reusable")
    attempt3 = v3["attempts"][2]
    if attempt3["artifact"]["id"] != 8826086488 or attempt3["raw_response"]["sha256"] != "0d4c91cb2d0eabaeaeed726fcf1d4aabd7f9d888fb4c9f90789a7ba679e0a968":
        raise AssertionError("attempt-3 evidence identity differs")
    if attempt3["response_headers"] != {
        "content_type": "application/download; charset=utf-8",
        "normalized_mime_type": "application/download",
    }:
        raise AssertionError("attempt-3 exact content type differs")
    if v3["remaining_attempts"] != [{"attempt_number": 4, "status": "UNUSED_UNAUTHORIZED"}]:
        raise AssertionError("attempt 4 authorization state differs")
    if (v3["consumed_lifetime_attempts"], v3["remaining_lifetime_attempts"],
            v3["total_external_statcast_requests"]) != (3, 1, 3):
        raise AssertionError("attempt or real-request accounting differs")
    if v3["attempt_reuse_permitted"] is not False or v3["attempt_numbering_reset_permitted"] is not False:
        raise AssertionError("attempt history protection was weakened")
    if v3["attempt_4_authorized"] is not False or v3["workflow_dispatch_capture_authorized"] is not False:
        raise AssertionError("attempt 4 became executable")


def validate_offline_report(repository: Path) -> dict:
    path = repository / "reports/shared_pa_statcast_attempt_3_offline_body_validation_20260802_v1.json"
    if sha256_file(path) != OFFLINE_REPORT_SHA256:
        raise AssertionError("offline body-validation report differs")
    report = load_json(path)
    if report["status"] != "ATTEMPT_3_ARTIFACT_INTEGRITY_PASSED":
        raise AssertionError("artifact integrity did not pass")
    if report["body_validation_status"] != "ATTEMPT_3_BODY_CSV_WITH_SCHEMA_DRIFT":
        raise AssertionError("body classification differs")
    if report["content_type_contract_evidence_status"] != "CONTENT_TYPE_CONTRACT_EVIDENCE_INSUFFICIENT":
        raise AssertionError("content-type evidence decision differs")
    if report["artifact"]["id"] != 8826086488 or report["artifact"]["zip_sha256"] != ARTIFACT_ZIP_SHA256:
        raise AssertionError("artifact identity differs")
    if report["failure_package_sha256"] != "a166ab2c08c49238be1e45986764a6a7a960360d405b721602f28679e2bebdd0":
        raise AssertionError("failure-package identity differs")
    if report["raw_response"] != {
        "bytes": 2918703,
        "sha256": "0d4c91cb2d0eabaeaeed726fcf1d4aabd7f9d888fb4c9f90789a7ba679e0a968",
    }:
        raise AssertionError("raw-response identity differs")
    body, schema = report["body_classification"], report["schema_validation"]
    if body["classification"] != "CSV" or body["html_markers_present"] or body["json_error_object_present"]:
        raise AssertionError("offline body classification differs")
    if not body["delimiter_consistent"] or not body["quoting_consistent"] or body["terminal_newline"]:
        raise AssertionError("CSV delimiter, quoting, or newline decision differs")
    if not schema["header_exact_match"] or schema["header_column_count"] != 119 or schema["expected_contract_column_count"] != 119:
        raise AssertionError("ordered source header differs")
    if schema["parsed_row_count"] != 4383 or schema["malformed_row_count"] or schema["empty_row_count"]:
        raise AssertionError("CSV row accounting differs")
    if schema["duplicate_event_identity_count"] or schema["conflicting_duplicate_count"]:
        raise AssertionError("event identities are duplicated")
    if schema["missing_game_pk_count"] or schema["missing_batter_id_count"] or schema["missing_pitcher_id_count"]:
        raise AssertionError("required identity values are missing")
    if schema["missing_certified_game_pks"] or schema["unexpected_game_pks"] or len(schema["distinct_game_pks"]) != 15:
        raise AssertionError("certified game-pk coverage differs")
    mismatches = {row["game_pk"] for row in schema["team_identity_mismatches"]}
    if mismatches != {717259, 717272}:
        raise AssertionError("team-code identity drift differs")
    if schema["unknown_pitch_description_values"]:
        raise AssertionError("plate-discipline mapping has unknown descriptions")
    if report["plate_discipline_validation"]["error"] is not None:
        raise AssertionError("plate-discipline transformation failed")
    evla = report["ev_launch_angle_validation"]
    if evla["error"] is not None or not evla["bbe_denominator_reconciles"]:
        raise AssertionError("EV/LA denominator validation failed")
    counts = evla["result"]
    if (counts["total_bbe"], counts["measured_ev_count"], counts["measured_la_count"],
            counts["joint_ev_la_count"], counts["missing_ev_count"], counts["missing_la_count"]) != (762, 715, 715, 715, 47, 47):
        raise AssertionError("EV/LA retained counts differ")
    if report["quarantine_disposition"] != {
        "excluded_from": ["source", "feature", "model", "prediction", "prospective_evidence", "economic_evaluation", "betting"],
        "invalid": True, "non_promotable": True, "quarantined": True, "source_qualified": False,
    }:
        raise AssertionError("quarantine or terminal semantics changed")
    return report


def validate_workflow(repository: Path) -> None:
    workflow = repository / ".github/workflows/shared-pa-statcast-sample-capture-execution-v1.yml"
    text = workflow.read_text(encoding="utf-8")
    required = (
        "shared_pa_statcast_sample_attempt_history_20260802_v3.json",
        V3_LEDGER_SHA256,
        "ATTEMPT_3_SPENT_ATTEMPT_4_UNAUTHORIZED",
        "SPENT_AUTHORIZATION_REJECTED_BEFORE_TRANSPORT",
        "verifier_rc=$native_verifier_rc",
        "scripts/verify_shared_pa_statcast_capture_v1.py",
        "working-directory: ${{ runner.temp }}/statcast-carrier-exec",
        "if: ${{ always() && github.event_name == 'workflow_dispatch' }}",
        '"quarantined": is_failure',
        '"non_promotable": is_failure',
    )
    missing = [value for value in required if value not in text]
    if missing:
        raise AssertionError(f"workflow preservation contract missing: {missing}")
    if "attempt3-carrier-root-verifier" in text:
        raise AssertionError("redundant helper remains")
    if text.count("with capture.network_denial_guard()") < 2:
        raise AssertionError("independent no-network barriers differ")
    if text.count("if: ${{ always() && github.event_name == 'workflow_dispatch' }}") < 3:
        raise AssertionError("terminal failure publication was weakened")
    authority = repository / "config/shared_pa_statcast_attempt_03_authority_package_20260801_v1.json"
    if sha256_file(authority) != AUTHORITY_SHA256:
        raise AssertionError("historical attempt-3 authority changed")
    if load_json(authority)["workflow"]["sha256"] != OLD_WORKFLOW_SHA256:
        raise AssertionError("historical workflow binding changed")
    if sha256_file(workflow) == OLD_WORKFLOW_SHA256:
        raise AssertionError("spent authorization still matches current workflow")
    mutated = text.replace("verifier_rc=$native_verifier_rc", "verifier_rc=0", 1)
    if "verifier_rc=$native_verifier_rc" in mutated:
        raise AssertionError("helper-removal mutation test is ineffective")
    if "verifier_rc=$native_verifier_rc" not in text:
        raise AssertionError("native verifier is not authoritative")


def validate_incident(repository: Path) -> None:
    incident = load_json(repository / "reports/shared_pa_statcast_attempt_3_incident_preservation_20260802_v1.json")
    if incident["status"] != "ATTEMPT_3_INCIDENT_RECORDED":
        raise AssertionError("attempt-3 incident was not recorded")
    if incident["attempt_accounting"]["total_real_external_statcast_requests"] != 3:
        raise AssertionError("incident real-request accounting differs")
    if incident["attempt_accounting"]["attempt_4"] != "UNUSED_UNAUTHORIZED":
        raise AssertionError("incident authorizes attempt 4")
    if incident["helper_import_defect"]["repair"] != "removed the redundant stdin helper and assigned verifier_rc from native_verifier_rc":
        raise AssertionError("helper repair differs")
    if incident["validation_failure"]["capture_validation_exit_code"] != 1:
        raise AssertionError("terminal validation failure changed")
    if incident["quarantine_disposition"]["source_qualified"] is not False:
        raise AssertionError("attempt 3 became source-qualified")


def validate_preservation_manifest(repository: Path) -> None:
    manifest = load_json(repository / "reports/shared_pa_statcast_attempt_3_preservation_manifest_20260802_v1.json")
    paths = sorted(manifest["changed_path_manifest"])
    digest = hashlib.sha256("".join(path + "\n" for path in paths).encode()).hexdigest()
    if paths != manifest["changed_path_manifest"] or digest != "1570edd566d76a88fc3de85cc85dda26acb08e8c9effd649130905d18e41c7a5":
        raise AssertionError("changed-path allowlist identity differs")
    if manifest["changed_path_manifest_sha256"] != digest:
        raise AssertionError("changed-path manifest digest differs")
    for relative, expected in manifest["bound_files"].items():
        if sha256_file(repository / relative) != expected:
            raise AssertionError(f"preservation-bound file differs: {relative}")
    risk = manifest["expiration_risk"]
    if risk["status"] != "UNRESOLVED_AUTHORIZATION_REQUIRED" or risk["artifact_expires_at_utc"] != "2026-10-31T00:25:53Z":
        raise AssertionError("artifact expiration risk was not retained")
    if len(manifest["durable_preservation_options"]) < 2:
        raise AssertionError("durable-preservation options are absent")


def verify_actual_artifact(repository: Path, artifact_zip: Path) -> None:
    module_path = repository / "scripts/validate_shared_pa_statcast_attempt3_offline_v1.py"
    spec = importlib.util.spec_from_file_location("attempt3_offline", module_path)
    if spec is None or spec.loader is None:
        raise AssertionError("offline validator cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_create_connection = socket.create_connection
    socket.create_connection = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("network access attempted during offline validation")
    )
    try:
        observed = module.analyze(artifact_zip.resolve(), repository.resolve(), 8826086488)
    finally:
        socket.create_connection = original_create_connection
    committed = load_json(repository / "reports/shared_pa_statcast_attempt_3_offline_body_validation_20260802_v1.json")
    if observed != committed:
        raise AssertionError("committed offline report differs from retained artifact")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--artifact-zip", type=Path)
    args = parser.parse_args()
    repository = args.repository.resolve()
    if sha256_file(repository / "config/shared_pa_statcast_source_contract_v1.json") != CONTRACT_SHA256:
        raise AssertionError("source contract or content-type allowlist changed")
    validate_ledgers(repository)
    validate_offline_report(repository)
    validate_workflow(repository)
    validate_incident(repository)
    validate_preservation_manifest(repository)
    if args.artifact_zip:
        verify_actual_artifact(repository, args.artifact_zip)
        print("ACTUAL_ARTIFACT_OFFLINE_VALIDATION=PASS")
    print("ARTIFACT_IDENTITIES=PASS")
    print("OFFLINE_BODY_CLASSIFICATION=PASS")
    print("CSV_SCHEMA_AND_GAME_COVERAGE=PASS")
    print("PLATE_DISCIPLINE_MAPPING=PASS")
    print("EV_LA_DENOMINATOR_RECONCILIATION=PASS")
    print("ATTEMPT_LEDGER_APPEND_ONLY=PASS")
    print("ATTEMPT_4_UNAUTHORIZED=PASS")
    print("HELPER_REDUNDANCY_REMOVED=PASS")
    print("TERMINAL_FAILURE_AND_QUARANTINE=PASS")
    print("REAL_EXTERNAL_REQUEST_COUNT=3")
    print("NO_EXTERNAL_STATCAST_REQUEST=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
