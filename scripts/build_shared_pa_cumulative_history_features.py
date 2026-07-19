#!/usr/bin/env python3
"""Build cumulative, point-in-time hitter features for 2023-2024 only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.canonical_pa_features import (  # noqa: E402
    PROFILE_FIELDS,
    REQUIRED_RAW_COLUMNS,
    SCHEMA_VERSION,
    canonical_profiles_between,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def atomic_csv_gz(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_csv(
        temporary,
        index=False,
        float_format="%.17g",
        compression={"method": "gzip", "mtime": 0},
    )
    os.replace(temporary, path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def empty_raw_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=sorted(REQUIRED_RAW_COLUMNS))


def read_cache(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(
            path,
            usecols=lambda column: column in REQUIRED_RAW_COLUMNS,
            low_memory=False,
        )
    except EmptyDataError:
        return empty_raw_frame()


def load_contract(path: Path, evidence_root: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("status") != "LOCKED_BEFORE_CUMULATIVE_HISTORY_BUILD":
        raise ValueError("cumulative history contract is not locked")
    if contract.get("selection_seasons") != [2023, 2024]:
        raise ValueError("cumulative history selection seasons changed")
    if not contract.get("confirmation_2025_forbidden") or not contract.get("may_2026_forbidden"):
        raise ValueError("cumulative history protected evidence was released")
    transformer = ROOT / contract["canonical_transformer"]["path"]
    if sha256(transformer) != contract["canonical_transformer"]["sha256"]:
        raise ValueError("cumulative history transformer hash changed")
    for name in ("training_source", "statcast_inventory", "event_taxonomy", "recent_feature_artifact", "recent_feature_certificate"):
        record = contract[name]
        artifact = evidence_root / record["path"]
        if not artifact.exists() or sha256(artifact) != record["sha256"]:
            raise ValueError(f"cumulative history input hash changed: {name}")
    taxonomy = json.loads((evidence_root / contract["event_taxonomy"]["path"]).read_text(encoding="utf-8"))
    if taxonomy.get("status") != contract["event_taxonomy"]["required_status"]:
        raise ValueError("cumulative history event taxonomy is not complete")
    if taxonomy.get("unmapped_events") or taxonomy.get("confirmation_2025_read") or taxonomy.get("may_2026_read"):
        raise ValueError("cumulative history event taxonomy crossed a protected boundary")
    certificate = json.loads((evidence_root / contract["recent_feature_certificate"]["path"]).read_text(encoding="utf-8"))
    if certificate.get("status") != contract["recent_feature_certificate"]["required_status"]:
        raise ValueError("recent feature certificate is invalid")
    return contract


def load_targets(path: Path, contract: dict[str, Any]) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        nrows=int(contract["training_source"]["maximum_rows_read"]),
        usecols=["season", "game_date", "game_pk", "player_id"],
        low_memory=False,
    )
    if len(frame) != int(contract["selection_rows"]):
        raise ValueError("cumulative history target row count changed")
    if set(frame["season"].astype(int)) != {2023, 2024}:
        raise ValueError("cumulative history target loader crossed into confirmation")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise")
    if dates.max().year >= 2025:
        raise ValueError("cumulative history target date crossed into confirmation")
    if frame[["game_pk", "player_id"]].isna().any().any() or frame.duplicated(["game_pk", "player_id"]).any():
        raise ValueError("cumulative history target identity is null or duplicated")
    return frame


def load_inventory(path: Path, contract: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    inventory = json.loads(path.read_text(encoding="utf-8"))
    if inventory.get("tree_sha256") != contract["statcast_inventory"]["tree_sha256"]:
        raise ValueError("cumulative history inventory tree hash changed")
    allowed = {str(year) for year in contract["history_window"]["source_years_allowed"]}
    records: dict[str, dict[str, Any]] = {}
    for item in inventory.get("files", []):
        normalized = str(item["path"]).replace("\\", "/")
        parts = normalized.split("/")
        if len(parts) >= 4 and parts[-2] in allowed:
            records[normalized] = item
    if any("/2025/" in f"/{path}/" for path in records):
        raise ValueError("confirmation Statcast source entered cumulative history build")
    return records, inventory


def build(evidence_root: Path, out_dir: Path, contract_path: Path) -> dict[str, Any]:
    contract = load_contract(contract_path, evidence_root)
    targets = load_targets(evidence_root / contract["training_source"]["path"], contract)
    records, full_inventory = load_inventory(evidence_root / contract["statcast_inventory"]["path"], contract)
    allowed_years = [int(year) for year in contract["history_window"]["source_years_allowed"]]
    start = str(contract["history_window"]["start_inclusive"])
    feature_rows: list[dict[str, Any]] = []
    verified: dict[str, dict[str, Any]] = {}
    missing_player_years = 0
    empty_player_years = 0
    grouped = list(targets.groupby("player_id", sort=True))
    for group_number, (raw_player_id, group) in enumerate(grouped, start=1):
        player_id = int(raw_player_id)
        raw_parts: list[pd.DataFrame] = []
        maximum_target_year = int(group["season"].max())
        for year in allowed_years:
            if year > maximum_target_year:
                continue
            relative = f"data/cache/statcast/{year}/batter_{player_id}.csv"
            record = records.get(relative)
            if record is None:
                missing_player_years += 1
                continue
            path = evidence_root / relative
            if not path.exists() or sha256(path) != record["sha256"]:
                raise ValueError(f"cumulative history cache hash changed: {relative}")
            raw = read_cache(path)
            verified[relative] = {
                "path": relative,
                "bytes": int(record["bytes"]),
                "sha256": record["sha256"],
            }
            if raw.empty:
                empty_player_years += 1
            else:
                raw_parts.append(raw)
        raw_history = pd.concat(raw_parts, ignore_index=True) if raw_parts else empty_raw_frame()
        target_dates = sorted(group["game_date"].astype(str).unique())
        profiles = canonical_profiles_between(
            raw_history,
            target_dates=target_dates,
            start_inclusive=start,
            entity_column="batter",
            entity_id=player_id,
            prefix="history",
        )
        by_date = {item["target_date"]: item for item in profiles}
        for _, target in group.iterrows():
            profile = by_date[str(target["game_date"])]
            feature_rows.append({
                "season": int(target["season"]),
                "game_date": str(target["game_date"]),
                "game_pk": int(target["game_pk"]),
                "player_id": player_id,
                **{key: value for key, value in profile.items() if key.startswith("history_")},
            })
        if group_number % 50 == 0 or group_number == len(grouped):
            print(f"cumulative hitter groups {group_number}/{len(grouped)}", flush=True)

    output = pd.DataFrame(feature_rows).sort_values(["game_date", "game_pk", "player_id"]).reset_index(drop=True)
    if len(output) != len(targets) or output.duplicated(["game_pk", "player_id"]).any():
        raise ValueError("cumulative history output identity coverage changed")
    if set(output["season"].astype(int)) != {2023, 2024}:
        raise ValueError("cumulative history output crossed into confirmation")
    expected_features = set(contract["feature_group"])
    if expected_features != {f"history_{name}" for name in PROFILE_FIELDS}:
        raise ValueError("cumulative history feature contract differs from transformer")
    if not expected_features.issubset(output.columns):
        raise ValueError("cumulative history output feature set is incomplete")
    for _, player in output.groupby("player_id", sort=False):
        ordered = player.sort_values(["game_date", "game_pk"])
        if (ordered["history_pa"].diff().dropna() < 0).any():
            raise ValueError("cumulative history PA exposure decreased within a player")

    artifact_path = out_dir / "canonical_hitter_history_2023_2024.csv.gz"
    atomic_csv_gz(artifact_path, output)
    verified_sources = sorted(verified.values(), key=lambda item: item["path"])
    subset_tree_sha = hashlib.sha256(canonical_json(verified_sources).encode("utf-8")).hexdigest()
    missing_rate = {column: float(output[column].isna().mean()) for column in sorted(expected_features)}
    manifest = {
        "schema_version": "shared-pa-cumulative-history-artifact-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "VALID_CUMULATIVE_HISTORY_FEATURE_ARTIFACT",
        "betting_authorized": False,
        "production_changed": False,
        "confirmation_2025_read": False,
        "may_2026_read": False,
        "canonical_transformer_schema": SCHEMA_VERSION,
        "contract": {
            "path": str(contract_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(contract_path),
        },
        "full_statcast_inventory": {
            **contract["statcast_inventory"],
            "file_count_reported": int(full_inventory.get("file_count", len(full_inventory.get("files", [])))),
        },
        "verified_selection_statcast_subset": {
            "years": allowed_years,
            "files": int(len(verified_sources)),
            "bytes": int(sum(item["bytes"] for item in verified_sources)),
            "tree_sha256": subset_tree_sha,
            "missing_player_year_cache_groups": int(missing_player_years),
            "empty_player_year_cache_groups": int(empty_player_years),
        },
        "artifact": {
            "path": str(artifact_path),
            "sha256": sha256(artifact_path),
            "bytes": artifact_path.stat().st_size,
            "rows": int(len(output)),
            "identity_key": ["game_pk", "player_id"],
            "duplicate_identity_rows": 0,
            "rows_by_season": {str(int(k)): int(v) for k, v in output.groupby("season").size().items()},
            "date_min": str(output["game_date"].min()),
            "date_max": str(output["game_date"].max()),
            "feature_missing_rate": missing_rate,
        },
        "protected_invariants": {
            "same_day_excluded": True,
            "league_fallback_imputed": False,
            "hand_specified_shrinkage_applied": False,
            "confirmation_2025_unread": True,
            "may_2026_unread": True,
            "production_unchanged": True,
        },
    }
    manifest_path = out_dir / "canonical_hitter_history_2023_2024.manifest.json"
    atomic_json(manifest_path, manifest)
    print(f"wrote {len(output)} cumulative hitter rows -> {artifact_path}")
    print(f"wrote manifest -> {manifest_path}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "config/shared_pa_cumulative_history_contract.json",
    )
    args = parser.parse_args()
    build(args.evidence_root.resolve(), args.out_dir.resolve(), args.contract.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
