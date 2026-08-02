#!/usr/bin/env python3
"""Offline-only development replay for the inactive Statcast source-contract-v2 proposal."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from typing import Any
import zipfile


ZIP_SHA256 = "a2fb41e783e80e067b958b0c0666d9e53cb36fffc591e33e04a0238f9c1c8fe4"
RAW_SHA256 = "0d4c91cb2d0eabaeaeed726fcf1d4aabd7f9d888fb4c9f90789a7ba679e0a968"
RAW_BYTES = 2918703
RAW_ENTRY = "statcast-carrier-exec/data/source/shared_pa_statcast_sample_2023-07-25_attempt-03_v1/statcast-2023-07-25/response-attempt-03.csv"
RESULT_ENTRY = "statcast-carrier-exec/data/source/shared_pa_statcast_sample_2023-07-25_attempt-03_v1/statcast-2023-07-25/result-03.json"
CONTRACT_V1_SHA256 = "7078857cc1815e5c8dcecd667a9b6d69079ef31c1fc68057473d1fb1dc9ceebd"
CONTRACT_V2_SHA256 = "166c49a5ea66bf4491c9d04c7f233c7bc51c032683b9883b695236dabc637328"
TEAM_POLICY_SHA256 = "c6422eb1cfdd494e958d2623a2acdc911a1bf47f93f6b8f27186241988696b55"
EOF_POLICY_SHA256 = "b8d8dbcdee611c81029a51db74f9c005115360fbbcd3f76078857d1f6d978293"


class V2ReplayError(ValueError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _load_prior(repository: Path):
    path = repository / "scripts/validate_shared_pa_statcast_attempt3_offline_v1.py"
    spec = importlib.util.spec_from_file_location("attempt3_offline_v1", path)
    if spec is None or spec.loader is None:
        raise V2ReplayError("offline v1 evidence validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_csv_body(
    raw: bytes,
    *,
    expected_header: list[str],
    receipt_bytes: int,
    receipt_sha256: str,
    content_length: str | None,
) -> dict[str, Any]:
    """Validate transport-independent body/EOF gates without modifying raw bytes."""
    before = sha256_bytes(raw)
    failures: list[str] = []
    if len(raw) != receipt_bytes or before != receipt_sha256:
        failures.append("receipt_identity_mismatch")
    if content_length is None:
        failures.append("content_length_absent_without_transport_complete_evidence")
    elif not content_length.isdecimal() or int(content_length) != len(raw):
        failures.append("content_length_mismatch")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = ""
        failures.append("invalid_utf8")
    lowered = text.lower()
    stripped = text.lstrip()
    if any(marker in lowered for marker in ("<!doctype html", "<html", "<body", "</html>")):
        failures.append("html_signature")
    if stripped.startswith(("{", "[")):
        try:
            if isinstance(json.loads(stripped), (dict, list)):
                failures.append("json_signature")
        except json.JSONDecodeError:
            pass
    if any(marker in lowered[:8192] for marker in (
        "baseball savant error", "statcast error", "request failed", "too many requests"
    )):
        failures.append("savant_error_signature")
    rows: list[list[str]] = []
    header: list[str] = []
    if text:
        try:
            parsed = csv.reader(io.StringIO(text, newline=""), strict=True)
            header = next(parsed)
            rows = list(parsed)
        except (csv.Error, StopIteration):
            failures.append("strict_csv_parse_failure")
    if header != expected_header:
        failures.append("ordered_header_mismatch")
    if len(set(header)) != len(header):
        failures.append("duplicate_header_column")
    malformed = sum(len(row) != len(expected_header) for row in rows)
    empty = sum(not any(row) for row in rows)
    if malformed:
        failures.append("row_field_count_mismatch")
    if empty:
        failures.append("empty_row")
    after = sha256_bytes(raw)
    if after != before:
        failures.append("raw_bytes_modified")
    return {
        "passed": not failures,
        "failures": sorted(set(failures)),
        "bom_present": raw.startswith(b"\xef\xbb\xbf"),
        "terminal_newline_present": raw.endswith((b"\n", b"\r")),
        "header_column_count": len(header),
        "parsed_row_count": len(rows) - empty,
        "malformed_row_count": malformed,
        "empty_row_count": empty,
        "raw_sha256_before": before,
        "raw_sha256_after": after,
        "raw_bytes_unmodified": before == after,
    }


def analyze(artifact_zip: Path, repository: Path, artifact_id: int) -> dict[str, Any]:
    if artifact_id != 8826086488:
        raise V2ReplayError("artifact ID differs")
    zip_bytes = artifact_zip.read_bytes()
    if len(zip_bytes) != 977314 or sha256_bytes(zip_bytes) != ZIP_SHA256:
        raise V2ReplayError("artifact ZIP identity differs")
    identities = {
        "config/shared_pa_statcast_source_contract_v1.json": CONTRACT_V1_SHA256,
        "config/shared_pa_statcast_source_contract_v2_proposal.json": CONTRACT_V2_SHA256,
        "config/shared_pa_statcast_canonical_team_identity_policy_v1.json": TEAM_POLICY_SHA256,
        "config/shared_pa_statcast_csv_eof_completeness_policy_v1.json": EOF_POLICY_SHA256,
    }
    for relative, expected in identities.items():
        if sha256_bytes((repository / relative).read_bytes()) != expected:
            raise V2ReplayError(f"identity differs: {relative}")
    v2 = json.loads((repository / "config/shared_pa_statcast_source_contract_v2_proposal.json").read_text(encoding="utf-8"))
    if v2["status"] != "INACTIVE_PROPOSAL_ONLY" or v2["source_release_authorized"]:
        raise V2ReplayError("proposal activation boundary differs")
    prior = _load_prior(repository)
    prior_report = prior.analyze(artifact_zip, repository, artifact_id)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        raw = archive.read(RAW_ENTRY)
        result = json.loads(archive.read(RESULT_ENTRY).decode("utf-8"))
    if len(raw) != RAW_BYTES or sha256_bytes(raw) != RAW_SHA256:
        raise V2ReplayError("raw response identity differs")
    headers = result.get("response_headers", {})
    raw_content_type = headers.get("content-type")
    normalized_mime = raw_content_type.split(";", 1)[0].strip().lower() if isinstance(raw_content_type, str) else None
    expected_header = list(v2["expected_csv_header"]["ordered_columns"])
    body = validate_csv_body(
        raw,
        expected_header=expected_header,
        receipt_bytes=result["byte_count"],
        receipt_sha256=result["sha256"],
        content_length=headers.get("content-length"),
    )
    schema = prior_report["schema_validation"]
    plate = prior_report["plate_discipline_validation"]
    evla = prior_report["ev_launch_angle_validation"]
    game_pks = schema["distinct_game_pks"]
    identity_failures: list[str] = []
    if len(game_pks) != 15 or schema["missing_certified_game_pks"] or schema["unexpected_game_pks"]:
        identity_failures.append("certified_game_universe_mismatch")
    if schema["game_date_values"] != ["2023-07-25"] or schema["game_type_values"] != ["R"]:
        identity_failures.append("date_or_game_type_mismatch")
    if schema["duplicate_event_identity_count"] or schema["conflicting_duplicate_count"]:
        identity_failures.append("duplicate_event_identity")
    if schema["missing_game_pk_count"] or schema["missing_batter_id_count"] or schema["missing_pitcher_id_count"]:
        identity_failures.append("missing_critical_identity")
    if any(sides != ["Bot", "Top"] for sides in schema["inning_side_coverage"].values()):
        identity_failures.append("certified_side_coverage_mismatch")
    if any(len(item["observed_team_pairs"]) != 1 for item in schema["team_identity_mismatches"]):
        identity_failures.append("raw_team_label_contradiction")
    downstream_failures: list[str] = []
    if schema["unknown_pitch_description_values"]:
        downstream_failures.append("unknown_pitch_description")
    if plate["error"] is not None:
        downstream_failures.append("plate_discipline_reconciliation")
    if evla["error"] is not None or not evla["bbe_denominator_reconciles"]:
        downstream_failures.append("ev_launch_angle_reconciliation")
    transport_failures: list[str] = []
    if result.get("http_status") != 200:
        transport_failures.append("http_status")
    if raw_content_type != "application/download; charset=utf-8" or normalized_mime != "application/download":
        transport_failures.append("conditional_mime_identity")
    failures = body["failures"] + identity_failures + downstream_failures + transport_failures
    decision = "ATTEMPT_3_V2_DEVELOPMENT_REPLAY_PASSED" if not failures else "ATTEMPT_3_V2_DEVELOPMENT_REPLAY_FAILED"
    return {
        "schema_version": "shared-pa-statcast-attempt-3-v2-development-replay-v1",
        "label": "DEVELOPMENT_REPLAY_ONLY",
        "decision": decision,
        "source_qualified": False,
        "source_release_produced": False,
        "independent_confirmation": False,
        "attempt_3_v1_disposition": "CONSUMED_INVALID_UNDER_V1_QUARANTINED_NON_PROMOTABLE",
        "artifact": {"id": artifact_id, "zip_bytes": len(zip_bytes), "zip_sha256": sha256_bytes(zip_bytes)},
        "raw_response": {"bytes": len(raw), "sha256": sha256_bytes(raw), "preserved_unmodified": body["raw_bytes_unmodified"]},
        "transport": {"http_status": result.get("http_status"), "raw_content_type": raw_content_type, "normalized_mime": normalized_mime, "content_length": headers.get("content-length")},
        "body_and_eof": body,
        "schema": {
            "exact_ordered_header": schema["ordered_csv_header"],
            "header_column_count": schema["header_column_count"],
            "parsed_row_count": schema["parsed_row_count"],
            "malformed_row_count": schema["malformed_row_count"],
            "duplicate_event_identity_count": schema["duplicate_event_identity_count"],
            "missing_critical_identity_count": schema["missing_game_pk_count"] + schema["missing_batter_id_count"] + schema["missing_pitcher_id_count"],
            "distinct_game_pks": game_pks,
            "date_values": schema["game_date_values"],
            "game_type_values": schema["game_type_values"],
        },
        "canonical_team_identity": {
            "binding": "game_pk + certified official date + side + official team ID",
            "all_games_have_top_and_bottom_batting_side": "certified_side_coverage_mismatch" not in identity_failures,
            "raw_labels_preserved_as_observations": True,
            "raw_label_differences": schema["team_identity_mismatches"],
            "raw_abbreviations_used_as_identity": False,
        },
        "plate_discipline": plate,
        "ev_launch_angle": evla,
        "failures": sorted(set(failures)),
        "protected_boundaries": {
            "v1_modified": False,
            "attempt_3_retroactively_qualified": False,
            "feature_model_or_market_use": False,
            "external_request_made": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-zip", type=Path, required=True)
    parser.add_argument("--artifact-id", type=int, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = analyze(args.artifact_zip.resolve(), args.repository.resolve(), args.artifact_id)
    payload = canonical_json(report)
    if args.output:
        if args.output.exists():
            raise V2ReplayError("no-overwrite output already exists")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload)
    print(payload.decode(), end="")
    return 0 if report["decision"] == "ATTEMPT_3_V2_DEVELOPMENT_REPLAY_PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
