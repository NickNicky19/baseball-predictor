"""Fail-closed contract for the pre-2026 HR level-mapping experiment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = {
    "hr-pre2026-level-mapping-protocol-v1",
    "hr-pre2026-level-mapping-protocol-v2",
    "hr-pre2026-level-mapping-protocol-v3",
    "hr-pre2026-level-mapping-protocol-v4",
}
STATUS = "LOCKED_BEFORE_RECONSTRUCTION_OR_MAPPING_RESULTS"
EXPECTED_GRID = [0.001, 0.01, 0.1, 1.0, 10.0, 100.0]
OUTCOME_TOKENS = ("out_", "actual", "result", "won", "profit", "roi")


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(recorded: str) -> Path:
    path = Path(recorded)
    return path if path.is_absolute() else ROOT / path


def _require_exact_dates(values: Any, *, label: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} must be a non-empty list")
    rendered = [str(value) for value in values]
    if rendered != sorted(rendered) or len(rendered) != len(set(rendered)):
        raise ValueError(f"{label} must be sorted and unique")
    for value in rendered:
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError(f"{label} contains a non-YYYY-MM-DD value: {value!r}")
    return rendered


def validate_protocol_payload(payload: dict[str, Any], *, verify_files: bool = True) -> dict[str, Any]:
    if payload.get("schema_version") not in SCHEMAS or payload.get("status") != STATUS:
        raise ValueError("unrecognized HR level-mapping protocol schema/status")
    if payload.get("betting_authorized") is not False:
        raise ValueError("level-mapping protocol cannot authorize betting")
    if payload.get("may_2026_opened") is not False:
        raise ValueError("May 2026 must remain sealed")

    inputs = payload.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError("protocol inputs are missing")
    for name, record in inputs.items():
        if not isinstance(record, dict):
            raise ValueError(f"input {name!r} is not an object")
        recorded_path = record.get("path")
        recorded_hash = record.get("sha256")
        if not isinstance(recorded_path, str) or not recorded_path:
            raise ValueError(f"input {name!r} path is missing")
        if not isinstance(recorded_hash, str) or len(recorded_hash) != 64:
            raise ValueError(f"input {name!r} sha256 is missing")
        if verify_files:
            source = _resolve(recorded_path)
            if not source.is_file():
                raise ValueError(f"input {name!r} is missing: {source}")
            actual = sha256(source)
            if actual != recorded_hash:
                raise ValueError(
                    f"input {name!r} hash mismatch: expected {recorded_hash}, got {actual}"
                )

    chronology = payload.get("chronology") or {}
    calibration = _require_exact_dates(
        chronology.get("calibration_dates"), label="calibration_dates"
    )
    confirmation = _require_exact_dates(
        chronology.get("confirmation_dates_open_once"),
        label="confirmation_dates_open_once",
    )
    if set(calibration) & set(confirmation):
        raise ValueError("calibration and confirmation dates overlap")
    if max(calibration) >= min(confirmation):
        raise ValueError("confirmation must begin strictly after calibration")
    if any(not value.startswith("2025-") for value in calibration + confirmation):
        raise ValueError("the locked level-mapping date universe must be 2025 only")
    if chronology.get("date_selection_used_outcomes") is not False:
        raise ValueError("date selection cannot use outcomes")
    if chronology.get("may_2026_forbidden") is not True:
        raise ValueError("May 2026 must be explicitly forbidden")
    schema = payload.get("schema_version")
    expected_history_rules = {
        "hr-pre2026-level-mapping-protocol-v2": (
            "at least one earlier completed regular-season source date"
        ),
        "hr-pre2026-level-mapping-protocol-v3": (
            "at least one active hitter has >=8 non-null Statcast event rows inside the "
            "preserved inclusive 45-day fetch window"
        ),
        "hr-pre2026-level-mapping-protocol-v4": (
            "at least one active hitter has >=8 non-null Statcast event rows inside the "
            "preserved inclusive 45-day fetch window"
        ),
    }
    if schema in expected_history_rules:
        if chronology.get("baseline_history_rule") != expected_history_rules[schema]:
            raise ValueError(f"{schema} baseline-history eligibility rule changed")

    if verify_files:
        date_payload = json.loads(
            _resolve(inputs["date_universe"]["path"]).read_text(encoding="utf-8")
        )
        if date_payload.get("calibration_dates") != calibration:
            raise ValueError("protocol calibration dates differ from the bound date artifact")
        if date_payload.get("confirmation_dates") != confirmation:
            raise ValueError("protocol confirmation dates differ from the bound date artifact")
        if date_payload.get("dates") != calibration + confirmation:
            raise ValueError("bound date artifact does not exactly equal both protocol arms")
        if schema in {
            "hr-pre2026-level-mapping-protocol-v3",
            "hr-pre2026-level-mapping-protocol-v4",
        }:
            availability = json.loads(
                _resolve(inputs["input_availability"]["path"]).read_text(encoding="utf-8")
            )
            if availability.get("uses_outcomes_for_availability") is not False:
                raise ValueError("v3 availability cannot use outcomes")
            available = set(availability.get("available_dates") or [])
            if not set(calibration + confirmation).issubset(available):
                raise ValueError("chronology includes an uncertified input date")
        if schema == "hr-pre2026-level-mapping-protocol-v4":
            if chronology.get("builder_schema") != "a3.2":
                raise ValueError("v4 requires builder schema a3.2")
            if chronology.get("population") != "original_starters_only":
                raise ValueError("v4 requires the original-starter population")
            if date_payload.get("builder_schema") != "a3.2":
                raise ValueError("v4 date artifact is not bound to a3.2")
            if availability.get("builder_schema") != "a3.2":
                raise ValueError("v4 availability artifact is not bound to a3.2")
            pa_payload = json.loads(
                _resolve(inputs["pa_distribution"]["path"]).read_text(encoding="utf-8")
            )
            pa_provenance = pa_payload.get("provenance") or {}
            if pa_provenance.get("sanity_profile") != "original_starters_a3_2":
                raise ValueError("v4 PA artifact is not the corrected original-starter fit")
            if pa_provenance.get("fit_before_exclusive") != "2025-01-01":
                raise ValueError("v4 PA artifact chronology changed")
        signal_report = json.loads(
            _resolve(inputs["qualified_signal_report"]["path"]).read_text(encoding="utf-8")
        )
        if signal_report.get("status") != inputs["qualified_signal_report"].get(
            "required_status"
        ):
            raise ValueError("bound signal report did not qualify reconstruction")
        decision = signal_report.get("decision") or {}
        if decision.get("current_model_reconstruction_permitted") is not True:
            raise ValueError("signal report does not permit current-model reconstruction")
        if decision.get("candidate_install_permitted") is not False:
            raise ValueError("signal report unexpectedly permits candidate installation")
        if decision.get("betting_authorized") is not False:
            raise ValueError("signal report unexpectedly authorizes betting")
        if schema == "hr-pre2026-level-mapping-protocol-v4":
            if signal_report.get("protocol_sha256") != inputs["signal_screen_protocol"]["sha256"]:
                raise ValueError("v4 signal report is not bound to its locked protocol")
            if signal_report.get("input_sha256") != inputs["point_in_time_feature_source"]["sha256"]:
                raise ValueError("v4 signal report is not bound to the corrected feature source")
            required_statuses = {
                "raw_validation": "VALID_RAW_A3_2",
                "enriched_validation": "VALID_ENRICHED_A3_2_A4_1",
                "assembled_validation": "VALID_ASSEMBLED_A3_2_A4_1",
                "pa_refit_validation": "VALID_PA_REFIT_ORIGINAL_STARTERS_A3_2",
                "signal_screen_validation": "VALID_PASS_QUALIFIES_MODEL_RECONSTRUCTION",
            }
            for input_name, required_status in required_statuses.items():
                validation = json.loads(
                    _resolve(inputs[input_name]["path"]).read_text(encoding="utf-8")
                )
                if validation.get("status") != required_status:
                    raise ValueError(
                        f"v4 {input_name} did not retain status {required_status}"
                    )
                if validation.get("may_2026_opened") is not False:
                    raise ValueError(f"v4 {input_name} unexpectedly opened May 2026")
                if validation.get("betting_authorized") is not False:
                    raise ValueError(f"v4 {input_name} unexpectedly authorized betting")

    reconstruction = payload.get("reconstruction") or {}
    if reconstruction.get("simulation_seed") != 17:
        raise ValueError("simulation seed must remain the predeclared value 17")
    if reconstruction.get("required_category_and_line") != ["home_runs", 0.5]:
        raise ValueError("protocol must remain HR over 0.5 only")
    if reconstruction.get("smoke_before_full_run") is not True:
        raise ValueError("one-date smoke must precede the full reconstruction")

    mapping = payload.get("mapping") or {}
    if mapping.get("identity_key") != ["mlb_game_pk", "player_id", "game_date"]:
        raise ValueError("mapping identity key changed")
    if mapping.get("control_features") != ["raw_model_logit"]:
        raise ValueError("calibration-only control changed")
    if mapping.get("candidate_features") != ["raw_model_logit", "signal_increment"]:
        raise ValueError("candidate feature contract changed")
    if mapping.get("regularization_grid") != EXPECTED_GRID:
        raise ValueError("regularization grid changed")
    if mapping.get("outcomes_may_not_enter_signal_features") is not True:
        raise ValueError("outcomes must be forbidden from signal features")
    for feature in mapping.get("control_features", []) + mapping.get("candidate_features", []):
        lowered = feature.lower()
        if any(token in lowered for token in OUTCOME_TOKENS):
            raise ValueError(f"outcome-like mapping feature is forbidden: {feature!r}")

    checks = payload.get("success_condition")
    if not isinstance(checks, dict) or not checks or not all(value is True for value in checks.values()):
        raise ValueError("all predeclared success conditions must remain required")
    if not payload.get("required_mutations"):
        raise ValueError("required mutation list is empty")
    return payload


def load_protocol(path: str | Path, *, verify_files: bool = True) -> dict[str, Any]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("HR level-mapping protocol must be a JSON object")
    sidecar = source.with_suffix(source.suffix + ".sha256")
    if sidecar.exists():
        expected = sidecar.read_text(encoding="utf-8").strip()
        if sha256(source) != expected:
            raise ValueError("HR level-mapping protocol hash mismatch")
    return validate_protocol_payload(payload, verify_files=verify_files)
