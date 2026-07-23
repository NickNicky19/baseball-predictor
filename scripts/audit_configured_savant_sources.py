#!/usr/bin/env python3
"""Outcome-blind inventory of configured Savant CSV source availability."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def audit(project_root: Path, config_paths: list[Path]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for config_path in sorted(config_paths, key=lambda path: path.as_posix()):
        raw = config_path.read_bytes()
        payload = json.loads(raw)
        value = payload.get("savant", {}).get("csv_path")
        resolved = None
        available = None
        if value not in (None, ""):
            if not isinstance(value, str):
                raise ValueError(f"{config_path}: savant.csv_path is not a string")
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = project_root / candidate
            candidate = candidate.resolve()
            try:
                resolved = str(candidate.relative_to(project_root)).replace("\\", "/")
            except ValueError:
                resolved = str(candidate)
            available = candidate.is_file()
        rows.append(
            {
                "config": str(config_path.relative_to(project_root)).replace("\\", "/"),
                "config_sha256": hashlib.sha256(raw).hexdigest(),
                "declared_csv_path": value,
                "resolved_csv_path": resolved,
                "available_regular_file": available,
            }
        )
    return {
        "artifact_version": "configured-savant-source-audit-v1",
        "configs": rows,
        "configured_count": sum(row["declared_csv_path"] not in (None, "") for row in rows),
        "configured_missing_count": sum(row["available_regular_file"] is False for row in rows),
        "source_rows_read": 0,
        "outcome_fields_read": False,
        "may_2026_touched": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("configs", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.project_root.resolve(), args.configs), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
