#!/usr/bin/env python3
"""Build the 2023-2024 canonical hitter feature artifact without reading 2025."""
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
    canonical_profiles,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_contract(path: Path, evidence_root: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    status = contract.get("status")
    if status not in {
        "LOCKED_BEFORE_CANONICAL_SELECTION_ARTIFACT",
        "LOCKED_BEFORE_REGULAR_SEASON_CANONICAL_SELECTION_ARTIFACT",
    }:
        raise ValueError("canonical feature contract is not locked")
    if contract.get("selection_seasons") != [2023, 2024]:
        raise ValueError("canonical selection seasons changed")
    source = evidence_root / contract["training_source"]["path"]
    inventory = evidence_root / contract["statcast_inventory"]["path"]
    taxonomy = evidence_root / contract["event_taxonomy"]["path"]
    if sha256(source) != contract["training_source"]["sha256"]:
        raise ValueError("canonical training source hash changed")
    if sha256(inventory) != contract["statcast_inventory"]["sha256"]:
        raise ValueError("canonical Statcast inventory hash changed")
    if sha256(taxonomy) != contract["event_taxonomy"]["sha256"]:
        raise ValueError("canonical event taxonomy hash changed")
    taxonomy_payload = json.loads(taxonomy.read_text(encoding="utf-8"))
    taxonomy_contract = contract["event_taxonomy"]
    if taxonomy_payload.get("status") != taxonomy_contract["required_status"]:
        raise ValueError("canonical event taxonomy is not complete")
    if int(taxonomy_payload.get("selection_files_verified", -1)) != int(taxonomy_contract["selection_files_verified"]):
        raise ValueError("canonical event taxonomy file coverage changed")
    if len(taxonomy_payload.get("terminal_events", {})) != int(taxonomy_contract["terminal_events_observed"]):
        raise ValueError("canonical event taxonomy observed class count changed")
    if len(taxonomy_payload.get("unmapped_events", {})) != int(taxonomy_contract["unmapped_events_required"]):
        raise ValueError("canonical event taxonomy contains unmapped events")
    if taxonomy_payload.get("confirmation_2025_read") or taxonomy_payload.get("may_2026_read"):
        raise ValueError("canonical event taxonomy crossed a protected evidence boundary")
    if status == "LOCKED_BEFORE_REGULAR_SEASON_CANONICAL_SELECTION_ARTIFACT":
        basis = contract.get("measured_basis", {}).get("audit", {})
        audit_path = ROOT / str(basis.get("path", ""))
        if not audit_path.is_file() or sha256(audit_path) != basis.get("sha256"):
            raise ValueError("regular-season candidate contamination audit hash changed")
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        if audit.get("status") != "OUTCOME_BLIND_INPUT_CONTAMINATION_MEASURED":
            raise ValueError("regular-season candidate contamination audit status changed")
        measured = audit.get("contamination", {})
        for field in ("affected_player_game_rows", "non_regular_event_rows_in_feature_windows"):
            if int(measured.get(field, -1)) != int(basis.get(field, -2)):
                raise ValueError(f"regular-season candidate contamination audit changed: {field}")
        policy = contract.get("canonical_transformer", {}).get("game_type_policy")
        if policy != {"mode": "regular_season_only", "allowed_game_types": ["R"], "unknown_or_missing": "fail_closed"}:
            raise ValueError("regular-season candidate game-type policy changed")
    return contract


def load_targets(source: Path, contract: dict[str, Any]) -> pd.DataFrame:
    maximum = int(contract["training_source"]["maximum_rows_read"])
    columns = [
        "season", "game_date", "game_pk", "player_id", "lineup_slot", "bats",
        "opp_sp_throws", "opp_sp_source", "is_home", "venue",
    ]
    frame = pd.read_csv(source, nrows=maximum, usecols=columns, low_memory=False)
    if len(frame) != int(contract["selection_rows"]):
        raise ValueError("canonical target row count changed")
    if set(frame["season"].astype(int)) != {2023, 2024}:
        raise ValueError("canonical target loader crossed into confirmation data")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise")
    if dates.max().year >= 2025:
        raise ValueError("canonical target date crossed into confirmation data")
    if frame[["game_pk", "player_id"]].isna().any().any():
        raise ValueError("canonical target identity is null")
    if frame.duplicated(["game_pk", "player_id"]).any():
        raise ValueError("canonical target identity is duplicated")
    postgame = frame["opp_sp_source"].astype(str).eq("actual_starter")
    frame.loc[postgame, "opp_sp_throws"] = pd.NA
    frame.loc[postgame, "opp_sp_source"] = "unavailable_historical"
    return frame


def load_inventory(path: Path, contract: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    inventory = json.loads(path.read_text(encoding="utf-8"))
    if inventory.get("tree_sha256") != contract["statcast_inventory"]["tree_sha256"]:
        raise ValueError("canonical Statcast inventory tree hash changed")
    allowed_years = {str(value) for value in contract["statcast_inventory"]["allowed_years_during_selection_build"]}
    records: dict[str, dict[str, Any]] = {}
    for item in inventory.get("files", []):
        normalized = str(item["path"]).replace("\\", "/")
        parts = normalized.split("/")
        if len(parts) >= 4 and parts[-2] in allowed_years:
            records[normalized] = item
    if any("/2025/" in f"/{path}/" for path in records):
        raise ValueError("confirmation Statcast inventory entered selection build")
    return records, inventory


def empty_raw_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=sorted(REQUIRED_RAW_COLUMNS))


def read_player_cache(path: Path, *, require_game_type: bool) -> pd.DataFrame:
    columns = set(REQUIRED_RAW_COLUMNS)
    if require_game_type:
        columns.add("game_type")
    try:
        return pd.read_csv(
            path,
            usecols=lambda column: column in columns,
            low_memory=False,
        )
    except EmptyDataError:
        return empty_raw_frame()


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


def build(evidence_root: Path, out_dir: Path, contract_path: Path) -> dict[str, Any]:
    contract = load_contract(contract_path, evidence_root)
    source = evidence_root / contract["training_source"]["path"]
    inventory_path = evidence_root / contract["statcast_inventory"]["path"]
    targets = load_targets(source, contract)
    inventory, inventory_full = load_inventory(inventory_path, contract)
    game_type_policy = contract["canonical_transformer"].get("game_type_policy")
    allowed_game_types: frozenset[str] | None = None
    if game_type_policy is not None:
        if game_type_policy.get("unknown_or_missing") != "fail_closed":
            raise ValueError("canonical game-type policy must fail closed")
        values = game_type_policy.get("allowed_game_types")
        if not isinstance(values, list) or not values or not all(isinstance(value, str) and value for value in values):
            raise ValueError("canonical game-type policy is invalid")
        allowed_game_types = frozenset(values)
    feature_rows: list[dict[str, Any]] = []
    verified_sources: list[dict[str, Any]] = []
    missing_cache_groups = 0
    empty_cache_groups = 0
    grouped = list(targets.groupby(["season", "player_id"], sort=True))
    for group_number, ((raw_season, raw_player_id), group) in enumerate(grouped, start=1):
        season = int(raw_season)
        player_id = int(raw_player_id)
        relative = f"data/cache/statcast/{season}/batter_{player_id}.csv"
        record = inventory.get(relative)
        if record is None:
            raw = empty_raw_frame()
            missing_cache_groups += 1
        else:
            path = evidence_root / relative
            if not path.exists() or sha256(path) != record["sha256"]:
                raise ValueError(f"canonical player cache hash changed: {relative}")
            raw = read_player_cache(path, require_game_type=allowed_game_types is not None)
            verified_sources.append({
                "path": relative,
                "bytes": int(record["bytes"]),
                "sha256": record["sha256"],
            })
            if raw.empty:
                empty_cache_groups += 1
        targets_for_player = sorted(group["game_date"].astype(str).unique())
        profiles = canonical_profiles(
            raw,
            target_dates=targets_for_player,
            entity_column="batter",
            entity_id=player_id,
            prefix="hitter",
            allowed_game_types=allowed_game_types,
        )
        by_date = {item["target_date"]: item for item in profiles}
        for _, target in group.iterrows():
            profile = by_date[str(target["game_date"])]
            feature_rows.append({
                "season": season,
                "game_date": str(target["game_date"]),
                "game_pk": int(target["game_pk"]),
                "player_id": player_id,
                "lineup_slot": int(target["lineup_slot"]),
                "bats": target["bats"],
                "opp_sp_throws": target["opp_sp_throws"],
                "opp_sp_source": target["opp_sp_source"],
                "is_home": int(target["is_home"]),
                "venue": target["venue"],
                **{key: value for key, value in profile.items() if key.startswith("hitter_")},
            })
        if group_number % 100 == 0 or group_number == len(grouped):
            print(f"canonical hitter groups {group_number}/{len(grouped)}", flush=True)

    output = pd.DataFrame(feature_rows).sort_values(["game_date", "game_pk", "player_id"]).reset_index(drop=True)
    if len(output) != len(targets) or output.duplicated(["game_pk", "player_id"]).any():
        raise ValueError("canonical feature output identity coverage changed")
    if set(output["season"].astype(int)) != {2023, 2024}:
        raise ValueError("canonical output crossed into confirmation data")
    expected_features = {f"hitter_{name}" for name in PROFILE_FIELDS}
    if not expected_features.issubset(output.columns):
        raise ValueError("canonical output feature set is incomplete")
    artifact_path = out_dir / "canonical_hitter_features_2023_2024.csv.gz"
    atomic_csv_gz(artifact_path, output)
    verified_sources = sorted(verified_sources, key=lambda item: item["path"])
    subset_tree_sha = hashlib.sha256(canonical_json(verified_sources).encode("utf-8")).hexdigest()
    missing_rate = {
        column: float(output[column].isna().mean())
        for column in sorted(expected_features)
    }
    manifest = {
        "schema_version": "shared-pa-canonical-hitter-artifact-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "VALID_SELECTION_FEATURE_ARTIFACT",
        "betting_authorized": False,
        "confirmation_2025_read": False,
        "may_2026_read": False,
        "canonical_transformer_schema": SCHEMA_VERSION,
        "game_type_policy": game_type_policy,
        "contract": {
            "path": str(contract_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(contract_path),
        },
        "training_source": contract["training_source"],
        "full_statcast_inventory": {
            **contract["statcast_inventory"],
            "file_count_reported": int(inventory_full.get("file_count", len(inventory_full.get("files", [])))),
        },
        "event_taxonomy": contract["event_taxonomy"],
        "verified_selection_statcast_subset": {
            "years": [2023, 2024],
            "files": int(len(verified_sources)),
            "bytes": int(sum(item["bytes"] for item in verified_sources)),
            "tree_sha256": subset_tree_sha,
            "missing_player_season_cache_groups": int(missing_cache_groups),
            "empty_player_season_cache_groups": int(empty_cache_groups),
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
            "confirmation_2025_unread": True,
            "may_2026_unread": True,
            "production_unchanged": True,
        },
    }
    manifest_path = out_dir / "canonical_hitter_features_2023_2024.manifest.json"
    atomic_json(manifest_path, manifest)
    print(f"wrote {len(output)} canonical hitter rows -> {artifact_path}")
    print(f"wrote manifest -> {manifest_path}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "config/shared_pa_runtime_feature_contract.json",
    )
    args = parser.parse_args()
    build(args.evidence_root.resolve(), args.out_dir.resolve(), args.contract.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
