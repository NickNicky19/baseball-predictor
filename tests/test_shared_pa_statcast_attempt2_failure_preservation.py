#!/usr/bin/env python3
"""No-network regression for attempt-2 failure preservation."""
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


CARRIER_COMMIT = "74395e8552cf65701e526d4cba1f08a3e7f286d8"
OLD_ATTEMPT_HISTORY_SHA256 = "eb28fbfc18ba777f796abe1db9735579f99a06907d5de856d696d4de5db0aaf5"
SUCCESSOR_ATTEMPT_HISTORY_SHA256 = "456787efef26d3d438e1afe13d1eee949cd9866d2f8d0a2c5c74d9928a97572e"
SOURCE_CONTRACT_SHA256 = "7078857cc1815e5c8dcecd667a9b6d69079ef31c1fc68057473d1fb1dc9ceebd"
REQUEST_PLAN_SHA256 = "a40abd52a42ed36a49b6fc5d3d91d2202f57e7075a077f8f1abe3252df016994"
AUTHORIZATION_SHA256 = "11c19c423404f7e75b8308dc6feea818808af166b64ce7abc7140bc0f6e8425f"
RUNTIME_ATTESTATION_SHA256 = "903e566a99d048a575dbe075378fd8b80ace92bf5028c6ddcbb0d200fe509ae5"
RUNTIME_POLICY_SHA256 = "31b6b16063bfb0f8e1535d4475259caa10ff548423df793a9d79bd2616f5f9c3"
SOURCE_BUNDLE_SHA256 = "22a36802fb7530cc21fb0f38a7c6e85422088de25f435916cf3b53f74d7367a1"
EXPECTED_CHANGED_PATHS = [
    ".github/workflows/shared-pa-statcast-sample-capture-execution-v1.yml",
    "config/shared_pa_statcast_sample_attempt_history_20260801_v2.json",
    "reports/shared_pa_statcast_attempt_2_incident_repair_20260801_v1.json",
    "tests/test_shared_pa_statcast_attempt2_failure_preservation.py",
]
EXPECTED_CHANGED_PATHS_SHA256 = "00ca3639608e1fa6063c219e62a827eec26d7cc280cb2ad297d26c499abafdf7"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_workflow_text(text: str) -> None:
    finalizer_carrier_binding = (
        "      - name: Verify and finalize capture or quarantined failure package from carrier\n"
        "        if: ${{ always() && github.event_name == 'workflow_dispatch' }}\n"
        "        shell: bash\n"
        "        working-directory: ${{ runner.temp }}/statcast-carrier-exec"
    )
    required = (
        "pull_request:\n    branches:",
        "workflow_dispatch:\n    inputs:",
        "github.event_name == 'pull_request' && 'preflight'",
        "github.event_name == 'workflow_dispatch' && 'capture'",
        "capture._transport = blocked_transport",
        "with capture.network_denial_guard()",
        "ATTEMPTS_3_AND_4_UNAUTHORIZED",
        "CAPTURE_AUTHORIZATION_DENIED_ATTEMPTS_3_AND_4_UNAUTHORIZED",
        "shared_pa_statcast_sample_attempt_history_20260801_v2.json",
        SUCCESSOR_ATTEMPT_HISTORY_SHA256,
        "scripts/verify_shared_pa_statcast_capture_v1.py",
        "working-directory: ${{ runner.temp }}/statcast-carrier-exec",
        "if: ${{ always() && github.event_name == 'workflow_dispatch' }}",
        "CAPTURE_QUARANTINED",
        '"invalid": is_failure',
        '"quarantined": is_failure',
        '"non_promotable": is_failure',
        "capture-command-status.json",
        "capture-validation-exit-code",
        "carrier-verifier-exit-code",
        "Report preserved terminal status after artifact upload",
        finalizer_carrier_binding,
        "test ! -e \"$output\"",
        "test ! -e \"$work\"",
        "test ! -e \"$lock\"",
    )
    missing = [needle for needle in required if needle not in text]
    if missing:
        raise AssertionError(f"workflow repair contract missing: {missing}")
    if text.count("with capture.network_denial_guard()") < 2:
        raise AssertionError("application and lower-level no-network barriers are both required")
    if text.count("if: ${{ always() && github.event_name == 'workflow_dispatch' }}") < 3:
        raise AssertionError("finalization, failure upload, and terminal reporting must all use always()")
    if "--authorization \"$AUTHORIZATION\" --attempt-history" in text:
        raise AssertionError("carrier verifier was called with unsupported arguments")
    if "working-directory: ${{ github.workspace }}" in text:
        raise AssertionError("carrier-bound capture verification must not execute from corrected main")


