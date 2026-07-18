"""Fail-closed loader for the corrected pre-2026 PA refit protocol."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = {
    "hr-pre2026-pa-refit-protocol-v1",
    "hr-pre2026-pa-refit-protocol-v2",
}


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_protocol(payload: dict[str, Any], *, verify_files: bool = True) -> dict[str, Any]:
    schema = payload.get("schema_version")
    if schema not in SCHEMAS or payload.get("status") != "LOCKED_BEFORE_REFIT":
        raise ValueError("unrecognized PA refit protocol")
    if schema == "hr-pre2026-pa-refit-protocol-v2":
        prior = payload.get("supersedes_failed_protocol") or {}
        if prior.get("sha256") != "1611c6a7abeffd3b4982e36efc650550d98c5593b76f29743ea5f74922e54a3a":
            raise ValueError("failed PA v1 protocol binding changed")
        if prior.get("failure_evidence_sha256") != "e738a3a67af240c88c5012472df2715ad6b680baa88b6ce6772693ab3db3b5d6":
            raise ValueError("failed PA v1 evidence binding changed")
        if payload.get("sanity_profile") != "original_starters_a3_2":
            raise ValueError("corrected PA population profile changed")
        if verify_files:
            for path_key, hash_key in (
                ("path", "sha256"),
                ("failure_evidence_path", "failure_evidence_sha256"),
            ):
                path = ROOT / str(prior.get(path_key, ""))
                if not path.is_file() or sha256(path) != prior.get(hash_key):
                    raise ValueError(f"preserved failed PA v1 evidence changed: {path_key}")
    if payload.get("betting_authorized") is not False or payload.get("may_2026_opened") is not False:
        raise ValueError("PA refit cannot open May or authorize betting")
    if payload.get("fit_seasons") != [2023, 2024] or payload.get("excluded_seasons") != [2025, 2026]:
        raise ValueError("PA refit seasons changed")
    if payload.get("fit_before_exclusive") != "2025-01-01":
        raise ValueError("PA refit chronology changed")
    if payload.get("identity_key") != ["game_pk", "player_id"]:
        raise ValueError("PA refit identity changed")
    inputs = payload.get("inputs") or {}
    required = {
        "certified_training", "assembled_validation", "migration_protocol",
        "fitter_source", "legacy_pa_artifact",
    }
    if set(inputs) != required:
        raise ValueError("PA refit inputs changed")
    for name, record in inputs.items():
        path = record.get("path")
        expected = record.get("sha256")
        if not isinstance(path, str) or not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"PA refit input incomplete: {name}")
        if verify_files:
            source = ROOT / path
            if not source.is_file() or sha256(source) != expected:
                raise ValueError(f"PA refit input hash mismatch: {name}")
    if inputs["legacy_pa_artifact"].get("status") != "PRESERVED_NOT_REINTERPRETED":
        raise ValueError("legacy PA preservation changed")
    output = payload.get("output") or {}
    if output.get("required_source_sha256") != inputs["certified_training"]["sha256"]:
        raise ValueError("PA output source binding changed")
    if output.get("path") == inputs["legacy_pa_artifact"].get("path"):
        raise ValueError("PA refit cannot overwrite legacy artifact")
    success = payload.get("success_conditions")
    if not isinstance(success, dict) or not success or not all(v is True for v in success.values()):
        raise ValueError("every PA refit success condition is required")
    mutations = payload.get("required_mutations")
    if not isinstance(mutations, list) or len(mutations) < 7:
        raise ValueError("PA refit mutations incomplete")
    return payload


def load_protocol(path: str | Path, *, verify_files: bool = True) -> dict[str, Any]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("PA refit protocol must be an object")
    sidecar = source.with_suffix(source.suffix + ".sha256")
    if sidecar.is_file() and sidecar.read_text(encoding="utf-8").strip() != sha256(source):
        raise ValueError("PA refit protocol sidecar mismatch")
    return validate_protocol(payload, verify_files=verify_files)
