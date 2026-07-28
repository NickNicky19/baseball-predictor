#!/usr/bin/env python3
"""Validate and certify a 2023 contact-quality feature panel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_contact_quality import (  # noqa: E402
    FEATURE_COLUMNS,
    validate_contact_feature_row,
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate(*, output_dir: Path, certificate_path: Path | None = None) -> dict[str, Any]:
    panel_path = output_dir / "contact_quality_panel_2023.csv.gz"
    manifest_path = output_dir / "contact_quality_manifest.json"
    require(panel_path.is_file() and manifest_path.is_file(), "contact panel or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == "shared-pa-contact-quality-panel-manifest-v1", "contact manifest schema changed")
    require(manifest.get("status") == "CONTACT_QUALITY_TIMING_CONTRACT_BUILT_RESEARCH_ONLY", "contact manifest status changed")
    identity = manifest.get("output", {})
    require(identity.get("sha256") == sha256_file(panel_path), "contact panel hash mismatch")
    require(identity.get("bytes") == panel_path.stat().st_size, "contact panel size mismatch")
    boundaries = manifest.get("protected_boundaries", {})
    require(
        boundaries == {
            "2024_opened": False,
            "2025_opened": False,
            "may_2026_opened": False,
            "outcomes_scored": False,
            "production_changed": False,
            "betting_authorized": False,
        },
        "contact protected boundary changed",
    )
    frame = pd.read_csv(panel_path, low_memory=False)
    expected = ["season", "game_date", "game_pk", "player_id", *FEATURE_COLUMNS]
    require(list(frame.columns) == expected, "contact panel column surface changed")
    require(len(frame) == identity.get("rows") == 43740, "contact panel row count changed")
    key = ["season", "game_date", "game_pk", "player_id"]
    require(frame[key].notna().all().all() and not frame.duplicated(key).any(), "contact panel identity is invalid")
    years = pd.to_numeric(frame["season"], errors="coerce")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    source = pd.to_datetime(frame["history_contact_max_source_date"], format="%Y-%m-%d", errors="coerce")
    require(years.notna().all() and set(years.astype(int)) == {2023}, "contact panel year changed")
    require(dates.notna().all() and dates.dt.year.eq(2023).all(), "contact panel date changed")
    require(not (source.notna() & source.ge(dates)).any(), "contact panel chronology violation")
    require(not any(name.startswith("out_") or name.startswith("target_") for name in frame.columns), "contact panel contains outcomes")
    for values in frame.loc[:, FEATURE_COLUMNS].to_dict(orient="records"):
        normalized = {key: (None if pd.isna(value) else value) for key, value in values.items()}
        validate_contact_feature_row(normalized)
    result = {
        "schema_version": "shared-pa-contact-quality-panel-certificate-v1",
        "status": "CONTACT_QUALITY_PANEL_CERTIFIED_RESEARCH_INPUT_ONLY",
        "panel": {
            "path": panel_path.name,
            "bytes": panel_path.stat().st_size,
            "rows": int(len(frame)),
            "sha256": sha256_file(panel_path),
        },
        "manifest": {
            "path": manifest_path.name,
            "bytes": manifest_path.stat().st_size,
            "sha256": sha256_file(manifest_path),
        },
        "validation": {
            "feature_columns": len(FEATURE_COLUMNS),
            "identity_duplicates": 0,
            "chronology_violations": 0,
            "outcome_columns": 0,
            "raw_files_rehashed": manifest["source_identity"]["raw_files_rehashed"],
        },
        "validator_sha256": sha256_file(Path(__file__)),
        "protected_boundaries": boundaries,
    }
    if certificate_path is not None:
        require(not certificate_path.exists(), "refusing to overwrite contact certificate")
        certificate_path.write_text(
            json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--certificate-path", type=Path)
    result = validate(**vars(parser.parse_args()))
    print(json.dumps({"status": result["status"], "rows": result["panel"]["rows"], "sha256": result["panel"]["sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
