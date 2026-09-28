"""Prepared clean-room June 28 reproduction; inert until separately authorized.

This standard-library implementation imports neither capture nor carrier verifier code.
It reads an exact ZIP plus separately retained receipts and writes one new report.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
import platform
from pathlib import Path, PurePosixPath
import socket
import subprocess
import sys
from typing import Any, Iterator
import zipfile


class ReproductionError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_object_bytes(value: bytes, label: str) -> dict[str, Any]:
    parsed = json.loads(value.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ReproductionError(f"JSON object required: {label}")
    return parsed


def load_object(path: Path) -> dict[str, Any]:
    return load_object_bytes(path.read_bytes(), str(path))


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def finite(value: str) -> float | None:
    if value == "":
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ReproductionError("non-finite numeric value")
    return parsed


@contextmanager
def no_network() -> Iterator[None]:
    old_socket, old_connection = socket.socket, socket.create_connection
    def blocked(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("independent reproduction network guard blocked networking")
    socket.socket = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket, socket.create_connection = old_socket, old_connection  # type: ignore[assignment]


def verify_dependencies(repository: Path, authority: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for relative, expected in authority["immutable_dependencies"].items():
        path = repository / relative
        if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
            failures.append(f"immutable_dependency:{relative}")
    runtime_path = repository / "config/shared_pa_statcast_confirmation_runtime_authority_v1.json"
    if runtime_path.is_file():
        runtime = load_object(runtime_path)
        expected_bundle = authority["bound_composite_identities"]["source_bundle_sha256"]
        if runtime.get("source_bundle", {}).get("canonical_path_hash_map_sha256") != expected_bundle:
            failures.append("source_bundle_identity")
    return failures


def verify_execution_authorization(
    execution: dict[str, Any], repository: Path, authority_path: Path,
    artifact_path: Path, receipt_paths: dict[str, Path], output_path: Path,
    expected_authorization_sha256: str, execution_authorization_path: Path,
    now_utc: datetime | None = None,
) -> list[str]:
    failures: list[str] = []
    if sha256_file(execution_authorization_path) != expected_authorization_sha256:
        failures.append("execution_authorization_external_sha256")
    if execution.get("schema_version") != "shared-pa-statcast-june28-independent-reproduction-execution-authorization-v1":
        failures.append("execution_authorization_schema")
    if execution.get("status") != "AUTHORIZED" or execution.get("execution_authorized") is not True:
        failures.append("execution_authorization_status")
    if execution.get("repository") != "NickNicky19/baseball-predictor":
        failures.append("execution_authorization_repository")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        clean = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repository,
            check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        failures.append("execution_repository_unverifiable")
        head, clean = "", "unknown"
    if execution.get("exact_execution_commit") != head or clean:
        failures.append("execution_repository_identity")
    if execution.get("authority_package_sha256") != sha256_file(authority_path):
        failures.append("execution_authority_package_sha256")
    script_path = Path(__file__).resolve()
    if execution.get("reproduction_verifier_sha256") != sha256_file(script_path):
        failures.append("execution_verifier_sha256")
    runtime = execution.get("python_runtime", {})
    if runtime.get("implementation") != platform.python_implementation() or runtime.get("version") != platform.python_version():
        failures.append("execution_python_runtime")
    if not (
        sys.flags.isolated and sys.flags.no_user_site and sys.flags.dont_write_bytecode
        and getattr(sys.flags, "safe_path", False)
    ):
        failures.append("execution_python_isolation_flags")
    if execution.get("network_disabled") is not True:
        failures.append("execution_network_boundary")
    if execution.get("artifact_zip_sha256") != sha256_file(artifact_path):
        failures.append("execution_artifact_sha256")
    binding_names = {
        "artifact_retrieval_receipt": "artifact_retrieval_receipt_sha256",
        "terminal_run_receipt": "post_terminal_run_receipt_sha256",
        "successor_ledger": "successor_attempt_ledger_sha256",
        "terminal_adjudication": "terminal_adjudication_sha256",
        "official_pa_receipt": "official_pa_denominator_receipt_sha256",
        "official_pa_source": "official_pa_source_sha256",
        "official_pa_release": "official_pa_certified_release_sha256",
        "official_pa_verifier": "official_pa_independent_verifier_sha256",
    }
    for name, field in binding_names.items():
        if execution.get(field) != sha256_file(receipt_paths[name]):
            failures.append(f"execution_input_sha256:{name}")
    try:
        relative_output = output_path.resolve().relative_to(repository.resolve()).as_posix()
    except ValueError:
        failures.append("execution_output_outside_repository")
        relative_output = ""
    if execution.get("output_path") != relative_output:
        failures.append("execution_output_path")
    all_inputs = [authority_path, artifact_path, *receipt_paths.values()]
    if any(path.is_symlink() for path in all_inputs):
        failures.append("symlinked_input")
    resolved_inputs = {path.resolve() for path in all_inputs}
    if output_path.resolve() in resolved_inputs or output_path.parent.is_symlink():
        failures.append("input_output_alias_or_symlink")
    if not execution.get("authorization_id") or not execution.get("authorized_at_utc") or not execution.get("valid_from_utc") or not execution.get("expires_at_utc"):
        failures.append("execution_authorization_identity_or_time")
    else:
        try:
            authorized = parse_utc(execution["authorized_at_utc"])
            valid_from = parse_utc(execution["valid_from_utc"])
            expires = parse_utc(execution["expires_at_utc"])
            observed = now_utc or datetime.now(timezone.utc)
            if not authorized < valid_from < expires:
                failures.append("execution_authorization_time_order")
            if observed < valid_from:
                failures.append("execution_authorization_not_yet_valid")
            if observed >= expires:
                failures.append("execution_authorization_expired")
        except (TypeError, ValueError):
            failures.append("execution_authorization_time_parse")
    return failures


def verify_external_receipts(authority: dict[str, Any], retrieval: dict[str, Any], terminal: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    artifact = authority["artifact"]
    expected_retrieval = {
        "schema_version": "shared-pa-statcast-github-artifact-retrieval-receipt-v1",
        "artifact_id": artifact["id"], "artifact_name": artifact["name"],
        "workflow_run_id": artifact["workflow_run_id"], "expired": False,
        "expires_at_utc": artifact["expires_at_utc"], "zip_byte_count": artifact["zip_byte_count"],
        "zip_sha256": artifact["zip_sha256"],
    }
    for key, expected in expected_retrieval.items():
        if retrieval.get(key) != expected:
            failures.append(f"artifact_retrieval_receipt:{key}")
    if retrieval.get("repository") != "NickNicky19/baseball-predictor" or not retrieval.get("retrieved_at_utc") or not retrieval.get("source_url_identity"):
        failures.append("artifact_retrieval_receipt:provenance")
    if terminal.get("schema_version") != "shared-pa-statcast-github-terminal-run-receipt-v1":
        failures.append("terminal_run_schema")
    if terminal.get("repository") != "NickNicky19/baseball-predictor" or terminal.get("workflow_run_id") != 30846344146 or terminal.get("run_attempt") != 1:
        failures.append("terminal_run_identity")
    if terminal.get("dispatch_commit") != artifact["dispatch_commit"] or terminal.get("conclusion") != "failure" or terminal.get("event") != "workflow_dispatch":
        failures.append("terminal_run_state")
    if terminal.get("actor_login") != "NickNicky19" or terminal.get("actor_id") != 208912933:
        failures.append("terminal_run_actor")
    if not all(terminal.get(key) for key in ("created_at_utc", "started_at_utc", "updated_at_utc", "completed_at_utc")):
        failures.append("terminal_completion_timestamp")
    else:
        try:
            if not (parse_utc(terminal["created_at_utc"]) <= parse_utc(terminal["started_at_utc"]) <= parse_utc(terminal["completed_at_utc"]) <= parse_utc(terminal["updated_at_utc"])):
                failures.append("terminal_timestamp_order")
        except (TypeError, ValueError):
            failures.append("terminal_timestamp_parse")
    return failures


def verify_successor_ledger(ledger: dict[str, Any], authority: dict[str, Any], repository: Path) -> list[str]:
    failures: list[str] = []
    expected_top = {
        "schema_version", "predecessor_path", "predecessor_sha256", "predecessor_document",
        "old_july_attempt_history_document", "total_real_statcast_requests",
        "confirmation_attempt_1", "append_only", "attempt_reset_or_reuse_allowed",
    }
    if set(ledger) != expected_top:
        failures.append("successor_ledger_shape")
    if ledger.get("schema_version") != "shared-pa-statcast-confirmation-sample-2023-06-28-attempt-history-v2":
        failures.append("successor_ledger_schema")
    if ledger.get("predecessor_path") != "config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json" or ledger.get("predecessor_sha256") != authority["immutable_dependencies"]["config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json"]:
        failures.append("successor_ledger_predecessor")
    predecessor = load_object(repository / ledger.get("predecessor_path", "missing"))
    if ledger.get("predecessor_document") != predecessor:
        failures.append("successor_predecessor_document")
    july_path = predecessor.get("old_july_25_attempt_history", {}).get("path", "")
    july_sha = predecessor.get("old_july_25_attempt_history", {}).get("sha256")
    if not july_path or sha256_file(repository / july_path) != july_sha:
        failures.append("successor_july_predecessor_identity")
        july_document = {}
    else:
        july_document = load_object(repository / july_path)
    if ledger.get("old_july_attempt_history_document") != july_document:
        failures.append("successor_july_predecessor_document")
    if ledger.get("total_real_statcast_requests") != 4:
        failures.append("successor_total_requests")
    july_attempts = july_document.get("attempts", [])
    if [item.get("attempt_number") for item in july_attempts] != [1, 2, 3] or not all("PERMANENTLY_CONSUMED" in item.get("status", "") for item in july_attempts):
        failures.append("successor_july_consumed_state")
    if july_document.get("remaining_attempts") != [{"attempt_number": 4, "status": "UNUSED_UNAUTHORIZED"}]:
        failures.append("successor_old_july_attempt4")
    confirmation = ledger.get("confirmation_attempt_1", {})
    expected_confirmation_keys = {
        "status", "workflow_run_id", "run_attempt", "artifact_id", "artifact_zip_sha256",
        "external_request_count", "automatic_http_retry_count", "replacement_request_count",
        "workflow_rerun_count", "source_qualified", "promotable",
        "reservation_timestamp_utc", "consumed_timestamp_utc",
    }
    if set(confirmation) != expected_confirmation_keys:
        failures.append("successor_confirmation_shape")
    expected = {
        "status": "CONSUMED", "workflow_run_id": 30846344146, "run_attempt": 1,
        "artifact_id": authority["artifact"]["id"], "artifact_zip_sha256": authority["artifact"]["zip_sha256"],
        "external_request_count": 1, "automatic_http_retry_count": 0,
        "replacement_request_count": 0, "workflow_rerun_count": 0,
        "source_qualified": False, "promotable": False,
    }
    for key, expected_value in expected.items():
        if confirmation.get(key) != expected_value:
            failures.append(f"successor_confirmation:{key}")
    if not confirmation.get("reservation_timestamp_utc") or not confirmation.get("consumed_timestamp_utc"):
        failures.append("successor_confirmation_timestamps")
    if ledger.get("append_only") is not True or ledger.get("attempt_reset_or_reuse_allowed") is not False:
        failures.append("successor_append_only")
    return failures


def verify_terminal_adjudication(adjudication: dict[str, Any], authority: dict[str, Any], ledger_sha256: str) -> list[str]:
    failures: list[str] = []
    expected = {
        "schema_version": "shared-pa-statcast-june28-confirmation-terminal-adjudication-v1",
        "workflow_run_id": 30846344146,
        "run_attempt": 1,
        "dispatch_commit": authority["artifact"]["dispatch_commit"],
        "artifact_id": authority["artifact"]["id"],
        "artifact_zip_sha256": authority["artifact"]["zip_sha256"],
        "raw_response_sha256": authority["artifact"]["raw_sha256"],
        "raw_response_byte_count": authority["artifact"]["raw_byte_count"],
        "raw_content_type": "application/download; charset=utf-8",
        "normalized_mime": "application/download",
        "reservation_sha256": "ca327f43c4026a66620ce2b2a4379b2e3496138d72c61ab0b6c42cc8e1d1d553",
        "request_receipt_sha256": "7be672c8c77ca4156abca3ea5887e3ddf6d37cbd2108f8159e44cadd62f03424",
        "result_record_sha256": "59d74a1315ae9f6a917c86974d59f9cf264659b1dfd2d605c939c564e3ca3a65",
        "validation_record_sha256": "02f6be82227f0645b5d074ab7aae66070edf2e6081483efd0120f53a72087868",
        "capture_manifest_sha256": "b784cdd552d2c16a7f69469d9a3649e66e259014311128cf1e75421f90b150f7",
        "successor_attempt_ledger_sha256": ledger_sha256,
        "request_started_at_utc": "2026-08-03T19:34:48.605410Z",
        "response_observed_at_utc": "2026-08-03T19:34:55.347057Z",
        "external_request_count": 1,
        "automatic_http_retry_count": 0,
        "replacement_request_count": 0,
        "workflow_rerun_count": 0,
        "technical_validation": "PASS",
        "source_qualified": False,
        "promotable": False,
        "confirmation_attempt_1": "CONSUMED",
        "total_real_statcast_requests": 4,
    }
    for key, expected_value in expected.items():
        if adjudication.get(key) != expected_value:
            failures.append(f"terminal_adjudication:{key}")
    if set(adjudication) != set(expected) | {"terminal_status"}:
        failures.append("terminal_adjudication_shape")
    if adjudication.get("terminal_status") != "CONFIRMATION_PENDING_SEPARATE_DURABLE_RETENTION_AND_INDEPENDENT_REPRODUCTION":
        failures.append("terminal_adjudication:terminal_status")
    return failures


def verify_official_pa_receipt(
    receipt: dict[str, Any], source: dict[str, Any], expected_games: list[dict[str, Any]],
    source_sha256: str, certified_release_sha256: str, independent_verifier_sha256: str,
) -> tuple[int | None, list[str]]:
    failures: list[str] = []
    if receipt.get("schema_version") != "shared-pa-statcast-june28-certified-official-pa-denominator-receipt-v1":
        failures.append("official_pa_schema")
    if receipt.get("official_date") != "2023-06-28" or receipt.get("derivation_uses_statcast_distinct_at_bat_number") is not False:
        failures.append("official_pa_source_method")
    if receipt.get("certified_source_release_sha256") != certified_release_sha256 or receipt.get("independent_verifier_sha256") != independent_verifier_sha256:
        failures.append("official_pa_provenance")
    if source.get("schema_version") != "shared-pa-statcast-june28-certified-official-pa-source-v1":
        failures.append("official_pa_source_schema")
    if receipt.get("official_pa_source_sha256") != source_sha256:
        failures.append("official_pa_source_sha256")
    if source.get("official_date") != "2023-06-28" or source.get("certified_source_release_sha256") != receipt.get("certified_source_release_sha256"):
        failures.append("official_pa_source_identity")
    components = receipt.get("per_game", [])
    source_components = source.get("per_game", [])
    if not isinstance(components, list) or len(components) != 15 or not isinstance(source_components, list) or len(source_components) != 15:
        failures.append("official_pa_components")
        return None, failures
    expected_ids = [item["game_pk"] for item in expected_games]
    games = [item.get("game_pk") for item in components]
    source_games = [item.get("game_pk") for item in source_components]
    if games != expected_ids or source_games != expected_ids:
        failures.append("official_pa_game_components")
        return None, failures
    recomputed: list[dict[str, int]] = []
    for expected, item in zip(expected_games, source_components, strict=True):
        required = {"game_pk", "home_team_id", "away_team_id", "home_plate_appearances", "away_plate_appearances"}
        if set(item) != required or item.get("home_team_id") != expected["home_team_id"] or item.get("away_team_id") != expected["away_team_id"]:
            failures.append("official_pa_team_identity")
            continue
        home, away = item.get("home_plate_appearances"), item.get("away_plate_appearances")
        if type(home) is not int or type(away) is not int or min(home, away) <= 0:
            failures.append("official_pa_source_components")
            continue
        recomputed.append({"game_pk": item["game_pk"], "official_pa_count": home + away})
    if components != recomputed:
        failures.append("official_pa_receipt_not_recomputed")
    total = sum(item["official_pa_count"] for item in recomputed)
    if receipt.get("official_pa_count") != total:
        failures.append("official_pa_sum")
    return total, failures


def verify_embedded_terminal_evidence(members: dict[str, bytes], authority: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    terminal_root = "confirmation-terminal-evidence/"
    try:
        capture_code = members[terminal_root + "capture-exit-code.txt"]
        verifier_code = members[terminal_root + "independent-verifier-exit-code.txt"]
        gate = load_object_bytes(members[terminal_root + "human-execution-authorization-gate.json"], "authorization gate")
        retention = load_object_bytes(members[terminal_root + "attempt3-retention-gate.json"], "attempt3 retention gate")
        branch = load_object_bytes(members[terminal_root + "default-branch-head.json"], "default branch")
        actor = load_object_bytes(members[terminal_root + "dispatch-actor.json"], "dispatch actor")
        finalization = load_object_bytes(members[terminal_root + "failure-package.json"], "finalization")
        authorization_id = members[terminal_root + "authorization-id.txt"].decode("utf-8").strip()
        runs = load_object_bytes(members[terminal_root + "confirmation-runs.json"], "confirmation runs")
        artifacts = load_object_bytes(members[terminal_root + "confirmation-artifacts.json"], "confirmation artifacts")
        embedded_verifier = load_object_bytes(members["june28-confirmation-carrier/data/source/shared_pa_statcast_v2_confirmation_2023-06-28_v1/independent-verifier.json"], "embedded verifier")
        stdout_verifier = load_object_bytes(members[terminal_root + "independent-verifier.stdout.txt"], "embedded verifier stdout")
        stderr_verifier = members[terminal_root + "independent-verifier.stderr.txt"]
    except KeyError as exc:
        return [f"terminal_member_missing:{exc}"]
    if capture_code != b"0\n" or verifier_code != b"0\n":
        failures.append("terminal_exit_codes")
    if authorization_id != "june28-a1-209b664-b96dbba2067789d13c4033af438e87a1":
        failures.append("terminal_authorization_id")
    expected_gate = {
        "status": "PASS", "authorization_id": "june28-a1-209b664-b96dbba2067789d13c4033af438e87a1",
        "dispatch_commit": authority["artifact"]["dispatch_commit"],
        "authorized_actor_login": "NickNicky19", "authorized_actor_numeric_user_id": 208912933,
        "workflow_sha256": "4367dc0bb530a3664aa30155a5a73654d13465acb3bcd924fce8d7d0abb98480",
        "single_use_snapshot_passed": True,
    }
    for key, expected in expected_gate.items():
        if gate.get(key) != expected:
            failures.append(f"authorization_gate:{key}")
    if retention.get("status") != "PASS" or retention.get("artifact_id") != 8826086488 or retention.get("zip_verified") is not True or retention.get("raw_verified") is not True:
        failures.append("attempt3_retention_gate")
    if branch.get("object", {}).get("sha") != authority["artifact"]["dispatch_commit"]:
        failures.append("dispatch_branch_head")
    if actor.get("login") != "NickNicky19" or actor.get("id") != 208912933:
        failures.append("dispatch_actor")
    expected_final = {
        "capture_exit_code": 0, "independent_verifier_exit_code": 0,
        "durable_retention_verified": False, "independent_reproduction_finalized": False,
        "invalid": False, "quarantined": False, "non_promotable": True,
        "source_qualified": False, "terminal_status_must_fail_after_publication": True,
    }
    for key, expected in expected_final.items():
        if finalization.get(key) != expected:
            failures.append(f"finalization:{key}")
    matching_runs = [item for item in runs.get("workflow_runs", []) if item.get("id") == 30846344146]
    if len(matching_runs) != 1 or matching_runs[0].get("event") != "workflow_dispatch" or matching_runs[0].get("head_sha") != authority["artifact"]["dispatch_commit"]:
        failures.append("embedded_confirmation_run_snapshot")
    terminal_name = authority["artifact"]["name"]
    if any(item.get("name") == terminal_name or item.get("id") == authority["artifact"]["id"] for item in artifacts.get("artifacts", [])):
        failures.append("embedded_artifact_snapshot_was_not_prepublication")
    if stderr_verifier != b"" or embedded_verifier != stdout_verifier:
        failures.append("embedded_verifier_stream_disagreement")
    verifier_expected = {
        "schema_version": "shared-pa-statcast-v2-confirmation-independent-verifier-v1",
        "canonical_identity_basis": "game_pk+official_date+side+official_team_id",
        "status": "PASS", "failures": [], "raw_byte_count": authority["artifact"]["raw_byte_count"],
        "raw_sha256": authority["artifact"]["raw_sha256"], "row_count": 4573,
        "game_pks": authority["primary_claims_for_post_calculation_comparison"]["games"],
        "total_bbe": 784, "measured_ev": 783, "measured_la": 784, "joint_ev_la": 783,
        "source_qualified": False, "scored_predictions_produced": False,
    }
    for key, expected in verifier_expected.items():
        if embedded_verifier.get(key) != expected:
            failures.append(f"embedded_verifier:{key}")
    return failures


def verify_zip(artifact_path: Path, authority: dict[str, Any]) -> tuple[zipfile.ZipFile, dict[str, bytes], list[str]]:
    failures: list[str] = []
    artifact = authority["artifact"]
    payload = artifact_path.read_bytes()
    if len(payload) != artifact["zip_byte_count"] or sha256_bytes(payload) != artifact["zip_sha256"]:
        raise ReproductionError("artifact_zip_identity")
    archive = zipfile.ZipFile(io.BytesIO(payload), "r")
    infos = archive.infolist()
    names = [item.filename for item in infos]
    if len(names) != len(set(names)):
        failures.append("duplicate_zip_member")
    if len(names) != artifact["entry_count"]:
        failures.append("zip_entry_count")
    members: dict[str, bytes] = {}
    for info in infos:
        pure = PurePosixPath(info.filename)
        if pure.is_absolute() or ".." in pure.parts or "\\" in info.filename:
            failures.append("unsafe_zip_member")
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            failures.append("zip_symlink")
        if info.flag_bits & 0x1:
            failures.append("encrypted_zip_member")
        members[info.filename] = archive.read(info.filename)
    expected = {item["path"]: item for item in authority["inventory"]}
    if set(members) != set(expected):
        failures.append("zip_inventory_names")
    for name, binding in expected.items():
        value = members.get(name)
        if value is None or len(value) != binding["bytes"] or sha256_bytes(value) != binding["sha256"]:
            failures.append(f"zip_member_identity:{name}")
    if archive.testzip() is not None:
        failures.append("zip_crc")
    return archive, members, failures


def validate_body(raw: bytes, receipt: dict[str, Any], plan: dict[str, Any], v1: dict[str, Any], v2: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    if receipt.get("byte_count") != len(raw) or receipt.get("sha256") != sha256_bytes(raw):
        failures.append("receipt_raw_identity")
    if receipt.get("http_status") != 200 or receipt.get("final_url") != plan["request"]["full_url"]:
        failures.append("transport_identity")
    headers = receipt.get("response_headers", {})
    content_type = headers.get("content-type", "")
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime not in v2["conditional_content_type_policy"]["normalized_mime_policies"] or mime == "application/octet-stream":
        failures.append("conditional_mime")
    length = headers.get("content-length")
    if length is not None and (not str(length).isdecimal() or int(length) != len(raw)):
        failures.append("content_length")
    if length is None and receipt.get("transport_complete") is not True:
        failures.append("transport_complete")
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig" if bom else "utf-8", errors="strict")
    except UnicodeDecodeError:
        text = ""
        failures.append("utf8")
    lowered, lead = text.lower(), text.lstrip()
    if any(marker in lowered for marker in ("<!doctype html", "<html", "<body", "</html>")):
        failures.append("html_signature")
    if lead.startswith(("{", "[")):
        try:
            json.loads(lead)
            failures.append("json_signature")
        except json.JSONDecodeError:
            pass
    if any(marker in lowered[:8192] for marker in ("baseball savant error", "statcast error", "request failed", "too many requests")):
        failures.append("savant_error_signature")
    bare_cr = raw.count(b"\r") - raw.count(b"\r\n")
    crlf, lf = raw.count(b"\r\n"), raw.count(b"\n")
    if bare_cr or (crlf and crlf != lf):
        failures.append("newline_convention")
    try:
        parsed = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error:
        parsed = []
        failures.append("strict_csv")
    expected_header = v2["expected_csv_header"]["ordered_columns"]
    if not parsed or parsed[0] != expected_header or len(expected_header) != 119 or len(set(expected_header)) != 119:
        failures.append("ordered_header")
    rows: list[dict[str, str]] = []
    for values in parsed[1:]:
        if not values or len(values) != len(expected_header):
            failures.append("row_width_or_empty")
            continue
        rows.append(dict(zip(expected_header, values, strict=True)))
    expected_games = {int(item["game_pk"]): item for item in plan["certified_games"]}
    games: set[int] = set()
    sides: dict[int, set[str]] = {key: set() for key in expected_games}
    raw_pairs: dict[int, set[tuple[str, str]]] = {key: set() for key in expected_games}
    events: set[tuple[int, int, int]] = set()
    categories = v1["plate_discipline"]["categories"]
    known = {value for group in categories.values() for value in group}
    bbe_events = set(v1["ev_launch_angle"]["terminal_bbe_event_allowlist"])
    plate = {"pitch_rows":0,"swing":0,"whiff":0,"contact":0,"take":0,"called_strike":0,"called_ball":0,"hbp_take":0,"pitchout_take":0,"automatic_ball":0,"automatic_strike":0,"physical_pitch_rows":0,"zone_opportunity":0,"zone_swing":0,"chase_opportunity":0,"chase_swing":0,"missing_zone":0,"invalid_zone":0,"unknown_description_count":0}
    ev = {"official_pa_count":None,"total_bbe":0,"measured_ev":0,"missing_ev":0,"measured_la":0,"missing_la":0,"joint_ev_la":0,"joint_missing":0,"classified":0,"unclassified_joint":0,"barrel":0,"hard_hit":0,"classified_hard_hit":0,"classified_hard_hit_non_barrel":0,"other_classified_contact":0,"sweet_spot":0,"ev50_support":0,"ev50_mean":None,"bbe_contradictions":0}
    ev_values: list[float] = []
    canonical_team_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        plate["pitch_rows"] += 1
        try:
            game, at_bat, pitch = int(row["game_pk"]), int(row["at_bat_number"]), int(row["pitch_number"])
            batter, pitcher = int(row["batter"]), int(row["pitcher"])
        except (KeyError, ValueError):
            failures.append("required_identity_parse")
            continue
        if min(game, at_bat, pitch, batter, pitcher) <= 0 or game not in expected_games:
            failures.append("required_identity_or_game")
            continue
        identity = (game, at_bat, pitch)
        if identity in events:
            failures.append("duplicate_event_identity")
        events.add(identity)
        games.add(game)
        if row["game_date"] != "2023-06-28" or row["game_type"] != "R":
            failures.append("date_or_game_type")
        try:
            inning, balls, strikes = int(row["inning"]), int(row["balls"]), int(row["strikes"])
            if inning <= 0 or balls < 0 or strikes < 0:
                raise ValueError
        except (KeyError, ValueError):
            failures.append("governed_integral_field")
        for numeric_field in (
            "plate_x", "plate_z", "sz_top", "sz_bot", "hit_distance_sc", "hc_x", "hc_y",
            "release_speed", "release_spin_rate", "pfx_x", "pfx_z", "release_extension", "spin_axis",
        ):
            try:
                finite(row[numeric_field])
            except (KeyError, ValueError, ReproductionError):
                failures.append(f"governed_numeric_field:{numeric_field}")
        if row.get("stand") not in {"L", "R"} or row.get("p_throws") not in {"L", "R"}:
            failures.append("batting_or_pitching_side")
        side = row["inning_topbot"]
        if side not in {"Top", "Bot"}:
            failures.append("canonical_side")
        else:
            sides[game].add(side)
            certified = expected_games[game]
            batting = certified["away_team_id"] if side == "Top" else certified["home_team_id"]
            fielding = certified["home_team_id"] if side == "Top" else certified["away_team_id"]
            if min(int(batting), int(fielding)) <= 0 or batting == fielding:
                failures.append("certified_team_identity")
            canonical_team_counts[f"{game}:{side}:{batting}:{fielding}"] += 1
        raw_pairs[game].add((row["home_team"], row["away_team"]))
        description = row["description"]
        if description not in known:
            failures.append("unknown_description")
            plate["unknown_description_count"] += 1
        swing = description in categories["SWING_WHIFF"] or description in categories["SWING_CONTACT"]
        if description in categories["SWING_WHIFF"]:
            plate["swing"] += 1; plate["whiff"] += 1
        elif description in categories["SWING_CONTACT"]:
            plate["swing"] += 1; plate["contact"] += 1
        else:
            plate["take"] += 1
        mapping = {"TAKE_CALLED_STRIKE":"called_strike","TAKE_CALLED_BALL":"called_ball","TAKE_HBP":"hbp_take","TAKE_PITCHOUT":"pitchout_take","RESIDUAL_AUTOMATIC_BALL":"automatic_ball","RESIDUAL_AUTOMATIC_STRIKE":"automatic_strike"}
        for group, key in mapping.items():
            plate[key] += int(description in categories[group])
        is_automatic = description in categories["RESIDUAL_AUTOMATIC_BALL"] or description in categories["RESIDUAL_AUTOMATIC_STRIKE"]
        if not is_automatic:
            plate["physical_pitch_rows"] += 1
            zone_text = row["zone"]
            if zone_text == "":
                plate["missing_zone"] += 1
            else:
                try:
                    zone_value = int(zone_text)
                except ValueError:
                    plate["invalid_zone"] += 1
                else:
                    if 1 <= zone_value <= 9:
                        plate["zone_opportunity"] += 1; plate["zone_swing"] += int(swing)
                    elif 11 <= zone_value <= 14:
                        plate["chase_opportunity"] += 1; plate["chase_swing"] += int(swing)
                    else:
                        plate["invalid_zone"] += 1
        is_bbe = row["type"] == "X" or description == "hit_into_play"
        if is_bbe:
            if not (row["type"] == "X" and description == "hit_into_play" and row["events"] in bbe_events):
                failures.append("bbe_type_description_event")
                ev["bbe_contradictions"] += 1
            ev["total_bbe"] += 1
            try:
                speed, angle = finite(row["launch_speed"]), finite(row["launch_angle"])
            except (ValueError, ReproductionError):
                failures.append("malformed_ev_la")
                speed = angle = None
            ev_ok = speed is not None and speed > 0
            la_ok = angle is not None and -90 <= angle <= 90
            ev["measured_ev"] += int(ev_ok); ev["missing_ev"] += int(not ev_ok)
            ev["measured_la"] += int(la_ok); ev["missing_la"] += int(not la_ok)
            ev["joint_ev_la"] += int(ev_ok and la_ok); ev["joint_missing"] += int(not (ev_ok and la_ok))
            if ev_ok:
                ev_values.append(speed)
            lsa_text = row["launch_speed_angle"]
            classified = ev_ok and la_ok and lsa_text.isdecimal() and 1 <= int(lsa_text) <= 6
            hard_hit = ev_ok and speed >= 95
            barrel = classified and int(lsa_text) == 6
            ev["classified"] += int(classified); ev["unclassified_joint"] += int(ev_ok and la_ok and not classified)
            ev["barrel"] += int(barrel); ev["hard_hit"] += int(hard_hit)
            ev["classified_hard_hit"] += int(classified and hard_hit)
            ev["classified_hard_hit_non_barrel"] += int(classified and hard_hit and not barrel)
            ev["other_classified_contact"] += int(classified and not hard_hit)
            ev["sweet_spot"] += int(ev_ok and la_ok and 8 <= angle <= 32)
            if barrel and not hard_hit:
                failures.append("barrel_not_hard_hit")
    if games != set(expected_games): failures.append("game_universe")
    if any(value != {"Top", "Bot"} for value in sides.values()): failures.append("side_coverage")
    if any(len(value) != 1 or any(not home or not away or home == away for home, away in value) for value in raw_pairs.values()): failures.append("raw_team_pair_contradiction")
    if plate["contact"] + plate["whiff"] != plate["swing"]: failures.append("plate_reconciliation")
    if plate["zone_opportunity"] + plate["chase_opportunity"] + plate["missing_zone"] + plate["invalid_zone"] != plate["physical_pitch_rows"]: failures.append("zone_reconciliation")
    if plate["invalid_zone"] != 0: failures.append("invalid_zone")
    if ev["measured_ev"] + ev["missing_ev"] != ev["total_bbe"] or ev["measured_la"] + ev["missing_la"] != ev["total_bbe"] or ev["joint_ev_la"] + ev["joint_missing"] != ev["total_bbe"]: failures.append("ev_missing_reconciliation")
    if ev["classified"] + ev["unclassified_joint"] != ev["joint_ev_la"] or ev["classified_hard_hit_non_barrel"] + ev["barrel"] != ev["classified_hard_hit"] or ev["classified_hard_hit"] + ev["other_classified_contact"] != ev["classified"]: failures.append("ev_classification_reconciliation")
    if ev_values:
        support = math.ceil(len(ev_values) * 0.5)
        tail = sorted(ev_values, reverse=True)[:support]
        ev["ev50_support"] = support
        ev["ev50_mean"] = sum(tail) / support
    return {
        "raw_byte_count": len(raw), "raw_sha256": sha256_bytes(raw), "raw_content_type": content_type,
        "normalized_mime": mime, "utf8_bom_present": bom, "terminal_newline_present": raw.endswith((b"\n", b"\r")),
        "newline_counts": {"lf":lf,"crlf":crlf,"bare_cr":bare_cr}, "header": parsed[0] if parsed else [],
        "row_count": len(rows), "game_pks": sorted(games), "sides": {str(k):sorted(v) for k,v in sides.items()},
        "raw_team_pairs": {str(k):sorted([list(x) for x in v]) for k,v in raw_pairs.items()},
        "canonical_team_counts": dict(sorted(canonical_team_counts.items())), "plate_discipline": plate, "ev_launch_angle": ev,
    }, sorted(set(failures))


def reproduce(artifact_path: Path, repository: Path, authority_path: Path, retrieval_path: Path, terminal_path: Path, successor_ledger_path: Path, terminal_adjudication_path: Path, official_pa_receipt_path: Path, official_pa_source_path: Path, official_pa_release_path: Path, official_pa_verifier_path: Path, execution_audit: dict[str, Any]) -> dict[str, Any]:
    authority = load_object(authority_path)
    failures = verify_dependencies(repository, authority)
    failures.extend(verify_external_receipts(authority, load_object(retrieval_path), load_object(terminal_path)))
    archive, members, zip_failures = verify_zip(artifact_path, authority)
    failures.extend(zip_failures)
    failures.extend(verify_embedded_terminal_evidence(members, authority))
    try:
        root = "june28-confirmation-carrier/data/source/shared_pa_statcast_v2_confirmation_2023-06-28_v1"
        request_root = root + "/statcast-v2-confirmation-2023-06-28"
        raw = members[authority["artifact"]["raw_member"]]
        receipt = load_object_bytes(members[request_root + "/receipt.json"], "receipt")
        result = load_object_bytes(members[request_root + "/result-01.json"], "result")
        reservation = load_object_bytes(members[request_root + "/reservation-01.json"], "reservation")
        primary = load_object_bytes(members[request_root + "/validation-01.json"], "primary validation")
        manifest = load_object_bytes(members[root + "/manifest.json"], "manifest")
        plan = load_object(repository / "config/shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json")
        v1 = load_object(repository / "config/shared_pa_statcast_source_contract_v1.json")
        v2 = load_object(repository / "config/shared_pa_statcast_source_contract_v2_proposal.json")
        calculations, body_failures = validate_body(raw, receipt, plan, v1, v2)
        failures.extend(body_failures)
        receipt_expected = {
            "schema_version": "shared-pa-statcast-v2-confirmation-receipt-v1", "attempt": 1,
            "request_plan_sha256": "0bcc3c9482ea67189e414502b9b58e6445cd66efeb27c309c5bcff4864108402",
            "execution_contract_sha256": "851e02beab8732b8f6d1a242ea4609d30ab1f3b462804134ae8194b3d42dc510",
            "http_status": 200, "byte_count": authority["artifact"]["raw_byte_count"],
            "sha256": authority["artifact"]["raw_sha256"], "automatic_http_retry_count": 0,
            "replacement_request_count": 0, "transport_complete": True,
        }
        for key, expected in receipt_expected.items():
            if receipt.get(key) != expected: failures.append(f"receipt:{key}")
        for key in ("attempt", "automatic_http_retry_count", "byte_count", "final_url", "http_status", "observed_at_utc", "replacement_request_count", "request_started_at_utc", "response_headers", "sha256", "transport_complete"):
            if result.get(key) != receipt.get(key): failures.append(f"receipt_result_disagreement:{key}")
        if reservation.get("status") != "CONSUMED_AT_TRANSPORT_BOUNDARY" or reservation.get("attempt") != 1:
            failures.append("reservation_state")
        expected_request = plan["request"]
        reserved_request = reservation.get("request", {})
        reservation_expected = {
            "method": "GET", "full_url": expected_request["full_url"],
            "full_url_sha256": "0efc234b3ef8e8490550bc11a28896bbc1e85ca39b31bf503484c65ed9d96aa9",
            "query_sha256": "16920ee504c363fb27b19b7100d8fc54c95649054a9357af887e4ab51ef58e23",
            "expected_request_count": 1, "minimum_request_start_interval_seconds": 1.1,
            "redirects_allowed": False, "automatic_http_retry_maximum": 0,
            "replacement_request_maximum": 0, "automatic_workflow_rerun_allowed": False,
            "official_date": "2023-06-28",
        }
        for key, expected in reservation_expected.items():
            if reserved_request.get(key) != expected: failures.append(f"reservation_request:{key}")
        if reservation.get("human_authorization_sha256") != "22a95ad6ad9e2d6390d0170bfdb70324f8c624898ec2a62a51e17058a622a0c3":
            failures.append("reservation_human_authorization")
        if not (parse_utc(reservation["request_started_at_utc"]) <= parse_utc(receipt["request_started_at_utc"]) <= parse_utc(receipt["observed_at_utc"])):
            failures.append("timestamp_order")
        if receipt.get("automatic_http_retry_count") != 0 or receipt.get("replacement_request_count") != 0:
            failures.append("retry_or_replacement")
        manifest_expected = {
            "schema_version": "shared-pa-statcast-v2-confirmation-capture-manifest-v1", "attempt": 1,
            "authority_package_sha256": "31181de53446951a4d48c5740e4687c36fb042005e1466ff09a9ac9e1cf3ef0d",
            "execution_contract_sha256": "851e02beab8732b8f6d1a242ea4609d30ab1f3b462804134ae8194b3d42dc510",
            "human_authorization_sha256": "22a95ad6ad9e2d6390d0170bfdb70324f8c624898ec2a62a51e17058a622a0c3",
            "external_request_count": 1, "automatic_http_retry_count": 0, "replacement_request_count": 0,
            "technical_validation_status": "PASS", "durable_retention_verified": False,
            "independent_reproduction_verified": False, "source_qualified": False,
            "promotable": False, "quarantined": False,
            "failure_artifact_publication_is_source_qualification": False,
            "scientific_disposition": "VALIDATION_PASSED_CONFIRMATION_PENDING_DURABLE_RETENTION_AND_INDEPENDENT_REPRODUCTION",
        }
        for key, expected in manifest_expected.items():
            if manifest.get(key) != expected: failures.append(f"manifest:{key}")
        claims = authority["primary_claims_for_post_calculation_comparison"]
        if calculations["row_count"] != claims["rows"] or calculations["game_pks"] != claims["games"]:
            failures.append("primary_scope_disagreement")
        for key, expected in claims["plate_discipline"].items():
            if calculations["plate_discipline"].get(key) != expected: failures.append(f"primary_plate_disagreement:{key}")
        for key, expected in claims["ev_launch_angle"].items():
            if calculations["ev_launch_angle"].get(key) != expected: failures.append(f"primary_ev_disagreement:{key}")
        primary_expected = {
            "schema_version": "shared-pa-statcast-v2-confirmation-validation-v1",
            "status": "PASS", "failures": [], "raw_byte_count": calculations["raw_byte_count"],
            "raw_sha256": calculations["raw_sha256"], "raw_content_type": calculations["raw_content_type"],
            "normalized_mime": calculations["normalized_mime"], "utf8_bom_present": calculations["utf8_bom_present"],
            "terminal_newline_present": calculations["terminal_newline_present"], "header_column_count": len(calculations["header"]),
            "row_count": calculations["row_count"], "observed_game_pks": calculations["game_pks"],
            "canonical_team_binding": "game_pk+official_date+side+official_team_id",
            "raw_abbreviations_used_as_identity": False, "source_release_produced": False,
            "scored_predictions_produced": False,
        }
        for key, expected in primary_expected.items():
            if primary.get(key) != expected: failures.append(f"primary_validation:{key}")
        for key in ("swing", "whiff", "contact", "take"):
            if primary.get("plate_discipline", {}).get(key) != calculations["plate_discipline"].get(key):
                failures.append(f"primary_validation_plate:{key}")
        for key in ("total_bbe", "measured_ev", "measured_la", "joint_ev_la", "classified", "barrel", "hard_hit", "sweet_spot"):
            if primary.get("ev_launch_angle", {}).get(key) != calculations["ev_launch_angle"].get(key):
                failures.append(f"primary_validation_ev:{key}")
        successor = load_object(successor_ledger_path)
        failures.extend(verify_successor_ledger(successor, authority, repository))
        ledger_sha256 = sha256_file(successor_ledger_path)
        failures.extend(verify_terminal_adjudication(load_object(terminal_adjudication_path), authority, ledger_sha256))
        pa_receipt = load_object(official_pa_receipt_path)
        pa_source = load_object(official_pa_source_path)
        official_pa_count, pa_failures = verify_official_pa_receipt(
            pa_receipt, pa_source, plan["certified_games"], sha256_file(official_pa_source_path),
            sha256_file(official_pa_release_path), sha256_file(official_pa_verifier_path),
        )
        failures.extend(pa_failures)
        if official_pa_count:
            calculations["ev_launch_angle"]["official_pa_count"] = official_pa_count
            calculations["ev_launch_angle"]["bbe_per_official_pa"] = calculations["ev_launch_angle"]["total_bbe"] / official_pa_count
        if sha256_bytes(raw) != authority["artifact"]["raw_sha256"]:
            failures.append("raw_post_calculation_identity")
    finally:
        archive.close()
    failures = sorted(set(failures))
    return {
        "schema_version": "shared-pa-statcast-june28-independent-reproduction-result-v1",
        "status": "INDEPENDENT_REPRODUCTION_PASSED" if not failures else "INDEPENDENT_REPRODUCTION_FAILED",
        "failures": failures,
        "artifact_id": authority["artifact"]["id"],
        "artifact_zip_sha256": authority["artifact"]["zip_sha256"],
        "execution_audit": execution_audit,
        "calculations": calculations if "calculations" in locals() else None,
        "raw_bytes_modified": False,
        "network_used": False,
        "source_qualified": False,
        "promotable": False,
        "overall_confirmation_status": "PENDING_SEPARATE_DURABLE_RETENTION_AND_LATER_ACTIVATION_REVIEW",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-zip", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--execution-authorization", type=Path, required=True)
    parser.add_argument("--expected-execution-authorization-sha256", required=True)
    parser.add_argument("--artifact-retrieval-receipt", type=Path, required=True)
    parser.add_argument("--terminal-run-receipt", type=Path, required=True)
    parser.add_argument("--successor-ledger", type=Path, required=True)
    parser.add_argument("--terminal-adjudication", type=Path, required=True)
    parser.add_argument("--official-pa-receipt", type=Path, required=True)
    parser.add_argument("--official-pa-source", type=Path, required=True)
    parser.add_argument("--official-pa-certified-release", type=Path, required=True)
    parser.add_argument("--official-pa-independent-verifier", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ReproductionError("output report already exists")
    required_inputs = {
        "artifact_zip": args.artifact_zip,
        "repository": args.repository,
        "authority": args.authority,
        "execution_authorization": args.execution_authorization,
        "artifact_retrieval_receipt": args.artifact_retrieval_receipt,
        "terminal_run_receipt": args.terminal_run_receipt,
        "successor_ledger": args.successor_ledger,
        "terminal_adjudication": args.terminal_adjudication,
        "official_pa_receipt": args.official_pa_receipt,
        "official_pa_source": args.official_pa_source,
        "official_pa_release": args.official_pa_certified_release,
        "official_pa_verifier": args.official_pa_independent_verifier,
    }
    missing = sorted(name for name, path in required_inputs.items() if not path.exists())
    if missing:
        report = {
            "schema_version": "shared-pa-statcast-june28-independent-reproduction-result-v1",
            "status": "INDEPENDENT_REPRODUCTION_INCOMPLETE",
            "failures": [f"missing_required_input:{name}" for name in missing],
            "raw_bytes_modified": False,
            "network_used": False,
            "source_qualified": False,
            "promotable": False,
            "overall_confirmation_status": "PENDING_REQUIRED_INPUTS_DURABLE_RETENTION_AND_LATER_ACTIVATION_REVIEW",
        }
    else:
        receipt_paths = {
            "artifact_retrieval_receipt": args.artifact_retrieval_receipt.resolve(),
            "terminal_run_receipt": args.terminal_run_receipt.resolve(),
            "successor_ledger": args.successor_ledger.resolve(),
            "terminal_adjudication": args.terminal_adjudication.resolve(),
            "official_pa_receipt": args.official_pa_receipt.resolve(),
            "official_pa_source": args.official_pa_source.resolve(),
            "official_pa_release": args.official_pa_certified_release.resolve(),
            "official_pa_verifier": args.official_pa_independent_verifier.resolve(),
        }
        try:
            authority = load_object(args.authority.resolve())
            execution_path = args.execution_authorization.resolve()
            execution = load_object(execution_path)
            execution_audit = {
                "execution_authorization_id": execution.get("authorization_id"),
                "execution_authorization_sha256": args.expected_execution_authorization_sha256,
                "authorization_valid_from_utc": execution.get("valid_from_utc"),
                "authorization_expires_at_utc": execution.get("expires_at_utc"),
                "execution_commit": execution.get("exact_execution_commit"),
                "authority_package_sha256": execution.get("authority_package_sha256"),
                "reproduction_verifier_sha256": execution.get("reproduction_verifier_sha256"),
                "artifact_zip_sha256": execution.get("artifact_zip_sha256"),
                "artifact_retrieval_receipt_sha256": execution.get("artifact_retrieval_receipt_sha256"),
                "post_terminal_run_receipt_sha256": execution.get("post_terminal_run_receipt_sha256"),
                "successor_attempt_ledger_sha256": execution.get("successor_attempt_ledger_sha256"),
                "terminal_adjudication_sha256": execution.get("terminal_adjudication_sha256"),
                "official_pa_denominator_receipt_sha256": execution.get("official_pa_denominator_receipt_sha256"),
                "official_pa_source_sha256": execution.get("official_pa_source_sha256"),
                "official_pa_certified_release_sha256": execution.get("official_pa_certified_release_sha256"),
                "official_pa_independent_verifier_sha256": execution.get("official_pa_independent_verifier_sha256"),
                "python_runtime": execution.get("python_runtime"),
                "network_disabled": execution.get("network_disabled"),
                "output_path": execution.get("output_path"),
            }
            authorization_failures = verify_execution_authorization(
                execution, args.repository.resolve(),
                args.authority.resolve(), args.artifact_zip.resolve(), receipt_paths,
                args.output.resolve(), args.expected_execution_authorization_sha256,
                execution_path,
            )
            if authorization_failures:
                report = {
                    "schema_version": "shared-pa-statcast-june28-independent-reproduction-result-v1",
                    "status": "INDEPENDENT_REPRODUCTION_FAILED",
                    "failures": sorted(set(authorization_failures)),
                    "raw_bytes_modified": False, "network_used": False,
                    "source_qualified": False, "promotable": False,
                    "overall_confirmation_status": "EXECUTION_AUTHORIZATION_FAILED",
                    "execution_audit": execution_audit,
                }
            else:
                with no_network():
                    report = reproduce(
                        args.artifact_zip.resolve(), args.repository.resolve(), args.authority.resolve(),
                        args.artifact_retrieval_receipt.resolve(), args.terminal_run_receipt.resolve(),
                        args.successor_ledger.resolve(), args.terminal_adjudication.resolve(),
                        args.official_pa_receipt.resolve(), args.official_pa_source.resolve(),
                        args.official_pa_certified_release.resolve(), args.official_pa_independent_verifier.resolve(),
                        execution_audit,
                    )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile, ReproductionError) as exc:
            report = {
                "schema_version": "shared-pa-statcast-june28-independent-reproduction-result-v1",
                "status": "INDEPENDENT_REPRODUCTION_FAILED",
                "failures": [f"deterministic_input_failure:{type(exc).__name__}"],
                "raw_bytes_modified": False, "network_used": False,
                "source_qualified": False, "promotable": False,
                "overall_confirmation_status": "REPRODUCTION_FAILED_CLOSED",
                "execution_audit": execution_audit if "execution_audit" in locals() else None,
            }
    payload = canonical_json(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    print(json.dumps({"status": report["status"], "output_sha256": sha256_bytes(payload), "output_bytes": len(payload)}, sort_keys=True))
    return 0 if report["status"] == "INDEPENDENT_REPRODUCTION_PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
