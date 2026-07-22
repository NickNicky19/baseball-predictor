#!/usr/bin/env python3
"""Audit factual prediction-input fallback rates from persisted feature bundles.

Run this only after a daily prediction was made with ``persist_features=True``.
It does not fetch live data, re-run simulation, alter predictions, or create a
betting recommendation.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.prediction_health import health_rows, summarize_health
from src.features.feature_store import FeatureManifestError, FeatureStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Feature-bundle date (YYYY-MM-DD)")
    parser.add_argument(
        "--category",
        action="append",
        required=True,
        choices=("hits", "home_runs", "hrr", "total_bases"),
        help="Explicit hitter projection category to audit; repeat for several.",
    )
    parser.add_argument("--feature-root", default="data/features")
    parser.add_argument("--out-dir", default="reports/prediction_input_health")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = FeatureStore(args.feature_root)
    try:
        manifest = store.verify_manifest(args.date)
    except FeatureManifestError as exc:
        print(f"[FAIL] Feature manifest verification failed: {exc}")
        return 3

    bundles = store.load(args.date)
    if not bundles:
        print(
            f"[FAIL] No persisted feature bundles for {args.date}. "
            "Run the daily predictor with persist_features=True first."
        )
        return 2

    rows = health_rows(bundles, categories=tuple(args.category))
    summary = summarize_health(rows)
    summary["game_date"] = args.date
    summary["categories"] = list(args.category)
    summary["feature_manifest"] = (
        {"status": "verified", "path": str(store.manifest_path(args.date)), "manifest": manifest}
        if manifest is not None
        else {
            "status": "legacy_unverified",
            "reason": "No manifest exists; this snapshot predates provenance capture.",
        }
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"input_health_{args.date}"
    json_path = out_dir / f"{stem}.json"
    csv_path = out_dir / f"{stem}.csv"
    json_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")

    fields = [key for key in rows[0] if key != "flags"] + ["flags"]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "flags": ";".join(row["flags"])})

    print("PREDICTION INPUT-HEALTH AUDIT")
    print("  factual fallback/source labels only; no calibrated uncertainty or betting verdict")
    print(f"  bundles: {len(bundles)}   projection rows: {summary['projection_rows']}")
    print(f"  feature manifest: {summary['feature_manifest']['status']}")
    for category, values in summary["by_category"].items():
        print(f"\n  {category}: {values['unique_player_games']} player-games")
        for flag, count in values["flag_counts"].items():
            print(f"    {flag}: {count}")
    print(f"\nwrote {json_path}\n      {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