def mutation_checks(original: str) -> None:
    finalizer_carrier_binding = (
        "      - name: Verify and finalize capture or quarantined failure package from carrier\n"
        "        if: ${{ always() && github.event_name == 'workflow_dispatch' }}\n"
        "        shell: bash\n"
        "        working-directory: ${{ runner.temp }}/statcast-carrier-exec"
    )
    finalizer_main_binding = finalizer_carrier_binding.replace(
        "working-directory: ${{ runner.temp }}/statcast-carrier-exec",
        "working-directory: ${{ github.workspace }}",
    )
    mutations = {
        "failure_publication_always_removed": original.replace(
            "if: ${{ always() && github.event_name == 'workflow_dispatch' }}",
            "if: ${{ github.event_name == 'workflow_dispatch' }}",
            1,
        ),
        "verification_redirected_to_main": original.replace(finalizer_carrier_binding, finalizer_main_binding, 1),
        "quarantine_marker_removed": original.replace(
            '              "quarantined": is_failure,',
            '              "quarantined": False,',
            1,
        ),
        "non_promotion_marker_removed": original.replace(
            '              "non_promotable": is_failure,',
            '              "non_promotable": False,',
            1,
        ),
        "successor_ledger_removed": original.replace(
            "shared_pa_statcast_sample_attempt_history_20260801_v2.json",
            "shared_pa_statcast_sample_attempt_history_20260731_v1.json",
        ),
        "attempt_3_gate_removed": original.replace(
            "CAPTURE_AUTHORIZATION_DENIED_ATTEMPTS_3_AND_4_UNAUTHORIZED",
            "CAPTURE_AUTHORIZATION_GRANTED",
            1,
        ),
    }
    for name, mutated in mutations.items():
        try:
            validate_workflow_text(mutated)
        except AssertionError:
            continue
        raise AssertionError(f"focused mutation was not rejected: {name}")


def validate_attempt_ledgers(repository_root: Path) -> None:
    predecessor = repository_root / "config/shared_pa_statcast_sample_attempt_history_20260731_v1.json"
    successor = repository_root / "config/shared_pa_statcast_sample_attempt_history_20260801_v2.json"
    if sha256_file(predecessor) != OLD_ATTEMPT_HISTORY_SHA256:
        raise AssertionError("attempt-1 incident record was overwritten or reinterpreted")
    if sha256_file(successor) != SUCCESSOR_ATTEMPT_HISTORY_SHA256:
        raise AssertionError("successor attempt ledger differs from its workflow binding")
    value = json.loads(successor.read_text(encoding="utf-8"))
    if [row["attempt_number"] for row in value["attempts"]] != [1, 2]:
        raise AssertionError("attempts 1 or 2 were erased, reset, or reordered")
    if any("PERMANENTLY_CONSUMED" not in row["status"] for row in value["attempts"]):
        raise AssertionError("consumed attempts became reusable")
    if value["total_external_statcast_requests"] != 2 or value["consumed_lifetime_attempts"] != 2:
        raise AssertionError("external request or attempt accounting was reset")
    if value["remaining_attempts"] != [
        {"attempt_number": 3, "status": "UNUSED_UNAUTHORIZED"},
        {"attempt_number": 4, "status": "UNUSED_UNAUTHORIZED"},
    ]:
        raise AssertionError("remaining attempt authorization state changed")
    if value["remaining_authorized_attempts"] != 0 or value["workflow_dispatch_capture_authorized"] is not False:
        raise AssertionError("attempt 3 became executable during pull-request preflight")
    if value["attempts"][1]["response_headers"]["retention_status"] != "ATTEMPT_2_CONTENT_TYPE_NOT_RETAINED":
        raise AssertionError("missing attempt-2 content type was reconstructed")


