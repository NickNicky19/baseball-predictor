#!/usr/bin/env python3
"""Verify all hash-bound sources and mutation guards in the foundation record."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation_status import (  # noqa: E402
    MultiMarketFoundationStatusError,
    validate_status,
)


def must_fail(payload: dict, roots: list[Path], label: str) -> None:
    try:
        validate_status(payload, evidence_roots=roots)
    except MultiMarketFoundationStatusError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-evidence-root", required=True, type=Path)
    args = parser.parse_args(argv)
    status_path = ROOT / "reports" / "multi_market_probability_foundation_status_v1.json"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    roots = [ROOT, args.parent_evidence_root.resolve()]
    validate_status(payload, evidence_roots=roots)
    print("[OK] multi-market foundation sources, hashes, states, and May incident validate")
    changes = [
        ("May incident erased", lambda p: p.update(may_2026_opened=False)),
        ("betting authorization", lambda p: p.update(betting_authorized=True)),
        ("HR full-game promoted", lambda p: p["market_status"]["home_runs_over_0_5"].update(current_state="FULL_GAME_SURVIVED")),
        ("evidence hash drift", lambda p: p["evidence"]["hits_1_5_market_gate"].update(sha256="0" * 64)),
        ("v13 protection weakened", lambda p: p["protected_invariants"].update(v13_operational_smoke_unchanged_and_non_economic=False)),
        ("operational repair misrepresented", lambda p: p["operational_prerequisite_before_any_future_collection"].update(not_a_model_or_betting_change=False)),
    ]
    for label, mutate in changes:
        candidate = copy.deepcopy(payload)
        mutate(candidate)
        must_fail(candidate, roots, label)
    print("6/6 mutations rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
