"""
Grading audit — verify the actual_value in the pairs file against a fresh
re-fetch of the real box score.

This settles WHY some recorded actuals look wrong. For each pair it prints:
  predicted | recorded actual | freshly re-derived actual | the raw box line

...so you can see at a glance whether the problem is:
  (a) a DATE mismatch (recorded actual is right for the pair's date, but you
      were comparing a different day's box score),
  (b) a FIELD-mapping bug (re-derived != recorded, and recorded looks like the
      wrong stat, e.g. at-bats), or
  (c) a PLAYER-ID match failure (recorded 0.0 for someone who clearly did
      something — the recorder didn't find them and defaulted).

Run it locally (needs MLB API / network). It changes nothing — read-only audit.

    python scripts/audit_grading.py --pairs data/learning/prediction_outcomes.csv
    python scripts/audit_grading.py --player "Francisco Lindor"
    python scripts/audit_grading.py --date 2026-07-05 --category hrr --limit 20
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.mlb_api import MLBStatsAPI  # noqa: E402
from src.learning.outcome_recorder import compute_actual_value  # noqa: E402
from src.simulation.monte_carlo import FantasyScoring  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="data/learning/prediction_outcomes.csv")
    ap.add_argument("--player", default="", help="filter to one player name (substring)")
    ap.add_argument("--date", default="", help="filter to one game_date (YYYY-MM-DD)")
    ap.add_argument("--category", default="", help="filter to one category")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--mismatches-only", action="store_true",
                    help="only show rows where recorded != re-derived")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.pairs, newline="", encoding="utf-8")))
    if args.player:
        rows = [r for r in rows if args.player.lower() in r["player_name"].lower()]
    if args.date:
        rows = [r for r in rows if r["game_date"] == args.date]
    if args.category:
        rows = [r for r in rows if r["category"] == args.category]
    rows = rows[: args.limit]
    if not rows:
        print("No matching rows.")
        return

    fantasy = FantasyScoring()

    # Cache actuals per date so we fetch each date once.
    cache: dict[str, tuple[dict, dict]] = {}

    def actuals_for(game_date: str):
        if game_date not in cache:
            api = MLBStatsAPI(season=int(game_date[:4]))
            cache[game_date] = api.get_actuals_for_date(game_date)
        return cache[game_date]

    print(f"{'player':22s} {'date':10s} {'cat':10s} "
          f"{'pred':>6s} {'recorded':>9s} {'refetch':>8s}  raw box (h/r/rbi/hr/ab)")
    print("-" * 100)

    mismatches = 0
    for r in rows:
        pid = int(r["player_id"])
        cat = r["category"]
        gdate = r["game_date"]
        pred = float(r["predicted_value"])
        recorded = float(r["actual_value"])

        try:
            hitting, pitching = actuals_for(gdate)
        except Exception as exc:
            print(f"{r['player_name']:22s} {gdate}  fetch failed: {exc}")
            continue

        if cat == "strikeouts":
            snap = pitching.get(pid)
            refetch = float(snap.strikeouts) if snap else None
            raw = f"K={snap.strikeouts}" if snap else "NOT FOUND in pitching"
        else:
            snap = hitting.get(pid)
            refetch = compute_actual_value(snap, cat, fantasy) if snap else None
            raw = (
                f"h={snap.hits} r={snap.runs} rbi={snap.rbi} hr={snap.home_runs} ab={snap.ab}"
                if snap else "NOT FOUND in hitting (id mismatch -> would record 0/None)"
            )

        is_mismatch = refetch is None or abs((refetch or 0) - recorded) > 0.001
        if args.mismatches_only and not is_mismatch:
            continue
        if is_mismatch:
            mismatches += 1
        flag = "  <-- MISMATCH" if is_mismatch else ""
        refetch_s = f"{refetch:.1f}" if refetch is not None else "None"
        print(f"{r['player_name']:22s} {gdate} {cat:10s} "
              f"{pred:6.2f} {recorded:9.1f} {refetch_s:>8s}  {raw}{flag}")

    print("-" * 100)
    print(f"{mismatches} mismatches out of {len(rows)} rows checked.")
    print()
    print("How to read this:")
    print("  - recorded == refetch everywhere: grading is CORRECT; any confusion")
    print("    was comparing the wrong day's box score.")
    print("  - refetch differs and 'raw box' shows real numbers: the recorder")
    print("    stored a wrong value (a real bug) — the refetch column is the truth.")
    print("  - 'NOT FOUND ... id mismatch': the player-id match failed; the")
    print("    recorder should SKIP these, not write 0. That's the bug to fix.")


if __name__ == "__main__":
    main()
