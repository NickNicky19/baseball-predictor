"""Fail-closed validator for the pre-2026 a3.2 identity migration protocol."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = {
    "hr-pre2026-a3.2-migration-protocol-v1",
    "hr-pre2026-a3.2-migration-protocol-v2",
}
STATUS = "LOCKED_BEFORE_EXPENSIVE_REBUILD"


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_protocol(payload: dict[str, Any], *, verify_files: bool = True) -> dict[str, Any]:
    schema = payload.get("schema_version")
    if schema not in SCHEMAS or payload.get("status") != STATUS:
        raise ValueError("unrecognized a3.2 migration protocol")
    if schema == "hr-pre2026-a3.2-migration-protocol-v2":
        prior = payload.get("supersedes_failed_protocol") or {}
        if prior.get("schema_version") != "hr-pre2026-a3.2-migration-protocol-v1":
            raise ValueError("v2 must identify the failed v1 protocol")
        if prior.get("sha256") != "a36eb24c88efb026432a5fecec59eaf60b46c9bd79d4f204d57a3ebafba54f2b":
            raise ValueError("v2 failed-protocol hash changed")
        if verify_files:
            for path_key, hash_key in (
                ("path", "sha256"),
                ("stdout_path", "stdout_sha256"),
                ("stderr_path", "stderr_sha256"),
            ):
                preserved = ROOT / str(prior.get(path_key, ""))
                if not preserved.is_file() or sha256(preserved) != prior.get(hash_key):
                    raise ValueError(f"preserved v1 evidence changed: {path_key}")
        defect = payload.get("known_failure_addressed") or {}
        if defect.get("game_pk") != 746942 or defect.get("player_id") != 643376:
            raise ValueError("v2 dual-team identity canary changed")
        if defect.get("old_behavior") != "player_id role collapse":
            raise ValueError("v2 old failure behavior changed")
        if defect.get("required_behavior") != "team-scoped original-lineup extraction":
            raise ValueError("v2 required identity behavior changed")
    if payload.get("betting_authorized") is not False or payload.get("may_2026_opened") is not False:
        raise ValueError("migration cannot open May or authorize betting")
    if payload.get("seasons") != [2023, 2024, 2025]:
        raise ValueError("migration seasons changed")
    if payload.get("identity_key") != ["game_pk", "player_id"]:
        raise ValueError("migration identity key changed")
    if payload.get("builder_schema") != "a3.2" or payload.get("roller_schema") != "a4.1":
        raise ValueError("migration schemas changed")
    game = payload.get("game_contract") or {}
    if game.get("allowed_game_types") != ["R"] or game.get("required_state") != "Final":
        raise ValueError("only final regular-season games are permitted")
    if game.get("starters_per_game") != 18:
        raise ValueError("original-starter count changed")
    if game.get("spring_training_forbidden") is not True or game.get("postseason_forbidden") is not True:
        raise ValueError("non-regular games must remain forbidden")
    if "sequence 0" not in str(game.get("hitter_population")):
        raise ValueError("original-starter sequence contract changed")

    inputs = payload.get("inputs") or {}
    if not inputs:
        raise ValueError("migration inputs missing")
    for name, record in inputs.items():
        path = record.get("path") if isinstance(record, dict) else None
        expected = record.get("sha256") if isinstance(record, dict) else None
        if not isinstance(path, str) or not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"input {name!r} is incomplete")
        if verify_files:
            source = Path(path) if Path(path).is_absolute() else ROOT / path
            if not source.is_file() or sha256(source) != expected:
                raise ValueError(f"input {name!r} missing or hash-mismatched")
    for name in ("legacy_training_evidence", "legacy_pa_evidence", "legacy_signal_report"):
        status = inputs.get(name, {}).get("status")
        if status not in {"PRESERVED_NOT_REINTERPRETED", "PRESERVED_REQUIRES_REFIT", "PRESERVED_REQUIRES_RECERTIFICATION"}:
            raise ValueError(f"legacy preservation status changed for {name}")

    chronology = payload.get("derived_evidence_chronology") or {}
    if chronology.get("pa_fit_seasons") != [2023, 2024]:
        raise ValueError("PA fit must remain 2023-2024 only")
    if chronology.get("signal_fit_seasons") != [2023]:
        raise ValueError("signal fit season changed")
    if chronology.get("signal_selection_seasons") != [2024]:
        raise ValueError("signal selection season changed")
    if chronology.get("signal_confirmation_open_once_seasons") != [2025]:
        raise ValueError("signal confirmation season changed")
    if chronology.get("may_2026_forbidden") is not True:
        raise ValueError("May 2026 must remain forbidden")
    success = payload.get("required_success_conditions")
    if not isinstance(success, dict) or not success or not all(value is True for value in success.values()):
        raise ValueError("every migration success condition must remain required")
    mutations = payload.get("required_mutations")
    if not isinstance(mutations, list) or len(mutations) < 10:
        raise ValueError("migration mutation set is incomplete")
    return payload


def load_protocol(path: str | Path, *, verify_files: bool = True) -> dict[str, Any]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("migration protocol must be an object")
    sidecar = source.with_suffix(source.suffix + ".sha256")
    if sidecar.is_file() and sidecar.read_text(encoding="utf-8").strip() != sha256(source):
        raise ValueError("migration protocol hash sidecar differs")
    return validate_protocol(payload, verify_files=verify_files)
