#!/usr/bin/env python3
"""
Audit one player's point-in-time snapshot against their raw game log.

Prints the as-of (season-to-date) snapshot the builder would use for a given
player + date, beside the individual game-log rows it aggregated — and, on
the other side of the line, the rows that were EXCLUDED (the slate date
itself and everything after). This turns the leakage check into a five-second
eyeball instead of squinting at a CSV, and lets you cross-check the totals
against Baseball-Reference's "entering this date" line.

Uses the same disk cache as the A3 builder, so if you've already built the
date, this costs zero API calls.

Usage:
    python run_audit_player.py --player 665742 --date 2025-06-15
    python run_audit_player.py --player 665742 --date 2025-06-15 --pitching
    python run_audit_player.py --name "Juan Soto" --date 2025-06-15
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from src.data.http_cache import RateLimiter
from src.data.point_in_time import PointInTimeStats, _parse_date
from src.learning.training_set_builder import CachedMLBAPI


def _resolve_name(api: CachedMLBAPI, name: str) -> int | None:
    data = api._get(f"{api.BASE_URL}/sports/1/players", {"season": api.season})
    wanted = name.strip().lower()
    matches = [
        p for p in data.get("people", [])
        if p.get("fullName", "").lower() == wanted
    ]
    if not matches:
        matches = [
            p for p in data.get("people", [])
            if wanted in p.get("fullName", "").lower()
        ]
    if not matches:
        print(f"No player matched '{name}' in {api.season}.", file=sys.stderr)
        return None
    if len(matches) > 1:
        print(f"Multiple matches for '{name}':", file=sys.stderr)
        for p in matches[:10]:
            print(f"  {p['id']}  {p['fullName']}", file=sys.stderr)
        print("Re-run with --player <id>.", file=sys.stderr)
        return None
    print(f"Resolved '{name}' -> {matches[0]['fullName']} ({matches[0]['id']})")
    return int(matches[0]["id"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit a player's as-of snapshot vs raw game log.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--player", type=int, help="MLB player id")
    group.add_argument("--name", help="Full name (season roster lookup)")
    parser.add_argument("--date", required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--pitching", action="store_true", help="Audit pitching log instead of hitting")
    parser.add_argument("--season", type=int, help="Override season (default: year of --date)")
    parser.add_argument("--cache-dir", default="data/cache/http")
    args = parser.parse_args(argv)

    try:
        target = date.fromisoformat(args.date)
    except ValueError:
        print(f"Invalid date: {args.date}", file=sys.stderr)
        return 2

    season = args.season or target.year
    api = CachedMLBAPI(season=season, cache_dir=args.cache_dir, rate_limiter=RateLimiter(0.4))
    pit = PointInTimeStats(mlb_api=api, season=season)

    player_id = args.player or _resolve_name(api, args.name)
    if player_id is None:
        return 2

    cutoff = _parse_date(args.date)
    group_name = "pitching" if args.pitching else "hitting"
    all_rows = pit._pitching_log(player_id) if args.pitching else pit._hitting_log(player_id)
    before = [r for r in all_rows if _parse_date(r.game_date) < cutoff]
    on_after = [r for r in all_rows if _parse_date(r.game_date) >= cutoff]

    print(f"\nAUDIT  player {player_id}  {group_name}  as-of {args.date}  (season {season})")
    print("=" * 70)
    print(f"Game-log rows in {season}: {len(all_rows)}  |  before date: {len(before)}  |  on/after (EXCLUDED): {len(on_after)}")

    if args.pitching:
        season_snap, recent_snap = pit.get_pitching_stats_as_of(player_id, args.date)
        print("\nINCLUDED rows (strictly before, newest 8 shown):")
        print(f"  {'date':<12}{'IP':>6}{'K':>5}{'BB':>5}{'HR':>5}")
        for r in before[-8:]:
            print(f"  {r.game_date:<12}{r.innings_pitched:>6.1f}{r.strikeouts:>5}{r.walks:>5}{r.home_runs:>5}")
        print("\nAS-OF SNAPSHOT (aggregate of ALL included rows — cross-check vs Baseball-Ref):")
        print(f"  season: IP={season_snap.innings_pitched:.1f}  K={season_snap.strikeouts}  "
              f"BB={season_snap.walks}  HR={season_snap.home_runs}  GS={season_snap.games_started}  "
              f"K/9={season_snap.k_per_9:.2f}")
        print(f"  recent: IP={recent_snap.innings_pitched:.1f}  K={recent_snap.strikeouts}  "
              f"GS={recent_snap.games_started}")
    else:
        season_snap, recent_snap = pit.get_hitting_stats_as_of(player_id, args.date)
        print("\nINCLUDED rows (strictly before, newest 10 shown):")
        print(f"  {'date':<12}{'PA':>4}{'AB':>4}{'H':>4}{'HR':>4}{'BB':>4}{'K':>4}")
        for r in before[-10:]:
            print(f"  {r.game_date:<12}{r.pa:>4}{r.ab:>4}{r.hits:>4}{r.home_runs:>4}{r.walks:>4}{r.strikeouts:>4}")
        print("\nAS-OF SNAPSHOT (aggregate of ALL included rows — cross-check vs Baseball-Ref):")
        print(f"  season: PA={season_snap.pa}  AB={season_snap.ab}  H={season_snap.hits}  "
              f"HR={season_snap.home_runs}  BB={season_snap.walks}  K={season_snap.strikeouts}  "
              f"AVG={season_snap.avg:.3f}  OBP={season_snap.obp:.3f}  SLG={season_snap.slg:.3f}")
        print(f"  recent: PA={recent_snap.pa}  AVG={recent_snap.avg:.3f}  HR={recent_snap.home_runs}")

    if on_after:
        label = "IP/K" if args.pitching else "H/HR"
        print(f"\nEXCLUDED rows (on/after {args.date} — must NOT be in the snapshot above):")
        for r in on_after[:5]:
            if args.pitching:
                print(f"  {r.game_date:<12}  {r.innings_pitched:.1f} IP, {r.strikeouts} K   <- correctly excluded")
            else:
                print(f"  {r.game_date:<12}  {r.hits}-for-{r.ab}, {r.home_runs} HR   <- correctly excluded")
        print(f"\nLEAKAGE CHECK: the {args.date} game is in the EXCLUDED list above.")
        print("If its stats are NOT reflected in the as-of snapshot, point-in-time is clean.")
    else:
        print(f"\n(No game on/after {args.date} in the log — player didn't play the slate, or season ended.)")

    print(f"\nCache: {api.cache_stats()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
