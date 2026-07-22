#!/usr/bin/env python3
"""Verify assembled a3.2/a4.1 files are exact ordered shard concatenations."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_pre2026_a3_2_migration import load_protocol, sha256  # noqa: E402

KEY = ["game_pk", "player_id"]


def _assert_equal(expected: pd.DataFrame, actual: pd.DataFrame, label: str) -> None:
    try:
        pd.testing.assert_frame_equal(
            expected.reset_index(drop=True), actual.reset_index(drop=True),
            check_dtype=False, check_exact=True,
        )
    except AssertionError as exc:
        raise ValueError(f"assembled {label} differs from ordered shards") from exc


def validate(protocol_path: Path, report_out: Path | None) -> dict[str, Any]:
    protocol = load_protocol(protocol_path, verify_files=True)
    root = ROOT / protocol["outputs"]["root"]
    seasons = [int(v) for v in protocol["seasons"]]
    hitter_parts: list[pd.DataFrame] = []
    pitcher_parts: list[pd.DataFrame] = []
    for season in seasons:
        manifest = json.loads((root / f"manifest_{season}.json").read_text(encoding="utf-8"))
        for date in sorted(manifest["dates"]):
            hitter_parts.append(pd.read_csv(root / str(season) / f"hitters_{date}_statcast.csv"))
            pitcher_parts.append(pd.read_csv(root / str(season) / f"pitchers_{date}.csv"))
    expected_h = pd.concat(hitter_parts, ignore_index=True)
    expected_p = pd.concat(pitcher_parts, ignore_index=True)
    assembled_h_path = root / "training_hitters_2023_2025_statcast.csv.gz"
    assembled_p_path = root / "training_pitchers_2023_2025.csv.gz"
    assembled_h = pd.read_csv(assembled_h_path)
    assembled_p = pd.read_csv(assembled_p_path)
    _assert_equal(expected_h, assembled_h, "hitters")
    _assert_equal(expected_p, assembled_p, "pitchers")
    for label, frame in (("hitters", assembled_h), ("pitchers", assembled_p)):
        if frame[KEY].isna().any().any() or frame.duplicated(KEY).any():
            raise ValueError(f"assembled {label} identity invalid")
        if set(frame.builder_schema.astype(str)) != {"a3.2"}:
            raise ValueError(f"assembled {label} builder schema invalid")
        if set(frame.season.astype(int)) != set(seasons):
            raise ValueError(f"assembled {label} seasons invalid")
    if set(assembled_h.roller_schema.astype(str)) != {"a4.1"}:
        raise ValueError("assembled hitter roller schema invalid")
    report = {
        "status": "VALID_ASSEMBLED_A3_2_A4_1",
        "seasons": seasons,
        "hitter_rows": len(assembled_h),
        "pitcher_rows": len(assembled_p),
        "hitter_sha256": sha256(assembled_h_path),
        "pitcher_sha256": sha256(assembled_p_path),
        "protocol_sha256": sha256(protocol_path),
        "raw_validation_sha256": sha256(protocol_path.parent / "raw_validation_report.json"),
        "enriched_validation_sha256": sha256(protocol_path.parent / "enriched_validation_report.json"),
        "may_2026_opened": False,
        "betting_authorized": False,
    }
    if report_out is not None:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        tmp = report_out.with_suffix(report_out.suffix + ".tmp")
        tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        tmp.replace(report_out)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--report-out", type=Path)
    args = parser.parse_args()
    report = validate(args.protocol.resolve(), args.report_out.resolve() if args.report_out else None)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
