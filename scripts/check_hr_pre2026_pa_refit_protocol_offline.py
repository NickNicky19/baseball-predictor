#!/usr/bin/env python3
"""Mutation checks for the corrected pre-2026 PA refit protocol."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_pre2026_pa_refit import load_protocol, validate_protocol  # noqa: E402

PROTOCOL = ROOT / "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/pa_refit_protocol_v2.json"


def must_fail(payload: dict, label: str) -> None:
    try:
        validate_protocol(payload, verify_files=True)
    except ValueError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation passed: {label}")


def main() -> int:
    original = load_protocol(PROTOCOL, verify_files=True)
    print("[OK] production PA refit protocol and source hashes validate")
    changes = [
        ("training hash", lambda p: p["inputs"]["certified_training"].update(sha256="0" * 64)),
        ("wrong population profile", lambda p: p.update(sanity_profile="legacy_final_occupants")),
        ("failed-v1 evidence", lambda p: p["supersedes_failed_protocol"].update(failure_evidence_sha256="0" * 64)),
        ("2025 admitted", lambda p: p.update(fit_seasons=[2023, 2024, 2025])),
        ("late fit boundary", lambda p: p.update(fit_before_exclusive="2026-01-01")),
        ("uncertified source", lambda p: p["inputs"]["certified_training"].update(path="data/training/training_hitters_2023_2025_statcast.csv.gz")),
        ("legacy overwrite", lambda p: p["output"].update(path=p["inputs"]["legacy_pa_artifact"]["path"])),
        ("May opened", lambda p: p.update(may_2026_opened=True)),
        ("betting authorized", lambda p: p.update(betting_authorized=True)),
    ]
    for label, mutate in changes:
        candidate = copy.deepcopy(original)
        mutate(candidate)
        must_fail(candidate, label)
    print("10/10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
