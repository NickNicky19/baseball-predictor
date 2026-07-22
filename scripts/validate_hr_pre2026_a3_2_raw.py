#!/usr/bin/env python3
"""Validate raw a3.2 original-starter shards before Statcast enrichment."""
from __future__ import annotations

import argparse
import copy
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

from src.evaluation.hr_pre2026_a3_2_migration import load_protocol, sha256  # noqa: E402

HKEY = ["game_pk", "player_id"]
REQUIRED_HITTER = {
    "builder_schema", "season", "game_date", "game_pk", "player_id",
    "is_home", "lineup_slot", "out_pa", "out_hits", "out_hr",
}
REQUIRED_PITCHER = {
    "builder_schema", "season", "game_date", "game_pk", "player_id",
    "is_home", "out_ip", "out_k", "out_hr",
}


def _inventory_digest(records: list[dict[str, Any]]) -> str:
    blob = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def _validate_root(root: Path, seasons: list[int]) -> dict[str, Any]:
    if not root.is_dir():
        raise ValueError(f"raw root missing: {root}")
    inventory: list[dict[str, Any]] = []
    seen_game_dates: dict[int, str] = {}
    totals = {"dates": 0, "games": 0, "hitter_rows": 0, "pitcher_rows": 0}
    season_reports: list[dict[str, Any]] = []

    for season in seasons:
        manifest_path = root / f"manifest_{season}.json"
        if not manifest_path.is_file():
            raise ValueError(f"missing manifest for {season}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("season") != season or manifest.get("builder_schema") != "a3.2":
            raise ValueError(f"manifest identity/schema mismatch for {season}")
        dates = manifest.get("dates")
        if not isinstance(dates, dict) or not dates:
            raise ValueError(f"manifest dates missing for {season}")
        season_dir = root / str(season)
        expected_hitters = {f"hitters_{date}.csv" for date in dates}
        expected_pitchers = {f"pitchers_{date}.csv" for date in dates}
        actual_hitters = {
            p.name for p in season_dir.glob("hitters_*.csv")
            if not p.name.endswith("_statcast.csv")
        }
        actual_pitchers = {p.name for p in season_dir.glob("pitchers_*.csv")}
        if actual_hitters != expected_hitters:
            raise ValueError(f"hitter shard inventory mismatch for {season}")
        if actual_pitchers != expected_pitchers:
            raise ValueError(f"pitcher shard inventory mismatch for {season}")

        sr = {"season": season, "dates": 0, "games": 0, "hitter_rows": 0, "pitcher_rows": 0}
        for date, rec in sorted(dates.items()):
            if not isinstance(rec, dict) or rec.get("status") not in {"done", "empty"}:
                raise ValueError(f"invalid manifest status for {date}")
            hp = season_dir / f"hitters_{date}.csv"
            pp = season_dir / f"pitchers_{date}.csv"
            h = pd.read_csv(hp)
            p = pd.read_csv(pp)
            if not REQUIRED_HITTER.issubset(h.columns) or not REQUIRED_PITCHER.issubset(p.columns):
                raise ValueError(f"required raw columns missing for {date}")
            expected_h = int(rec.get("hitter_rows", -1))
            expected_p = int(rec.get("pitcher_rows", -1))
            games = int(rec.get("games", -1))
            if len(h) != expected_h or len(p) != expected_p:
                raise ValueError(f"manifest row count mismatch for {date}")
            if (games == 0) != (rec.get("status") == "empty"):
                raise ValueError(f"manifest empty status mismatch for {date}")
            if expected_h != games * 18 or expected_p != games * 2:
                raise ValueError(f"starter row contract mismatch for {date}")

            for label, frame in (("hitter", h), ("pitcher", p)):
                if frame.empty:
                    continue
                if frame[HKEY].isna().any().any() or frame.duplicated(HKEY).any():
                    raise ValueError(f"{label} identity invalid for {date}")
                if set(frame["builder_schema"].astype(str)) != {"a3.2"}:
                    raise ValueError(f"{label} builder schema mismatch for {date}")
                if set(frame["season"].astype(int)) != {season}:
                    raise ValueError(f"{label} season mismatch for {date}")
                if set(frame["game_date"].astype(str)) != {date}:
                    raise ValueError(f"{label} date mismatch for {date}")

            if not h.empty:
                h_games = set(h.game_pk.astype(int))
                p_games = set(p.game_pk.astype(int))
                if h_games != p_games or len(h_games) != games:
                    raise ValueError(f"game population mismatch for {date}")
                if not (h.groupby("game_pk").size() == 18).all():
                    raise ValueError(f"not exactly 18 hitters per game for {date}")
                if not (p.groupby("game_pk").size() == 2).all():
                    raise ValueError(f"not exactly 2 pitchers per game for {date}")
                for (game_pk, is_home), group in h.groupby(["game_pk", "is_home"]):
                    if sorted(group.lineup_slot.astype(int).tolist()) != list(range(1, 10)):
                        raise ValueError(f"lineup slots invalid for {date} game {game_pk} side {is_home}")
                for game_pk in h_games:
                    prior = seen_game_dates.setdefault(int(game_pk), date)
                    if prior != date:
                        raise ValueError(f"game_pk={game_pk} maps to multiple dates")

            for kind, path, rows in (("hitters", hp, len(h)), ("pitchers", pp, len(p))):
                inventory.append({
                    "season": season, "date": date, "kind": kind,
                    "path": path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path),
                    "rows": rows, "sha256": sha256(path),
                })
            sr["dates"] += 1
            sr["games"] += games
            sr["hitter_rows"] += len(h)
            sr["pitcher_rows"] += len(p)
        for key in totals:
            totals[key] += sr[key]
        season_reports.append(sr)

    canary_path = root / "2024" / "hitters_2024-06-26.csv"
    if 2024 in seasons and canary_path.is_file():
        canary = pd.read_csv(canary_path)
        dual = canary[(canary.game_pk == 746942) & (canary.player_id == 643376)]
        if len(dual) != 1 or int(dual.iloc[0].is_home) != 0 or int(dual.iloc[0].lineup_slot) != 7:
            raise ValueError("real dual-team original-starter canary failed")

    return {
        "status": "VALID_RAW_A3_2",
        "seasons": seasons,
        "totals": totals,
        "season_reports": season_reports,
        "inventory_files": len(inventory),
        "inventory_sha256": _inventory_digest(inventory),
        "inventory": inventory,
        "may_2026_opened": False,
        "betting_authorized": False,
    }


