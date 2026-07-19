"""Fail-closed contracts for the multi-market probability foundation.

The evidence registry prevents a rejected experiment from being relabeled or
silently repeated.  It deliberately records readiness separately from model
quality: price inventory or an implemented output path is not evidence that a
market is predictive, profitable, or authorized.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "multi-market-probability-foundation-protocol-v1"
STATUS = "LOCKED_BEFORE_CHALLENGER_FIT"
MARKETS = ["hits", "home_runs", "total_bases", "rbi", "hrr", "strikeouts"]
PA_OUTCOMES = [
    "strikeout", "walk", "single", "double", "triple", "home_run",
    "bip_out", "other_non_ab",
]


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _at(value: Any, keys: list[str]) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            raise ValueError(f"missing asserted JSON path: {'.'.join(keys)}")
        current = current[key]
    return current


def _any_true(value: Any, forbidden_keys: set[str]) -> bool:
    if isinstance(value, dict):
        return any(
            (key in forbidden_keys and item is True)
            or _any_true(item, forbidden_keys)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_any_true(item, forbidden_keys) for item in value)
    return False


def validate_protocol(
    protocol: dict[str, Any], *, evidence_root: str | Path, verify_files: bool = True
) -> dict[str, Any]:
    if protocol.get("schema_version") != SCHEMA or protocol.get("status") != STATUS:
        raise ValueError("unrecognized multi-market foundation protocol")
    if protocol.get("source_commit") != "a6374ca7b3154a50b3a389e5ad2eda2e5634426f":
        raise ValueError("immutable production baseline commit changed")
    if protocol.get("markets") != MARKETS:
        raise ValueError("market set or order changed")
    if protocol.get("shared_pa_outcomes") != PA_OUTCOMES:
        raise ValueError("shared PA outcome accounting changed")
    if protocol.get("betting_authorized") is not False:
        raise ValueError("foundation protocol cannot authorize betting")

    chronology = protocol.get("chronology") or {}
    required_chronology = {
        "initial_fit_seasons": [2023],
        "selection_seasons": [2024],
        "confirmation_open_once_seasons": [2025],
        "open_2026_research_months": ["2026-03", "2026-04", "2026-06"],
        "may_2026_forbidden": True,
    }
    if chronology != required_chronology:
        raise ValueError("chronological separation changed")

    gates = protocol.get("material_improvement_gate") or {}
    required_gates = {
        "beat_current_model",
        "beat_strongest_simple_baseline",
        "paired_brier_improvement",
        "paired_log_loss_improvement",
        "calibration_noninferior",
        "discrimination_noninferior",
        "season_and_line_stability",
        "coverage_noninferior",
        "no_leakage",
        "no_unmeasured_fallback",
        "open_2026_economic_coherence",
    }
    if set(gates) != required_gates or not all(value is True for value in gates.values()):
        raise ValueError("material-improvement gate weakened")

    records = protocol.get("evidence")
    if not isinstance(records, list) or len(records) < 8:
        raise ValueError("prior evidence registry is incomplete")
    identifiers = [record.get("id") for record in records if isinstance(record, dict)]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate evidence id")
    required_dispositions = {
        "hits_consolidated_baseline": "ACCEPTED_BASELINE",
        "hits_contact_open_rejection": "REJECTED",
        "pitcher_contact_rejection": "REJECTED",
        "hr_batted_ball_open_rejection": "REJECTED",
        "hits_policy_rejection": "REJECTED",
        "total_bases_readiness": "READINESS_ONLY",
        "rbi_readiness": "READINESS_ONLY",
        "hrr_readiness": "READINESS_ONLY",
        "pitcher_strikeout_readiness": "READINESS_ONLY",
        "legacy_gbm_benchmark": "READINESS_ONLY",
    }
    actual_dispositions = {
        record["id"]: record.get("disposition") for record in records
    }
    if actual_dispositions != required_dispositions:
        raise ValueError("evidence disposition or required record changed")

    root = Path(evidence_root)
    training = protocol.get("training_inputs") or {}
    required_training = {
        "migration_protocol": (
            "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/migration_protocol.json",
            "3476d59538f1027150e44fd8721cafae497cbabc96f3052dec18c8f423df8bc0",
        ),
        "assembled_validation": (
            "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/assembled_validation_report.json",
            "47236d30ac63684c7fa7b70e0fa1d3d9124c8f6b9c743032aa555923bc289113",
        ),
        "corrected_hitters": (
            "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/training/training_hitters_2023_2025_statcast.csv.gz",
            "3fc38325007845d6a7a99102274f7e520ed6f4310cc2a3248ebc441afeb814c5",
        ),
        "corrected_pitchers": (
            "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/training/training_pitchers_2023_2025.csv.gz",
            "18501bac763ff82599e93e435bfe21977797ef5df3676949470222caa1b4981b",
        ),
    }
    actual_training = {
        name: (record.get("path"), record.get("sha256"))
        for name, record in training.items() if isinstance(record, dict)
    }
    if actual_training != required_training:
        raise ValueError("corrected a3.2/a4.1 training binding changed")
    for rel, expected in required_training.values():
        path = root / rel
        if verify_files and (not path.is_file() or sha256(path) != expected):
            raise ValueError(f"training input missing or hash-mismatched: {rel}")

    forbidden = {"betting_authorized", "may_2026_opened", "may_2026_holdout_read"}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("evidence record must be an object")
        rel = record.get("path")
        expected = record.get("sha256")
        disposition = record.get("disposition")
        if not isinstance(rel, str) or not isinstance(expected, str) or len(expected) != 64:
            raise ValueError("evidence record path/hash is incomplete")
        if disposition not in {"ACCEPTED_BASELINE", "REJECTED", "READINESS_ONLY"}:
            raise ValueError("invalid evidence disposition")
        path = root / rel
        if verify_files and (not path.is_file() or sha256(path) != expected):
            raise ValueError(f"evidence missing or hash-mismatched: {rel}")
        if verify_files and path.suffix.lower() == ".json":
            payload = _json(path)
            if _any_true(payload, forbidden):
                raise ValueError(f"evidence opened May or authorized betting: {rel}")
            for assertion in record.get("assertions", []):
                keys = assertion.get("path")
                if not isinstance(keys, list) or _at(payload, keys) != assertion.get("equals"):
                    raise ValueError(f"evidence assertion failed: {rel}: {keys}")

    supersession = protocol.get("supersession") or {}
    if supersession.get("hits_contact_selection") != "hits_contact_open_rejection":
        raise ValueError("later Hits contact rejection must supersede selection support")
    return protocol


def load_protocol(
    path: str | Path, *, evidence_root: str | Path, verify_files: bool = True
) -> dict[str, Any]:
    protocol = _json(Path(path))
    return validate_protocol(protocol, evidence_root=evidence_root, verify_files=verify_files)
