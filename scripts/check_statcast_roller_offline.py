#!/usr/bin/env python3
"""
Offline end-to-end check for the A4 rolling Statcast builder — no network.

A fixture fetch_fn returns a canned per-player Statcast frame, so the roller,
the disk cache, the game-based windowing, and the join-onto-rows path all run
for real without pybaseball.

Canary: every player's frame contains a MONSTER batted-ball game ON the target
date (five 115-mph barrels). If any of that leaks into the as-of window, the
rolling xwOBA/EV/barrel spike and the check fails.

Usage:  python scripts/check_statcast_roller_offline.py
Exit 0 = all pass.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.data.statcast_roller import (
    RollerConfig,
    StatcastRoller,
    rolling_feature_columns,
)

TARGET = "2024-06-15"
DATE2 = "2024-06-16"
PID = 592450

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def _bip(game_date: str, xwoba: float, ev: float, barrel: int, hard_hit: int) -> dict:
    return {
        "game_date": game_date,
        "type": "X",
        "description": "hit_into_play",
        "estimated_woba_using_speedangle": xwoba,
        "estimated_ba_using_speedangle": xwoba * 0.8,
        "estimated_slg_using_speedangle": xwoba * 1.4,
        "launch_speed": ev,
        "launch_angle": 15.0,
        "launch_speed_angle": 6 if barrel else (4 if hard_hit else 2),
        "barrel": barrel,
        "hard_hit": hard_hit,
    }


def _whiff(game_date: str) -> dict:
    d = _bip(game_date, 0.0, 0.0, 0, 0)
    d["type"] = "S"
    d["description"] = "swinging_strike"
    return d


def _lsa_bip(game_date: str, xwoba: float, ev: float, la: float, lsa: int) -> dict:
    """A batted ball as the REAL statcast_batter feed shapes it: no precomputed
    barrel/hard_hit column, but launch_speed_angle + raw launch_speed."""
    return {
        "game_date": game_date,
        "type": "X",
        "description": "hit_into_play",
        "estimated_woba_using_speedangle": xwoba,
        "estimated_ba_using_speedangle": xwoba * 0.8,
        "estimated_slg_using_speedangle": xwoba * 1.4,
        "launch_speed": ev,
        "launch_angle": la,
        "launch_speed_angle": lsa,
    }


def fixture_fetch(player_id: int, start: str, end: str) -> pd.DataFrame:
    """40 modest batted balls over 20 pre-date games + a MONSTER canary game."""
    rows = []
    # 20 games, June 1..20 of prior span; use dates strictly building up.
    for i in range(1, 21):
        gd = f"2024-05-{i:02d}" if i <= 20 else None
        # two BiP + one whiff per game, all modest
        rows.append(_bip(f"2024-05-{i:02d}", xwoba=0.300, ev=88.0, barrel=0, hard_hit=0))
        rows.append(_bip(f"2024-05-{i:02d}", xwoba=0.320, ev=90.0, barrel=0, hard_hit=1))
        rows.append(_whiff(f"2024-05-{i:02d}"))
    # A few June pre-target games so roll15 vs roll30 differ
    for d in (10, 11, 12, 13, 14):
        rows.append(_bip(f"2024-06-{d:02d}", xwoba=0.310, ev=89.0, barrel=0, hard_hit=1))
        rows.append(_bip(f"2024-06-{d:02d}", xwoba=0.305, ev=87.0, barrel=0, hard_hit=0))
    # CANARY: monster game ON the target date — must be excluded.
    for _ in range(5):
        rows.append(_bip(TARGET, xwoba=1.500, ev=115.0, barrel=1, hard_hit=1))
    # And a strong game on DATE2 (advancement check for the second date).
    for _ in range(3):
        rows.append(_bip(DATE2, xwoba=0.900, ev=105.0, barrel=1, hard_hit=1))
    return pd.DataFrame(rows)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="a4check_"))
    try:
        roller = StatcastRoller(
            cache_dir=work / "cache",
            config=RollerConfig(rate_limit_seconds=0),
            fetch_fn=fixture_fetch,
        )

        print("ROLLING FEATURES + LEAKAGE CANARY")
        feats = roller.rolling_features(PID, TARGET)

        # Modest inputs => xwOBA ~0.30s, EV ~88-90. A leak of the 1.500/115 canary
        # would blow these up.
        check("roll15_xwoba present and modest (<0.5)",
              feats["roll15_xwoba"] != "" and float(feats["roll15_xwoba"]) < 0.5,
              f"got {feats['roll15_xwoba']}")
        check("roll15_ev modest (<95, canary 115 excluded)",
              feats["roll15_ev"] != "" and float(feats["roll15_ev"]) < 95,
              f"got {feats['roll15_ev']}")
        check("roll15_barrel_rate ~0 (no barrels before date)",
              feats["roll15_barrel_rate"] != "" and float(feats["roll15_barrel_rate"]) < 0.2,
              f"got {feats['roll15_barrel_rate']}")
        check("roll30_xwoba present and modest",
              feats["roll30_xwoba"] != "" and float(feats["roll30_xwoba"]) < 0.5,
              f"got {feats['roll30_xwoba']}")

        print("\nGAME-BASED WINDOWS")
        # roll15 sees the last 15 distinct game_dates before target; roll30 sees 30.
        check("roll15_games == 15", feats["roll15_games"] == 15, f"got {feats['roll15_games']}")
        check("roll30_games == 25 (only 25 game-days exist pre-target)",
              feats["roll30_games"] == 25, f"got {feats['roll30_games']}")
        check("whiff rate computed from all pitches", feats["roll15_whiff_rate"] != "")

        print("\nAS-OF ADVANCEMENT — next day includes the target-date monster game")
        feats2 = roller.rolling_features(PID, DATE2)
        check("date2 roll15_ev higher (canary now in-window)",
              feats2["roll15_ev"] != "" and float(feats2["roll15_ev"]) > float(feats["roll15_ev"]),
              f"{feats['roll15_ev']} -> {feats2['roll15_ev']}")
        check("date2 roll15_barrel_rate rises (barrels now included)",
              float(feats2["roll15_barrel_rate"]) > float(feats["roll15_barrel_rate"]),
              f"{feats['roll15_barrel_rate']} -> {feats2['roll15_barrel_rate']}")

        print("\nJOIN ONTO A3-STYLE ROWS")
        rows = [
            {"player_id": PID, "game_date": TARGET, "player_name": "Test Hitter", "out_hits": 1},
            {"player_id": "", "game_date": TARGET, "player_name": "No ID"},          # blank id -> blanks
            {"player_id": PID, "game_date": "", "player_name": "No date"},           # blank date -> blanks
        ]
        roller.join_onto_rows(rows)
        cols = rolling_feature_columns()
        check("all roll cols added to row", all(c in rows[0] for c in cols))
        check("real row populated", rows[0]["roll15_xwoba"] != "")
        check("blank-id row -> blank features", rows[1]["roll15_xwoba"] == "")
        check("blank-date row -> blank features", rows[2]["roll15_xwoba"] == "")
        check("original columns preserved", rows[0]["out_hits"] == 1 and rows[0]["player_name"] == "Test Hitter")

        print("\nDISK CACHE — second run hits cache, zero new fetches")
        fetches_before = roller.cache_stats()["fetches"]
        roller2 = StatcastRoller(
            cache_dir=work / "cache",
            config=RollerConfig(rate_limit_seconds=0),
            fetch_fn=fixture_fetch,
        )
        roller2.rolling_features(PID, TARGET)
        check("second roller served from disk cache (0 fetches)",
              roller2.cache_stats()["fetches"] == 0 and roller2.cache_stats()["hits"] == 1,
              str(roller2.cache_stats()))

        print("\nMIN-BIP GUARD — sparse window emits blanks not noise")
        sparse = StatcastRoller(
            cache_dir=work / "cache2",
            config=RollerConfig(rate_limit_seconds=0, min_bip=100),  # force under threshold
            fetch_fn=fixture_fetch,
        )
        sfeat = sparse.rolling_features(PID, TARGET)
        check("under min_bip -> xwoba blank", sfeat["roll15_xwoba"] == "", f"got {sfeat['roll15_xwoba']}")
        check("games count still reported", sfeat["roll15_games"] == 15)

        print("\nBARREL / HARD-HIT DERIVATION (real pybaseball feed has no precomputed cols)")
        # Real statcast_batter returns launch_speed_angle (6 == barrel) and raw
        # launch_speed, NOT a precomputed 'barrel'/'hard_hit' column. Verify the
        # derivation path used on real data.
        def fetch_lsa(pid, start, end):
            rows = []
            for i in range(1, 21):
                rows.append(_lsa_bip(f"2024-05-{i:02d}", xwoba=0.35, ev=102.0, la=28.0, lsa=6))   # barrel + hard-hit
                rows.append(_lsa_bip(f"2024-05-{i:02d}", xwoba=0.32, ev=97.0, la=10.0, lsa=4))    # hard-hit only
                rows.append(_lsa_bip(f"2024-05-{i:02d}", xwoba=0.20, ev=80.0, la=5.0, lsa=2))     # neither
            return pd.DataFrame(rows)

        lsa_roller = StatcastRoller(
            cache_dir=work / "cache_lsa",
            config=RollerConfig(rate_limit_seconds=0),
            fetch_fn=fetch_lsa,
        )
        lf = lsa_roller.rolling_features(674000, TARGET)
        check("barrel_rate from launch_speed_angle==6 (~0.333)",
              lf["roll15_barrel_rate"] != "" and abs(float(lf["roll15_barrel_rate"]) - 0.3333) < 0.01,
              f"got {lf['roll15_barrel_rate']}")
        check("hardhit_rate from EV>=95 (~0.667)",
              lf["roll15_hardhit_rate"] != "" and abs(float(lf["roll15_hardhit_rate"]) - 0.6667) < 0.01,
              f"got {lf['roll15_hardhit_rate']}")

        print(f"\n{PASS} passed, {FAIL} failed")
        return 0 if FAIL == 0 else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
