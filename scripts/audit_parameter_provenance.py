#!/usr/bin/env python3
"""Report provenance gaps in prediction-affecting configuration.

This is an audit only: it does not change parameters, fit values, or make a
promotion decision.  Its job is to make unsupported numeric choices visible.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.parameter_provenance import audit_report, canonical_json


DEFAULT_ROOTS = (
    "league_avg",
    "weights",
    "recency",
    "pitcher_matchup",
    "simulation_slot_pa",
    "lineup_intelligence",
    "park_factors",
    "base_running",
    "simulation",
    "pa_simulator",
    "calibration",
    "edge",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/config.json", type=Path)
    parser.add_argument("--out-dir", default="reports/parameter_provenance", type=Path)
    parser.add_argument(
        "--roots",
        nargs="+",
        default=list(DEFAULT_ROOTS),
        help="Config blocks to audit; defaults to all prediction-affecting blocks.",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Exit non-zero unless every audited value has approved provenance.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    report = audit_report(config_path, config, roots=args.roots)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = config_path.stem
    json_path = args.out_dir / f"{stem}.provenance.json"
    csv_path = args.out_dir / f"{stem}.provenance.csv"
    json_path.write_text(canonical_json(report), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("path", "value", "origin", "status", "reason", "evidence", "artifact_sha256"),
        )
        writer.writeheader()
        writer.writerows(report["rows"])

    print("PARAMETER PROVENANCE AUDIT")
    print(f"  config:  {config_path}")
    print(f"  sha256:  {report['config_sha256']}")
    print(f"  values:  {len(report['rows'])}")
    print(f"  origins: {report['counts_by_origin']}")
    print(f"  verdict: {report['verdict']}")
    if report["verdict"] != "PROMOTION_ELIGIBLE":
        first = next(row for row in report["rows"] if row["status"] != "approved")
        print(f"  first blocking path: {first['path']} ({first['reason']})")
    print(f"wrote {json_path}\n      {csv_path}")

    return 0 if not args.require_complete or report["verdict"] == "PROMOTION_ELIGIBLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
