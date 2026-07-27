#!/usr/bin/env python3
"""Build the 2023-only strictly-prior contact-quality feature panel."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_contact_quality import (  # noqa: E402
    FEATURE_COLUMNS,
    REQUIRED_SOURCE_COLUMNS,
    contact_quality_features,
    prepare_contact_source,
    validate_contact_feature_row,
)


PANEL_SHA256 = "643a4c6533dbe59fc4e6b5e5932683c0877ae0a5d982945c799976d36af1ebf6"
SOURCE_MANIFEST_SHA256 = "083fe961b00299f0561130e204e5264dfa05f0b234b72ce2af214ac5f681e9a4"
SOURCE_CERTIFICATE_SHA256 = "aa3fd225f946b91ab1e1249eb8b7ce415f0fccabb18a041e3960552b2aeaf8ae"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_contract(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(value.get("schema_version") == "shared-pa-contact-quality-input-contract-v1", "contact contract schema changed")
    require(value.get("status") == "LOCKED_BEFORE_2023_FEATURE_PANEL_BUILD", "contact contract is not pre-build locked")
    require(value["scope"]["years"] == [2023] and value["scope"]["outcomes_scored"] is False, "contact contract scope changed")
    require(value["features"] == list(FEATURE_COLUMNS), "contact feature order changed")
    require(value["protected_boundaries"]["may_2026_sealed"] is True, "May seal is absent")
    require(value["protected_boundaries"]["production_changed"] is False, "contact contract claims production change")
    return value


def _source_identity(
    *, panel: Path, source_manifest: Path, source_certificate: Path, contract: dict[str, Any]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    for path, expected in (
        (panel, PANEL_SHA256),
        (source_manifest, SOURCE_MANIFEST_SHA256),
        (source_certificate, SOURCE_CERTIFICATE_SHA256),
    ):
        require(path.is_file() and sha256_file(path) == expected, f"contact source identity mismatch: {path.name}")
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    certificate = json.loads(source_certificate.read_text(encoding="utf-8"))
    require(manifest.get("status") == "DIRECT_BATTER_PA_TIMING_CONTRACT_PASSED_RESEARCH_ONLY", "source timing contract is not passed")
    require(manifest.get("may_2026_opened") is False and manifest.get("confirmation_2025_opened") is False, "source manifest opened protected evidence")
    require(certificate.get("status") == "DIRECT_BATTER_PA_PANEL_CERTIFIED_RESEARCH_ONLY", "source certificate is invalid")
    raw_hashes = manifest.get("raw_source_sha256")
    require(isinstance(raw_hashes, dict) and len(raw_hashes) == contract["source"]["raw_files_expected"], "raw source hash population changed")
    require(manifest.get("raw_root") == contract["source"]["raw_root"], "raw source root differs from contract")
    identity = pd.read_csv(
        panel,
        usecols=["season", "game_date", "game_pk", "player_id"],
        low_memory=False,
    )
    require(len(identity) == 43740, "contact target identity row count changed")
    require(not identity.duplicated(["season", "game_date", "game_pk", "player_id"]).any(), "contact target identity is duplicated")
    years = pd.to_numeric(identity["season"], errors="coerce")
    dates = pd.to_datetime(identity["game_date"], format="%Y-%m-%d", errors="coerce")
    require(years.notna().all() and set(years.astype(int)) == {2023}, "contact target year changed")
    require(dates.notna().all() and dates.dt.year.eq(2023).all(), "contact target date changed")
    return identity, {"manifest": manifest, "raw_hashes": raw_hashes}


def build(
    *,
    panel: Path,
    source_manifest: Path,
    source_certificate: Path,
    contract_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    require(not output_dir.exists(), "refusing to overwrite contact-quality output")
    contract = load_contract(contract_path)
    targets, source = _source_identity(
        panel=panel,
        source_manifest=source_manifest,
        source_certificate=source_certificate,
        contract=contract,
    )
    raw_root = Path(contract["source"]["raw_root"])
    raw_hashes: dict[str, str] = source["raw_hashes"]
    records: list[dict[str, Any]] = []
    rehashed: dict[str, str] = {}
    for player_id, player_targets in targets.groupby("player_id", sort=True):
        numeric_player = int(player_id)
        relative = str(Path("2023") / f"batter_{numeric_player}.csv").replace("/", "\\")
        require(relative in raw_hashes, f"contact raw receipt is missing for player {numeric_player}")
        raw_path = raw_root / relative
        require(raw_path.is_file(), f"contact raw file is missing: {relative}")
        actual_hash = sha256_file(raw_path)
        require(actual_hash == raw_hashes[relative], f"contact raw hash mismatch: {relative}")
        rehashed[relative] = actual_hash
        try:
            raw = pd.read_csv(
                raw_path,
                usecols=lambda column: column in REQUIRED_SOURCE_COLUMNS,
                low_memory=False,
            )
        except pd.errors.EmptyDataError as exc:
            raise ValueError(f"raw receipt lacks a schema: {relative}") from exc
        prepared = prepare_contact_source(raw, player_id=numeric_player)
        by_date = {
            date: contact_quality_features(prepared, player_id=numeric_player, target_date=date)
            for date in sorted(player_targets["game_date"].astype(str).unique())
        }
        for target in player_targets.itertuples(index=False):
            features = dict(by_date[str(target.game_date)])
            validate_contact_feature_row(features)
            records.append(
                {
                    "season": int(target.season),
                    "game_date": str(target.game_date),
                    "game_pk": int(target.game_pk),
                    "player_id": numeric_player,
                    **features,
                }
            )
    require(len(rehashed) == len(raw_hashes) == 643, "not every raw contact source was rehashed")
    output = pd.DataFrame(records)
    expected_columns = ["season", "game_date", "game_pk", "player_id", *FEATURE_COLUMNS]
    require(list(output.columns) == expected_columns, "contact output column order changed")
    output = output.sort_values(["game_date", "game_pk", "player_id"], kind="mergesort").reset_index(drop=True)
    require(len(output) == len(targets), "contact output silently lost target rows")
    require(not output.duplicated(["season", "game_date", "game_pk", "player_id"]).any(), "contact output identity duplicated")
    dates = pd.to_datetime(output["game_date"], format="%Y-%m-%d", errors="raise")
    source_dates = pd.to_datetime(output["history_contact_max_source_date"], format="%Y-%m-%d", errors="coerce")
    require(not (source_dates.notna() & source_dates.ge(dates)).any(), "contact output chronology violation")
    forbidden = [name for name in output.columns if name.startswith("out_") or name.startswith("target_")]
    require(not forbidden, "contact output contains outcomes")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        panel_path = staging / "contact_quality_panel_2023.csv.gz"
        panel_bytes = gzip.compress(
            output.to_csv(index=False, lineterminator="\n").encode("utf-8"),
            compresslevel=9,
            mtime=0,
        )
        panel_path.write_bytes(panel_bytes)
        missingness = {
            name: float(output[name].isna().mean())
            for name in FEATURE_COLUMNS
        }
        identity_files = {
            "config/shared_pa_contact_quality_input_v1.json": sha256_file(contract_path),
            "scripts/build_shared_pa_contact_quality_panel.py": sha256_file(Path(__file__)),
            "src/evaluation/shared_pa_contact_quality.py": sha256_file(
                ROOT / "src/evaluation/shared_pa_contact_quality.py"
            ),
            "tests/test_shared_pa_contact_quality.py": sha256_file(
                ROOT / "tests/test_shared_pa_contact_quality.py"
            ),
        }
        manifest = {
            "schema_version": "shared-pa-contact-quality-panel-manifest-v1",
            "status": "CONTACT_QUALITY_TIMING_CONTRACT_BUILT_RESEARCH_ONLY",
            "output": {
                "path": panel_path.name,
                "bytes": panel_path.stat().st_size,
                "rows": int(len(output)),
                "sha256": sha256_file(panel_path),
            },
            "source_identity": {
                "panel_sha256": PANEL_SHA256,
                "panel_manifest_sha256": SOURCE_MANIFEST_SHA256,
                "panel_certificate_sha256": SOURCE_CERTIFICATE_SHA256,
                "raw_root": str(raw_root),
                "raw_files_rehashed": len(rehashed),
                "raw_source_sha256": rehashed,
            },
            "population": {
                "physical_target_rows": int(len(targets)),
                "output_rows": int(len(output)),
                "identity_duplicates": 0,
                "chronology_violations": 0,
                "outcome_columns": 0,
                "date_min": dates.min().date().isoformat(),
                "date_max": dates.max().date().isoformat(),
            },
            "features": {
                "ordered": list(FEATURE_COLUMNS),
                "count": len(FEATURE_COLUMNS),
                "missing_rate": missingness,
                "target_game_lineup_consumed": False,
                "expected_stat_fields_consumed": False,
                "spray_pull_consumed": False,
            },
            "identity_files_sha256": identity_files,
            "protected_boundaries": {
                "2024_opened": False,
                "2025_opened": False,
                "may_2026_opened": False,
                "outcomes_scored": False,
                "production_changed": False,
                "betting_authorized": False,
            },
        }
        (staging / "contact_quality_manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--source-certificate", required=True, type=Path)
    parser.add_argument(
        "--contract-path",
        type=Path,
        default=ROOT / "config/shared_pa_contact_quality_input_v1.json",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    result = build(**vars(parser.parse_args()))
    print(json.dumps({"status": result["status"], "rows": result["output"]["rows"], "sha256": result["output"]["sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
