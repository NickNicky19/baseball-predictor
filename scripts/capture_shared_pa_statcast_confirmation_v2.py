"""Fail-closed one-request capture and validation for the frozen June 28 confirmation.

This module is inert unless a separately protected workflow invokes ``capture``.
It never retries or follows redirects, and it never qualifies or promotes a source.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import socket
import ssl
import sys
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


class ConfirmationError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_new(path: Path, value: bytes | dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value if isinstance(value, bytes) else canonical_json(value)
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ConfirmationError(f"JSON root is not an object: {path}")
    return value


@dataclass(frozen=True)
class CapturedResponse:
    status: int
    body: bytes
    headers: dict[str, str]
    final_url: str
    requested_at_utc: str
    observed_at_utc: str
    transport_complete: bool


Transport = Callable[[str, int], CapturedResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _safe_headers(message) -> dict[str, str]:  # noqa: ANN001
    allowed = {"cache-control", "content-disposition", "content-length", "content-type", "date", "etag", "last-modified"}
    result: dict[str, str] = {}
    for name, value in message.items():
        key = name.strip().lower()
        if key not in allowed:
            continue
        if key in result or "\r" in value or "\n" in value:
            raise ConfirmationError("duplicate or unsafe response header")
        result[key] = value.strip()
    return result


def one_request_transport(full_url: str, maximum_bytes: int) -> CapturedResponse:
    requested = utc_now()
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(full_url, method="GET", headers={"Accept": "text/csv,*/*;q=0.1", "User-Agent": "mlb-source-confirmation-v2/1"})
    try:
        with opener.open(request, timeout=90.0) as response:
            body = response.read(maximum_bytes + 1)
            if len(body) > maximum_bytes:
                raise ConfirmationError("response exceeds maximum authorized bytes")
            return CapturedResponse(int(response.status), body, _safe_headers(response.headers), str(response.geturl()), requested, utc_now(), True)
    except urllib.error.HTTPError as exc:
        body = exc.read(maximum_bytes + 1)
        if len(body) > maximum_bytes:
            raise ConfirmationError("error response exceeds maximum authorized bytes") from exc
        return CapturedResponse(int(exc.code), body, _safe_headers(exc.headers), full_url, requested, utc_now(), True)


@contextmanager
def socket_denial_guard() -> Iterator[None]:
    old_socket, old_connection = socket.socket, socket.create_connection
    def blocked(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("lower-level network guard blocked networking")
    socket.socket = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket, socket.create_connection = old_socket, old_connection  # type: ignore[assignment]


def verify_frozen_dependencies(repository: Path, contract: dict[str, Any]) -> None:
    for label, binding in contract["frozen_dependencies"].items():
        path = repository / binding["path"]
        if not path.is_file() or path.is_symlink() or sha256_file(path) != binding["sha256"]:
            raise ConfirmationError(f"frozen dependency mismatch: {label}")
    ledger = load_json(repository / contract["frozen_dependencies"]["independent_attempt_ledger"]["path"])
    if ledger.get("attempts") != [] or ledger.get("next_attempt") != {"attempt_number": 1, "status": "UNUSED_UNAUTHORIZED", "reserved": False, "consumed": False}:
        raise ConfirmationError("confirmation attempt 1 is not unused and unauthorized")
    if ledger.get("global_real_external_statcast_request_count") != 3:
        raise ConfirmationError("real Statcast request accounting differs")
    old = load_json(repository / contract["frozen_dependencies"]["old_july_attempt_ledger"]["path"])
    if old.get("remaining_attempts") != [{"attempt_number": 4, "status": "UNUSED_UNAUTHORIZED"}]:
        raise ConfirmationError("old July attempt 4 boundary differs")


def verify_attempt3_retention_receipt(path: Path, contract: dict[str, Any]) -> dict[str, Any]:
    receipt = load_json(path)
    expected = contract["temporary_attempt_3_retention_gate"]
    if receipt.get("schema_version") != "shared-pa-statcast-attempt-3-temporary-retention-gate-v1" or receipt.get("status") != "PASS":
        raise ConfirmationError("attempt-3 retention gate did not pass")
    checks = {
        "artifact_id": expected["artifact_id"],
        "artifact_name": expected["artifact_name"],
        "artifact_zip_bytes": expected["artifact_zip_bytes"],
        "artifact_zip_sha256": expected["artifact_zip_sha256"],
        "raw_member": expected["raw_member"],
        "raw_response_bytes": expected["raw_response_bytes"],
        "raw_response_sha256": expected["raw_response_sha256"],
        "expires_at_utc": expected["expires_at_utc"],
    }
    if any(receipt.get(key) != value for key, value in checks.items()):
        raise ConfirmationError("attempt-3 retention gate identity differs")
    if receipt.get("zip_verified") is not True or receipt.get("raw_verified") is not True or receipt.get("tracked_identities_verified") is not True:
        raise ConfirmationError("attempt-3 retention gate verification is incomplete")
    checked = datetime.fromisoformat(str(receipt.get("checked_at_utc", "")).replace("Z", "+00:00"))
    expires = datetime.fromisoformat(expected["expires_at_utc"].replace("Z", "+00:00"))
    if checked >= expires:
        raise ConfirmationError("attempt-3 retention artifact is expired")
    return receipt


def verify_authority(
    repository: Path,
    authority_path: Path,
    expected_authority_sha256: str,
    workflow_path: Path,
    expected_workflow_sha256: str,
    runtime_path: Path,
    expected_runtime_sha256: str,
    expected_carrier_commit: str,
    human_authorization_sha256: str,
) -> dict[str, Any]:
    for value, label in ((expected_authority_sha256, "authority"), (expected_workflow_sha256, "workflow"), (expected_runtime_sha256, "runtime"), (human_authorization_sha256, "human authorization")):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ConfirmationError(f"{label} SHA-256 is malformed")
    if human_authorization_sha256 == expected_authority_sha256:
        raise ConfirmationError("authority package cannot authorize itself")
    if sha256_file(authority_path) != expected_authority_sha256 or sha256_file(workflow_path) != expected_workflow_sha256 or sha256_file(runtime_path) != expected_runtime_sha256:
        raise ConfirmationError("execution identity hash mismatch")
    authority = load_json(authority_path)
    if authority.get("preparation_is_execution_authorization") is not False or authority.get("execution_authorized") is not False:
        raise ConfirmationError("prepared authority boundary differs")
    if authority.get("carrier_commit") != expected_carrier_commit:
        raise ConfirmationError("carrier commit mismatch")
    if authority["bindings"]["workflow_sha256"] != expected_workflow_sha256 or authority["bindings"]["runtime_authority_sha256"] != expected_runtime_sha256:
        raise ConfirmationError("authority execution binding mismatch")
    return authority


def _finite(value: str) -> float | None:
    if value == "":
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfirmationError("numeric field is malformed") from exc
    return parsed if math.isfinite(parsed) else None


def validate_response(raw: bytes, response: CapturedResponse, request_plan: dict[str, Any], v2: dict[str, Any], v1: dict[str, Any]) -> dict[str, Any]:
    original_sha = sha256_bytes(raw)
    failures: list[str] = []
    request = request_plan["request"]
    if response.status != 200: failures.append("http_status")
    if response.final_url != request["full_url"]: failures.append("redirect_or_final_url")
    content_type = response.headers.get("content-type", "")
    normalized_mime = content_type.split(";", 1)[0].strip().lower()
    allowed = v2["conditional_content_type_policy"]["normalized_mime_policies"]
    if normalized_mime not in allowed: failures.append("conditional_mime")
    if normalized_mime == "application/octet-stream" or not normalized_mime: failures.append("conditional_mime")
    content_length = response.headers.get("content-length")
    if content_length is not None:
        if not content_length.isdecimal() or int(content_length) != len(raw): failures.append("content_length")
    elif not response.transport_complete:
        failures.append("transport_completion")
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig" if bom else "utf-8", errors="strict")
    except UnicodeDecodeError:
        text = ""
        failures.append("utf8")
    lowered = text.lower()
    lead = text.lstrip()
    if any(marker in lowered for marker in ("<!doctype html", "<html", "<body", "</html>")): failures.append("html_signature")
    if lead.startswith(("{", "[")):
        try:
            json.loads(lead)
            failures.append("json_signature")
        except json.JSONDecodeError:
            pass
    if any(marker in lowered[:8192] for marker in ("baseball savant error", "statcast error", "request failed", "too many requests")): failures.append("savant_error_signature")
    bare_cr = raw.count(b"\r") - raw.count(b"\r\n")
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    if bare_cr or (crlf and lf != crlf): failures.append("mixed_or_bare_cr_newlines")
    rows: list[dict[str, str]] = []
    header: list[str] = []
    if text:
        try:
            parsed = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        except csv.Error:
            parsed = []
            failures.append("strict_csv")
        if parsed:
            header = parsed[0]
            expected = v2["expected_csv_header"]["ordered_columns"]
            if header != expected or len(header) != 119 or len(set(header)) != len(header): failures.append("header")
            for values in parsed[1:]:
                if not values or len(values) != len(header):
                    failures.append("row_width")
                    continue
                rows.append(dict(zip(header, values, strict=True)))
        else:
            failures.append("empty_or_unparseable")
    expected_games = {int(item["game_pk"]): item for item in request_plan["certified_games"]}
    if any(int(item["home_team_id"]) <= 0 or int(item["away_team_id"]) <= 0 or item["home_team_id"] == item["away_team_id"] for item in expected_games.values()):
        failures.append("certified_team_identity")
    observed_games: set[int] = set()
    sides: dict[int, set[str]] = {game_pk: set() for game_pk in expected_games}
    raw_pairs: dict[int, set[tuple[str, str]]] = {game_pk: set() for game_pk in expected_games}
    events: set[tuple[int, int, int]] = set()
    description_map = {item for values in v1["plate_discipline"]["categories"].values() for item in values}
    unknown_descriptions: set[str] = set()
    plate = {"swing": 0, "whiff": 0, "contact": 0, "take": 0}
    ev = {"total_bbe": 0, "measured_ev": 0, "measured_la": 0, "joint_ev_la": 0, "classified": 0, "barrel": 0, "hard_hit": 0, "sweet_spot": 0}
    bbe_events = set(v1["ev_launch_angle"]["terminal_bbe_event_allowlist"])
    categories = v1["plate_discipline"]["categories"]
    for row in rows:
        try:
            game_pk, at_bat, pitch = int(row["game_pk"]), int(row["at_bat_number"]), int(row["pitch_number"])
            batter, pitcher = int(row["batter"]), int(row["pitcher"])
        except (KeyError, ValueError):
            failures.append("required_identity")
            continue
        if min(game_pk, at_bat, pitch, batter, pitcher) <= 0 or game_pk not in expected_games: failures.append("required_identity_or_unexpected_game")
        observed_games.add(game_pk)
        identity = (game_pk, at_bat, pitch)
        if identity in events: failures.append("duplicate_event_identity")
        events.add(identity)
        if row.get("game_date") != "2023-06-28" or row.get("game_type") != "R": failures.append("date_or_game_type")
        side = row.get("inning_topbot", "")
        if side not in {"Top", "Bot"}: failures.append("canonical_side")
        elif game_pk in sides: sides[game_pk].add(side)
        raw_pairs.setdefault(game_pk, set()).add((row.get("home_team", ""), row.get("away_team", "")))
        desc = row.get("description", "")
        if desc not in description_map: unknown_descriptions.add(desc)
        if desc in categories["SWING_WHIFF"]: plate["swing"] += 1; plate["whiff"] += 1
        elif desc in categories["SWING_CONTACT"]: plate["swing"] += 1; plate["contact"] += 1
        else: plate["take"] += 1
        is_bbe = row.get("type") == "X" or desc == "hit_into_play"
        if is_bbe:
            if not (row.get("type") == "X" and desc == "hit_into_play" and row.get("events") in bbe_events): failures.append("bbe_type_description")
            ev["total_bbe"] += 1
            speed, angle = _finite(row.get("launch_speed", "")), _finite(row.get("launch_angle", ""))
            measured_ev = speed is not None and speed > 0
            measured_la = angle is not None and -90 <= angle <= 90
            ev["measured_ev"] += int(measured_ev); ev["measured_la"] += int(measured_la); ev["joint_ev_la"] += int(measured_ev and measured_la)
            lsa = row.get("launch_speed_angle", "")
            classified = measured_ev and measured_la and lsa.isdecimal() and 1 <= int(lsa) <= 6
            ev["classified"] += int(classified); ev["barrel"] += int(classified and int(lsa) == 6)
            ev["hard_hit"] += int(measured_ev and speed >= 95); ev["sweet_spot"] += int(measured_ev and measured_la and 8 <= angle <= 32)
            if classified and int(lsa) == 6 and speed < 95: failures.append("barrel_not_hard_hit")
    if observed_games != set(expected_games): failures.append("game_universe")
    if any(value != {"Top", "Bot"} for value in sides.values()): failures.append("batting_side_coverage")
    if any(len(value) != 1 or any(not home or not away for home, away in value) for value in raw_pairs.values()): failures.append("raw_team_pair_contradiction")
    if unknown_descriptions: failures.append("unknown_pitch_description")
    if plate["contact"] + plate["whiff"] != plate["swing"]: failures.append("plate_discipline_reconciliation")
    if ev["joint_ev_la"] > min(ev["measured_ev"], ev["measured_la"]) or ev["classified"] > ev["joint_ev_la"] or ev["barrel"] > ev["hard_hit"]: failures.append("ev_la_reconciliation")
    if sha256_bytes(raw) != original_sha: failures.append("raw_bytes_mutated")
    failures = sorted(set(failures))
    return {
        "schema_version": "shared-pa-statcast-v2-confirmation-validation-v1",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "raw_byte_count": len(raw),
        "raw_sha256": original_sha,
        "raw_content_type": content_type,
        "normalized_mime": normalized_mime,
        "utf8_bom_present": bom,
        "terminal_newline_present": raw.endswith((b"\n", b"\r")),
        "header_column_count": len(header),
        "row_count": len(rows),
        "observed_game_pks": sorted(observed_games),
        "canonical_team_binding": "game_pk+official_date+side+official_team_id",
        "raw_abbreviations_used_as_identity": False,
        "plate_discipline": plate,
        "ev_launch_angle": ev,
        "scored_predictions_produced": False,
        "source_release_produced": False,
    }


def capture(repository: Path, contract_path: Path, authority: dict[str, Any], output_dir: Path, transport: Transport) -> dict[str, Any]:
    contract = load_json(contract_path)
    verify_frozen_dependencies(repository, contract)
    request_plan = load_json(repository / contract["frozen_dependencies"]["request_plan"]["path"])
    v2 = load_json(repository / contract["frozen_dependencies"]["source_contract_v2_proposal"]["path"])
    v1 = load_json(repository / contract["frozen_dependencies"]["source_contract_v1"]["path"])
    if output_dir.exists(): raise ConfirmationError("no-overwrite output already exists")
    work = output_dir.with_name(output_dir.name + ".work")
    if work.exists(): raise ConfirmationError("no-overwrite work path already exists")
    request_dir = work / request_plan["request"]["request_id"]
    request_dir.mkdir(parents=True)
    started = utc_now()
    write_new(request_dir / "reservation-01.json", {"attempt": 1, "status": "CONSUMED_AT_TRANSPORT_BOUNDARY", "request_started_at_utc": started, "request": request_plan["request"], "human_authorization_sha256": authority["observed_human_authorization_sha256"]})
    response: CapturedResponse | None = None
    validation: dict[str, Any]
    try:
        response = transport(request_plan["request"]["full_url"], 64 * 1024 * 1024)
        raw_name = "response-attempt-01.csv" if response.status == 200 else "response-attempt-01.bin"
        write_new(request_dir / raw_name, response.body)
        result = {"attempt": 1, "request_started_at_utc": response.requested_at_utc, "observed_at_utc": response.observed_at_utc, "http_status": response.status, "final_url": response.final_url, "response_headers": response.headers, "transport_complete": response.transport_complete, "byte_count": len(response.body), "sha256": sha256_bytes(response.body), "automatic_http_retry_count": 0, "replacement_request_count": 0}
        write_new(request_dir / "result-01.json", result)
        validation = validate_response(response.body, response, request_plan, v2, v1)
        write_new(request_dir / "validation-01.json", validation)
        write_new(request_dir / "receipt.json", {"schema_version": "shared-pa-statcast-v2-confirmation-receipt-v1", **result, "request_plan_sha256": sha256_file(repository / contract["frozen_dependencies"]["request_plan"]["path"]), "execution_contract_sha256": sha256_file(contract_path)})
    except Exception as exc:
        validation = {"schema_version": "shared-pa-statcast-v2-confirmation-validation-v1", "status": "FAIL", "failures": [type(exc).__name__], "message": str(exc), "source_release_produced": False, "scored_predictions_produced": False}
        write_new(request_dir / "validation-01.json", validation)
    disposition = "VALIDATION_PASSED_CONFIRMATION_PENDING_DURABLE_RETENTION_AND_INDEPENDENT_REPRODUCTION" if validation["status"] == "PASS" else "INVALID_QUARANTINED_NON_PROMOTABLE"
    manifest = {"schema_version": "shared-pa-statcast-v2-confirmation-capture-manifest-v1", "technical_validation_status": validation["status"], "scientific_disposition": disposition, "attempt": 1, "external_request_count": 1, "automatic_http_retry_count": 0, "replacement_request_count": 0, "source_qualified": False, "promotable": False, "quarantined": validation["status"] != "PASS", "durable_retention_verified": False, "independent_reproduction_verified": False, "failure_artifact_publication_is_source_qualification": False, "authority_package_sha256": authority["authority_package_sha256"], "human_authorization_sha256": authority["observed_human_authorization_sha256"], "execution_contract_sha256": sha256_file(contract_path), "created_at_utc": utc_now()}
    write_new(work / "manifest.json", manifest)
    os.replace(work, output_dir)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--expected-authority-sha256", required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--expected-workflow-sha256", required=True)
    parser.add_argument("--runtime-authority", type=Path, required=True)
    parser.add_argument("--expected-runtime-authority-sha256", required=True)
    parser.add_argument("--expected-carrier-commit", required=True)
    parser.add_argument("--human-authorization-sha256", required=True)
    parser.add_argument("--attempt3-retention-gate-receipt", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    repository = args.repository.resolve()
    authority = verify_authority(repository, args.authority.resolve(), args.expected_authority_sha256, args.workflow.resolve(), args.expected_workflow_sha256, args.runtime_authority.resolve(), args.expected_runtime_authority_sha256, args.expected_carrier_commit, args.human_authorization_sha256)
    contract = load_json(args.contract.resolve())
    verify_attempt3_retention_receipt(args.attempt3_retention_gate_receipt.resolve(), contract)
    authority["authority_package_sha256"] = args.expected_authority_sha256
    authority["observed_human_authorization_sha256"] = args.human_authorization_sha256
    result = capture(repository, args.contract.resolve(), authority, args.output_dir.resolve(), one_request_transport)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["technical_validation_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
