#!/usr/bin/env python3
"""Verify the benchmark retry is limited to the measured input omission."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(payload: dict) -> None:
    if payload.get("schema_version") != "open-2026-eb-benchmark-source-retry-v2":
        raise ValueError("retry schema changed")
    if payload.get("status") != "LOCKED_AFTER_MECHANICAL_INPUT_OMISSION_BEFORE_RETRY":
        raise ValueError("retry status changed")
    if payload.get("betting_authorized") is not False or payload.get("may_2026_opened") is not False or payload.get("confirmation_2025_opened") is not False:
        raise ValueError("retry opened sealed evidence or authorized betting")
    for name in ("failure_record", "unchanged_protocol"):
        record = payload[name]
        path = ROOT / record["path"]
        if not path.is_file() or sha(path) != record["sha256"]:
            raise ValueError(f"retry bound artifact changed: {name}")
    repair = payload.get("single_allowed_repair") or {}
    if repair != {
        "kind": "bind_certified_official_training_outcomes",
        "path": "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/training/training_hitters_2023_2025_statcast.csv.gz",
        "sha256": "3fc38325007845d6a7a99102274f7e520ed6f4310cc2a3248ebc441afeb814c5",
        "maximum_rows_read": 87462,
        "required_loaded_seasons": [2023, 2024],
        "forbid_loaded_year_at_or_after": 2025,
        "canonical_certificate_path": "data/analysis/shared_pa_foundation_v1/canonical_hitter_features_2023_2024.certificate_v2.json",
        "canonical_certificate_sha256": "1927f4c7fea085ce40df4a2459f150de24e644c2a7c5933ca1a74d7b09ae98b4",
    }:
        raise ValueError("retry repair scope changed")
    if len(payload.get("forbidden_changes") or []) != 12:
        raise ValueError("retry forbidden-change inventory weakened")


def must_fail(original: dict, label: str, mutate) -> None:
    payload = copy.deepcopy(original)
    mutate(payload)
    try:
        validate(payload)
    except ValueError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    path = ROOT / "config/open_2026_eb_benchmark_source_retry_v2.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate(payload)
    mutations = [
        ("May opened", lambda p: p.update(may_2026_opened=True)),
        ("2025 rows readable", lambda p: p["single_allowed_repair"].update(forbid_loaded_year_at_or_after=2026)),
        ("row boundary enlarged", lambda p: p["single_allowed_repair"].update(maximum_rows_read=131202)),
        ("training hash changed", lambda p: p["single_allowed_repair"].update(sha256="0" * 64)),
        ("protocol changed", lambda p: p["unchanged_protocol"].update(sha256="f" * 64)),
        ("forbidden rule removed", lambda p: p["forbidden_changes"].pop()),
        ("betting authorized", lambda p: p.update(betting_authorized=True)),
    ]
    for label, mutate in mutations:
        must_fail(payload, label, mutate)
    print("8/8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
