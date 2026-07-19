#!/usr/bin/env python3
"""Mutation checks for the multi-market foundation evidence contract."""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import load_protocol, validate_protocol  # noqa: E402


def must_fail(payload: dict, evidence_root: Path, label: str) -> None:
    try:
        validate_protocol(payload, evidence_root=evidence_root, verify_files=True)
    except ValueError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    protocol_path = ROOT / "config/multi_market_probability_foundation_protocol.json"
    original = load_protocol(protocol_path, evidence_root=args.evidence_root, verify_files=True)
    print("[OK] evidence hashes, dispositions, chronology, and gates validate")

    changes = [
        ("source commit", lambda p: p.update(source_commit="0" * 40)),
        ("May admitted", lambda p: p["chronology"].update(may_2026_forbidden=False)),
        ("2025 used for selection", lambda p: p["chronology"].update(selection_seasons=[2024, 2025])),
        ("market removed", lambda p: p["markets"].remove("rbi")),
        ("PA outcome removed", lambda p: p["shared_pa_outcomes"].remove("other_non_ab")),
        ("old hitter training", lambda p: p["training_inputs"]["corrected_hitters"].update(path="data/training/training_hitters_2023_2025_statcast.csv.gz")),
        ("gate weakened", lambda p: p["material_improvement_gate"].update(paired_log_loss_improvement=False)),
        ("evidence hash", lambda p: p["evidence"][0].update(sha256="0" * 64)),
        ("rejection relabeled", lambda p: p["evidence"][1].update(disposition="ACCEPTED_BASELINE")),
        ("supersession removed", lambda p: p.update(supersession={})),
        ("betting authorized", lambda p: p.update(betting_authorized=True)),
    ]
    for label, mutate in changes:
        candidate = copy.deepcopy(original)
        mutate(candidate)
        must_fail(candidate, args.evidence_root, label)
    print("12/12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
