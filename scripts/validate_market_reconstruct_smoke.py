#!/usr/bin/env python3
"""Validate a strict-market reconstruction smoke before the full run."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.market_reconstruct_validation import validate_full, validate_smoke  # noqa: E402
from src.evaluation.strict_market_artifact import artifact_path_for_manifest  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    universe = ap.add_mutually_exclusive_group(required=True)
    universe.add_argument("--strict-manifest")
    universe.add_argument("--policy-source-manifest")
    ap.add_argument("--date", default=None,
                    help="one smoke date; omit to validate the full manifest universe")
    ap.add_argument("--frozen", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--frozen-outcomes", required=True)
    ap.add_argument("--candidate-outcomes", required=True)
    ap.add_argument("--frozen-provenance", required=True)
    ap.add_argument("--candidate-provenance", required=True)
    ap.add_argument("--chronological-protocol", default=None)
    ap.add_argument("--chronological-role", choices=("fit", "holdout"), default=None)
    args = ap.parse_args(argv)

    manifest = args.strict_manifest or args.policy_source_manifest
    artifact = artifact_path_for_manifest(manifest)
    inputs = (
        args.frozen, args.candidate, args.frozen_outcomes, args.candidate_outcomes,
        args.frozen_provenance, args.candidate_provenance, artifact,
        manifest,
    )
    if args.date:
        summary = validate_smoke(
            *inputs, args.date,
            chronological_protocol=args.chronological_protocol,
            chronological_role=args.chronological_role,
        )
        print("SMOKE VALID")
    else:
        summary = validate_full(
            *inputs,
            chronological_protocol=args.chronological_protocol,
            chronological_role=args.chronological_role,
        )
        print("FULL UNIVERSE VALID")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