def validate_changed_path_manifest(repository_root: Path) -> None:
    report = json.loads(
        (repository_root / "reports/shared_pa_statcast_attempt_2_incident_repair_20260801_v1.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = "".join(path + "\n" for path in sorted(EXPECTED_CHANGED_PATHS)).encode("utf-8")
    if hashlib.sha256(manifest).hexdigest() != EXPECTED_CHANGED_PATHS_SHA256:
        raise AssertionError("declared changed-path manifest digest is inconsistent")
    if report["changed_path_manifest"] != sorted(EXPECTED_CHANGED_PATHS):
        raise AssertionError("machine-readable incident report path manifest differs")
    if report["changed_path_manifest_sha256"] != EXPECTED_CHANGED_PATHS_SHA256:
        raise AssertionError("machine-readable incident report path-manifest digest differs")


def run_synthetic_disallowed_content_type(carrier_root: Path, artifact_dir: Path) -> None:
    os.environ["GIT_CONFIG_COUNT"] = "1"
    os.environ["GIT_CONFIG_KEY_0"] = "safe.directory"
    os.environ["GIT_CONFIG_VALUE_0"] = carrier_root.as_posix()
    active = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=carrier_root, check=True, capture_output=True, text=True
    ).stdout.strip()
    if active != CARRIER_COMMIT:
        raise AssertionError("synthetic regression is not running from the exact carrier")
    verifier = carrier_root / "scripts/verify_shared_pa_statcast_capture_v1.py"
    if not verifier.is_file():
        raise AssertionError("carrier verifier is absent")

    sys.path.insert(0, str(carrier_root))
    import scripts.capture_shared_pa_statcast_source_v1 as capture  # type: ignore[import-not-found]
    from scripts.capture_direct_batter_pa_source_transport_v2 import (  # type: ignore[import-not-found]
        CapturedResponse,
        RuntimeAuthorization,
    )
    from src.evaluation.shared_pa_statcast_historical_source_access_v1 import (  # type: ignore[import-not-found]
        VerifiedStatcastHistoricalSourceAccess,
    )

    plan = carrier_root / "config/shared_pa_statcast_source_preparation_v1/sample_request_plan.json"
    contract = carrier_root / "config/shared_pa_statcast_source_contract_v1.json"
    history = carrier_root / "config/shared_pa_statcast_sample_attempt_history_20260731_v1.json"
    if sha256_file(plan) != REQUEST_PLAN_SHA256 or sha256_file(contract) != SOURCE_CONTRACT_SHA256:
        raise AssertionError("synthetic regression carrier inputs differ")
    if sha256_file(history) != OLD_ATTEMPT_HISTORY_SHA256:
        raise AssertionError("synthetic regression attempt history differs")
    if capture.source_bundle_sha256() != SOURCE_BUNDLE_SHA256:
        raise AssertionError("synthetic regression source bundle differs")

    output = carrier_root / "data/source/shared_pa_statcast_sample_2023-07-25_v1"
    if output.exists() or output.with_name(output.name + ".work").exists() or output.with_name(output.name + ".lock").exists():
        raise AssertionError("synthetic regression requires a new no-overwrite output path")
    body = b"pitch_type,game_date,game_pk\nFF,2023-07-25,717255\n"
    observed_headers = {
        "content-length": str(len(body)),
        "content-type": "application/json; charset=utf-8",
        "date": "Tue, 25 Jul 2023 00:00:00 GMT",
    }
    transport_calls: list[str] = []
    parser_calls: list[bool] = []

    def synthetic_transport(full_url: str, _timeout: float, _maximum: int) -> CapturedResponse:
        transport_calls.append(full_url)
        requested = capture._utc()
        return CapturedResponse(200, body, observed_headers, full_url, requested, capture._utc())

    original_parser = capture.parse_csv_bytes

    def parser_must_not_start(*_args, **_kwargs):
        parser_calls.append(True)
        raise AssertionError("body parser started after disallowed content type")

    capture.parse_csv_bytes = parser_must_not_start
    try:
        manifest = capture.capture(
            plan,
            contract,
            output,
            runtime_authorization=RuntimeAuthorization(
                attestation_sha256=RUNTIME_ATTESTATION_SHA256,
                attestation={},
                source_sha256="synthetic-no-network",
                policy_sha256=RUNTIME_POLICY_SHA256,
            ),
            source_access=VerifiedStatcastHistoricalSourceAccess(
                authorization_id="synthetic-no-network-regression",
                authorization_file_sha256=AUTHORIZATION_SHA256,
                carrier_commit=CARRIER_COMMIT,
                runtime_policy_sha256=RUNTIME_POLICY_SHA256,
                source_bundle_sha256=SOURCE_BUNDLE_SHA256,
                source_contract_sha256=SOURCE_CONTRACT_SHA256,
                request_plan_sha256=REQUEST_PLAN_SHA256,
                output_path="data/source/shared_pa_statcast_sample_2023-07-25_v1",
                attempt_history_sha256=OLD_ATTEMPT_HISTORY_SHA256,
                prior_attempts_consumed=1,
                remaining_lifetime_attempts=3,
            ),
            carrier_commit=CARRIER_COMMIT,
            transport=synthetic_transport,
            attempt_history_path=history,
        )
    finally:
        capture.parse_csv_bytes = original_parser

    if len(transport_calls) != 1 or parser_calls:
        raise AssertionError("synthetic disallowed-content-type boundary was not preserved")
    if manifest["success_count"] != 0 or manifest["failure_count"] != 1:
        raise AssertionError("synthetic invalid response became a valid release")
    request_dir = output / "statcast-2023-07-25"
    reservation = json.loads((request_dir / "reservation-02.json").read_text(encoding="utf-8"))
    result = json.loads((request_dir / "result-02.json").read_text(encoding="utf-8"))
    failure = json.loads((request_dir / "validation-failure-02.json").read_text(encoding="utf-8"))
    raw_path = request_dir / "response-attempt-02.csv"
    if reservation["attempt"] != 2 or result["attempt"] != 2:
        raise AssertionError("synthetic attempt 2 was not consumed")
    if raw_path.read_bytes() != body or result["response_headers"] != observed_headers:
        raise AssertionError("synthetic raw bytes or safe response headers were not preserved")
    if failure != {"error_type": "CaptureError", "message": "source response content type is not allowlisted"}:
        raise AssertionError("synthetic validation failure differs")
    if (request_dir / "response.csv").exists():
        raise AssertionError("synthetic invalid response produced a valid source release")

    verifier_result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "scripts/verify_shared_pa_statcast_capture_v1.py",
            "--capture",
            str(output),
            "--request-plan",
            str(plan),
            "--contract",
            str(contract),
            "--expected-capture-digest",
            manifest["observed_capture_digest"],
        ],
        cwd=carrier_root,
        capture_output=True,
        text=True,
    )
    if verifier_result.returncode == 0:
        raise AssertionError("carrier verifier accepted a validation-failed capture")
    if "can't open file" in verifier_result.stderr or "cannot find" in verifier_result.stderr.lower():
        raise AssertionError("carrier verifier did not actually execute")

    try:
        with capture.network_denial_guard():
            socket.create_connection(("127.0.0.1", 9), timeout=0.01)
    except capture.CaptureError as exc:
        if "lower-layer network guard" not in str(exc):
            raise
    else:
        raise AssertionError("lower-level socket/network guard was ineffective")

    artifact_dir.mkdir(parents=True, exist_ok=False)
    shutil.copytree(output, artifact_dir / "quarantined-capture")
    package = {
        "schema_version": "shared-pa-statcast-synthetic-failure-package-v1",
        "status": "CAPTURE_QUARANTINED",
        "invalid": True,
        "quarantined": True,
        "non_promotable": True,
        "capture_command_exit_code": 0,
        "capture_validation_exit_code": 1,
        "carrier_verifier_exit_code": verifier_result.returncode,
        "terminal_workflow_status_after_publication": "FAILURE",
        "raw_bytes_retained": True,
        "safe_response_headers_retained": True,
        "external_request_count": 0,
        "excluded_from": ["source", "feature", "model", "evaluation"],
    }
    (artifact_dir / "failure-package.json").write_text(
        json.dumps(package, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    if not (artifact_dir / "quarantined-capture/statcast-2023-07-25/response-attempt-02.csv").is_file():
        raise AssertionError("downloadable synthetic failure artifact omitted response bytes")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--carrier-root", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    repository_root = args.workflow.resolve().parents[2]
    workflow_text = args.workflow.read_text(encoding="utf-8")
    validate_workflow_text(workflow_text)
    mutation_checks(workflow_text)
    validate_attempt_ledgers(repository_root)
    validate_changed_path_manifest(repository_root)
    if sha256_file(repository_root / "config/shared_pa_statcast_source_contract_v1.json") != SOURCE_CONTRACT_SHA256:
        raise AssertionError("source contract or content-type allowlist changed")
    if (repository_root / "scripts/verify_shared_pa_statcast_capture_v1.py").exists():
        raise AssertionError("regression requires verifier to be absent from corrected main")
    run_synthetic_disallowed_content_type(args.carrier_root.resolve(), args.artifact_dir.resolve())
    print("ATTEMPT_HISTORY_REGRESSION=PASS")
    print("NO_NETWORK_BARRIERS=PASS")
    print("SYNTHETIC_DISALLOWED_CONTENT_TYPE=PASS")
    print("CARRIER_VERIFIER_PATH=PASS")
    print("FAILURE_ARTIFACT_PUBLICATION=PASS")
    print("TERMINAL_FAILURE_PRESERVATION=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
