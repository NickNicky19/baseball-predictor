#!/usr/bin/env python3
"""Outcome-blind audit of zone-field denominators in pre-2026 raw Statcast."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def audit(raw_root: Path, seasons: tuple[int, ...]) -> dict[str, object]:
    if not seasons or any(season not in {2023, 2024} for season in seasons):
        raise ValueError("this locked audit accepts only the 2023/2024 development seasons")
    result: dict[str, object] = {
        "seasons": list(seasons),
        "files": 0,
        "empty_files": 0,
        "empty_file_samples": [],
        "rows": 0,
        "missing_zone": 0,
        "nonnumeric_nonmissing": 0,
        "numeric_outside_1_14": 0,
        "outside_samples": [],
        "outcome_fields_read": False,
        "may_2026_touched": False,
    }
    samples: list[float] = []
    for season in seasons:
        for path in sorted((raw_root / str(season)).glob("*.csv")):
            result["files"] = int(result["files"]) + 1
            try:
                zone = pd.read_csv(path, usecols=["zone"], low_memory=False)["zone"]
            except pd.errors.EmptyDataError:
                result["empty_files"] = int(result["empty_files"]) + 1
                empty_samples = result["empty_file_samples"]
                assert isinstance(empty_samples, list)
                if len(empty_samples) < 5:
                    empty_samples.append(str(path.relative_to(raw_root)))
                continue
            numeric = pd.to_numeric(zone, errors="coerce")
            bad = zone.notna() & numeric.isna()
            outside = numeric.notna() & ~numeric.between(1, 14)
            result["rows"] = int(result["rows"]) + len(zone)
            result["missing_zone"] = int(result["missing_zone"]) + int(zone.isna().sum())
            result["nonnumeric_nonmissing"] = int(result["nonnumeric_nonmissing"]) + int(bad.sum())
            result["numeric_outside_1_14"] = int(result["numeric_outside_1_14"]) + int(outside.sum())
            if bool(outside.any()) and len(samples) < 5:
                samples.extend(float(value) for value in numeric[outside].head(5 - len(samples)))
    result["outside_samples"] = samples
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--seasons", nargs="+", type=int, default=[2023, 2024])
    args = parser.parse_args()
    print(json.dumps(audit(args.raw_root, tuple(args.seasons)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