def validate(protocol_path: Path, report_out: Path | None) -> dict[str, Any]:
    protocol = load_protocol(protocol_path, verify_files=True)
    root = ROOT / protocol["outputs"]["root"]
    report = _validate_root(root, [int(v) for v in protocol["seasons"]])
    report["protocol_path"] = protocol_path.relative_to(ROOT).as_posix()
    report["protocol_sha256"] = sha256(protocol_path)
    if report_out is not None:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        tmp = report_out.with_suffix(report_out.suffix + ".tmp")
        tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        tmp.replace(report_out)
    return report


def _fixture(root: Path) -> None:
    for season in (2023, 2024, 2025):
        date = f"{season}-04-01"
        sd = root / str(season)
        sd.mkdir(parents=True)
        hitters = []
        for is_home in (0, 1):
            for slot in range(1, 10):
                hitters.append({
                    "builder_schema": "a3.2", "season": season,
                    "game_date": date, "game_pk": season * 10,
                    "player_id": season * 100 + is_home * 10 + slot,
                    "is_home": is_home, "lineup_slot": slot,
                    "out_pa": 4, "out_hits": 1, "out_hr": 0,
                })
        pitchers = [
            {"builder_schema": "a3.2", "season": season, "game_date": date,
             "game_pk": season * 10, "player_id": season * 1000 + side,
             "is_home": side, "out_ip": 6, "out_k": 6, "out_hr": 1}
            for side in (0, 1)
        ]
        pd.DataFrame(hitters).to_csv(sd / f"hitters_{date}.csv", index=False)
        pd.DataFrame(pitchers).to_csv(sd / f"pitchers_{date}.csv", index=False)
        (root / f"manifest_{season}.json").write_text(json.dumps({
            "season": season, "builder_schema": "a3.2", "dates": {
                date: {"status": "done", "games": 1, "hitter_rows": 18, "pitcher_rows": 2}
            }
        }), encoding="utf-8")


def self_test() -> int:
    work = Path(tempfile.mkdtemp(prefix="raw_a3_2_validate_"))
    try:
        _fixture(work)
        _validate_root(work, [2023, 2024, 2025])
        print("[OK] valid raw fixture passes")
        mutations = []

        def mutation(label: str, edit) -> None:
            root = work.parent / f"{work.name}_{len(mutations)}"
            shutil.copytree(work, root)
            edit(root)
            try:
                _validate_root(root, [2023, 2024, 2025])
            except ValueError:
                print(f"[OK] MUTATION {label} fails")
            else:
                raise AssertionError(f"mutation passed: {label}")
            finally:
                shutil.rmtree(root, ignore_errors=True)
            mutations.append(label)

        date = "2023-04-01"
        mutation("missing shard", lambda r: (r / "2023" / f"pitchers_{date}.csv").unlink())
        mutation("wrong builder schema", lambda r: _rewrite(r / "2023" / f"hitters_{date}.csv", "builder_schema", "a3.1"))
        mutation("duplicate identity", lambda r: _duplicate(r / "2023" / f"hitters_{date}.csv"))
        mutation("invalid lineup slot", lambda r: _rewrite(r / "2023" / f"hitters_{date}.csv", "lineup_slot", 2, row=0))
        mutation("wrong game date", lambda r: _rewrite(r / "2023" / f"hitters_{date}.csv", "game_date", "2023-04-02"))
        mutation("manifest row count", lambda r: _manifest_count(r / "manifest_2023.json"))
        print("7/7")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _rewrite(path: Path, column: str, value: Any, row: int | None = None) -> None:
    frame = pd.read_csv(path)
    if row is None:
        frame[column] = value
    else:
        frame.loc[row, column] = value
    frame.to_csv(path, index=False)


def _duplicate(path: Path) -> None:
    frame = pd.read_csv(path)
    pd.concat([frame, frame.iloc[[0]]], ignore_index=True).to_csv(path, index=False)


def _manifest_count(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    next(iter(payload["dates"].values()))["hitter_rows"] = 17
    path.write_text(json.dumps(payload), encoding="utf-8")


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
    print(json.dumps({k: v for k, v in report.items() if k != "inventory"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
