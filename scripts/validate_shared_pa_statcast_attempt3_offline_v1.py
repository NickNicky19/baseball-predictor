#!/usr/bin/env python3
"""Offline-only validation of the retained attempt-3 quarantined artifact."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import types
from typing import Any
import zipfile


ARTIFACT_ID = 8826086488
ARTIFACT_NAME = "shared-pa-statcast-attempt-03-30725195810"
ARTIFACT_ZIP_SHA256 = "a2fb41e783e80e067b958b0c0666d9e53cb36fffc591e33e04a0238f9c1c8fe4"
FAILURE_PACKAGE_SHA256 = "a166ab2c08c49238be1e45986764a6a7a960360d405b721602f28679e2bebdd0"
RAW_BYTES = 2918703
RAW_SHA256 = "0d4c91cb2d0eabaeaeed726fcf1d4aabd7f9d888fb4c9f90789a7ba679e0a968"
CARRIER_COMMIT = "74395e8552cf65701e526d4cba1f08a3e7f286d8"
CARRIER_PARSER_SHA256 = "ac98b4414694618127b39dd15baee5e10c2c8f49d30b61a12164b7c4c1182d8a"
CONTRACT_SHA256 = "7078857cc1815e5c8dcecd667a9b6d69079ef31c1fc68057473d1fb1dc9ceebd"
PLAN_SHA256 = "a40abd52a42ed36a49b6fc5d3d91d2202f57e7075a077f8f1abe3252df016994"
ZIP_ROOT = "statcast-carrier-exec/data/source/shared_pa_statcast_sample_2023-07-25_attempt-03_v1"
REQUEST_ROOT = f"{ZIP_ROOT}/statcast-2023-07-25"
RAW_ENTRY = f"{REQUEST_ROOT}/response-attempt-03.csv"
RESULT_ENTRY = f"{REQUEST_ROOT}/result-03.json"
RESERVATION_ENTRY = f"{REQUEST_ROOT}/reservation-03.json"
MANIFEST_ENTRY = f"{ZIP_ROOT}/manifest.json"
CONTEXT_ENTRY = f"{ZIP_ROOT}/capture_context.json"
FAILURE_ENTRY = "capture-failure-package/failure-package.json"


class OfflineValidationError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read_json(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    return json.loads(archive.read(name).decode("utf-8"))


def _load_carrier_parser(repository: Path):
    source = subprocess.check_output(
        ["git", "show", f"{CARRIER_COMMIT}:src/data/shared_pa_statcast_source_v1.py"],
        cwd=repository,
    )
    if sha256_bytes(source) != CARRIER_PARSER_SHA256:
        raise OfflineValidationError("carrier parser identity differs")
    module = types.ModuleType("attempt3_carrier_shared_pa_statcast_source_v1")
    module.__file__ = "<carrier:src/data/shared_pa_statcast_source_v1.py>"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _classify(raw: bytes) -> tuple[str, list[str], list[list[str]], dict[str, Any]]:
    if not raw:
        return "empty", [], [], {"parse_error": "body is empty"}
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig")
        encoding = "UTF-8 with BOM" if bom else "UTF-8"
    except UnicodeDecodeError as exc:
        return "another format", [], [], {"parse_error": f"UTF-8 decode failed: {exc}"}
    lowered = raw.lower()
    html = any(marker in lowered for marker in (b"<!doctype html", b"<html", b"<body", b"</html>"))
    json_error = False
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            parsed_json = json.loads(stripped)
            json_error = isinstance(parsed_json, (dict, list))
        except json.JSONDecodeError:
            pass
    savant_error = any(
        marker in lowered[:8192]
        for marker in (b"baseball savant error", b"statcast error", b"request failed", b"too many requests")
    )
    if html:
        classification = "HTML"
    elif json_error:
        classification = "JSON error object"
    else:
        classification = "CSV"
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader)
        values = list(reader)
        parse_error = None
    except (csv.Error, StopIteration) as exc:
        header, values, parse_error = [], [], f"strict CSV parse failed: {exc}"
        if classification == "CSV":
            classification = "another format"
    details = {
        "classification": classification,
        "first_64_hex": raw[:64].hex(),
        "first_64_escaped": repr(raw[:64]),
        "bom_status": "UTF-8 BOM" if bom else "none",
        "encoding_result": encoding,
        "newline_convention": "LF" if b"\n" in raw and b"\r" not in raw else "mixed_or_other",
        "lf_count": raw.count(b"\n"),
        "crlf_count": raw.count(b"\r\n"),
        "bare_cr_count": raw.count(b"\r") - raw.count(b"\r\n"),
        "terminal_newline": raw.endswith((b"\n", b"\r")),
        "delimiter": ",",
        "delimiter_consistent": bool(header) and all(len(row) == len(header) for row in values),
        "quoting_consistent": parse_error is None,
        "strict_csv_parse_error": parse_error,
        "html_markers_present": html,
        "json_error_object_present": json_error,
        "baseball_savant_error_or_warning_payload_present": savant_error,
        "stable_csv_header_present": bool(header),
    }
    return classification, header, values, details


def analyze(artifact_zip: Path, repository: Path, artifact_id: int) -> dict[str, Any]:
    if artifact_id != ARTIFACT_ID:
        raise OfflineValidationError("artifact ID differs")
    zip_bytes = artifact_zip.read_bytes()
    if sha256_bytes(zip_bytes) != ARTIFACT_ZIP_SHA256:
        raise OfflineValidationError("artifact ZIP identity differs")
    contract_path = repository / "config/shared_pa_statcast_source_contract_v1.json"
    plan_path = repository / "config/shared_pa_statcast_source_preparation_v1/sample_request_plan.json"
    if sha256_bytes(contract_path.read_bytes()) != CONTRACT_SHA256:
        raise OfflineValidationError("source contract v1 differs")
    if sha256_bytes(plan_path.read_bytes()) != PLAN_SHA256:
        raise OfflineValidationError("sample request plan differs")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    carrier = _load_carrier_parser(repository)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        inventory = [
            {"path": item.filename, "bytes": item.file_size, "sha256": sha256_bytes(archive.read(item))}
            for item in archive.infolist() if not item.is_dir()
        ]
        failure_raw = archive.read(FAILURE_ENTRY)
        raw = archive.read(RAW_ENTRY)
        if sha256_bytes(failure_raw) != FAILURE_PACKAGE_SHA256:
            raise OfflineValidationError("failure-package identity differs")
        if len(raw) != RAW_BYTES or sha256_bytes(raw) != RAW_SHA256:
            raise OfflineValidationError("raw response identity differs")
        failure = json.loads(failure_raw)
        result = _read_json(archive, RESULT_ENTRY)
        reservation = _read_json(archive, RESERVATION_ENTRY)
        manifest = _read_json(archive, MANIFEST_ENTRY)
        context = _read_json(archive, CONTEXT_ENTRY)
    if result.get("response_headers", {}).get("content-type") != "application/download; charset=utf-8":
        raise OfflineValidationError("exact safe Content-Type differs")
    if not all((failure.get("raw_bytes_retained"), failure.get("safe_response_headers_retained"),
                failure.get("attempt_journal_retained"))):
        raise OfflineValidationError("failure package did not retain required evidence")
    if (reservation.get("request_started_at_utc") != "2026-08-02T00:27:06.075755Z"
            or result.get("request_started_at_utc") != "2026-08-02T00:27:06.076349Z"
            or result.get("observed_at_utc") != "2026-08-02T00:27:11.110860Z"):
        raise OfflineValidationError("retained timestamps differ")
    for key, expected in {
        "carrier_commit": CARRIER_COMMIT,
        "runtime_attestation_sha256": "903e566a99d048a575dbe075378fd8b80ace92bf5028c6ddcbb0d200fe509ae5",
        "runtime_policy_sha256": "31b6b16063bfb0f8e1535d4475259caa10ff548423df793a9d79bd2616f5f9c3",
        "source_contract_sha256": CONTRACT_SHA256,
        "request_plan_sha256": PLAN_SHA256,
    }.items():
        observed = context.get(key) if key in context else manifest.get(key)
        if observed != expected:
            raise OfflineValidationError(f"retained identity differs: {key}")

    classification, header, values, body = _classify(raw)
    expected_header = list(contract["fixture_observed_complete_header"])
    malformed = sum(len(row) != len(header) for row in values)
    empty_rows = sum(not any(row) for row in values)
    raw_rows = [dict(zip(header, row)) for row in values if len(row) == len(header)]
    parsed_rows: list[dict[str, Any]] = []
    identities: dict[tuple[int, int, int], list[dict[str, str]]] = defaultdict(list)
    parse_errors: list[str] = []
    for index, row in enumerate(raw_rows, 2):
        try:
            parsed = dict(row)
            for field in ("game_pk", "at_bat_number", "pitch_number", "batter", "pitcher"):
                parsed[field] = carrier._positive_int(row[field], field)
            parsed["zone"] = carrier._zone(row["zone"])
            for field in ("launch_speed", "launch_angle", "launch_speed_angle"):
                parsed[field] = carrier._optional_float(row[field], field)
            parsed_rows.append(parsed)
            identities[(parsed["game_pk"], parsed["at_bat_number"], parsed["pitch_number"])].append(row)
        except Exception as exc:  # retained diagnostic, never repaired or imputed
            parse_errors.append(f"row {index}: {type(exc).__name__}: {exc}")
    duplicates = sum(len(rows) - 1 for rows in identities.values() if len(rows) > 1)
    exact_duplicates = sum(
        len(rows) - 1 for rows in identities.values()
        if len(rows) > 1 and all(row == rows[0] for row in rows[1:])
    )
    conflicting_duplicates = duplicates - exact_duplicates
    request = plan["requests"][0]
    expected_games = {int(item["game_pk"]): item for item in request["expected"]["certified_games"]}
    observed_games = sorted({int(row["game_pk"]) for row in parsed_rows})
    team_mismatches: list[dict[str, Any]] = []
    side_coverage: dict[str, list[str]] = {}
    for game_pk, game in expected_games.items():
        game_rows = [row for row in parsed_rows if row["game_pk"] == game_pk]
        side_coverage[str(game_pk)] = sorted({str(row["inning_topbot"]) for row in game_rows})
        observed_pairs = sorted({(str(row["home_team"]), str(row["away_team"])) for row in game_rows})
        expected_pair = (game["home_team_code"], game["away_team_code"])
        if observed_pairs != [expected_pair]:
            team_mismatches.append({
                "game_pk": game_pk,
                "expected_home_team": expected_pair[0], "expected_away_team": expected_pair[1],
                "observed_team_pairs": [list(pair) for pair in observed_pairs],
            })
    required = list(contract["required_raw_fields"])
    missingness = {field: sum(str(row.get(field, "")).strip() == "" for row in raw_rows) for field in required}
    known_descriptions = {
        value for values_for_category in contract["plate_discipline"]["categories"].values()
        for value in values_for_category
    }
    unknown_descriptions = sorted({str(row.get("description")) for row in raw_rows} - known_descriptions)
    plate_result = evla_result = None
    plate_error = evla_error = None
    try:
        plate_result = carrier.plate_discipline_counts(parsed_rows, contract)
    except Exception as exc:
        plate_error = f"{type(exc).__name__}: {exc}"
    try:
        evla_result = carrier.ev_launch_angle_counts(parsed_rows)
    except Exception as exc:
        evla_error = f"{type(exc).__name__}: {exc}"
    existing_parser_error = None
    certified = {
        game_pk: {
            "official_date": request["expected"]["official_date"],
            "home_team_id": game["home_team_id"], "away_team_id": game["away_team_id"],
            "home_team_code": game["home_team_code"], "away_team_code": game["away_team_code"],
        } for game_pk, game in expected_games.items()
    }
    try:
        carrier.parse_csv_bytes(
            raw, contract=contract, expected_date=request["expected"]["official_date"],
            certified_games=certified,
        )
    except Exception as exc:
        existing_parser_error = f"{type(exc).__name__}: {exc}"
    bbe_reconciles = bool(
        evla_result
        and evla_result["measured_ev_count"] + evla_result["missing_ev_count"] == evla_result["total_bbe"]
        and evla_result["measured_la_count"] + evla_result["missing_la_count"] == evla_result["total_bbe"]
        and evla_result["joint_ev_la_count"] + evla_result["joint_missing_count"] == evla_result["total_bbe"]
    )
    schema_drift = []
    if header != expected_header:
        schema_drift.append("ordered_header_differs")
    if not body["terminal_newline"]:
        schema_drift.append("terminal_newline_absent")
    if team_mismatches:
        schema_drift.append("certified_team_codes_differ")
    if parse_errors:
        schema_drift.append("row_value_parse_failure")
    if malformed:
        schema_drift.append("malformed_rows")
    if duplicates:
        schema_drift.append("duplicate_event_identity")
    if unknown_descriptions or plate_error or evla_error or not bbe_reconciles:
        schema_drift.append("downstream_transformation_failure")
    if classification != "CSV" or body["strict_csv_parse_error"]:
        body_status = "ATTEMPT_3_BODY_NOT_VALID_CSV"
    elif schema_drift or existing_parser_error:
        body_status = "ATTEMPT_3_BODY_CSV_WITH_SCHEMA_DRIFT"
    else:
        body_status = "ATTEMPT_3_BODY_VALID_CSV_UNDER_EXISTING_SCHEMA"
    contract_evidence = (
        "CONTENT_TYPE_CONTRACT_EVIDENCE_SUFFICIENT_FOR_V2_PROPOSAL"
        if body_status == "ATTEMPT_3_BODY_VALID_CSV_UNDER_EXISTING_SCHEMA"
        else "CONTENT_TYPE_CONTRACT_EVIDENCE_INSUFFICIENT"
    )
    return {
        "schema_version": "shared-pa-statcast-attempt-3-offline-body-validation-v1",
        "status": "ATTEMPT_3_ARTIFACT_INTEGRITY_PASSED",
        "body_validation_status": body_status,
        "content_type_contract_evidence_status": contract_evidence,
        "source_contract_modified": False,
        "source_qualified": False,
        "artifact": {
            "id": artifact_id, "name": ARTIFACT_NAME, "zip_sha256": ARTIFACT_ZIP_SHA256,
            "entry_count": len(inventory), "inventory": sorted(inventory, key=lambda row: row["path"]),
        },
        "failure_package_sha256": FAILURE_PACKAGE_SHA256,
        "raw_response": {"bytes": len(raw), "sha256": sha256_bytes(raw)},
        "retained_transport": {
            "http_status": result["http_status"],
            "raw_content_type": result["response_headers"]["content-type"],
            "normalized_mime_type": result["response_headers"]["content-type"].split(";", 1)[0].strip().lower(),
            "safe_response_headers": result["response_headers"],
            "request_reservation_timestamp_utc": reservation["request_started_at_utc"],
            "result_record_request_timestamp_utc": result["request_started_at_utc"],
            "response_observation_timestamp_utc": result["observed_at_utc"],
        },
        "body_classification": body,
        "schema_validation": {
            "ordered_csv_header": header, "header_column_count": len(header),
            "expected_contract_column_count": len(expected_header),
            "header_exact_match": header == expected_header,
            "missing_expected_columns": [column for column in expected_header if column not in header],
            "unexpected_columns": [column for column in header if column not in expected_header],
            "duplicate_column_names": sorted(column for column, count in Counter(header).items() if count > 1),
            "parsed_row_count": len(parsed_rows), "malformed_row_count": malformed,
            "empty_row_count": empty_rows, "row_value_parse_errors": parse_errors,
            "duplicate_event_identity_count": duplicates, "exact_duplicate_count": exact_duplicates,
            "conflicting_duplicate_count": conflicting_duplicates,
            "missing_game_pk_count": missingness["game_pk"],
            "missing_batter_id_count": missingness["batter"],
            "missing_pitcher_id_count": missingness["pitcher"],
            "distinct_game_pks": observed_games,
            "missing_certified_game_pks": sorted(set(expected_games) - set(observed_games)),
            "unexpected_game_pks": sorted(set(observed_games) - set(expected_games)),
            "game_date_values": sorted({str(row["game_date"]) for row in parsed_rows}),
            "game_type_values": sorted({str(row["game_type"]) for row in parsed_rows}),
            "home_team_values": sorted({str(row["home_team"]) for row in parsed_rows}),
            "away_team_values": sorted({str(row["away_team"]) for row in parsed_rows}),
            "team_identity_mismatches": team_mismatches,
            "inning_side_coverage": side_coverage,
            "required_fields_present": sorted(set(required) & set(header)),
            "required_fields_missing_from_header": sorted(set(required) - set(header)),
            "missingness_by_required_field": missingness,
            "unknown_pitch_description_values": unknown_descriptions,
            "existing_parser_result": existing_parser_error,
            "schema_drift_reasons": schema_drift,
        },
        "plate_discipline_validation": {"result": plate_result, "error": plate_error},
        "ev_launch_angle_validation": {
            "result": evla_result, "error": evla_error, "bbe_denominator_reconciles": bbe_reconciles,
        },
        "quarantine_disposition": {
            "invalid": True, "quarantined": True, "non_promotable": True,
            "source_qualified": False,
            "excluded_from": ["source", "feature", "model", "prediction", "prospective_evidence", "economic_evaluation", "betting"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-zip", required=True, type=Path)
    parser.add_argument("--artifact-id", required=True, type=int)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(args.artifact_zip.resolve(), args.repository.resolve(), args.artifact_id)
    payload = canonical_json(report)
    if args.output:
        if args.output.exists():
            raise OfflineValidationError("no-overwrite output already exists")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
    print(payload.decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
