#!/usr/bin/env python3
"""Audit pitcher-K challenger inputs without opening any May 2026 file."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


KEY = ["game_pk", "player_id", "game_date"]
PREGAME_FEATURES = [
    "is_home", "throws", "pit_ip", "pit_k9", "pit_bb9", "pit_hr9", "pit_gs",
    "pit_recent_ip", "pit_recent_k9", "pit_recent_bb9", "pit_recent_hr9",
    "pit_recent_gs", "pit_recent_ip_per_gs", "park_hits_factor", "park_hr_factor",
    "park_runs_factor", "park_resolved", "weather_temp", "weather_wind",
    "weather_is_dome", "weather_resolved", "umpire_resolved", "has_prior_data",
]
OUTCOME_COLUMNS = ["out_ip", "out_k", "out_bb", "out_hr"]
OPEN_MONTHS = {"2026-03", "2026-04", "2026-06"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = set(KEY + PREGAME_FEATURES + OUTCOME_COLUMNS)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"pitcher training schema missing {missing}: {path}")
    frame = frame.copy()
    frame["game_date"] = pd.to_datetime(frame["game_date"], errors="raise").dt.strftime("%Y-%m-%d")
    frame["game_pk"] = pd.to_numeric(frame["game_pk"], errors="raise").astype(int)
    frame["player_id"] = pd.to_numeric(frame["player_id"], errors="raise").astype(int)
    if frame[KEY].isna().any().any() or frame.duplicated(KEY).any():
        raise ValueError(f"pitcher training identity is null or duplicated: {path}")
    if frame[OUTCOME_COLUMNS].isna().any().any():
        raise ValueError(f"pitcher training official outcome fields are incomplete: {path}")
    return frame


def _summary(frame: pd.DataFrame) -> dict:
    valid = frame[pd.to_numeric(frame["out_ip"], errors="raise").gt(0)]
    return {
        "rows": int(len(frame)),
        "starter_like_out_ip_positive_rows": int(len(valid)),
        "dates": int(frame.game_date.nunique()),
        "seasons": sorted(int(value) for value in pd.to_datetime(frame.game_date).dt.year.unique()),
        "pregame_feature_missing_counts": {column: int(frame[column].isna().sum()) for column in PREGAME_FEATURES},
    }


def _open_dates(manifest_path: Path) -> list[str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "SOURCES_BOUND_BEFORE_BENCHMARK_SCORING_V2":
        raise ValueError("open-date manifest status is not certified")
    dates = [str(value) for value in manifest.get("dates") or []]
    if len(dates) != 56 or len(set(dates)) != 56 or any(date[:7] not in OPEN_MONTHS for date in dates):
        raise ValueError("open-date manifest changes the 56-date sealed-May universe")
    return dates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pre2026", type=Path, required=True)
    parser.add_argument("--open-date-manifest", type=Path, required=True)
    parser.add_argument("--daily-root", type=Path, required=True)
    parser.add_argument("--official-outcomes", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    pre = _read_csv(args.pre2026)
    if set(pd.to_datetime(pre.game_date).dt.year.unique()) != {2023, 2024, 2025}:
        raise ValueError("pre-2026 challenger training seasons changed")
    dates = _open_dates(args.open_date_manifest)
    daily_paths = [args.daily_root / f"pitchers_{date}.csv" for date in dates]
    missing = [str(path) for path in daily_paths if not path.is_file()]
    if missing:
        raise ValueError(f"approved open-date pitcher files missing: {missing[:3]}")
    open_2026 = pd.concat([_read_csv(path) for path in daily_paths], ignore_index=True)
    if set(open_2026.game_date.unique()) != set(dates) or open_2026.game_date.str.startswith("2026-05").any():
        raise ValueError("daily source accessed a non-approved or May date")
    if open_2026.duplicated(KEY).any():
        raise ValueError("combined open-date pitcher identity is duplicated")

    official = pd.read_csv(args.official_outcomes)
    official_key = ["mlb_game_pk", "player_id", "game_date"]
    required = set(official_key + ["terminal_state", "actual_strikeouts"])
    if missing := sorted(required - set(official.columns)):
        raise ValueError(f"official benchmark outcomes missing {missing}")
    official = official.copy()
    official["game_date"] = pd.to_datetime(official.game_date, errors="raise").dt.strftime("%Y-%m-%d")
    official = official.rename(columns={"mlb_game_pk": "game_pk"})
    official = official[official.terminal_state.eq("started")].copy()
    if official[KEY].isna().any().any() or official.duplicated(KEY).any():
        raise ValueError("official benchmark starter identity is invalid")
    joined = official.merge(open_2026[KEY + ["out_k"]], on=KEY, how="left", validate="one_to_one")
    if joined.out_k.isna().any() or not (pd.to_numeric(joined.out_k) == pd.to_numeric(joined.actual_strikeouts)).all():
        raise ValueError("approved open-date training outcomes disagree with official benchmark outcomes")

    payload = {
        "schema_version": "pitcher-k-challenger-input-audit-v2",
        "status": "POINT_IN_TIME_INPUTS_READY_FOR_PROTOCOL_DESIGN",
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "purpose": "Read-only data availability and outcome-separation audit. Only the 56 explicitly named March-April-June daily source files were opened.",
        "sources": {
            "pre2026": {"path": str(args.pre2026.resolve()), "sha256": sha256(args.pre2026)},
            "open_date_manifest": {"path": str(args.open_date_manifest.resolve()), "sha256": sha256(args.open_date_manifest), "dates": dates},
            "daily_pitcher_files": [{"path": str(path.resolve()), "sha256": sha256(path)} for path in daily_paths],
            "official_outcomes": {"path": str(args.official_outcomes.resolve()), "sha256": sha256(args.official_outcomes)},
        },
        "pre2026": _summary(pre),
        "open2026": _summary(open_2026),
        "official_started_crosscheck": {
            "official_started_pitcher_games": int(len(official)),
            "matched_point_in_time_training_rows": int(joined.out_k.notna().sum()),
            "official_k_exact_match": True,
        },
        "feature_contract": {
            "prediction_features": PREGAME_FEATURES,
            "outcome_columns_forbidden_during_prediction": OUTCOME_COLUMNS,
            "no_player_id_or_game_id_feature": True,
            "may_rows_excluded": True,
            "approved_open_dates_only": True,
        },
        "next_step": "Only a separately locked chronological challenger protocol may fit or select a model from these inputs.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.out), "sha256": sha256(args.out), "official_started": len(official)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
