#!/usr/bin/env python3
"""Independently validate and mutation-test cumulative taxonomy coverage."""
from __future__ import annotations

import copy
import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.canonical_cumulative_pa_features import expected_cache_groups  # noqa: E402


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate(report: dict, contract: dict, targets: pd.DataFrame, inventory: dict) -> dict[str, int]:
    taxonomy = contract["event_taxonomy"]
    if report.get("status") != taxonomy["required_status"] or report.get("unmapped_events"):
        raise ValueError("cumulative taxonomy is incomplete")
    if report.get("confirmation_2025_read") or report.get("may_2026_read") or report.get("betting_authorized"):
        raise ValueError("cumulative taxonomy crossed protected state")
    if report.get("source") != contract["training_source"] or report.get("inventory") != contract["statcast_inventory"]:
        raise ValueError("cumulative taxonomy source binding changed")
    allowed = [int(year) for year in contract["history_window"]["source_years_allowed"]]
    groups = expected_cache_groups(targets[["season", "player_id"]].drop_duplicates(), allowed_years=allowed)
    by_path = {str(item["path"]).replace("\\", "/"): item for item in inventory["files"]}
    verified = []
    missing = []
    for season, player_id in groups:
        relative = f"data/cache/statcast/{season}/batter_{player_id}.csv"
        item = by_path.get(relative)
        if item is None:
            missing.append(relative)
        else:
            verified.append({"path": relative, "bytes": int(item["bytes"]), "sha256": item["sha256"]})
    verified.sort(key=lambda item: item["path"])
    tree = hashlib.sha256(canonical_json(verified).encode("utf-8")).hexdigest()
    values = {
        "expected_player_year_groups": len(groups),
        "verified_files": len(verified),
        "missing_player_year_groups": len(missing),
    }
    for key, value in values.items():
        if int(report.get(key, -1)) != value or int(taxonomy[key]) != value:
            raise ValueError(f"cumulative taxonomy count differs: {key}")
    if report.get("verified_tree_sha256") != tree or taxonomy["verified_tree_sha256"] != tree:
        raise ValueError("cumulative taxonomy tree differs")
    missing_hash = hashlib.sha256("\n".join(missing).encode("utf-8")).hexdigest()
    if report.get("missing_paths_sha256") != missing_hash:
        raise ValueError("cumulative taxonomy missing-path set differs")
    if any("/2025/" in f"/{item['path']}/" for item in verified):
        raise ValueError("2025 source entered cumulative taxonomy")
    if len(report.get("terminal_events", {})) != 22:
        raise ValueError("cumulative taxonomy terminal event class count changed")
    return values


def expect_failure(fn: object) -> bool:
    try:
        fn()  # type: ignore[operator]
    except ValueError:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, default=ROOT)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    contract = json.loads((ROOT / "config/shared_pa_cumulative_history_contract.json").read_text(encoding="utf-8"))
    report = json.loads((evidence_root / contract["event_taxonomy"]["path"]).read_text(encoding="utf-8"))
    source = pd.read_csv(
        evidence_root / contract["training_source"]["path"],
        nrows=int(contract["training_source"]["maximum_rows_read"]),
        usecols=["season", "player_id"],
    )
    inventory = json.loads((evidence_root / contract["statcast_inventory"]["path"]).read_text(encoding="utf-8"))
    checks = [("valid cumulative taxonomy", validate(report, contract, source, inventory)["verified_files"] == 1290)]
    checks.extend([
        ("status mutation rejected", expect_failure(lambda: validate({**report, "status": "UNKNOWN"}, contract, source, inventory))),
        ("unmapped event rejected", expect_failure(lambda: validate({**report, "unmapped_events": {"new_event": 1}}, contract, source, inventory))),
        ("confirmation mutation rejected", expect_failure(lambda: validate({**report, "confirmation_2025_read": True}, contract, source, inventory))),
        ("verified-count mutation rejected", expect_failure(lambda: validate({**report, "verified_files": 1288}, contract, source, inventory))),
        ("missing-count mutation rejected", expect_failure(lambda: validate({**report, "missing_player_year_groups": 0}, contract, source, inventory))),
        ("tree mutation rejected", expect_failure(lambda: validate({**report, "verified_tree_sha256": "0" * 64}, contract, source, inventory))),
        ("terminal-class mutation rejected", expect_failure(lambda: validate({**report, "terminal_events": {}}, contract, source, inventory))),
    ])
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"cumulative taxonomy checks failed: {failed}")
    print(f"CUMULATIVE TAXONOMY VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
