#!/usr/bin/env python3
"""Build the locked 2023-only direct batter PA development panel v4."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from src.features.direct_batter_pa_history import (  # noqa: E402
    EVENT_TO_OUTCOME, NON_PA_EVENTS, OUTCOMES, history_features, prepare_raw,
)


IDENTITY = ["season", "game_date", "game_pk", "player_id"]
OFFICIAL = [
    "season", "game_date", "game_pk", "player_id", "lineup_slot", "out_pa", "out_ab",
    "out_hits", "out_doubles", "out_triples", "out_hr", "out_bb", "out_k",
]
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def outcome_counts(frame: pd.DataFrame) -> pd.DataFrame:
    numeric = frame[["out_pa", "out_ab", "out_hits", "out_doubles", "out_triples", "out_hr", "out_bb", "out_k"]].apply(pd.to_numeric, errors="raise")
    out = pd.DataFrame(index=frame.index)
    out["strikeout"] = numeric["out_k"]
    out["walk"] = numeric["out_bb"]
    out["single"] = numeric["out_hits"] - numeric["out_doubles"] - numeric["out_triples"] - numeric["out_hr"]
    out["double"] = numeric["out_doubles"]
    out["triple"] = numeric["out_triples"]
    out["home_run"] = numeric["out_hr"]
    out["bip_out"] = numeric["out_ab"] - numeric["out_hits"] - numeric["out_k"]
    out["other_non_ab"] = numeric["out_pa"] - numeric["out_ab"] - numeric["out_bb"]
    if (out < 0).any().any():
        raise ValueError("official PA outcome decomposition is negative")
    if not out.sum(axis=1).eq(numeric["out_pa"]).all():
        raise ValueError("official PA outcome decomposition does not sum to out_pa")
    return out.loc[:, OUTCOMES].astype(int)


def raw_terminal_outcome_counts(frame: pd.DataFrame, *, player_id: int) -> pd.DataFrame:
    """Build player-game truth from unique regular-season terminal PA events."""
    prepared = prepare_raw(frame, player_id=player_id)
    regular = prepared.loc[
        prepared["game_type"].astype(str).eq("R") & prepared["events"].notna()
    ].copy()
    events = regular["events"].astype(str)
    if unknown := sorted(set(events).difference(EVENT_TO_OUTCOME).difference(NON_PA_EVENTS)):
        raise ValueError(f"unmapped raw terminal events: {unknown}")
    terminal = regular.loc[~events.isin(NON_PA_EVENTS)].copy()
    terminal["outcome"] = terminal["events"].astype(str).map(EVENT_TO_OUTCOME)
    if terminal["outcome"].isna().any():
        raise ValueError("raw terminal PA outcome mapping failed")
    if terminal.empty:
        return pd.DataFrame(columns=[*IDENTITY, *[f"target_{name}" for name in OUTCOMES]])
    terminal["game_date"] = terminal["_date"].dt.strftime("%Y-%m-%d")
    terminal["season"] = terminal["_date"].dt.year.astype(int)
    grouped = terminal.groupby(
        ["season", "game_date", "game_pk", "batter", "outcome"], sort=False,
    ).size().unstack(fill_value=0)
    for name in OUTCOMES:
        if name not in grouped:
            grouped[name] = 0
    grouped = grouped.loc[:, OUTCOMES].reset_index().rename(columns={"batter": "player_id", **{name: f"target_{name}" for name in OUTCOMES}})
    if grouped[[f"target_{name}" for name in OUTCOMES]].to_numpy().sum() != len(terminal):
        raise ValueError("raw terminal PA denominator accounting failed")
    return grouped


def _normalize_identity(frame: pd.DataFrame, *, context: str) -> pd.DataFrame:
    if missing := sorted(set(IDENTITY).difference(frame.columns)):
        raise ValueError(f"{context} identity columns missing: {missing}")
    normalized = frame.copy()
    for column in ("season", "game_pk", "player_id"):
        values = pd.to_numeric(normalized[column], errors="coerce")
        if values.isna().any() or not values.eq(values.round()).all():
            raise ValueError(f"{context} {column} identity is invalid")
        normalized[column] = values.astype(int)
    dates = pd.to_datetime(normalized["game_date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or not dates.dt.strftime("%Y-%m-%d").eq(normalized["game_date"].astype(str)).all():
        raise ValueError(f"{context} game_date identity is not canonical ISO")
    if not dates.dt.year.eq(normalized["season"]).all():
        raise ValueError(f"{context} season and game_date identity disagree")
    normalized["game_date"] = dates.dt.strftime("%Y-%m-%d")
    return normalized


def validate_zero_pa_evidence(frame: pd.DataFrame) -> pd.DataFrame:
    required = {*IDENTITY, "verified_zero_pa", "source_kind", "source_sha256"}
    if missing := sorted(required.difference(frame.columns)):
        raise ValueError(f"zero-PA evidence columns missing: {missing}")
    zero = _normalize_identity(frame.loc[:, sorted(required)], context="zero-PA evidence")
    if zero.duplicated(IDENTITY).any():
        raise ValueError("zero-PA evidence identity is duplicated")
    if not zero.empty:
        if not pd.api.types.is_bool_dtype(zero["verified_zero_pa"]) or not zero["verified_zero_pa"].all():
            raise ValueError("zero-PA evidence must explicitly verify every zero-PA row")
    if not zero["source_kind"].eq("official_final_boxscore_zero_pa").all():
        raise ValueError("zero-PA evidence source kind is not approved")
    hashes = zero["source_sha256"].astype("string")
    if hashes.isna().any() or not hashes.str.fullmatch(r"[0-9a-f]{64}").all():
        raise ValueError("zero-PA evidence source hash is invalid")
    return zero


def apply_raw_outcome_truth(
    targets: pd.DataFrame, raw_counts: pd.DataFrame, zero_pa_evidence: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    """Replace inherited aggregates with raw truth and prove consumption parity."""
    targets = _normalize_identity(targets, context="target")
    raw_counts = _normalize_identity(raw_counts, context="raw terminal")
    zero_pa_evidence = validate_zero_pa_evidence(zero_pa_evidence)
    if targets.duplicated(IDENTITY).any() or raw_counts.duplicated(IDENTITY).any():
        raise ValueError("raw outcome reconciliation identity is duplicated")
    target_keys = set(targets[IDENTITY].itertuples(index=False, name=None))
    raw_keys = set(raw_counts[IDENTITY].itertuples(index=False, name=None))
    zero_keys = set(zero_pa_evidence[IDENTITY].itertuples(index=False, name=None))
    if orphaned := sorted(raw_keys.difference(target_keys)):
        raise ValueError(f"raw outcome evidence has target-orphan identities: {orphaned[:3]}")
    if orphaned := sorted(zero_keys.difference(target_keys)):
        raise ValueError(f"zero-PA evidence has target-orphan identities: {orphaned[:3]}")
    if overlap := sorted(raw_keys.intersection(zero_keys)):
        raise ValueError(f"raw terminal and zero-PA evidence contradict: {overlap[:3]}")
    original = outcome_counts(targets).add_prefix("official_")
    base = targets.drop(columns=[f"target_{name}" for name in OUTCOMES]).copy()
    merged = base.merge(raw_counts, on=IDENTITY, how="left", validate="one_to_one", indicator=True)
    target_columns = [f"target_{name}" for name in OUTCOMES]
    missing_mask = merged["_merge"].eq("left_only")
    missing_keys = set(merged.loc[missing_mask, IDENTITY].itertuples(index=False, name=None))
    if missing_keys != zero_keys:
        missing_evidence = sorted(missing_keys.difference(zero_keys))
        unused_evidence = sorted(zero_keys.difference(missing_keys))
        raise ValueError(
            "raw target coverage is not exactly explained by zero-PA evidence: "
            f"missing={missing_evidence[:3]} unused={unused_evidence[:3]}"
        )
    official_pa = pd.to_numeric(merged["out_pa"], errors="raise").astype(int)
    if not official_pa.loc[missing_mask].eq(0).all():
        raise ValueError("positive-PA official target lacks raw terminal evidence")
    if merged.loc[~missing_mask, target_columns].isna().any().any():
        raise ValueError("raw terminal outcome evidence is partially missing")
    merged.loc[missing_mask, target_columns] = 0
    merged[target_columns] = merged[target_columns].astype(int)
    merged.drop(columns="_merge", inplace=True)
    repaired = pd.DataFrame(index=merged.index)
    repaired["out_k"] = merged["target_strikeout"]
    repaired["out_bb"] = merged["target_walk"]
    repaired["out_hits"] = merged[["target_single", "target_double", "target_triple", "target_home_run"]].sum(axis=1)
    repaired["out_doubles"] = merged["target_double"]
    repaired["out_triples"] = merged["target_triple"]
    repaired["out_hr"] = merged["target_home_run"]
    repaired["out_ab"] = repaired["out_k"] + repaired["out_hits"] + merged["target_bip_out"]
    repaired["out_pa"] = merged[target_columns].sum(axis=1)
    for column in repaired:
        merged[column] = repaired[column].astype(int)
    consumed = outcome_counts(merged)
    expected = merged[target_columns].rename(columns={f"target_{name}": name for name in OUTCOMES})
    if not consumed.equals(expected):
        raise ValueError("probability consumer outcome columns do not equal raw terminal truth")
    official_mismatches = int((original.reset_index(drop=True) != expected.add_prefix("official_").reset_index(drop=True)).any(axis=1).sum())
    return merged, official_mismatches


def load_targets(path: Path, *, maximum_rows: int) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=OFFICIAL, nrows=int(maximum_rows), low_memory=False)
    if len(frame) != maximum_rows:
        raise ValueError(f"official selection prefix row count changed: {len(frame)}")
    years = pd.to_numeric(frame["season"], errors="raise").astype(int)
    if set(years.unique()) != {2023}:
        raise ValueError("direct batter v4 development prefix must contain only 2023")
    if frame.duplicated(IDENTITY).any():
        raise ValueError("official selection identities are duplicated")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or not dates.dt.year.eq(years).all():
        raise ValueError("official selection dates are malformed or disagree with season")
    if dates.dt.strftime("%Y-%m").eq("2026-05").any():
        raise ValueError("May 2026 is forbidden")
    counts = outcome_counts(frame)
    return pd.concat([frame.reset_index(drop=True), counts.add_prefix("target_")], axis=1)


def build(
    *, official: Path, raw_root: Path, zero_pa_evidence: Path, protocol: Path,
    output: Path, manifest: Path, maximum_rows: int,
) -> dict[str, Any]:
    if output.exists() or manifest.exists():
        raise FileExistsError("refusing to overwrite direct batter PA evidence")
    contract = json.loads(protocol.read_text(encoding="utf-8"))
    if contract.get("status") != "LOCKED_BEFORE_2023_ONLY_PANEL_V4_BUILD":
        raise ValueError("direct batter v4 protocol is not locked")
    if contract.get("chronology", {}).get("development_years") != [2023]:
        raise ValueError("direct batter v4 protocol is not 2023-only")
    if contract.get("chronology", {}).get("historical_selection_windows") != []:
        raise ValueError("direct batter v4 protocol improperly claims a historical selection window")
    targets = load_targets(official, maximum_rows=maximum_rows)
    zero_pa = pd.read_csv(zero_pa_evidence, low_memory=False)
    feature_rows: list[dict[str, Any]] = []
    raw_target_parts: list[pd.DataFrame] = []
    raw_hashes: dict[str, str] = {}
    missing_prior_year_files: list[str] = []
    raw_non_target_groups_excluded = 0
    for player_id, player_targets in targets.groupby("player_id", sort=True):
        parts = []
        target_years = set(pd.to_numeric(player_targets["season"], errors="raise").astype(int))
        for year in (2023,):
            source = raw_root / str(year) / f"batter_{int(player_id)}.csv"
            if not source.is_file():
                if year in target_years:
                    raise FileNotFoundError(f"missing target-year batter source: {source}")
                missing_prior_year_files.append(str(source.relative_to(raw_root)))
                continue
            raw_hashes[str(source.relative_to(raw_root))] = sha256_file(source)
            parts.append(pd.read_csv(source, usecols=lambda name: name in {
                "game_date", "game_type", "batter", "events", "description", "type", "zone",
                "game_pk", "at_bat_number", "pitch_number",
                "pitch_type", "release_speed", "pfx_x", "pfx_z", "plate_x", "plate_z",
                "launch_speed", "launch_angle", "launch_speed_angle",
            }, low_memory=False))
        if not parts:
            raise FileNotFoundError(f"no direct batter source for player {int(player_id)}")
        raw_player = pd.concat(parts, ignore_index=True)
        all_raw_counts = raw_terminal_outcome_counts(raw_player, player_id=int(player_id))
        player_target_identity = _normalize_identity(
            player_targets.loc[:, IDENTITY], context="player target",
        )
        matched_raw_counts = all_raw_counts.merge(
            player_target_identity, on=IDENTITY, how="inner", validate="one_to_one",
        )
        raw_non_target_groups_excluded += int(len(all_raw_counts) - len(matched_raw_counts))
        raw_target_parts.append(matched_raw_counts)
        prepared = prepare_raw(raw_player, player_id=int(player_id))
        for target_date in sorted(player_targets["game_date"].astype(str).unique()):
            feature_rows.append(history_features(prepared, player_id=int(player_id), target_date=target_date))
    targets, official_mismatches = apply_raw_outcome_truth(
        targets, pd.concat(raw_target_parts, ignore_index=True), zero_pa,
    )
    features = pd.DataFrame(feature_rows)
    if features.duplicated(["player_id", "target_date"]).any():
        raise ValueError("direct batter feature player/date identity is duplicated")
    merged = targets.merge(features, left_on=["player_id", "game_date"], right_on=["player_id", "target_date"], how="left", validate="many_to_one")
    if merged["target_date"].isna().any() or len(merged) != len(targets):
        raise ValueError("direct batter feature coverage is incomplete")
    invalid_chronology = merged["max_source_date"].notna() & (merged["max_source_date"] >= merged["game_date"])
    if invalid_chronology.any():
        raise ValueError("direct batter panel contains same-day/future feature input")
    merged.sort_values(["game_date", "game_pk", "player_id"], inplace=True, kind="stable")
    merged.reset_index(drop=True, inplace=True)
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, filename="") as zipped:
        zipped.write(merged.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    identity_paths = [
        ROOT / "scripts" / "build_direct_batter_pa_panel.py",
        ROOT / "scripts" / "collect_direct_batter_zero_pa_receipts.py",
        ROOT / "scripts" / "validate_direct_batter_pa_panel.py",
        ROOT / "src" / "features" / "direct_batter_pa_history.py",
        ROOT / "src" / "data" / "statcast_integrity.py",
        ROOT / "tests" / "test_build_direct_batter_pa_panel.py",
        ROOT / "tests" / "test_collect_direct_batter_zero_pa_receipts.py",
        ROOT / "tests" / "test_direct_batter_pa_history.py",
        ROOT / "tests" / "test_statcast_integrity_repair.py",
        ROOT / "requirements.txt",
        ROOT / "pyproject.toml",
    ]
    if missing_identity := [str(path) for path in identity_paths if not path.is_file()]:
        raise FileNotFoundError(f"panel identity files missing: {missing_identity}")
    identity_files = {
        path.relative_to(ROOT).as_posix(): sha256_file(path)
        for path in identity_paths
    }
    atomic_bytes(output, buffer.getvalue())
    payload: dict[str, Any] = {
        "schema_version": "direct-batter-pa-panel-manifest-v4",
        "status": "DIRECT_BATTER_PA_TIMING_CONTRACT_PASSED_RESEARCH_ONLY",
        "protocol": {"path": str(protocol), "sha256": sha256_file(protocol)},
        "official": {"path": str(official), "sha256": sha256_file(official), "maximum_rows_read": maximum_rows},
        "raw_root": str(raw_root),
        "raw_source_sha256": raw_hashes,
        "zero_pa_evidence": {
            "path": str(zero_pa_evidence), "sha256": sha256_file(zero_pa_evidence),
            "rows": len(zero_pa),
        },
        "missing_prior_year_files": sorted(set(missing_prior_year_files)),
        "output": {"path": str(output), "sha256": sha256_file(output), "rows": len(merged)},
        "population": {
            "physical_target_rows_2023": int(len(merged)),
            "fit_eligible_rows_2023": int(merged["out_pa"].gt(0).sum()),
            "selection_rows_2024": 0,
            "zero_pa_rows": int(merged[[f"target_{name}" for name in OUTCOMES]].sum(axis=1).eq(0).sum()),
            "feature_coverage": 1.0,
            "chronology_violations": 0,
            "official_outcome_mismatch_rows_repaired": official_mismatches,
            "probability_consumer_raw_truth_parity": True,
            "raw_non_target_terminal_groups_retained_only_as_history": raw_non_target_groups_excluded,
        },
        "feature_policy": {
            "batter_only": True, "regular_season_only": True, "same_day_excluded": True,
            "league_value_imputation": False, "pitcher_matchup_features": False,
            "history_scope": "available_2023_regular_season_to_date",
            "all_prior_claimed": False,
        },
        "outcome_truth": (
            "unique 2023 regular-season raw terminal PA events plus independently "
            "receipt-bound official-final-boxscore zero-PA evidence"
        ),
        "identity_policy": {
            "join_keys": IDENTITY,
            "raw_game_date_required": True,
            "raw_season_date_parity_required": True,
            "terminal_event_must_be_final_pitch": True,
            "silent_zero_fill": False,
        },
        "identity_files_sha256": identity_files,
        "dependency_identity": {
            "declaration": "requirements.txt",
            "declaration_sha256": identity_files["requirements.txt"],
            "build_metadata": "pyproject.toml",
            "build_metadata_sha256": identity_files["pyproject.toml"],
            "exact_environment_lock_claimed": False,
        },
        "build_runtime_identity": {
            "python_version": os.sys.version,
            "python_executable": str(Path(os.sys.executable).resolve()),
            "python_executable_sha256": sha256_file(Path(os.sys.executable).resolve()),
            "pandas_version": pd.__version__,
            "numpy_version": np.__version__,
        },
        "script_sha256": identity_files["scripts/build_direct_batter_pa_panel.py"],
        "may_2026_opened": False,
        "confirmation_2025_opened": False,
        "production_changed": False,
        "betting_authorized": False,
    }
    atomic_bytes(manifest, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official", required=True, type=Path)
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--zero-pa-evidence", required=True, type=Path)
    parser.add_argument("--protocol", type=Path, default=ROOT / "config/direct_batter_pa_foundation_v4.json")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--maximum-rows", type=int, default=43740)
    args = parser.parse_args()
    result = build(**vars(args))
    print(json.dumps({"status": result["status"], "output": result["output"], "population": result["population"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
