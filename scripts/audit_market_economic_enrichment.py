#!/usr/bin/env python3
"""Validate a separate raw-payout enrichment of a strict-market baseline.

This does not select bets, fit thresholds, size stakes, or alter a capture
baseline.  It proves only that the enriched CSV is byte-for-value equivalent to
the selected baseline on every old field and adds exact, internally consistent
decimal payout prices for later research.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.market_economic_artifact import validate_economic_enrichment  # noqa: E402


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", required=True,
                    help="immutable strict-market CSV already used for the baseline")
    ap.add_argument("--enriched", required=True,
                    help="new CSV built with --include-raw-odds")
    ap.add_argument("--out", required=True,
                    help="new JSON audit output; never overwrites either artifact")
    args = ap.parse_args(argv)

    baseline_path = Path(args.baseline)
    enriched_path = Path(args.enriched)
    report_path = Path(args.out)
    if not baseline_path.exists() or not enriched_path.exists():
        raise FileNotFoundError("both --baseline and --enriched must exist")
    if report_path.resolve() in {baseline_path.resolve(), enriched_path.resolve()}:
        raise ValueError("--out must be a distinct audit report, never a market artifact")

    summary = validate_economic_enrichment(
        pd.read_csv(baseline_path), pd.read_csv(enriched_path)
    )
    payload = {
        "kind": "market_economic_enrichment_audit_v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "baseline": {"path": str(baseline_path), "sha256": sha256(baseline_path)},
        "enriched": {"path": str(enriched_path), "sha256": sha256(enriched_path)},
        "summary": summary,
        "verdict": "INPUT_VALIDATED_RESEARCH_ONLY",
        "verdict_reason": (
            "Exact decimal payout inputs were validated. This says nothing about "
            "whether a model has positive expected value, which bets to select, "
            "or whether betting is authorized."
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print("ECONOMIC ENRICHMENT VALID")
    print(f"  rows: {summary['rows']:,}")
    print(f"  dates: {len(summary['official_date_universe'])}")
    print(f"  baseline sha: {payload['baseline']['sha256'][:16]}")
    print(f"  enriched sha: {payload['enriched']['sha256'][:16]}")
    print(f"  wrote: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
