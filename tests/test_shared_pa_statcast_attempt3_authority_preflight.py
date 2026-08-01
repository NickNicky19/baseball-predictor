#!/usr/bin/env python3
"""Exact no-network preflight for the prepared attempt-3 authority package."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys


CARRIER = "74395e8552cf65701e526d4cba1f08a3e7f286d8"
RUNTIME_ATTESTATION = "903e566a99d048a575dbe075378fd8b80ace92bf5028c6ddcbb0d200fe509ae5"
RUNTIME_POLICY = "31b6b16063bfb0f8e1535d4475259caa10ff548423df793a9d79bd2616f5f9c3"
CONTRACT = "7078857cc1815e5c8dcecd667a9b6d69079ef31c1fc68057473d1fb1dc9ceebd"
BUNDLE = "22a36802fb7530cc21fb0f38a7c6e85422088de25f435916cf3b53f74d7367a1"
PLAN = "a40abd52a42ed36a49b6fc5d3d91d2202f57e7075a077f8f1abe3252df016994"
LEDGER = "456787efef26d3d438e1afe13d1eee949cd9866d2f8d0a2c5c74d9928a97572e"
INCIDENT = "cff61fb46dbaae2ca6ca4726174a0c1b9804118a3546cbe4ff9b852e1b09c010"
OLD_AUTHORIZATION = "11c19c423404f7e75b8308dc6feea818808af166b64ce7abc7140bc0f6e8425f"
OUTPUT = "data/source/shared_pa_statcast_sample_2023-07-25_attempt-03_v1"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_authority(repository: Path, workflow: Path, authority: Path) -> dict:
    value = json.loads(authority.read_text(encoding="utf-8"))
    if value.get("schema_version") != "shared-pa-statcast-attempt-3-authority-package-v1":
        raise AssertionError("stale authorization schema cannot authorize attempt 3")
    if value.get("status") != "PREPARED_REQUIRES_EXPLICIT_HUMAN_EXECUTION_APPROVAL":
        raise AssertionError("authority package is not in the prepared-only state")
    if value.get("attempt_number") != 3 or value.get("preparation_is_capture_authorization") is not False:
        raise AssertionError("attempt-3 preparation boundary differs")
    if value["workflow"] != {
        "path": ".github/workflows/shared-pa-statcast-sample-capture-execution-v1.yml",
        "sha256": sha256_file(workflow),
        "manual_dispatch_count": 1,
        "automatic_rerun_allowed": False,
        "pull_request_mode": "PREFLIGHT_ONLY",
        "workflow_dispatch_mode": "CAPTURE_ONLY_AFTER_SEPARATE_EXPLICIT_HUMAN_AUTHORIZATION",
    }:
        raise AssertionError("workflow binding differs")
    expected_execution = {
        "carrier_commit": CARRIER,
        "runtime_attestation_sha256": RUNTIME_ATTESTATION,
        "runtime_policy_sha256": RUNTIME_POLICY,
        "source_contract_sha256": CONTRACT,
        "source_bundle_sha256": BUNDLE,
        "sample_request_plan_sha256": PLAN,
    }
    if value.get("execution_identities") != expected_execution:
        raise AssertionError("execution identity chain differs")
    state = value["attempt_state"]
    if state["path"] != "config/shared_pa_statcast_sample_attempt_history_20260801_v2.json":
        raise AssertionError("successor ledger path differs")
    if state["sha256"] != LEDGER or sha256_file(repository / state["path"]) != LEDGER:
        raise AssertionError("successor ledger identity differs")
    if (state["prior_attempts_consumed"], state["remaining_lifetime_attempts"],
            state["total_real_external_statcast_requests"]) != (2, 2, 2):
        raise AssertionError("attempt accounting differs")
    if state["attempt_3_status"] != "UNUSED_UNAUTHORIZED_UNTIL_TRANSPORT":
        raise AssertionError("attempt 3 is not the next unused attempt")
    incident = value["attempt_2_incident_record"]
    if incident["sha256"] != INCIDENT or sha256_file(repository / incident["path"]) != INCIDENT:
        raise AssertionError("attempt-2 incident identity differs")
    if value["output"] != {
        "path": OUTPUT,
        "no_overwrite": True,
        "artifact_name": "shared-pa-statcast-attempt-03",
        "preserve_valid_or_quarantined_failure_package": True,
        "failure_upload_is_source_qualification": False,
    }:
        raise AssertionError("attempt-3 output/artifact identity differs")
    if value["request"]["external_request_attempts"] != 1:
        raise AssertionError("attempt-3 request count differs")
    if value["request"]["minimum_request_start_interval_seconds"] != 1.1:
        raise AssertionError("attempt-3 pacing differs")
    if value["request"]["automatic_http_retries"] != 0:
        raise AssertionError("attempt-3 retry boundary differs")
    if value["validation_boundaries"]["content_type_allowlist_modified"] is not False:
        raise AssertionError("content-type contract changed")
    return value


def validate_ledger(repository: Path) -> None:
    ledger_path = repository / "config/shared_pa_statcast_sample_attempt_history_20260801_v2.json"
    if sha256_file(ledger_path) != LEDGER:
        raise AssertionError("successor ledger changed")
    value = json.loads(ledger_path.read_text(encoding="utf-8"))
    if [row["attempt_number"] for row in value["attempts"]] != [1, 2]:
        raise AssertionError("attempts 1 or 2 were erased or reset")
    if not all("PERMANENTLY_CONSUMED" in row["status"] for row in value["attempts"]):
        raise AssertionError("a consumed attempt became reusable")
    if value["remaining_attempts"] != [
        {"attempt_number": 3, "status": "UNUSED_UNAUTHORIZED"},
        {"attempt_number": 4, "status": "UNUSED_UNAUTHORIZED"},
    ]:
        raise AssertionError("remaining attempt state differs")
    if value["total_external_statcast_requests"] != 2:
        raise AssertionError("real external request accounting differs")
    if value["remaining_authorized_attempts"] != 0 or value["workflow_dispatch_capture_authorized"] is not False:
        raise AssertionError("preparation or preflight authorized capture")


def validate_workflow_mutations(text: str) -> None:
    required = (
        "pull_request:\n    branches:",
        "workflow_dispatch:\n    inputs:",
        "expected_authorization_sha256:",
        "capture._transport = blocked_transport",
        "with capture.network_denial_guard()",
        "attempt3_authority_verifier",
        "attempt3_history_validator",
        "reservation-03.json",
        '"prior_attempt_count"] == 2',
        "ACTIVE_AUTHORIZATION_SHA256",
        "shared_pa_statcast_sample_attempt_history_20260801_v2.json",
        "shared_pa_statcast_attempt_03_authority_package_20260801_v1.json",
        "if: ${{ always() && github.event_name == 'workflow_dispatch' }}",
        '"quarantined": is_failure',
        '"non_promotable": is_failure',
        '"raw_content_types_retained": raw_content_types',
        "scripts/verify_shared_pa_statcast_capture_v1.py",
        "working-directory: ${{ runner.temp }}/statcast-carrier-exec",
        "shared_pa_statcast_sample_2023-07-25_attempt-03_v1",
    )
    missing = [needle for needle in required if needle not in text]
    if missing:
        raise AssertionError(f"attempt-3 workflow contract missing: {missing}")
    if text.count("with capture.network_denial_guard()") < 2:
        raise AssertionError("both no-network barriers are required")
    if text.count("if: ${{ always() && github.event_name == 'workflow_dispatch' }}") < 3:
        raise AssertionError("failure publication and terminal preservation require always()")
    mutations = {
        "failure_publication_removed": text.replace(
            "if: ${{ always() && github.event_name == 'workflow_dispatch' }}",
            "if: ${{ github.event_name == 'workflow_dispatch' }}", 1),
        "attempt_history_protection_removed": text.replace("attempt3_history_validator", "unprotected_history", 1),
        "old_authorization_rejection_removed": text.replace("attempt3_authority_verifier", "stale_authority_verifier", 1),
        "quarantine_removed": text.replace('"quarantined": is_failure', '"quarantined": False', 1),
    }
    for name, mutated in mutations.items():
        try:
            validate_workflow_mutations(mutated)
        except AssertionError:
            continue
        raise AssertionError(f"focused mutation was not rejected: {name}")


def prepare_carrier(carrier_root: Path):
    os.environ["GIT_CONFIG_COUNT"] = "1"
    os.environ["GIT_CONFIG_KEY_0"] = "safe.directory"
    os.environ["GIT_CONFIG_VALUE_0"] = carrier_root.as_posix()
    active = subprocess.run(["git", "rev-parse", "HEAD"], cwd=carrier_root, check=True,
                            capture_output=True, text=True).stdout.strip()
    if active != CARRIER:
        raise AssertionError("test did not execute from the exact carrier")
    if not (carrier_root / "scripts/verify_shared_pa_statcast_capture_v1.py").is_file():
        raise AssertionError("carrier verifier is absent")
    sys.path.insert(0, str(carrier_root))
    import scripts.capture_shared_pa_statcast_source_v1 as capture  # type: ignore[import-not-found]
    from scripts.capture_direct_batter_pa_source_transport_v2 import CapturedResponse, RuntimeAuthorization  # type: ignore[import-not-found]
    from src.evaluation.shared_pa_statcast_historical_source_access_v1 import VerifiedStatcastHistoricalSourceAccess  # type: ignore[import-not-found]
    from src.evaluation.shared_pa_statcast_stage_a_v1 import synthetic_csv  # type: ignore[import-not-found]
    return capture, CapturedResponse, RuntimeAuthorization, VerifiedStatcastHistoricalSourceAccess, synthetic_csv


def verify_attempt3_capture(capture, root: Path, plan_path: Path, contract_path: Path, expected_digest: str) -> None:
    from src.data.shared_pa_statcast_source_v1 import validate_raw_receipt  # type: ignore[import-not-found]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if capture._manifest_digest(manifest) != expected_digest or manifest.get("observed_capture_digest") != expected_digest:
        raise AssertionError("attempt-3 capture digest differs")
    if capture._enumerate_files(root, exclude_manifest=True) != manifest.get("files"):
        raise AssertionError("attempt-3 capture file inventory differs")
    contract = capture.load_contract(contract_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    capture._validate_sample_plan(plan, contract, contract_path)
    context = json.loads((root / "capture_context.json").read_text(encoding="utf-8"))
    if context.get("prior_attempt_count") != 2 or context.get("attempt_history_sha256") != LEDGER:
        raise AssertionError("attempt-3 context accounting differs")
    request = plan["requests"][0]
    request_dir = root / request["request_id"]
    terminal = json.loads((request_dir / "terminal.json").read_text(encoding="utf-8"))
    if terminal.get("state") != "SUCCESS" or terminal.get("attempt") != 3:
        raise AssertionError("attempt-3 capture is not a terminal success")
    reservation = json.loads((request_dir / "reservation-03.json").read_text(encoding="utf-8"))
    result = json.loads((request_dir / "result-03.json").read_text(encoding="utf-8"))
    if reservation.get("attempt") != 3 or reservation.get("request") != request or result.get("attempt") != 3:
        raise AssertionError("attempt-3 journal identity differs")
    raw_path = request_dir / "response-attempt-03.csv"
    if sha256_file(raw_path) != result.get("sha256") or raw_path.stat().st_size != result.get("byte_count"):
        raise AssertionError("attempt-3 retained response differs")
    expected_files = {
        "capture_context.json", f"{request['request_id']}/terminal.json",
        f"{request['request_id']}/reservation-03.json", f"{request['request_id']}/result-03.json",
        f"{request['request_id']}/response-attempt-03.csv", f"{request['request_id']}/response.csv",
        f"{request['request_id']}/receipt.json",
    }
    if {row["path"] for row in manifest["files"]} != expected_files:
        raise AssertionError("attempt-3 authorized file set differs")
    raw = (request_dir / "response.csv").read_bytes()
    receipt = json.loads((request_dir / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("attempt_number") != 3:
        raise AssertionError("attempt-3 receipt identity differs")
    validate_raw_receipt(
        raw=raw, receipt=receipt, expected_request=request,
        contract_sha256=sha256_file(contract_path), parser_sha256=manifest["parser_sha256"],
        request_plan_sha256=sha256_file(plan_path),
    )
    certified = {int(item["game_pk"]): {
        "official_date": request["expected"]["official_date"], "home_team_id": item["home_team_id"],
        "away_team_id": item["away_team_id"], "home_team_code": item["home_team_code"],
        "away_team_code": item["away_team_code"],
    } for item in request["expected"]["certified_games"]}
    capture.parse_csv_bytes(raw, contract=contract, expected_date=request["expected"]["official_date"],
                            certified_games=certified)


def run_capture(carrier_root: Path, repository: Path, artifact_dir: Path, *, valid: bool) -> None:
    capture, CapturedResponse, RuntimeAuthorization, VerifiedAccess, synthetic_csv = prepare_carrier(carrier_root)
    plan = carrier_root / "config/shared_pa_statcast_source_preparation_v1/sample_request_plan.json"
    contract = carrier_root / "config/shared_pa_statcast_source_contract_v1.json"
    ledger = repository / "config/shared_pa_statcast_sample_attempt_history_20260801_v2.json"
    if sha256_file(plan) != PLAN or sha256_file(contract) != CONTRACT or capture.source_bundle_sha256() != BUNDLE:
        raise AssertionError("carrier source identity differs")
    output = artifact_dir.parent / ("synthetic-attempt-3-valid-capture" if valid else "synthetic-attempt-3-invalid-capture")
    if output.exists() or output.with_name(output.name + ".work").exists() or output.with_name(output.name + ".lock").exists():
        raise AssertionError("synthetic no-overwrite output must begin absent")
    plan_value = json.loads(plan.read_text(encoding="utf-8"))
    base_lines = synthetic_csv(json.loads(contract.read_text(encoding="utf-8"))).decode().splitlines()
    body_lines = [base_lines[0]]
    for game in plan_value["requests"][0]["expected"]["certified_games"]:
        for line in base_lines[1:]:
            body_lines.append(line.replace("2023-04-01", "2023-07-25")
                              .replace("700001", str(game["game_pk"]))
                              .replace("HOM", game["home_team_code"])
                              .replace("AWY", game["away_team_code"]))
    valid_body = ("\n".join(body_lines) + "\n").encode()
    body = valid_body if valid else b"pitch_type,game_date,game_pk\nFF,2023-07-25,717255\n"
    content_type = "text/csv; charset=utf-8" if valid else "application/json; charset=utf-8"
    headers = {"content-length": str(len(body)), "content-type": content_type,
               "date": "Tue, 25 Jul 2023 00:00:00 GMT"}
    calls: list[str] = []

    def synthetic_transport(url: str, _timeout: float, _maximum: int):
        calls.append(url)
        return CapturedResponse(200, body, headers, url, "2026-08-01T00:00:00.000000Z",
                                "2026-08-01T00:00:01.000000Z")

    access = VerifiedAccess(
        authorization_id="attempt-3-synthetic-no-network",
        authorization_file_sha256="a" * 64,
        carrier_commit=CARRIER,
        runtime_policy_sha256=RUNTIME_POLICY,
        source_bundle_sha256=BUNDLE,
        source_contract_sha256=CONTRACT,
        request_plan_sha256=PLAN,
        output_path=str(output),
        attempt_history_sha256=LEDGER,
        prior_attempts_consumed=2,
        remaining_lifetime_attempts=2,
    )
    original_history = capture._validate_attempt_history

    def protected_history(path, *, request_id, source_access, maximum_attempts):
        if Path(path) != ledger or sha256_file(ledger) != source_access.attempt_history_sha256:
            raise capture.CaptureError("successor ledger differs")
        if request_id != "statcast-2023-07-25" or maximum_attempts != 4:
            raise capture.CaptureError("attempt policy differs")
        validate_ledger(repository)
        return 2

    capture._validate_attempt_history = protected_history
    try:
        manifest = capture.capture(
            plan, contract, output,
            runtime_authorization=RuntimeAuthorization(RUNTIME_ATTESTATION, {}, "synthetic", RUNTIME_POLICY),
            source_access=access, carrier_commit=CARRIER, transport=synthetic_transport,
            attempt_history_path=ledger,
        )
    finally:
        capture._validate_attempt_history = original_history
    if len(calls) != 1 or manifest["prior_attempt_count"] != 2:
        raise AssertionError("synthetic attempt-3 transport/accounting differs")
    request_dir = output / "statcast-2023-07-25"
    reservation = json.loads((request_dir / "reservation-03.json").read_text(encoding="utf-8"))
    if reservation["attempt"] != 3:
        raise AssertionError("synthetic attempt 3 was not consumed")
    result = json.loads((request_dir / "result-03.json").read_text(encoding="utf-8"))
    if result["response_headers"]["content-type"] != content_type:
        raise AssertionError("exact synthetic Content-Type was not retained")
    if (request_dir / "response-attempt-03.csv").read_bytes() != body:
        raise AssertionError("synthetic response bytes were not retained")
    verifier = subprocess.run([
        sys.executable, "-I", "-B", "scripts/verify_shared_pa_statcast_capture_v1.py",
        "--capture", str(output), "--request-plan", str(plan), "--contract", str(contract),
        "--expected-capture-digest", manifest["observed_capture_digest"],
    ], cwd=carrier_root, capture_output=True, text=True)
    if valid:
        if verifier.returncode == 0:
            raise AssertionError("native carrier verifier unexpectedly ignored its attempt-1 journal assumption")
        verify_attempt3_capture(capture, output, plan, contract, manifest["observed_capture_digest"])
        if manifest["success_count"] != 1 or not (request_dir / "response.csv").is_file():
            raise AssertionError("synthetic valid response did not follow the valid publication path")
        package_status = "SYNTHETIC_VALID_SAMPLE_PUBLICATION"
        terminal_status = "SUCCESS"
    else:
        if manifest["failure_count"] != 1 or verifier.returncode == 0 or (request_dir / "response.csv").exists():
            raise AssertionError("synthetic disallowed response was promoted")
        failure = json.loads((request_dir / "validation-failure-03.json").read_text(encoding="utf-8"))
        if failure["message"] != "source response content type is not allowlisted":
            raise AssertionError("synthetic content-type rejection differs")
        package_status = "CAPTURE_QUARANTINED"
        terminal_status = "FAILURE"
    artifact_dir.mkdir(parents=True, exist_ok=False)
    shutil.copytree(output, artifact_dir / "capture")
    package = {
        "status": package_status,
        "invalid": not valid,
        "quarantined": not valid,
        "non_promotable": not valid,
        "source_qualified": False,
        "raw_content_type": content_type,
        "normalized_mime_type": content_type.split(";", 1)[0].strip().lower(),
        "raw_bytes_retained": True,
        "safe_response_headers_retained": True,
        "external_request_count": 0,
        "terminal_workflow_status_after_publication": terminal_status,
    }
    (artifact_dir / "package.json").write_text(json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--carrier-valid", type=Path, required=True)
    parser.add_argument("--carrier-invalid", type=Path, required=True)
    parser.add_argument("--artifact-valid", type=Path, required=True)
    parser.add_argument("--artifact-invalid", type=Path, required=True)
    args = parser.parse_args()
    repository = args.workflow.resolve().parents[2]
    workflow = args.workflow.resolve()
    text = workflow.read_text(encoding="utf-8")
    validate_workflow_mutations(text)
    validate_ledger(repository)
    validate_authority(repository, workflow, args.authorization.resolve())
    old = repository / "config/shared_pa_statcast_historical_source_access_authorization_20260731_v1.json"
    if sha256_file(old) != OLD_AUTHORIZATION:
        raise AssertionError("spent attempt-2 authorization identity differs")
    try:
        validate_authority(repository, workflow, old)
    except AssertionError:
        pass
    else:
        raise AssertionError("spent attempt-2 authorization could authorize attempt 3")
    if sha256_file(repository / "config/shared_pa_statcast_source_contract_v1.json") != CONTRACT:
        raise AssertionError("source contract or content-type allowlist changed")
    run_capture(args.carrier_valid.resolve(), repository, args.artifact_valid.resolve(), valid=True)
    for name in [key for key in sys.modules if key == "scripts" or key.startswith("scripts.") or key == "src" or key.startswith("src.")]:
        del sys.modules[name]
    run_capture(args.carrier_invalid.resolve(), repository, args.artifact_invalid.resolve(), valid=False)
    capture, *_ = prepare_carrier(args.carrier_invalid.resolve())
    try:
        with capture.network_denial_guard():
            socket.create_connection(("127.0.0.1", 9), timeout=0.01)
    except capture.CaptureError as exc:
        if "lower-layer network guard" not in str(exc):
            raise
    else:
        raise AssertionError("lower-level socket guard was ineffective")
    print("ATTEMPT_3_AUTHORITY_PACKAGE=PASS")
    print("APPLICATION_TRANSPORT_GUARD=PASS")
    print("LOWER_LEVEL_SOCKET_GUARD=PASS")
    print("OLD_AUTHORIZATION_REJECTION=PASS")
    print("ATTEMPT_HISTORY_PROTECTION=PASS")
    print("SYNTHETIC_VALID_PUBLICATION=PASS")
    print("SYNTHETIC_DISALLOWED_CONTENT_TYPE=PASS")
    print("FAILURE_ARTIFACT_PUBLICATION=PASS")
    print("TERMINAL_STATUS_PRESERVATION=PASS")
    print("REAL_EXTERNAL_REQUEST_COUNT=2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
