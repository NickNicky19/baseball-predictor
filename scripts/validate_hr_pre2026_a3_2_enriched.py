#!/usr/bin/env python3
"""Certify exact raw/enriched a3.2/a4.1 twins before assembly."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.statcast_roller import ROLLER_SCHEMA, rolling_feature_columns  # noqa: E402
from src.evaluation.hr_pre2026_a3_2_migration import load_protocol, sha256  # noqa: E402
from scripts.validate_hr_pre2026_a3_2_raw import _validate_root  # noqa: E402

KEY = ["game_pk", "player_id"]
EXTRA = rolling_feature_columns((15, 30)) + ["roller_schema"]


def _digest(records: list[dict[str, Any]]) -> str:
    blob = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _equal_raw(raw: pd.DataFrame, enriched: pd.DataFrame, date: str) -> None:
    try:
        pd.testing.assert_frame_equal(
            raw.reset_index(drop=True),
            enriched.loc[:, raw.columns].reset_index(drop=True),
            check_dtype=False,
            check_exact=True,
        )
    except AssertionError as exc:
        raise ValueError(f"raw columns changed during enrichment for {date}") from exc


def _validate_enriched_root(root: Path, seasons: list[int]) -> dict[str, Any]:
    raw_report = _validate_root(root, seasons)
    inventory: list[dict[str, Any]] = []
    cache_inventory: list[dict[str, Any]] = []
    totals = {"dates": 0, "rows": 0, "advanced_rows": 0, "fallback_rows": 0}
    season_reports: list[dict[str, Any]] = []

    for season in seasons:
        manifest = json.loads((root / f"manifest_{season}.json").read_text(encoding="utf-8"))
        dates = sorted(manifest["dates"])
        sd = root / str(season)
        expected = {f"hitters_{date}_statcast.csv" for date in dates}
        actual = {p.name for p in sd.glob("hitters_*_statcast.csv")}
        if actual != expected:
            raise ValueError(f"enriched shard inventory mismatch for {season}")
        sr = {"season": season, "dates": 0, "rows": 0, "advanced_rows": 0, "fallback_rows": 0}
        player_ids: set[int] = set()
        for date in dates:
            raw_path = sd / f"hitters_{date}.csv"
            enriched_path = sd / f"hitters_{date}_statcast.csv"
            raw = pd.read_csv(raw_path)
            enriched = pd.read_csv(enriched_path)
            if len(raw) != len(enriched):
                raise ValueError(f"raw/enriched row count mismatch for {date}")
            actual_columns = list(enriched.columns)
            raw_width = len(raw.columns)
            if actual_columns[:raw_width] != list(raw.columns):
                raise ValueError(f"raw column prefix changed for {date}")
            added = actual_columns[raw_width:]
            if (
                len(added) != len(EXTRA)
                or set(added) != set(EXTRA)
                or len(added) != len(set(added))
                or added[-1] != "roller_schema"
            ):
                raise ValueError(f"enriched column contract mismatch for {date}")
            _equal_raw(raw, enriched, date)
            if not enriched.empty:
                if not raw[KEY].equals(enriched[KEY]):
                    raise ValueError(f"raw/enriched identity order mismatch for {date}")
                if set(enriched["roller_schema"].astype(str)) != {ROLLER_SCHEMA}:
                    raise ValueError(f"roller schema mismatch for {date}")
                if enriched[EXTRA[:-1]].replace([float("inf"), float("-inf")], pd.NA).isna().all(axis=1).any():
                    # `games` and `bip` are always factual even when rate
                    # features are blank; an entirely blank row means the join
                    # never ran, not a legitimate small-sample fallback.
                    raise ValueError(f"unjoined Statcast row for {date}")
                player_ids.update(enriched.player_id.astype(int))
                advanced = enriched[["roll15_bip", "roll30_bip"]].fillna(0).max(axis=1) > 0
                sr["advanced_rows"] += int(advanced.sum())
                sr["fallback_rows"] += int((~advanced).sum())
            inventory.append({
                "season": season, "date": date,
                "path": enriched_path.relative_to(ROOT).as_posix(),
                "rows": len(enriched), "sha256": sha256(enriched_path),
            })
            sr["dates"] += 1
            sr["rows"] += len(enriched)

        for player_id in sorted(player_ids):
            cache_path = ROOT / "data/cache/statcast" / str(season) / f"batter_{player_id}.csv"
            if not cache_path.is_file():
                raise ValueError(f"used Statcast cache file missing: {season}/{player_id}")
            cache_inventory.append({
                "season": season, "player_id": player_id,
                "path": cache_path.relative_to(ROOT).as_posix(),
                "sha256": sha256(cache_path),
            })
        for key in totals:
            totals[key] += sr[key]
        season_reports.append(sr)

    if totals["rows"] != raw_report["totals"]["hitter_rows"]:
        raise ValueError("full raw/enriched row totals differ")
    return {
        "status": "VALID_ENRICHED_A3_2_A4_1",
        "seasons": seasons,
        "totals": totals,
        "season_reports": season_reports,
        "raw_inventory_sha256": raw_report["inventory_sha256"],
        "enriched_files": len(inventory),
        "enriched_inventory_sha256": _digest(inventory),
        "statcast_cache_files": len(cache_inventory),
        "statcast_cache_inventory_sha256": _digest(cache_inventory),
        "enriched_inventory": inventory,
        "statcast_cache_inventory": cache_inventory,
        "may_2026_opened": False,
        "betting_authorized": False,
    }


def validate(protocol_path: Path, report_out: Path | None) -> dict[str, Any]:
    protocol = load_protocol(protocol_path, verify_files=True)
    root = ROOT / protocol["outputs"]["root"]
    report = _validate_enriched_root(root, [int(v) for v in protocol["seasons"]])
    report["protocol_path"] = protocol_path.relative_to(ROOT).as_posix()
    report["protocol_sha256"] = sha256(protocol_path)
    if report_out is not None:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        tmp = report_out.with_suffix(report_out.suffix + ".tmp")
        tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        tmp.replace(report_out)
    return report


def _fixture(root: Path) -> None:
    extras = {name: 0.0 for name in EXTRA[:-1]}
    for season in (2023, 2024, 2025):
        date = f"{season}-04-01"
        sd = root / str(season)
        sd.mkdir(parents=True)
        hitters = []
        for is_home in (0, 1):
            for slot in range(1, 10):
                hitters.append({
                    "builder_schema": "a3.2", "season": season, "game_date": date,
                    "game_pk": season * 10, "player_id": season * 100 + is_home * 10 + slot,
                    "is_home": is_home, "lineup_slot": slot, "out_pa": 4,
                    "out_hits": 1, "out_hr": 0,
                })
        pitchers = [
            {"builder_schema": "a3.2", "season": season, "game_date": date,
             "game_pk": season * 10, "player_id": season * 1000 + side,
             "is_home": side, "out_ip": 6, "out_k": 6, "out_hr": 1}
            for side in (0, 1)
        ]
        h = pd.DataFrame(hitters)
        h.to_csv(sd / f"hitters_{date}.csv", index=False)
        pd.DataFrame(pitchers).to_csv(sd / f"pitchers_{date}.csv", index=False)
        e = h.copy()
        for col, value in extras.items():
            e[col] = value
        e["roller_schema"] = ROLLER_SCHEMA
        e.to_csv(sd / f"hitters_{date}_statcast.csv", index=False)
        (root / f"manifest_{season}.json").write_text(json.dumps({
            "season": season, "builder_schema": "a3.2", "dates": {
                date: {"status": "done", "games": 1, "hitter_rows": 18, "pitcher_rows": 2}
            }
        }), encoding="utf-8")
        cache = ROOT / "data/cache/statcast" / str(season)
        cache.mkdir(parents=True, exist_ok=True)
        # Tests avoid touching the real cache by not calling full cache
        # validation; `_validate_twins_only` below isolates file mutations.


def _validate_twins_only(root: Path) -> None:
    for season in (2023, 2024, 2025):
        date = f"{season}-04-01"
        raw = pd.read_csv(root / str(season) / f"hitters_{date}.csv")
        enriched = pd.read_csv(root / str(season) / f"hitters_{date}_statcast.csv")
        actual = list(enriched.columns)
        added = actual[len(raw.columns):]
        if (
            actual[:len(raw.columns)] != list(raw.columns)
            or len(added) != len(EXTRA)
            or set(added) != set(EXTRA)
            or len(added) != len(set(added))
            or added[-1] != "roller_schema"
        ):
            raise ValueError("columns")
        if len(raw) != len(enriched):
            raise ValueError("rows")
        _equal_raw(raw, enriched, date)
        if set(enriched.roller_schema.astype(str)) != {ROLLER_SCHEMA}:
            raise ValueError("schema")


def self_test() -> int:
    work = Path(tempfile.mkdtemp(prefix="enriched_a3_2_validate_"))
    try:
        _fixture(work)
        _validate_twins_only(work)
        print("[OK] valid raw/enriched twins pass")

        def mutation(label: str, edit) -> None:
            target = work.parent / f"{work.name}_{label.replace(' ', '_')}"
            shutil.copytree(work, target)
            edit(target)
            try:
                _validate_twins_only(target)
            except ValueError:
                print(f"[OK] MUTATION {label} fails")
            else:
                raise AssertionError(f"mutation passed: {label}")
            finally:
                shutil.rmtree(target, ignore_errors=True)

        ep = lambda r: r / "2023" / "hitters_2023-04-01_statcast.csv"
        mutation("missing row", lambda r: _drop_row(ep(r)))
        mutation("changed identity", lambda r: _rewrite(ep(r), "player_id", 999, 0))
        mutation("changed raw value", lambda r: _rewrite(ep(r), "out_hits", 9, 0))
        mutation("wrong roller schema", lambda r: _rewrite(ep(r), "roller_schema", "a4.0", None))
        mutation("missing feature column", lambda r: _drop_column(ep(r), "roll30_whiff_rate"))
        print("6/6")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _drop_row(path: Path) -> None:
    pd.read_csv(path).iloc[:-1].to_csv(path, index=False)


def _rewrite(path: Path, column: str, value: Any, row: int | None) -> None:
    frame = pd.read_csv(path)
    if row is None:
        frame[column] = value
    else:
        frame.loc[row, column] = value
    frame.to_csv(path, index=False)


def _drop_column(path: Path, column: str) -> None:
    pd.read_csv(path).drop(columns=[column]).to_csv(path, index=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.protocol is None:
        parser.error("--protocol is required unless --self-test is used")
    report = validate(args.protocol.resolve(), args.report_out.resolve() if args.report_out else None)
    print(json.dumps({k: v for k, v in report.items() if not k.endswith("inventory")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
