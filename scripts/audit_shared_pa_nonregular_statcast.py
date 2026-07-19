#!/usr/bin/env python3
"""Measure non-regular-season Statcast contamination in canonical PA inputs.

This is an outcome-blind provenance audit.  It reads only player/game/date
identity from the training artifact and only Statcast event date/type from the
raw caches.  It does not fit, select, score, or modify a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd


SCHEMA = "shared-pa-nonregular-statcast-audit-v1"
IDENTITY_COLUMNS = ("game_date", "game_pk", "player_id")
RAW_COLUMNS = ("game_date", "game_type", "events")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _read_identity(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=list(IDENTITY_COLUMNS))
    if tuple(frame.columns) != IDENTITY_COLUMNS:
        raise ValueError("training identity column order drifted")
    frame["game_date"] = pd.to_datetime(frame["game_date"], errors="raise").dt.normalize()
    for column in ("game_pk", "player_id"):
        if frame[column].isna().any():
            raise ValueError(f"training identity contains null {column}")
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("int64")
    if frame.duplicated(list(IDENTITY_COLUMNS)).any():
        raise ValueError("training identity is not unique")
    return frame


def _cache_events(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, usecols=list(RAW_COLUMNS), low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=RAW_COLUMNS)
    if set(frame.columns) != set(RAW_COLUMNS):
        raise ValueError(f"Statcast cache lacks required fields: {path}")
    frame = frame.loc[frame["events"].notna(), ["game_date", "game_type"]].copy()
    frame["game_date"] = pd.to_datetime(frame["game_date"], errors="raise").dt.normalize()
    frame["game_type"] = frame["game_type"].astype("string")
    if frame["game_type"].isna().any() or frame["game_type"].str.len().eq(0).any():
        raise ValueError(f"Statcast cache contains missing game_type: {path}")
    return frame


def audit(training: Path, cache_root: Path, window_days: int) -> dict[str, Any]:
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    targets = _read_identity(training)
    target_windows = targets[["player_id", "game_date"]].drop_duplicates().copy()
    target_dates = (
        target_windows.groupby("player_id", sort=False)["game_date"]
        .agg(lambda values: sorted(set(values))).to_dict()
    )
    results: list[dict[str, Any]] = []
    cache_types: Counter[str] = Counter()
    missing_caches: list[dict[str, int]] = []
    empty_caches: list[dict[str, int]] = []

    for player_id, dates in target_dates.items():
        by_year: dict[int, list[pd.Timestamp]] = {}
        for target_date in dates:
            by_year.setdefault(int(target_date.year), []).append(target_date)
        for season, dates_in_season in by_year.items():
            cache_path = cache_root / str(season) / f"batter_{int(player_id)}.csv"
            cache_marker = {"player_id": int(player_id), "season": int(season)}
            if not cache_path.is_file():
                missing_caches.append(cache_marker)
                continue
            events = _cache_events(cache_path)
            if events.empty:
                empty_caches.append(cache_marker)
                continue
            cache_types.update(events["game_type"].astype(str))
            for target_date in dates_in_season:
                start = target_date - timedelta(days=window_days + 1)
                window = events.loc[
                    events["game_date"].ge(start) & events["game_date"].lt(target_date)
                ]
                if window.empty:
                    non_regular = 0
                    total = 0
                    types: dict[str, int] = {}
                else:
                    total = int(len(window))
                    non_regular_rows = window.loc[window["game_type"].ne("R")]
                    non_regular = int(len(non_regular_rows))
                    types = {
                        str(key): int(value)
                        for key, value in non_regular_rows["game_type"].value_counts().sort_index().items()
                    }
                results.append(
                    {
                        "player_id": int(player_id),
                        "game_date": target_date.date().isoformat(),
                        "statcast_event_rows": total,
                        "non_regular_event_rows": non_regular,
                        "non_regular_game_types": types,
                    }
                )

    profile = pd.DataFrame(results)
    total_targets = int(len(targets))
    total_windows = int(len(target_windows))
    if profile.empty:
        profile = pd.DataFrame(columns=["player_id", "game_date", "statcast_event_rows", "non_regular_event_rows", "non_regular_game_types"])
    profile["game_date"] = pd.to_datetime(profile["game_date"], errors="raise").dt.normalize()
    profile_rows = targets.merge(profile, on=["player_id", "game_date"], how="left", validate="many_to_one")
    unavailable_pairs = {
        (int(item["player_id"]), int(item["season"]))
        for item in missing_caches + empty_caches
    }
    unexplained = profile_rows.loc[
        profile_rows["statcast_event_rows"].isna()
        & ~profile_rows.apply(
            lambda row: (int(row.player_id), int(row.game_date.year)) in unavailable_pairs,
            axis=1,
        )
    ]
    if not unexplained.empty:
        raise ValueError("target coverage loss has an unexplained cause")
    covered = int(profile_rows["statcast_event_rows"].notna().sum())
    affected = profile_rows.loc[profile_rows["non_regular_event_rows"].fillna(0).gt(0)].copy()
    by_date = (
        affected.groupby("game_date", sort=True)
        .agg(
            affected_player_game_rows=("player_id", "size"),
            non_regular_event_rows=("non_regular_event_rows", "sum"),
        )
        .reset_index()
    )
    by_date["game_date"] = pd.to_datetime(by_date["game_date"], errors="raise").dt.date.astype(str)
    type_counts: Counter[str] = Counter()
    for value in affected["non_regular_game_types"]:
        type_counts.update(value)

    return {
        "schema_version": SCHEMA,
        "status": "OUTCOME_BLIND_INPUT_CONTAMINATION_MEASURED",
        "scope": {
            "outcome_columns_read": [],
            "model_fit": False,
            "model_selection": False,
            "model_scoring": False,
            "market_or_economic_scoring": False,
            "may_2026_read": False,
        },
        "input_contract": {
            "training_path": str(training.resolve()),
            "training_sha256": sha256(training),
            "identity_columns_only": list(IDENTITY_COLUMNS),
            "cache_root": str(cache_root.resolve()),
            "raw_columns_only": list(RAW_COLUMNS),
            "regular_season_game_type": "R",
            "feature_window": f"[target_date-{window_days + 1} days, target_date)",
        },
        "coverage": {
            "training_player_game_rows": total_targets,
            "unique_player_date_feature_windows": total_windows,
            "player_game_rows_with_readable_cache": covered,
            "missing_cache_player_ids": sorted(missing_caches),
            "empty_cache_player_ids": sorted(empty_caches),
            "cache_game_type_counts": dict(sorted(cache_types.items())),
        },
        "contamination": {
            "affected_player_game_rows": int(len(affected)),
            "affected_share_of_readable_profiles": (
                float(len(affected) / covered) if covered else None
            ),
            "non_regular_event_rows_in_feature_windows": int(affected["non_regular_event_rows"].sum()),
            "non_regular_game_type_counts_in_feature_windows": dict(sorted(type_counts.items())),
            "affected_dates": by_date.to_dict(orient="records"),
        },
        "decision": {
            "production_change_permitted": False,
            "candidate_refit_permitted": bool(len(affected) > 0),
            "reason": (
                "Non-regular Statcast events are demonstrably present in the canonical "
                "feature window. A candidate may only filter them by refitting and "
                "strictly adjudicating the complete chronological protocol."
                if len(affected) > 0
                else "No non-regular Statcast events were found in the canonical feature window."
            ),
            "required_next_step_if_affected": [
                "Freeze a regular-season-only canonical feature contract with source/runtime parity.",
                "Refit using 2023 only; select only on 2024; keep 2025 as untouched confirmation.",
                "Require material paired scoring, calibration, discrimination, coverage, and market gates.",
                "Do not change production unless every predeclared gate passes.",
            ],
        },
        "betting_authorized": False,
        "production_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--training", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--window-days", type=int, default=45)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload = audit(args.training, args.cache_root, args.window_days)
    atomic_json(args.out, payload)
    print(f"audited {payload['coverage']['training_player_game_rows']} player-game windows")
    print(f"non-regular affected: {payload['contamination']['affected_player_game_rows']}")
    print(f"wrote {args.out}")
    print(f"sha256 {sha256(args.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
