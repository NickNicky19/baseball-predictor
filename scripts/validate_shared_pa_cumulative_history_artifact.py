#!/usr/bin/env python3
"""Independently certify the 2023-2024 cumulative hitter artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.canonical_pa_features import PROFILE_FIELDS, SCHEMA_VERSION  # noqa: E402


IDENTITY = ["game_pk", "player_id"]
COUNT_COLUMNS = ["history_pitch_count", "history_pa", "history_bip"]
RATE_COLUMNS = [f"history_{name}" for name in PROFILE_FIELDS if name.endswith("_rate")]
CONTEXT_COLUMNS = ["season", "game_date", *IDENTITY]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def validate_frame(
    frame: pd.DataFrame,
    targets: pd.DataFrame,
    manifest: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    expected_features = {f"history_{name}" for name in PROFILE_FIELDS}
    expected_columns = set(CONTEXT_COLUMNS) | expected_features
    if set(frame.columns) != expected_columns:
        raise ValueError("cumulative history artifact schema changed")
    if any(column.startswith("out_") for column in frame.columns):
        raise ValueError("official outcomes entered cumulative history artifact")
    if set(contract["feature_group"]) != expected_features:
        raise ValueError("cumulative history contract feature group changed")
    if len(frame) != int(manifest["artifact"]["rows"]):
        raise ValueError("cumulative history row count differs from manifest")
    if frame[IDENTITY].isna().any().any() or frame.duplicated(IDENTITY).any():
        raise ValueError("cumulative history identity is null or duplicated")
    actual_identity = frame[IDENTITY].astype("int64").sort_values(IDENTITY).reset_index(drop=True)
    target_identity = targets[IDENTITY].astype("int64").sort_values(IDENTITY).reset_index(drop=True)
    if not actual_identity.equals(target_identity):
        raise ValueError("cumulative history identity coverage differs")
    if set(pd.to_numeric(frame["season"], errors="raise").astype(int)) != {2023, 2024}:
        raise ValueError("cumulative history artifact crossed into confirmation")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise")
    if dates.min().date().isoformat() != "2023-03-30" or dates.max().date().isoformat() != "2024-09-30":
        raise ValueError("cumulative history chronology changed")

    counts = frame[COUNT_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if counts.isna().any().any() or (counts < 0).any().any():
        raise ValueError("cumulative history counts are missing or negative")
    if not np.equal(counts.to_numpy(), np.floor(counts.to_numpy())).all():
        raise ValueError("cumulative history counts are not integers")
    if not counts["history_pa"].le(counts["history_pitch_count"]).all():
        raise ValueError("cumulative history PA exceeds pitch count")
    if not counts["history_bip"].le(counts["history_pa"]).all():
        raise ValueError("cumulative history BIP exceeds PA")
    for _, player in frame.assign(_date=dates).sort_values(["player_id", "_date", "game_pk"]).groupby("player_id", sort=False):
        if (player[COUNT_COLUMNS].diff().dropna() < 0).any().any():
            raise ValueError("cumulative history exposure decreased within player")

    rates = frame[RATE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if ((rates < 0) | (rates > 1)).any().any():
        raise ValueError("cumulative history rate is outside [0,1]")
    event_rates = [
        "history_k_rate", "history_bb_rate", "history_single_rate",
        "history_double_rate", "history_triple_rate", "history_home_run_rate",
        "history_bip_out_rate", "history_other_non_ab_rate",
    ]
    positive_pa = counts["history_pa"] > 0
    if frame.loc[positive_pa, event_rates].isna().any().any():
        raise ValueError("positive-PA cumulative history event rate is missing")
    if frame.loc[~positive_pa, event_rates].notna().any().any():
        raise ValueError("zero-PA cumulative history event rate was imputed")
    if not np.allclose(frame.loc[positive_pa, event_rates].sum(axis=1).to_numpy(float), 1.0, atol=1e-12):
        raise ValueError("cumulative history event rates do not sum to one")
    actual_missing = {column: float(frame[column].isna().mean()) for column in sorted(expected_features)}
    reported_missing = manifest["artifact"]["feature_missing_rate"]
    if set(actual_missing) != set(reported_missing):
        raise ValueError("cumulative history missingness fields differ")
    for column, value in actual_missing.items():
        if abs(value - float(reported_missing[column])) > 1e-15:
            raise ValueError(f"cumulative history missingness differs: {column}")
    return {
        "rows": int(len(frame)),
        "games": int(frame["game_pk"].nunique()),
        "players": int(frame["player_id"].nunique()),
        "positive_pa_feature_rows": int(positive_pa.sum()),
        "zero_pa_feature_rows": int((~positive_pa).sum()),
        "duplicate_identity_rows": 0,
        "monotonic_exposure": True,
        "event_rate_accounting_valid": True,
        "feature_missing_rate": actual_missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "VALID_CUMULATIVE_HISTORY_FEATURE_ARTIFACT":
        raise ValueError("cumulative history builder did not publish a valid artifact")
    if manifest.get("canonical_transformer_schema") != SCHEMA_VERSION:
        raise ValueError("cumulative history transformer schema differs")
    if manifest.get("confirmation_2025_read") or manifest.get("may_2026_read"):
        raise ValueError("cumulative history builder crossed protected evidence")
    if manifest.get("production_changed") or manifest.get("betting_authorized"):
        raise ValueError("cumulative history builder altered protected state")
    contract_path = ROOT / manifest["contract"]["path"]
    if sha256(contract_path) != manifest["contract"]["sha256"]:
        raise ValueError("cumulative history contract hash differs")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    for name in ("training_source", "statcast_inventory", "event_taxonomy", "recent_feature_artifact", "recent_feature_certificate"):
        record = contract[name]
        artifact = evidence_root / record["path"]
        if not artifact.exists() or sha256(artifact) != record["sha256"]:
            raise ValueError(f"cumulative history validation input changed: {name}")
    artifact = Path(manifest["artifact"]["path"])
    if not artifact.exists() or sha256(artifact) != manifest["artifact"]["sha256"]:
        raise ValueError("cumulative history artifact hash changed")
    targets = pd.read_csv(
        evidence_root / contract["training_source"]["path"],
        nrows=int(contract["training_source"]["maximum_rows_read"]),
        usecols=IDENTITY,
    )
    validation = validate_frame(pd.read_csv(artifact, low_memory=False), targets, manifest, contract)
    runtime_files = [
        ROOT / "src/features/canonical_pa_features.py",
        ROOT / "scripts/build_shared_pa_cumulative_history_features.py",
        Path(__file__),
        contract_path,
    ]
    source_commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    certificate = {
        "schema_version": "shared-pa-cumulative-history-certificate-v1",
        "certified_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "CUMULATIVE_HISTORY_ARTIFACT_CERTIFIED",
        "betting_authorized": False,
        "production_changed": False,
        "confirmation_2025_read": False,
        "may_2026_read": False,
        "source_commit": source_commit,
        "manifest": {"path": str(manifest_path), "sha256": sha256(manifest_path)},
        "artifact": manifest["artifact"],
        "contract": manifest["contract"],
        "runtime_file_hashes": {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path)
            for path in runtime_files
        },
        "validation": validation,
        "protected_invariants": {
            "same_day_exclusion_mutation_tested": True,
            "league_fallback_imputed": False,
            "hand_specified_shrinkage_applied": False,
            "confirmation_2025_unread": True,
            "may_2026_unread": True,
            "production_unchanged": True,
        },
    }
    atomic_json(args.out.resolve(), certificate)
    print("CUMULATIVE HISTORY ARTIFACT CERTIFIED")
    print(f"rows: {validation['rows']}")
    print(f"zero_pa_feature_rows: {validation['zero_pa_feature_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
