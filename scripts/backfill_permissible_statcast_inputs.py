#!/usr/bin/env python3
"""Backfill only permissible historical Statcast input columns.

This is reconstructed research data, never prospective evidence. Queries are
split before execution so no request can include May 2026. Returned frames are
immediately projected onto the outcome-blind 2026 input allowlist.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Callable, Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.historical_backfill_contract import (
    BackfillContractError,
    SEALED_END,
    SEALED_START,
    assert_not_may_2026,
    assert_path_outside_sealed_month,
    project_2026_point_in_time_inputs,
)
from src.data.statcast_integrity import derive_batted_ball_evidence
from src.utils.provenance import sha256_file


SCHEMA_VERSION = "permissible-statcast-input-backfill-v2"
DEFAULT_START = date(2026, 3, 25)
DEFAULT_END = date(2026, 7, 21)


def permissible_chunks(start: date, end: date, *, days: int = 7) -> list[tuple[date, date]]:
    if end < start:
        raise BackfillContractError("backfill end precedes start")
    if days < 1 or days > 31:
        raise BackfillContractError("chunk days must be in [1,31]")
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        if SEALED_START <= cursor <= SEALED_END:
            cursor = SEALED_END + timedelta(days=1)
            continue
        boundary = min(end, cursor + timedelta(days=days - 1))
        if cursor < SEALED_START <= boundary:
            boundary = SEALED_START - timedelta(days=1)
        assert_not_may_2026(cursor, context="chunk start")
        assert_not_may_2026(boundary, context="chunk end")
        chunks.append((cursor, boundary))
        cursor = boundary + timedelta(days=1)
    return chunks


def _canonical_bytes(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, lineterminator="\n").encode("utf-8")


def _validate_projected_frame(frame: pd.DataFrame, start: date, end: date) -> dict[str, int]:
    if frame.empty:
        return {"rows": 0, "players": 0, "measured_batted_balls": 0}
    dates = pd.to_datetime(frame["game_date"], errors="raise").dt.date
    if any(value < start or value > end for value in dates):
        raise BackfillContractError("source returned rows outside the requested chunk")
    if any(SEALED_START <= value <= SEALED_END for value in dates):
        raise BackfillContractError("source returned sealed May 2026 rows")

    identity = [
        column
        for column in (
            "game_date", "batter", "pitcher", "at_bat_number", "pitch_number", "sv_id"
        )
        if column in frame.columns
    ]
    if len(identity) >= 5 and bool(frame.duplicated(identity, keep=False).any()):
        raise BackfillContractError(f"duplicate Statcast input identity: {identity}")

    measured = 0
    if {"launch_speed", "launch_speed_angle"}.issubset(frame.columns):
        for _, group in frame.groupby("batter", sort=False):
            evidence = derive_batted_ball_evidence(group)
            if evidence is not None:
                measured += evidence.measured_batted_balls
    return {
        "rows": int(len(frame)),
        "players": int(pd.to_numeric(frame["batter"], errors="coerce").nunique()),
        "measured_batted_balls": int(measured),
    }


def write_chunk(
    *,
    raw: pd.DataFrame,
    start: date,
    end: date,
    out_root: Path,
    config_path: Path,
    overwrite: bool = False,
) -> Path:
    assert_path_outside_sealed_month(out_root)
    projected = project_2026_point_in_time_inputs(raw)
    stats = _validate_projected_frame(projected, start, end)
    chunk_name = f"statcast_inputs_{start.isoformat()}_{end.isoformat()}"
    data_path = out_root / f"{chunk_name}.csv"
    manifest_path = out_root / f"{chunk_name}.manifest.json"
    if (data_path.exists() or manifest_path.exists()) and not overwrite:
        raise FileExistsError(f"immutable chunk already exists: {chunk_name}")
    out_root.mkdir(parents=True, exist_ok=True)

    payload = _canonical_bytes(projected)
    digest = hashlib.sha256(payload).hexdigest()
    data_tmp = data_path.with_suffix(".csv.tmp")
    data_tmp.write_bytes(payload)
    data_tmp.replace(data_path)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "evidence_class": "HISTORICAL_RECONSTRUCTED_INPUT_ONLY_NONPROSPECTIVE",
        "authorization": "RESEARCH_ONLY_NO_BETTING",
        "query": {"start": start.isoformat(), "end": end.isoformat()},
        "sealed_month_rule": "MAY_2026_NO_FETCH_NO_READ_NO_PARSE_NO_WRITE",
        "outcome_fields": "EXCLUDED",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "columns": list(projected.columns),
        "statistics": stats,
        "artifact": {
            "path": data_path.name,
            "bytes": len(payload),
            "sha256": digest,
        },
        "bindings": {
            "runner_sha256": sha256_file(Path(__file__)),
            "contract_code_sha256": sha256_file(
                ROOT / "src" / "data" / "historical_backfill_contract.py"
            ),
            "config_sha256": sha256_file(config_path),
        },
    }
    manifest_tmp = manifest_path.with_suffix(".json.tmp")
    manifest_tmp.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    manifest_tmp.replace(manifest_path)
    return manifest_path


def _fetch_statcast(start: date, end: date) -> pd.DataFrame:
    try:
        import pybaseball as pyb
    except ImportError as exc:
        raise RuntimeError("pybaseball is required for Statcast backfill") from exc
    pyb.cache.enable()
    result = pyb.statcast(start_dt=start.isoformat(), end_dt=end.isoformat())
    if result is None:
        raise RuntimeError("Statcast provider returned no frame")
    return result


def run(
    *,
    start: date,
    end: date,
    out_root: Path,
    config_path: Path,
    chunk_days: int,
    fetcher: Callable[[date, date], pd.DataFrame] = _fetch_statcast,
) -> list[Path]:
    outputs: list[Path] = []
    for chunk_start, chunk_end in permissible_chunks(start, end, days=chunk_days):
        raw = fetcher(chunk_start, chunk_end)
        outputs.append(
            write_chunk(
                raw=raw,
                start=chunk_start,
                end=chunk_end,
                out_root=out_root,
                config_path=config_path,
            )
        )
    return outputs


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=DEFAULT_END.isoformat())
    parser.add_argument("--chunk-days", type=int, default=7)
    parser.add_argument(
        "--out-root",
        default="data/analysis/system_integrity_v1/raw_2026_inputs",
    )
    parser.add_argument(
        "--config",
        default="config/historical_raw_backfill_v1.json",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    outputs = run(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        out_root=Path(args.out_root),
        config_path=Path(args.config),
        chunk_days=args.chunk_days,
    )
    print(json.dumps({"status": "complete", "manifests": [str(p) for p in outputs]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
