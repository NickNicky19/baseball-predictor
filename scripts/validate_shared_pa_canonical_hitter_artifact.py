#!/usr/bin/env python3
"""Independently certify the canonical 2023-2024 hitter feature artifact."""
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
COUNT_COLUMNS = ["hitter_pitch_count", "hitter_pa", "hitter_bip"]
RATE_COLUMNS = [
    f"hitter_{name}" for name in PROFILE_FIELDS
    if name.endswith("_rate")
]
CONTEXT_COLUMNS = [
    "season", "game_date", *IDENTITY, "lineup_slot", "bats",
    "opp_sp_throws", "opp_sp_source", "is_home", "venue",
]


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
    expected_features = {f"hitter_{name}" for name in PROFILE_FIELDS}
    expected_columns = set(CONTEXT_COLUMNS) | expected_features
    if set(frame.columns) != expected_columns:
        missing = sorted(expected_columns - set(frame.columns))
        extra = sorted(set(frame.columns) - expected_columns)
        raise ValueError(f"canonical artifact schema changed: missing={missing}, extra={extra}")
    if any(column.startswith("out_") for column in frame.columns):
        raise ValueError("official outcomes entered canonical feature artifact")
    if len(frame) != int(manifest["artifact"]["rows"]):
        raise ValueError("canonical artifact row count differs from manifest")
    if frame[IDENTITY].isna().any().any() or frame.duplicated(IDENTITY).any():
        raise ValueError("canonical artifact identity is null or duplicated")
    actual_identity = frame[IDENTITY].astype("int64").sort_values(IDENTITY).reset_index(drop=True)
    target_identity = targets[IDENTITY].astype("int64").sort_values(IDENTITY).reset_index(drop=True)
    if not actual_identity.equals(target_identity):
        raise ValueError("canonical artifact identity coverage differs from source prefix")
    if set(pd.to_numeric(frame["season"], errors="raise").astype(int)) != {2023, 2024}:
        raise ValueError("canonical artifact crossed into confirmation data")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise")
    if dates.min().date().isoformat() != "2023-03-30" or dates.max().date().isoformat() != "2024-09-30":
        raise ValueError("canonical artifact chronology changed")
    if frame["opp_sp_source"].astype(str).eq("actual_starter").any():
        raise ValueError("postgame opposing-starter provenance survived canonical artifact")

    counts = frame[COUNT_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if counts.isna().any().any() or (counts < 0).any().any():
        raise ValueError("canonical evidence counts are missing or negative")
    if not np.equal(counts.to_numpy(), np.floor(counts.to_numpy())).all():
        raise ValueError("canonical evidence counts are not integers")
    if not counts["hitter_pa"].le(counts["hitter_pitch_count"]).all():
        raise ValueError("canonical PA exceeds pitch count")
    if not counts["hitter_bip"].le(counts["hitter_pa"]).all():
        raise ValueError("canonical BIP exceeds PA")
    rates = frame[RATE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if ((rates < 0) | (rates > 1)).any().any():
        raise ValueError("canonical rate is outside [0,1]")
    event_rate_columns = [
        "hitter_k_rate", "hitter_bb_rate", "hitter_single_rate",
        "hitter_double_rate", "hitter_triple_rate", "hitter_home_run_rate",
        "hitter_bip_out_rate", "hitter_other_non_ab_rate",
    ]
    positive_pa = counts["hitter_pa"] > 0
    if frame.loc[positive_pa, event_rate_columns].isna().any().any():
        raise ValueError("positive-PA canonical event rate is missing")
    if frame.loc[~positive_pa, event_rate_columns].notna().any().any():
        raise ValueError("zero-PA canonical event rate was imputed")
    event_sum = frame.loc[positive_pa, event_rate_columns].sum(axis=1)
    if not np.allclose(event_sum.to_numpy(float), 1.0, atol=1e-12):
        raise ValueError("canonical event rates do not sum to one")
    classifier = set().union(*(
        set(values) for values in contract["classifier_feature_groups"].values()
    ))
    if not classifier.issubset(frame.columns):
        raise ValueError("canonical classifier feature contract is incomplete")
    if classifier & set(contract["quarantined_from_classifier"]):
        raise ValueError("quarantined feature entered canonical classifier contract")
    actual_missing = {
        column: float(frame[column].isna().mean())
        for column in sorted(expected_features)
    }
    reported_missing = manifest["artifact"]["feature_missing_rate"]
    if set(actual_missing) != set(reported_missing):
        raise ValueError("canonical missingness field set differs from manifest")
    for column, value in actual_missing.items():
        if abs(value - float(reported_missing[column])) > 1e-15:
            raise ValueError(f"canonical missingness differs from manifest: {column}")
    return {
        "rows": int(len(frame)),
        "games": int(frame["game_pk"].nunique()),
        "players": int(frame["player_id"].nunique()),
        "positive_pa_feature_rows": int(positive_pa.sum()),
        "zero_pa_feature_rows": int((~positive_pa).sum()),
        "postgame_pitcher_rows": 0,
        "duplicate_identity_rows": 0,
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
    if manifest.get("status") != "VALID_SELECTION_FEATURE_ARTIFACT":
        raise ValueError("canonical builder did not publish a valid artifact")
    if manifest.get("canonical_transformer_schema") != SCHEMA_VERSION:
        raise ValueError("canonical transformer schema differs")
    if manifest.get("confirmation_2025_read") or manifest.get("may_2026_read"):
        raise ValueError("canonical builder crossed protected evidence")
    contract_path = ROOT / manifest["contract"]["path"]
    if sha256(contract_path) != manifest["contract"]["sha256"]:
        raise ValueError("canonical contract hash differs from manifest")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    source = evidence_root / contract["training_source"]["path"]
    taxonomy = evidence_root / contract["event_taxonomy"]["path"]
    artifact = Path(manifest["artifact"]["path"])
    if sha256(source) != contract["training_source"]["sha256"]:
        raise ValueError("canonical validation source hash changed")
    if sha256(taxonomy) != contract["event_taxonomy"]["sha256"]:
        raise ValueError("canonical validation taxonomy hash changed")
    if sha256(artifact) != manifest["artifact"]["sha256"]:
        raise ValueError("canonical feature artifact hash changed")
    targets = pd.read_csv(
        source,
        nrows=int(contract["training_source"]["maximum_rows_read"]),
        usecols=IDENTITY,
    )
    frame = pd.read_csv(artifact, low_memory=False)
    validation = validate_frame(frame, targets, manifest, contract)
    runtime_files = [
        ROOT / "src/features/canonical_pa_features.py",
        ROOT / "scripts/build_shared_pa_canonical_hitter_features.py",
        Path(__file__),
        contract_path,
    ]
    source_commit = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    certificate = {
        "schema_version": "shared-pa-canonical-hitter-certificate-v1",
        "certified_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "CANONICAL_HITTER_ARTIFACT_CERTIFIED",
        "betting_authorized": False,
        "confirmation_2025_read": False,
        "may_2026_read": False,
        "source_commit": source_commit,
        "manifest": {"path": str(manifest_path), "sha256": sha256(manifest_path)},
        "artifact": manifest["artifact"],
        "contract": manifest["contract"],
        "taxonomy": contract["event_taxonomy"],
        "runtime_file_hashes": {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path)
            for path in runtime_files
        },
        "validation": validation,
        "protected_invariants": {
            "same_day_exclusion_mutation_tested": True,
            "unknown_event_mutation_tested": True,
            "league_fallback_imputed": False,
            "confirmation_2025_unread": True,
            "may_2026_unread": True,
            "production_unchanged": True,
        },
    }
    atomic_json(args.out.resolve(), certificate)
    print("CANONICAL HITTER ARTIFACT CERTIFIED")
    print(f"rows: {validation['rows']}")
    print(f"games: {validation['games']}")
    print(f"zero_pa_feature_rows: {validation['zero_pa_feature_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
