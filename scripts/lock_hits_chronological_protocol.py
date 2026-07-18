#!/usr/bin/env python3
"""Lock the outcome-blind hits fit/holdout chronology and pin every input."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.chronological_market_protocol import build_protocol  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-manifest", required=True)
    parser.add_argument("--fit-manifest", required=True)
    parser.add_argument("--holdout-manifest", required=True)
    parser.add_argument("--holdout-start", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    payload = build_protocol(
        parent_manifest=args.parent_manifest,
        fit_manifest=args.fit_manifest,
        holdout_manifest=args.holdout_manifest,
        holdout_start=args.holdout_start,
        output=args.out,
    )
    print("CHRONOLOGICAL PROTOCOL LOCKED — RESEARCH ONLY")
    print(f"  fit     {payload['arms']['fit']['dates']} dates  "
          f"{payload['arms']['fit']['rows']:,} rows")
    print(f"  holdout {payload['arms']['holdout']['dates']} dates  "
          f"{payload['arms']['holdout']['rows']:,} rows")
    print(f"  holdout starts {payload['holdout_start']}")
    print(f"  sha256 {payload['protocol_sha256']}")
    print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
