#!/usr/bin/env python3
"""Independently validate and certify the direct batter PA panel."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path: os.sys.path.insert(0, str(ROOT))
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.features.direct_batter_pa_history import OUTCOMES  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""): digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists(): raise FileExistsError(f"refusing to overwrite certificate: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def validate_batted_ball_lineage(frame: pd.DataFrame) -> None:
    """Prove serialized direct-history counts, denominator, and rates agree."""
    columns = [
        "history_batted_ball_denominator", "history_barrel_count",
        "history_hard_hit_count", "history_barrel_rate", "history_hard_hit_rate",
    ]
    if missing := sorted(set(columns).difference(frame.columns)):
        raise ValueError(f"batted-ball lineage columns missing: {missing}")
    try:
        values = frame[columns].apply(pd.to_numeric, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError("batted-ball lineage contains nonnumeric values") from exc
    partial = values.notna().any(axis=1) & ~values.notna().all(axis=1)
    if partial.any():
        raise ValueError("partial batted-ball count/rate lineage")
    present = values.notna().all(axis=1)
    if not present.any():
        raise ValueError("batted-ball count/rate lineage has no measured rows")
    measured = values.loc[present]
    denominator = measured["history_batted_ball_denominator"]
    barrels = measured["history_barrel_count"]
    hard_hits = measured["history_hard_hit_count"]
    if (denominator <= 0).any() or (barrels < 0).any() or (hard_hits < 0).any():
        raise ValueError("invalid batted-ball counts or denominator")
    if not denominator.mod(1).eq(0).all() or not barrels.mod(1).eq(0).all() or not hard_hits.mod(1).eq(0).all():
        raise ValueError("batted-ball counts and denominator must be integers")
    if (barrels > hard_hits).any() or (hard_hits > denominator).any():
        raise ValueError("impossible batted-ball count ordering")
    expected_barrel = barrels / denominator
    expected_hard_hit = hard_hits / denominator
    if not (measured["history_barrel_rate"] - expected_barrel).abs().le(1e-12).all():
        raise ValueError("barrel count/rate serialization mismatch")
    if not (measured["history_hard_hit_rate"] - expected_hard_hit).abs().le(1e-12).all():
        raise ValueError("hard-hit count/rate serialization mismatch")


def validate(*, panel: Path, manifest: Path, raw_root: Path, certificate: Path) -> dict[str, Any]:
    source = json.loads(manifest.read_text(encoding="utf-8"))
    if source.get("status") != "DIRECT_BATTER_PA_TIMING_CONTRACT_PASSED_RESEARCH_ONLY":
        raise ValueError("panel manifest status is not admissible")
    if source.get("output", {}).get("sha256") != sha256_file(panel):
        raise ValueError("panel content hash mismatch")
    raw_hashes = source.get("raw_source_sha256")
    if not isinstance(raw_hashes, dict) or not raw_hashes:
        raise ValueError("raw source hashes are missing")
    for relative, expected in raw_hashes.items():
        path = raw_root / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"raw source hash mismatch: {relative}")
    frame = pd.read_csv(panel, low_memory=False)
    if len(frame) != source["output"]["rows"]:
        raise ValueError("panel row count mismatch")
    if frame.duplicated(["game_pk", "player_id"]).any():
        raise ValueError("panel identity is duplicated")
    years = pd.to_numeric(frame["season"], errors="raise").astype(int)
    if set(years.unique()) != {2023, 2024}:
        raise ValueError("panel contains a forbidden year")
    target = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    source_date = pd.to_datetime(frame["max_source_date"], format="%Y-%m-%d", errors="coerce")
    if target.isna().any() or (source_date.notna() & source_date.ge(target)).any():
        raise ValueError("panel chronology is invalid")
    counts = outcome_counts(frame)
    if (counts < 0).any().any() or not counts.sum(axis=1).eq(pd.to_numeric(frame["out_pa"], errors="raise")).all():
        raise ValueError("panel PA outcomes are invalid")
    target_columns = [f"target_{name}" for name in OUTCOMES]
    if missing := sorted(set(target_columns).difference(frame.columns)):
        raise ValueError(f"raw target columns missing: {missing}")
    raw_truth = frame[target_columns].rename(columns={f"target_{name}": name for name in OUTCOMES}).astype(int)
    if not counts.loc[:, OUTCOMES].astype(int).equals(raw_truth):
        raise ValueError("probability consumer outcome columns do not equal raw terminal truth")
    if any(column.startswith(("opp_sp_", "weather_", "park_", "umpire_")) for column in frame.columns):
        raise ValueError("forbidden context entered direct batter panel")
    history_columns = [column for column in frame.columns if column.startswith("history_") or column.startswith("days_since_")]
    if not history_columns:
        raise ValueError("direct batter feature set is empty")
    validate_batted_ball_lineage(frame)
    result = {
        "schema_version": "direct-batter-pa-panel-certificate-v1",
        "status": "DIRECT_BATTER_PA_PANEL_CERTIFIED_RESEARCH_ONLY",
        "panel": {"path": str(panel), "sha256": sha256_file(panel), "rows": len(frame)},
        "manifest": {"path": str(manifest), "sha256": sha256_file(manifest)},
        "validation": {
            "raw_files_rehashed": len(raw_hashes), "identity_duplicates": 0,
            "chronology_violations": 0, "feature_columns": len(history_columns),
            "fit_rows_2023": int(years.eq(2023).sum()), "selection_rows_2024": int(years.eq(2024).sum()),
            "zero_pa_rows": int(counts.sum(axis=1).eq(0).sum()),
            "probability_consumer_raw_truth_parity": True,
        },
        "protected_invariants": {
            "confirmation_2025_opened": False, "may_2026_opened": False,
            "pitcher_matchup_features": False, "production_changed": False,
            "betting_authorized": False,
        },
        "validator_sha256": sha256_file(Path(__file__)),
    }
    atomic_json(certificate, result)
    return result


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel",required=True,type=Path); parser.add_argument("--manifest",required=True,type=Path)
    parser.add_argument("--raw-root",required=True,type=Path); parser.add_argument("--certificate",required=True,type=Path)
    args=parser.parse_args(); result=validate(**vars(args)); print(json.dumps({"status":result["status"],"validation":result["validation"]},sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
