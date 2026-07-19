#!/usr/bin/env python3
"""Bind cached official MLB outcomes and production lineups before scoring."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.open_2026_benchmark_sources import (  # noqa: E402
    SCHEMA, STATUS, crosscheck_certified_hitter_outcomes, load_cached_official_outcomes,
    load_lineup_snapshots, relative, sha256, validate_probability_artifact,
    validate_source_manifest,
)


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        frame.to_csv(handle, index=False, lineterminator="\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def atomic_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        handle.write((json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def artifact(root: Path, path: Path, **extra: object) -> dict:
    return {"path": relative(root, path), "sha256": sha256(path), **extra}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    output_dir = args.output_dir.resolve()

    base = evidence_root / "data/analysis/hr_over_contract_v1/batted_ball_candidate_v1/full_open_v1"
    probability_path = base / "frozen.csv"
    model_manifest_path = base / "frozen.manifest.json"
    certificate_path = base / "full_validation_certificate.json"
    reconstructed_outcomes_path = base / "outcomes_frozen.csv"
    history_path = evidence_root / "data/analysis/shared_pa_foundation_v1/cumulative_history_v4/canonical_hitter_history_2023_2024.csv.gz"
    history_certificate_path = evidence_root / "data/analysis/shared_pa_foundation_v1/cumulative_history_v4/canonical_hitter_history_2023_2024.certificate.json"
    official_training_path = evidence_root / "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/training/training_hitters_2023_2025_statcast.csv.gz"
    canonical_training_certificate_path = evidence_root / "data/analysis/shared_pa_foundation_v1/canonical_hitter_features_2023_2024.certificate_v2.json"
    canonical_selection_protocol_path = ROOT / "config/shared_pa_canonical_selection_protocol.json"
    pa_path = evidence_root / "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/pa_distribution_fit_2023_2024.json"

    model = pd.read_csv(probability_path)
    dates = validate_probability_artifact(model)
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    if certificate.get("status") != "FULL_ARTIFACTS_VALID_RESEARCH_ONLY" or certificate.get("dates") != dates:
        raise ValueError("production full-universe certificate is missing or date-mismatched")
    model_manifest = json.loads(model_manifest_path.read_text(encoding="utf-8"))
    if model_manifest.get("dates") != dates or model_manifest.get("total_bases_candidate") is not False:
        raise ValueError("production manifest dates changed or Total Bases was relabeled")

    official, feed_records = load_cached_official_outcomes(evidence_root=evidence_root, model=model)
    lineups, feature_records = load_lineup_snapshots(
        evidence_root=evidence_root, model_manifest=model_manifest, dates=dates
    )
    hitter_keys = model[model["category"].isin(["hits", "home_runs"])][["mlb_game_pk", "player_id", "game_date"]].drop_duplicates()
    if len(hitter_keys.merge(lineups, how="left", on=["mlb_game_pk", "player_id", "game_date"])) != len(hitter_keys):
        raise ValueError("lineup merge changed production hitter row count")
    missing_slot = hitter_keys.merge(lineups, how="left", on=["mlb_game_pk", "player_id", "game_date"])["lineup_slot"].isna()
    if missing_slot.any():
        raise ValueError("production hitter lacks a bound lineup slot")
    reconstructed = pd.read_csv(reconstructed_outcomes_path)
    crosscheck = crosscheck_certified_hitter_outcomes(official, reconstructed, model)
    bounded_training = pd.read_csv(official_training_path, nrows=87462, compression="gzip")
    required_training = {"season", "game_date", "player_id", "out_pa", "out_ab", "out_hits",
                         "out_doubles", "out_triples", "out_hr", "out_bb", "out_k"}
    if len(bounded_training) != 87462 or not required_training.issubset(bounded_training.columns):
        raise ValueError("bounded official training source schema or row count changed")
    loaded_seasons = [
        int(value)
        for value in sorted(pd.to_numeric(bounded_training["season"], errors="raise").astype(int).unique())
    ]
    if loaded_seasons != [2023, 2024]:
        raise ValueError("bounded official training source admitted 2025 confirmation")
    loaded_dates = bounded_training["game_date"].astype(str)

    official_path = output_dir / "official_batter_outcomes.csv"
    lineups_path = output_dir / "production_lineup_snapshots.csv"
    manifest_path = output_dir / "source_manifest.json"
    atomic_csv(official, official_path)
    atomic_csv(lineups, lineups_path)
    payload = {
        "schema_version": SCHEMA,
        "status": STATUS,
        "betting_authorized": False,
        "may_2026_opened": False,
        "production_unchanged": True,
        "dates": dates,
        "months": ["2026-03", "2026-04", "2026-06"],
        "model_key": ["mlb_game_pk", "player_id", "game_date", "category", "line"],
        "official_hitter_key": ["mlb_game_pk", "player_id", "game_date"],
        "official_outcome_source": "retained_statsapi_final_feed",
        "official_feed_count": len(feed_records),
        "official_outcome_rows": len(official),
        "lineup_snapshot_rows": len(lineups),
        "historical_outcome_boundary": {
            "maximum_rows_read": 87462,
            "loaded_rows": len(bounded_training),
            "loaded_seasons": loaded_seasons,
            "date_min": loaded_dates.min(),
            "date_max": loaded_dates.max(),
            "forbid_loaded_year_at_or_after": 2025,
        },
        "crosscheck": crosscheck,
        "total_bases_production_comparator_available": False,
        "total_bases_status": "EMPIRICAL_BAYES_VS_LEAGUE_READINESS_ONLY",
        "artifacts": {
            "production_probabilities": artifact(evidence_root, probability_path, rows=len(model)),
            "production_manifest": artifact(evidence_root, model_manifest_path),
            "production_certificate": artifact(evidence_root, certificate_path),
            "certified_reconstruction_outcomes": artifact(evidence_root, reconstructed_outcomes_path, rows=len(reconstructed)),
            "cumulative_history": artifact(evidence_root, history_path),
            "cumulative_history_certificate": artifact(evidence_root, history_certificate_path),
            "official_training_outcomes": artifact(evidence_root, official_training_path, maximum_rows_read=87462),
            "canonical_training_certificate": artifact(evidence_root, canonical_training_certificate_path),
            "canonical_selection_protocol": {
                "path": "config/shared_pa_canonical_selection_protocol.json",
                "sha256": sha256(canonical_selection_protocol_path),
            },
            "pa_distribution": artifact(evidence_root, pa_path),
            "official_outcomes": artifact(evidence_root, official_path, rows=len(official)),
            "lineup_snapshots": artifact(evidence_root, lineups_path, rows=len(lineups)),
        },
        "official_mlb_feeds": feed_records,
        "feature_snapshots": feature_records,
        "runtime": {
            "module": {
                "path": "src/evaluation/open_2026_benchmark_sources.py",
                "sha256": sha256(ROOT / "src/evaluation/open_2026_benchmark_sources.py"),
            },
            "builder": {
                "path": "scripts/build_open_2026_benchmark_sources.py",
                "sha256": sha256(ROOT / "scripts/build_open_2026_benchmark_sources.py"),
            },
        },
    }
    atomic_json(payload, manifest_path)
    validate_source_manifest(payload, evidence_root=evidence_root)
    print(f"OPEN_2026_BENCHMARK_SOURCES_BOUND dates={len(dates)} games={len(feed_records)} official_rows={len(official)}")
    print(f"manifest_sha256={sha256(manifest_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
