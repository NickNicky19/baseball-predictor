#!/usr/bin/env python3
"""Mutation-test the material boundaries of a real open-2026 source manifest."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.open_2026_benchmark_sources import validate_source_manifest  # noqa: E402


def must_fail(payload: dict, root: Path, label: str) -> None:
    try:
        validate_source_manifest(payload, evidence_root=root)
    except ValueError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    original = json.loads(args.manifest.read_text(encoding="utf-8"))
    validate_source_manifest(original, evidence_root=args.evidence_root)
    mutations = [
        ("May opened", lambda p: p.update(may_2026_opened=True)),
        ("betting authorized", lambda p: p.update(betting_authorized=True)),
        ("May date admitted", lambda p: p["dates"].append("2026-05-01")),
        ("official outcome hash", lambda p: p["artifacts"]["official_outcomes"].update(sha256="0" * 64)),
        ("official game removed", lambda p: p["official_mlb_feeds"].pop()),
        ("feature date removed", lambda p: p["feature_snapshots"].pop()),
        ("Total Bases comparator invented", lambda p: p.update(total_bases_production_comparator_available=True)),
        ("runtime changed", lambda p: p["runtime"]["module"].update(sha256="f" * 64)),
        ("crosscheck changed", lambda p: p["crosscheck"].update(hits_crosschecked=0)),
    ]
    for label, mutate in mutations:
        candidate = copy.deepcopy(original)
        mutate(candidate)
        must_fail(candidate, args.evidence_root, label)
    print("10/10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
