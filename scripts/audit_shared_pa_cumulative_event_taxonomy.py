#!/usr/bin/env python3
"""Audit every existing cache in the cumulative 2023-2024 source universe."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.canonical_cumulative_pa_features import expected_cache_groups  # noqa: E402
from src.features.canonical_pa_features import (  # noqa: E402
    BIP_OUT_EVENTS,
    HIT_EVENTS,
    NON_PA_TERMINAL_EVENTS,
    OTHER_NON_AB_EVENTS,
    STRIKEOUT_EVENTS,
    WALK_EVENTS,
)


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "config/shared_pa_cumulative_history_contract.json",
    )
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    contract_path = args.contract.resolve()
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("selection_seasons") != [2023, 2024]:
        raise ValueError("cumulative taxonomy selection seasons changed")
    if not contract.get("confirmation_2025_forbidden") or not contract.get("may_2026_forbidden"):
        raise ValueError("cumulative taxonomy protected evidence was released")
    source_record = contract["training_source"]
    inventory_record = contract["statcast_inventory"]
    source_path = evidence_root / source_record["path"]
    inventory_path = evidence_root / inventory_record["path"]
    if sha256(source_path) != source_record["sha256"] or sha256(inventory_path) != inventory_record["sha256"]:
        raise ValueError("cumulative taxonomy input hash changed")
    targets = pd.read_csv(
        source_path,
        nrows=int(source_record["maximum_rows_read"]),
        usecols=["season", "player_id"],
    ).drop_duplicates()
    allowed_years = [int(year) for year in contract["history_window"]["source_years_allowed"]]
    groups = expected_cache_groups(targets, allowed_years=allowed_years)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if inventory.get("tree_sha256") != inventory_record["tree_sha256"]:
        raise ValueError("cumulative taxonomy inventory tree changed")
    by_path = {str(item["path"]).replace("\\", "/"): item for item in inventory["files"]}
    counts: Counter[str] = Counter()
    verified: list[dict[str, Any]] = []
    missing: list[str] = []
    empty_files = 0
    for index, (season, player_id) in enumerate(groups, start=1):
        relative = f"data/cache/statcast/{season}/batter_{player_id}.csv"
        item = by_path.get(relative)
        if item is None:
            missing.append(relative)
            continue
        path = evidence_root / relative
        if not path.exists() or sha256(path) != item["sha256"]:
            raise ValueError(f"cumulative taxonomy cache hash changed: {relative}")
        try:
            events = pd.read_csv(path, usecols=["events"], low_memory=False)["events"].dropna().astype(str)
        except EmptyDataError:
            events = pd.Series(dtype="string")
            empty_files += 1
        counts.update(events.tolist())
        verified.append({"path": relative, "bytes": int(item["bytes"]), "sha256": item["sha256"]})
        if index % 100 == 0 or index == len(groups):
            print(f"cumulative taxonomy groups {index}/{len(groups)}", flush=True)

    mapped = set().union(
        STRIKEOUT_EVENTS,
        WALK_EVENTS,
        OTHER_NON_AB_EVENTS,
        *(values for values in HIT_EVENTS.values()),
        BIP_OUT_EVENTS,
        NON_PA_TERMINAL_EVENTS,
    )
    observed = set(counts)
    verified.sort(key=lambda item: item["path"])
    verified_tree = hashlib.sha256(
        json.dumps(verified, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload = {
        "schema_version": "shared-pa-cumulative-event-taxonomy-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "VALID_COMPLETE_CUMULATIVE_TAXONOMY" if observed <= mapped else "UNMAPPED_EVENTS_PRESENT",
        "betting_authorized": False,
        "confirmation_2025_read": False,
        "may_2026_read": False,
        "contract": {"path": str(contract_path.relative_to(ROOT)).replace("\\", "/"), "sha256": sha256(contract_path)},
        "source": source_record,
        "inventory": inventory_record,
        "source_years": allowed_years,
        "expected_player_year_groups": int(len(groups)),
        "verified_files": int(len(verified)),
        "verified_bytes": int(sum(item["bytes"] for item in verified)),
        "verified_tree_sha256": verified_tree,
        "missing_player_year_groups": int(len(missing)),
        "missing_paths_sha256": hashlib.sha256("\n".join(missing).encode("utf-8")).hexdigest(),
        "empty_files": int(empty_files),
        "terminal_events": {name: int(counts[name]) for name in sorted(counts)},
        "terminal_pa": int(sum(counts.values())),
        "unmapped_events": {name: int(counts[name]) for name in sorted(observed - mapped)},
        "protected_invariants": {
            "only_2023_2024_files_read": True,
            "confirmation_2025_unread": True,
            "may_2026_unread": True,
            "production_unchanged": True,
        },
    }
    atomic_json(args.out.resolve(), payload)
    print(f"cumulative taxonomy status: {payload['status']}")
    print(f"verified files: {payload['verified_files']}")
    print(f"missing groups: {payload['missing_player_year_groups']}")
    print(f"unmapped events: {payload['unmapped_events']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
