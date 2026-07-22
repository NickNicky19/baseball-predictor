#!/usr/bin/env python3
"""
STAGE 0 — verify MLB's schedule shape BEFORE building a crosswalk on it.

*** RULE 1. I have never read this endpoint's raw output. I am not designing a
    join around a structure I have assumed. ***

WHAT THE CROSSWALK NEEDS FROM MLB, and what this checks:
  1. gamePk                     the stable game identity
  2. both teams                 to confirm a SmartStake game's roster maps here
  3. the game's start time      to order a doubleheader's two games
  4. a doubleheader flag        MLB marks these explicitly -- if so, we do not
                                have to INFER them from timestamps

THE PLAN THIS SUPPORTS (and why it does not need SmartStake to carry teams):

    SmartStake gives us, per game_id: ~25-36 PLAYER NAMES.
    Those players are on exactly TWO MLB rosters.
    *** THE PLAYERS *ARE* THE TEAMS. ***

    So: resolve the names -> player_ids -> the two teams -> the ONE gamePk on
    that date with those two teams. Verified, not guessed.

    And a doubleheader is the same two teams TWICE -- disambiguated by start
    time at the HOUR level (a 1pm game and a 7pm game), which is exactly the
    granularity SmartStake's start_time IS reliable at. The minute-level drift
    that fragments (game_id, start_time) is irrelevant when you are separating
    1pm from 7pm.

    And once we know the gamePk, we know the two rosters -- so
    (gamePk, normalized_name) -> player_id is unique BY CONSTRUCTION. Two
    Max Muncys are never on the same roster. The collision that produced the
    21,171 + 21,171 -> 21,201 cross-join becomes STRUCTURALLY IMPOSSIBLE.

WHY A BOX SCORE BEATS A ROSTER (the better idea, found by reading the source):
    get_game_boxscore_stats(game_pk) returns the players who ACTUALLY APPEARED
    in that game. A roster includes players who did not play. SmartStake prices
    players who are IN the lineup. So the box score is the tighter, more
    discriminating match -- and it is the same call reconstruct already makes.

Usage:
    python scripts/probe_mlb_schedule_shape.py --date 2026-06-14
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.mlb_api import MLBStatsAPI          # noqa: E402


def dig(obj, *path, default=None):
    """Walk a nested dict/list safely. Never raises -- a missing key is a
    FINDING (the field is not there), not a crash."""
    cur = obj
    for k in path:
        if isinstance(cur, dict):
            cur = cur.get(k)
        elif isinstance(cur, list) and isinstance(k, int) and 0 <= k < len(cur):
            cur = cur[k]
        else:
            return default
        if cur is None:
            return default
    return cur


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-06-14")
    ap.add_argument("--dump-first", action="store_true",
                    help="print the FULL raw JSON of the first game -- the only "
                         "way to be certain what fields exist")
    args = ap.parse_args(argv)

    api = MLBStatsAPI(season=int(args.date[:4]))
    games = api.get_schedule(args.date)

    if not games:
        print(f"FATAL: no games on {args.date}.", file=sys.stderr)
        return 2

    print("=" * 84)
    print(f"MLB SCHEDULE — {args.date}   ({len(games)} games)")
    print("=" * 84)

    if args.dump_first:
        print("\nRAW JSON of the first game (top-level keys and their types):")
        g = games[0]
        for k in sorted(g):
            v = g[k]
            t = type(v).__name__
            preview = (json.dumps(v)[:70] + "...") if not isinstance(v, (int, str, bool, float)) else v
            print(f"  {k:24s} {t:8s} {preview}")
        print()

    # ---- the four things the crosswalk needs -----------------------------
    print(f"  {'gamePk':>9s}  {'away':22s} {'home':22s} {'gameDate (UTC)':22s} "
          f"{'DH':>3s} {'game#':>5s}")
    print("  " + "-" * 92)

    rows = []
    for g in games:
        pk = g.get("gamePk")
        away = dig(g, "teams", "away", "team", "name", default="?")
        home = dig(g, "teams", "home", "team", "name", default="?")
        away_id = dig(g, "teams", "away", "team", "id")
        home_id = dig(g, "teams", "home", "team", "id")
        gd = g.get("gameDate", "?")
        # MLB marks doubleheaders EXPLICITLY. If this field exists we do not
        # have to INFER a doubleheader from timestamps -- which is exactly the
        # kind of inference that has bitten this project three times.
        dh = g.get("doubleHeader", "?")          # 'N' | 'Y' | 'S' (split)
        gnum = g.get("gameNumber", "?")
        rows.append(dict(pk=pk, away=away, home=home, away_id=away_id,
                         home_id=home_id, gd=gd, dh=dh, gnum=gnum))
        print(f"  {str(pk):>9s}  {away[:22]:22s} {home[:22]:22s} {str(gd)[:22]:22s} "
              f"{str(dh):>3s} {str(gnum):>5s}")

    # ---- the checks that decide whether the crosswalk is buildable --------
    print()
    print("=" * 84)
    print("CAN THE CROSSWALK BE BUILT ON THIS?")
    print("=" * 84)

    ok = True

    have_pk = all(r["pk"] is not None for r in rows)
    print(f"  [{'OK ' if have_pk else 'NO '}] every game has a gamePk")
    ok &= have_pk

    have_teams = all(r["away_id"] and r["home_id"] for r in rows)
    print(f"  [{'OK ' if have_teams else 'NO '}] every game has BOTH team ids")
    ok &= have_teams

    have_time = all(r["gd"] != "?" for r in rows)
    print(f"  [{'OK ' if have_time else 'NO '}] every game has a gameDate")
    ok &= have_time

    have_dh = all(r["dh"] != "?" for r in rows)
    print(f"  [{'OK ' if have_dh else 'NO '}] every game has an explicit "
          f"doubleHeader flag")
    if have_dh:
        print(f"       -> we do NOT have to INFER doubleheaders from timestamps. "
              f"MLB marks them.")
    ok &= have_dh

    # *** THE DECISIVE CHECK ***
    # The crosswalk maps a SmartStake game to an MLB game via its TWO TEAMS.
    # That only works if (date, away_id, home_id) identifies the game -- OR, for
    # a doubleheader, if (date, away_id, home_id) plus an ORDERING does.
    pairs: dict[tuple, list] = {}
    for r in rows:
        pairs.setdefault((r["away_id"], r["home_id"]), []).append(r)
    dupes = {k: v for k, v in pairs.items() if len(v) > 1}

    print()
    print(f"  distinct (away, home) matchups : {len(pairs)}")
    print(f"  matchups appearing MORE THAN ONCE (doubleheaders): {len(dupes)}")
    if dupes:
        for (a, h), v in dupes.items():
            print(f"    {v[0]['away']} @ {v[0]['home']}:")
            for r in sorted(v, key=lambda x: str(x["gd"])):
                print(f"       gamePk {r['pk']}  gameNumber {r['gnum']}  "
                      f"DH={r['dh']}  {r['gd']}")
        print()
        print("    -> a doubleheader is the SAME two teams twice. The crosswalk")
        print("       must order them. gameNumber (1/2) and gameDate BOTH do it,")
        print("       and gameNumber is EXACT -- no timestamp comparison needed.")

    print()
    print("=" * 84)
    print("VERDICT")
    print("=" * 84)
    if ok:
        print("  The MLB side has everything the crosswalk needs:")
        print("    gamePk        -- the stable identity we key on")
        print("    both team ids -- what SmartStake's PLAYER LIST will resolve to")
        print("    gameNumber    -- an EXACT doubleheader order (not a timestamp)")
        print("    doubleHeader  -- an explicit flag, so nothing is inferred")
        print()
        print("  NEXT: verify the SmartStake side -- do the ~25-36 players in one")
        print("  vendor game_id actually resolve to exactly TWO teams? That is the")
        print("  claim the whole crosswalk rests on, and it has NOT been measured.")
        return 0

    print("  The MLB side is MISSING something the crosswalk needs. Read the")
    print("  checks above. Do not build on this until they all pass.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
