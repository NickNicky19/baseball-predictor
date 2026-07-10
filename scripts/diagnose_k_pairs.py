#!/usr/bin/env python3
"""
A6 diagnostic -- explain what run_analyze_k_error.py dropped, and characterize
what survived. READ-ONLY: only reads the pairs CSV, writes nothing, changes
nothing. Purely to understand the 32-dropped / 56-kept split before trusting
any K-error numbers.

Run from repo root:
    $env:PYTHONPATH="."          # PowerShell (per PROJECT_CONTEXT gotchas)
    python scripts/diagnose_k_pairs.py --pairs data/learning/prediction_outcomes.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="data/learning/prediction_outcomes.csv")
    args = ap.parse_args(argv)

    path = Path(args.pairs)
    if not path.exists():
        print(f"Not found: {path}")
        return 1

    # Read everything as strings, exactly like the analysis tool does, so we
    # see the raw cells (blank vs "0" vs a number) rather than pandas' guesses.
    df = pd.read_csv(path, dtype=str, keep_default_na=False, low_memory=False)
    print(f"Loaded {len(df)} total pair rows from {path}")
    print(f"Columns: {list(df.columns)}\n")

    # ---- overall category mix -------------------------------------------
    print("=" * 60)
    print("CATEGORY MIX (all rows)")
    print("=" * 60)
    print(df["category"].value_counts().to_string())

    k = df[df["category"] == "strikeouts"].copy()
    print(f"\nstrikeouts rows: {len(k)}")

    # ---- model_version breakdown on K rows ------------------------------
    print("\n" + "=" * 60)
    print("model_version ON strikeouts ROWS")
    print("=" * 60)
    if "model_version" in k.columns:
        mv = k["model_version"].replace("", "<blank>").value_counts()
        print(mv.to_string())
    else:
        print("(no model_version column -- legacy CSV)")

    # ---- why each K row would be dropped --------------------------------
    # Mirror run_analyze_k_error.load_k_rows' filters, but attribute the DROP
    # REASON per row instead of collapsing them.
    pv = _num(k.get("predicted_value", pd.Series(dtype=str)))
    ak = _num(k.get("actual_strikeouts", pd.Series(dtype=str)))
    ip = _num(k.get("actual_ip", pd.Series(dtype=str)))

    reasons = pd.DataFrame(index=k.index)
    reasons["predicted_value_missing"] = pv.isna()
    reasons["actual_strikeouts_missing"] = ak.isna()
    reasons["actual_ip_missing"] = ip.isna()
    reasons["actual_ip_zero_or_neg"] = ip.notna() & (ip <= 0)

    dropped_mask = (reasons["predicted_value_missing"] | reasons["actual_strikeouts_missing"]
                    | reasons["actual_ip_missing"] | reasons["actual_ip_zero_or_neg"])
    kept = k[~dropped_mask]
    dropped = k[dropped_mask]

    print("\n" + "=" * 60)
    print(f"DROP ATTRIBUTION ({len(dropped)} dropped of {len(k)} strikeouts rows)")
    print("=" * 60)
    for col in reasons.columns:
        print(f"  {col:<30} {int(reasons[col].sum())}")
    print("  (a row can hit more than one reason; counts need not sum to total)")

    # Cross the dominant reason with model_version -- this is the key question:
    # are the drops the expected pre-2026-07-09 version-blank/hrr-only legacy
    # rows, or something else (a live recorder gap)?
    if "model_version" in k.columns and len(dropped):
        print("\nDROPPED rows by model_version:")
        dmv = dropped["model_version"].replace("", "<blank>").value_counts()
        print(dmv.to_string())

        # For blank-version dropped rows, show a couple raw examples so you can
        # eyeball whether they're old/degenerate rows or fresh-but-broken ones.
        blank = dropped[dropped["model_version"] == ""]
        if len(blank):
            print(f"\n{len(blank)} dropped rows have BLANK model_version "
                  f"(PROJECT_CONTEXT says exclude pre-2026-07-09 version-blank CI pairs).")
            cols = [c for c in ("player_name", "game_date", "predicted_value",
                                 "actual_strikeouts", "actual_ip", "actual_value")
                    if c in blank.columns]
            print("  sample:")
            print(blank[cols].head(6).to_string(index=False))

    # ---- date range of dropped vs kept ----------------------------------
    if "game_date" in k.columns:
        print("\n" + "=" * 60)
        print("DATE RANGE (dropped vs kept)")
        print("=" * 60)
        def rng(frame):
            d = pd.to_datetime(frame["game_date"], errors="coerce").dropna()
            return (d.min(), d.max(), d.dt.date.nunique()) if len(d) else (None, None, 0)
        for label, frame in (("dropped", dropped), ("kept", kept)):
            lo, hi, nd = rng(frame)
            print(f"  {label:<8} n={len(frame):<4} dates {lo} .. {hi}  ({nd} distinct dates)")

    # ---- composition of the KEPT rows (the 56) --------------------------
    print("\n" + "=" * 60)
    print(f"KEPT rows ({len(kept)}) -- what you're actually analyzing")
    print("=" * 60)
    if len(kept):
        ip_kept = _num(kept["actual_ip"])
        print(f"  actual_ip: min={ip_kept.min():.1f}  median={ip_kept.median():.1f}  "
              f"max={ip_kept.max():.1f}")
        print(f"  distinct pitchers: {_num(kept['player_id']).nunique()}")
        print(f"  distinct dates:    {pd.to_datetime(kept['game_date'], errors='coerce').dt.date.nunique()}")
        # short-outing concentration -- the buckets driving the bias signal
        short = int((ip_kept < 3.0).sum())
        print(f"  rows with actual_ip < 3.0 (the high-bias short-outing zone): {short}")
        if short < 10:
            print("  NOTE: fewer than 10 short-outing rows -- the opener/bulk bias magnitude "
                  "rests on a handful of games. Shape is suggestive; magnitude is not yet "
                  "fittable. (Matches the thin-K-sample caveat in PROJECT_CONTEXT.)")

    print("\nDone. This script wrote nothing; it only read the pairs CSV.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
