#!/usr/bin/env python3
"""
BEFORE writing the edge measurement: verify the two inputs actually join.

Rule 1/9. I have NOT checked any of the following and will not assume them:
  1. That data/learning/predictions/predictions_<date>.json carries a
     `simulation.p_ge_threshold` block (run_slate.py's docstring says it does;
     PredictionArchive._dict_to_projection reportedly sets simulation=None on
     ROUND-TRIP, which is a different path -- but I have not read either).
  2. That SmartStake's mon=2026-07 partition covers 2026-07-05..07-11.
  3. That the graded `result` column is populated for those dates -- the dataset
     card says "July rows carry odds but result and won are null."  If that is
     true for the whole month, THERE IS NOTHING TO GRADE and the whole plan is
     dead. This is the single most likely blocker.
  4. That the player names join.

If (3) fails, we cannot measure edge on the live prediction window and must
fall back to reconstructing a MAY or JUNE date instead (where grading exists).

Usage:
    python scripts/probe_edge_inputs.py
"""
from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

import duckdb
import pandas as pd

PRED_DIR = Path("data/learning/predictions")


def norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s))
                if not unicodedata.combining(c))
    s = s.lower().strip()
    for ch in ".'`-":
        s = s.replace(ch, "")
    parts = [p for p in s.split() if p not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(parts)


print("=" * 72)
print("1. LIVE PREDICTION FILES -- do they carry p_ge_threshold?")
print("=" * 72)
files = sorted(PRED_DIR.glob("predictions_*.json"))
if not files:
    print(f"FATAL: no prediction files in {PRED_DIR}", file=sys.stderr)
    sys.exit(2)

rows = []
for f in files:
    doc = json.loads(f.read_text(encoding="utf-8"))
    hp = doc.get("hitter_projections", []) or []
    pp = doc.get("pitcher_projections", []) or []
    with_sim = sum(
        1 for p in hp + pp
        if (p.get("simulation") or {}).get("p_ge_threshold")
    )
    cats = sorted({p.get("category") for p in hp + pp})
    rows.append({
        "file": f.name,
        "date": doc.get("game_date", f.stem.replace("predictions_", "")),
        "n_proj": len(hp) + len(pp),
        "with_p_ge": with_sim,
        "categories": ",".join(str(c) for c in cats),
    })
pf = pd.DataFrame(rows)
print(pf.to_string(index=False))

if pf["with_p_ge"].sum() == 0:
    print("\nFATAL: NO prediction carries a p_ge_threshold block. The archive round-trip\n"
          "  drops the simulation block (PredictionArchive._dict_to_projection sets\n"
          "  simulation=None). Without P(over) there is nothing to compare to a price.\n"
          "  FALLBACK: reconstruct the dates via run_gate_reconstruct.py instead.",
          file=sys.stderr)
    sys.exit(2)

dates = sorted(pf["date"].tolist())
print(f"\nprediction dates: {dates[0]} .. {dates[-1]}")

# ---------------------------------------------------------------------------
print()
print("=" * 72)
print("2. SMARTSTAKE JULY -- is `result` populated? (the likely blocker)")
print("=" * 72)
HF = "'hf://datasets/SmartStake/mlb-player-props/mon=2026-07/*.parquet'"
duckdb.sql("INSTALL httpfs; LOAD httpfs;")
duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

cov = duckdb.sql(f"""
    SELECT CAST(start_time AT TIME ZONE 'UTC'
                           AT TIME ZONE 'America/New_York' AS DATE) AS game_date,
           count(DISTINCT game_id)                        AS games,
           count(*)                                       AS rows,
           sum(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) AS graded_rows,
           round(100.0 * sum(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END)
                 / count(*), 1)                           AS pct_graded
    FROM {HF}
    GROUP BY 1 ORDER BY 1
""").df()
print(cov.to_string(index=False))

overlap = cov[cov.game_date.astype(str).isin(dates)]
print(f"\noverlap with prediction dates ({len(overlap)} of {len(dates)}):")
if overlap.empty:
    print("  NONE. SmartStake July has no rows on the prediction dates.")
else:
    print(overlap.to_string(index=False))
    tot = int(overlap.graded_rows.sum())
    print(f"\n  GRADED rows on overlapping dates: {tot:,}")
    if tot == 0:
        print("\n  FATAL: rows exist but NOTHING IS GRADED on those dates. The dataset\n"
              "  card warns: 'July rows carry odds but result and won are null.'\n"
              "  We cannot score the model against outcomes here.\n"
              "  FALLBACK: reconstruct a MAY/JUNE date (graded) via run_gate_reconstruct.py.",
              file=sys.stderr)

# ---------------------------------------------------------------------------
print()
print("=" * 72)
print("3. NAME JOIN -- do the live predictions' players exist in SmartStake?")
print("=" * 72)
doc = json.loads(files[-1].read_text(encoding="utf-8"))
pred_names = {norm(p["player_name"])
              for p in (doc.get("hitter_projections") or [])}
print(f"  prediction hitters ({files[-1].name}): {len(pred_names)}")

mk = duckdb.sql(f"""
    SELECT DISTINCT player FROM {HF}
    WHERE market IN ('player hits', 'player home runs', 'player bases')
""").df()
mk_names = {norm(p) for p in mk.player}
print(f"  SmartStake July hitters: {len(mk_names)}")
hit = pred_names & mk_names
print(f"  matched: {len(hit)}/{len(pred_names)} ({100*len(hit)/max(1,len(pred_names)):.1f}%)")
miss = sorted(pred_names - mk_names)
if miss:
    print(f"  unmatched ({len(miss)}), first 20:")
    for m in miss[:20]:
        print(f"    {m}")

print()
print("=" * 72)
print("VERDICT")
print("=" * 72)
print("  Proceed ONLY if: p_ge_threshold present AND graded rows > 0 on")
print("  overlapping dates AND name match >= 90%.")
print("  Any FATAL above means the live-prediction window cannot be scored;")
print("  fall back to reconstructing a graded May/June date.")
