#!/usr/bin/env python3
"""Build an outcome-blind availability map for the preserved HR reconstructor."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_pre2026_input_availability import (  # noqa: E402
    SCHEMA,
    STATUS,
    date_availability,
    regular_season_event_dates,
    validate_source_columns,
)
from src.utils.provenance import sha256_file  # noqa: E402


MODEL_SOURCE_PATHS = (
    Path("src/features/legacy_statcast_features.py"),
    Path("src/data/savant.py"),
)


def _preserved_statcast_defaults(source_path: Path) -> tuple[int, int]:
    """Read constructor defaults without importing the live data/network package."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "StatcastFeatureEngine":
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "__init__":
                    args = child.args.args
                    defaults = child.args.defaults
                    names = [item.arg for item in args[-len(defaults):]] if defaults else []
                    values = {name: ast.literal_eval(value) for name, value in zip(names, defaults)}
                    lookback = values.get("lookback_days")
                    minimum = values.get("min_pa")
                    if type(lookback) is int and type(minimum) is int:
                        return lookback, minimum
    raise ValueError("could not read preserved Statcast lookback/min_pa defaults")


def _aggregate_hash(records: list[dict[str, object]]) -> str:
    payload = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def build(
    *,
    training_path: Path,
    manifest_path: Path,
    cache_dir: Path,
    builder_schema: str = "a3.1",
) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("season") != 2025 or manifest.get("builder_schema") != builder_schema:
        raise ValueError(
            f"availability audit requires the 2025 {builder_schema} training manifest"
        )
    done_dates = sorted(
        value
        for value, row in manifest.get("dates", {}).items()
        if isinstance(row, dict) and row.get("status") == "done" and int(row.get("hitter_rows", 0)) > 0
    )
    if not done_dates:
        raise ValueError("training manifest has no completed hitter dates")

    source_columns = ["game_date", "game_pk", "player_id"]
    validate_source_columns(source_columns)
    training = pd.read_csv(training_path, usecols=source_columns)
    training["game_date"] = training["game_date"].astype(str)
    training = training[training["game_date"].isin(done_dates)].copy()
    if training.empty or training.duplicated(["game_date", "game_pk", "player_id"]).any():
        raise ValueError("training identity rows are empty or duplicated")

    lookback_days, min_events = _preserved_statcast_defaults(
        ROOT / "src/features/legacy_statcast_features.py"
    )
    players = sorted(int(value) for value in training["player_id"].unique())
    event_dates_by_player: dict[int, list[str]] = {}
    cache_records: list[dict[str, object]] = []
    missing_cache: list[int] = []
    empty_cache: list[int] = []
    for player_id in players:
        path = cache_dir / f"batter_{player_id}.csv"
        if not path.is_file():
            missing_cache.append(player_id)
            event_dates_by_player[player_id] = []
            continue
        cache_records.append(
            {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
        try:
            frame = pd.read_csv(
                path, usecols=lambda name: name in {"game_date", "events", "game_type"}
            )
        except pd.errors.EmptyDataError:
            empty_cache.append(player_id)
            event_dates_by_player[player_id] = []
            continue
        required = {"game_date", "events", "game_type"}
        if not required.issubset(frame.columns):
            raise ValueError(f"Statcast cache lacks game_date/events/game_type: {path}")
        event_rows = frame.loc[frame["events"].notna(), ["game_date", "game_type"]]
        event_dates_by_player[player_id] = regular_season_event_dates(
            event_rows["game_date"].astype(str).tolist(),
            event_rows["game_type"].astype(str).tolist(),
        )

    date_rows: dict[str, dict[str, object]] = {}
    for target_date in done_dates:
        active_rows = training.loc[training["game_date"] == target_date]
        active = sorted(int(value) for value in active_rows["player_id"].unique())
        expected = int(manifest["dates"][target_date]["hitter_rows"])
        if len(active_rows) != expected:
            raise ValueError(
                f"training/manifest hitter count mismatch on {target_date}: {len(active_rows)} != {expected}"
            )
        evidence = date_availability(
            active,
            event_dates_by_player,
            target_date=target_date,
            lookback_days=lookback_days,
            min_events=min_events,
        )
        evidence["active_unique_players"] = evidence.pop("active_player_rows")
        evidence["active_player_rows"] = len(active_rows)
        date_rows[target_date] = evidence

    available_dates = [date for date in done_dates if date_rows[date]["runner_requirement_satisfied"]]
    unavailable_dates = [date for date in done_dates if not date_rows[date]["runner_requirement_satisfied"]]
    return {
        "schema_version": SCHEMA,
        "status": STATUS,
        "season": 2025,
        "builder_schema": builder_schema,
        "model_contract": {
            "lookback_days_argument": lookback_days,
            "inclusive_calendar_dates_fetched": lookback_days + 1,
            "minimum_non_null_event_rows_per_advanced_profile": min_events,
            "cache_lower_bound_game_type": "R",
            "runner_requirement": "at least one active hitter has an advanced Statcast profile",
            "model_sources": [
                {"path": str(path), "sha256": sha256_file(ROOT / path)}
                for path in MODEL_SOURCE_PATHS
            ],
        },
        "source_contract": {
            "identity_columns_only": source_columns,
            "outcome_columns_read": [],
            "training_path": str(training_path),
            "training_sha256": sha256_file(training_path),
            "training_manifest_path": str(manifest_path),
            "training_manifest_sha256": sha256_file(manifest_path),
            "statcast_cache_directory": str(cache_dir),
            "statcast_cache_files_used": len(cache_records),
            "statcast_cache_inventory_sha256": _aggregate_hash(cache_records),
            "missing_player_cache_count": len(missing_cache),
            "missing_player_ids": missing_cache,
            "empty_player_cache_count": len(empty_cache),
            "empty_player_ids": empty_cache,
        },
        "done_dates": done_dates,
        "available_dates": available_dates,
        "unavailable_dates": unavailable_dates,
        "dates": date_rows,
        "uses_outcomes_for_availability": False,
        "may_2026_opened": False,
        "betting_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--training",
        type=Path,
        default=Path("data/training/training_hitters_2023_2025_statcast.csv.gz"),
    )
    ap.add_argument("--manifest", type=Path, default=Path("data/training/manifest_2025.json"))
    ap.add_argument("--builder-schema", choices=("a3.1", "a3.2"), default="a3.1")
    ap.add_argument("--cache-dir", type=Path, default=Path("data/cache/statcast/2025"))
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(
            "data/analysis/hr_over_contract_v1/pre2026_fitted_batted_ball_mapping_v1/"
            "input_availability_2025_v1.json"
        ),
    )
    args = ap.parse_args(argv)
    payload = build(
        training_path=args.training.resolve(),
        manifest_path=args.manifest.resolve(),
        cache_dir=args.cache_dir.resolve(),
        builder_schema=args.builder_schema,
    )
    _atomic_json(args.out, payload)
    print(f"audited {len(payload['done_dates'])} completed dates without outcomes")
    print(f"available {len(payload['available_dates'])}; all-fallback {len(payload['unavailable_dates'])}")
    print(f"missing player caches {payload['source_contract']['missing_player_cache_count']}")
    print(f"wrote {args.out}")
    print(f"sha256 {sha256_file(args.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
