#!/usr/bin/env python3
"""Build strictly-prior batter batted-ball features from permissible inputs."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.historical_backfill_contract import (
    BackfillContractError,
    assert_not_may_2026,
    assert_path_outside_sealed_month,
)
from src.data.statcast_integrity import derive_batted_ball_evidence
from src.utils.provenance import sha256_file


SCHEMA_VERSION = "repaired-batter-batted-ball-features-v3"
FEATURE_COLUMNS = (
    "schema_version",
    "target_date",
    "window_start_inclusive",
    "window_end_exclusive",
    "max_source_date",
    "player_id",
    "source_pitch_rows",
    "batted_ball_denominator",
    "barrel_count",
    "hard_hit_count",
    "barrel_rate",
    "hard_hit_rate",
    "avg_exit_velocity",
    "avg_launch_angle",
    "xwoba",
    "xba",
    "xslg",
    "batted_ball_rate_definition",
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_verified_inputs(root: Path) -> tuple[pd.DataFrame, list[dict]]:
    assert_path_outside_sealed_month(root)
    manifests: list[dict] = []
    frames: list[pd.DataFrame] = []
    for path in sorted(root.glob("*.manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("evidence_class") != "HISTORICAL_RECONSTRUCTED_INPUT_ONLY_NONPROSPECTIVE":
            raise BackfillContractError(f"unexpected source evidence class: {path}")
        query = manifest.get("query", {})
        assert_not_may_2026(query.get("start"), context=f"{path.name} query start")
        assert_not_may_2026(query.get("end"), context=f"{path.name} query end")
        artifact = manifest.get("artifact", {})
        data_path = path.parent / str(artifact.get("path"))
        if sha256_file(data_path) != artifact.get("sha256"):
            raise BackfillContractError(f"source artifact hash mismatch: {data_path}")
        frame = pd.read_csv(data_path)
        if "events" in frame.columns or "result" in frame.columns:
            raise BackfillContractError(f"outcome field reached feature workflow: {data_path}")
        frames.append(frame)
        manifests.append(
            {
                "path": path.name,
                "sha256": sha256_file(path),
                "artifact_sha256": artifact.get("sha256"),
            }
        )
    if not frames:
        raise BackfillContractError(f"no verified source manifests in {root}")
    combined = pd.concat(frames, ignore_index=True)
    combined["_game_date"] = pd.to_datetime(
        combined["game_date"], format="%Y-%m-%d", errors="raise"
    ).dt.date
    for value in combined["_game_date"].unique():
        assert_not_may_2026(value, context="source row")
    return combined, manifests


def build_target_features(
    source: pd.DataFrame,
    *,
    target_date: date,
    lookback_days: int = 46,
) -> pd.DataFrame:
    assert_not_may_2026(target_date, context="feature target")
    start = target_date - timedelta(days=lookback_days)
    history = source.loc[
        source["_game_date"].ge(start) & source["_game_date"].lt(target_date)
    ].copy()
    rows: list[dict] = []
    for raw_batter, group in history.groupby("batter", sort=True):
        evidence = derive_batted_ball_evidence(group)
        if evidence is None:
            continue
        batted_balls = group
        if "type" in group.columns:
            batted_balls = group.loc[group["type"].astype("string").eq("X")]
        ev = pd.to_numeric(batted_balls["launch_speed"], errors="coerce")
        la = pd.to_numeric(batted_balls["launch_angle"], errors="coerce")

        def mean(column: str) -> float | None:
            values = pd.to_numeric(batted_balls[column], errors="coerce").dropna()
            return None if values.empty else float(values.mean())

        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "target_date": target_date.isoformat(),
                "window_start_inclusive": start.isoformat(),
                "window_end_exclusive": target_date.isoformat(),
                "max_source_date": group["_game_date"].max().isoformat(),
                "player_id": int(raw_batter),
                "source_pitch_rows": int(len(group)),
                "batted_ball_denominator": evidence.measured_batted_balls,
                "barrel_count": evidence.barrel_count,
                "hard_hit_count": evidence.hard_hit_count,
                "barrel_rate": evidence.barrel_rate,
                "hard_hit_rate": evidence.hard_hit_rate,
                "avg_exit_velocity": None if ev.dropna().empty else float(ev.mean()),
                "avg_launch_angle": None if la.dropna().empty else float(la.mean()),
                "xwoba": mean("estimated_woba_using_speedangle"),
                "xba": mean("estimated_ba_using_speedangle"),
                "xslg": mean("estimated_slg_using_speedangle"),
                "batted_ball_rate_definition": evidence.classification,
            }
        )
    # Empty target dates remain explicit, schema-bearing artifacts rather than
    # zero-byte files that cannot be verified or consumed deterministically.
    result = pd.DataFrame(rows, columns=FEATURE_COLUMNS)
    if not result.empty:
        if not pd.to_datetime(result["max_source_date"]).lt(pd.Timestamp(target_date)).all():
            raise BackfillContractError("feature chronology certificate failed")
        if not (result["barrel_count"] <= result["hard_hit_count"]).all():
            raise BackfillContractError("feature barrel/hard-hit count invariant failed")
        if not (result["hard_hit_count"] <= result["batted_ball_denominator"]).all():
            raise BackfillContractError("feature hard-hit/denominator invariant failed")
    return result


def target_dates(start: date, end: date) -> list[date]:
    if end < start:
        raise BackfillContractError("feature end precedes start")
    output: list[date] = []
    cursor = start
    while cursor <= end:
        try:
            assert_not_may_2026(cursor, context="feature target")
        except BackfillContractError:
            cursor += timedelta(days=1)
            continue
        output.append(cursor)
        cursor += timedelta(days=1)
    return output


def run(
    *,
    source_root: Path,
    out_root: Path,
    start: date,
    end: date,
    lookback_days: int = 46,
) -> Path:
    assert_path_outside_sealed_month(out_root)
    source, source_manifests = load_verified_inputs(source_root)
    out_root.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict] = []
    for target in target_dates(start, end):
        frame = build_target_features(
            source, target_date=target, lookback_days=lookback_days
        )
        path = out_root / f"batter_batted_ball_features_{target.isoformat()}.csv"
        if path.exists():
            raise FileExistsError(f"immutable feature artifact exists: {path}")
        payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
        tmp = path.with_suffix(".csv.tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)
        artifacts.append(
            {
                "target_date": target.isoformat(),
                "path": path.name,
                "rows": int(len(frame)),
                "bytes": len(payload),
                "sha256": _sha256_bytes(payload),
            }
        )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "classification": "INTEGRITY_REPAIR_RESEARCH_CANDIDATE_NOT_PROMOTED",
        "authorization": "RESEARCH_ONLY_NO_BETTING",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "lookback_calendar_days": lookback_days,
        "sealed_month_rule": "MAY_2026_NO_FETCH_NO_READ_NO_PARSE_NO_WRITE",
        "selection_rule": "ALL_PLAYERS_WITH_STRICTLY_PRIOR_PERMISSIBLE_HISTORY_NOT_TARGET_DAY_PARTICIPANTS",
        "source_manifests": source_manifests,
        "artifacts": artifacts,
        "bindings": {
            "runner_sha256": sha256_file(Path(__file__)),
            "integrity_code_sha256": sha256_file(
                ROOT / "src" / "data" / "statcast_integrity.py"
            ),
        },
    }
    manifest_path = out_root / "manifest.json"
    if manifest_path.exists():
        raise FileExistsError(f"immutable feature manifest exists: {manifest_path}")
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(manifest_path)
    return manifest_path


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--start", default="2026-03-25")
    parser.add_argument("--end", default="2026-07-21")
    parser.add_argument("--lookback-days", type=int, default=46)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    path = run(
        source_root=Path(args.source_root),
        out_root=Path(args.out_root),
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        lookback_days=args.lookback_days,
    )
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
