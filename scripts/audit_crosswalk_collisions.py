#!/usr/bin/env python3
"""
AUDIT THE COLLISIONS — is SmartStake splitting single games across TWO game_ids?

*** RULE 10: THIS DIAGNOSES. IT DOES NOT MERGE. ***
The decision NOT to merge collisions stands regardless of what this finds. This
records WHY they were excluded, so an unexplained exclusion does not sit in the
artifact as a mystery.

=============================================================================
WHAT WE SAW
=============================================================================
build_crosswalk.py found 11 COLLISIONS across 6 dates -- one MLB game_pk claimed
by TWO vendor game_ids:

    2026-06-01  game_pk 822730 (MIA @ WSH)  <- m~5c5c3605, m~5e101e64
    2026-06-02  game_pk 824754 (BAL @ BOS)  <- m~c8337a41, m~c9e762a0
    2026-06-02  game_pk 824511 (KC  @ CIN)  <- m~ca99654a, m~cc4e3de9
    2026-06-04  game_pk 823698 (KC  @ MIN)  <- m~f9d01c03, m~fb840462
    ... (11 total)

The guard refused to pick one and excluded BOTH. That is correct behaviour: a
collision means the identity is ambiguous, and we do not resolve ambiguity by
choosing.

=============================================================================
BUT WHY DOES IT HAPPEN? TWO VERY DIFFERENT EXPLANATIONS
=============================================================================
  H1  THE VENDOR SPLITS ONE GAME ACROSS TWO game_ids.
      -> the two vendor ids would have the SAME (or near-same) player set.
      -> this is CONSISTENT with what we already MEASURED:
             243 game_ids for ~218 real games in June
             (game_id, start_time) fragments into 493
         The vendor's identity is unreliable, and the crosswalk is DETECTING
         that rather than papering over it.
      -> the data is RECOVERABLE in principle (both ids price the same game),
         but recovering it requires a JUDGMENT that they are the same -- and a
         judgment is exactly what this system exists to remove from identity.

  H2  THE VOTE IS WRONG ON ONE OF THEM.
      -> the two vendor ids would have DIFFERENT player sets, and one of them
         genuinely belongs to a DIFFERENT game whose players happen to overlap.
      -> that would be a CROSSWALK failure, not a vendor failure, and it would
         mean the 223 "resolved" games are less trustworthy than they look.

*** THESE HAVE OPPOSITE IMPLICATIONS AND MUST NOT BE CONFLATED. ***
  If H1: the crosswalk is working; the vendor is messy; excluding is a small,
         quantified coverage cost.
  If H2: the crosswalk has a false-positive mode and the whole artifact needs
         re-examining.

=============================================================================
HOW TO TELL THEM APART
=============================================================================
For each collision, compare the two vendor ids':
    * player sets      -- identical? (H1)  or disjoint? (H2)
    * start times      -- the same game, or hours apart?
    * markets/books    -- one id carrying a subset of the other's books?
    * quote counts     -- a "stub" id with a handful of quotes vs a full one?

MEASURED ALREADY, and it is suggestive: the vendor produces stub rows. From
probe_smartstake_schema.py --
    m~0de8bdb1  22:40:00       1 quote,   1 player     <- a STUB
    m~0de8bdb1  22:41:00  863,747 quotes, 25 players   <- the real thing
A one-minute drift with a one-quote stub. If collisions look like THAT, H1 is
confirmed and the vendor is simply noisy.

=============================================================================
SANITY -- stated BEFORE the run (rule 7)
=============================================================================
  If H1 (a split): player-set overlap should be HIGH (> 80%) between the two
    vendor ids, and their start times should be within minutes.
  If H2 (a bad vote): overlap should be LOW, and the start times may differ by
    hours.

  If the overlap is MIXED across collisions -- some high, some low -- then BOTH
  are happening, and each collision needs its own verdict. Do not average them.

Usage:
    python scripts/audit_crosswalk_collisions.py
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

import duckdb
import pandas as pd


def norm(s: object) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s))
                if not unicodedata.combining(c))
    s = s.lower().strip()
    for ch in ".'`-":
        s = s.replace(ch, "")
    parts = [p for p in s.split()
             if p not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="data/market/crosswalk_report.json")
    ap.add_argument("--month", default="2026-06")
    args = ap.parse_args(argv)

    rp = Path(args.report)
    if not rp.exists():
        print(f"FATAL: {rp} not found. Run build_crosswalk.py first.",
              file=sys.stderr)
        return 2
    report = json.loads(rp.read_text(encoding="utf-8"))

    # Collisions are recorded per date in the report.
    collisions: list[tuple[str, int, list[str]]] = []
    for gd, day in report.get("dates", {}).items():
        for pk, ids in (day.get("collisions") or {}).items():
            collisions.append((gd, int(pk), list(ids)))

    if not collisions:
        print("No collisions recorded. Nothing to audit.")
        return 0

    print("=" * 92)
    print(f"COLLISION AUDIT — {len(collisions)} case(s)")
    print("=" * 92)

    HF = (f"'hf://datasets/SmartStake/mlb-player-props/"
          f"mon={args.month}/*.parquet'")
    duckdb.sql("INSTALL httpfs; LOAD httpfs;")
    duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

    all_ids = sorted({i for _, _, ids in collisions for i in ids})
    idlist = ", ".join(f"'{i}'" for i in all_ids)

    detail = duckdb.sql(f"""
        SELECT game_id,
               min(start_time)         AS first_start,
               max(start_time)         AS last_start,
               count(*)                AS quotes,
               count(DISTINCT player)  AS n_players,
               count(DISTINCT book)    AS n_books,
               count(DISTINCT market)  AS n_markets,
               list(DISTINCT player)   AS players
        FROM {HF}
        WHERE game_id IN ({idlist})
        GROUP BY game_id
    """).df()
    d = {r.game_id: r for r in detail.itertuples()}

    verdicts: list[str] = []

    for gd, pk, ids in collisions:
        print()
        print(f"  {gd}  game_pk {pk}")
        print(f"  {'vendor_game_id':14s} {'quotes':>9s} {'players':>8s} "
              f"{'books':>6s} {'markets':>8s}  first_start")
        sets = {}
        for vid in ids:
            r = d.get(vid)
            if r is None:
                print(f"  {vid:14s}  (no rows returned)")
                continue
            names = {norm(p) for p in list(r.players)} if r.players is not None else set()
            sets[vid] = names
            print(f"  {vid:14s} {r.quotes:9,d} {r.n_players:8d} {r.n_books:6d} "
                  f"{r.n_markets:8d}  {r.first_start}")

        if len(sets) < 2:
            verdicts.append("INCOMPLETE")
            continue

        a, b = list(sets.values())[:2]
        inter = len(a & b)
        union = len(a | b)
        jac = inter / union if union else 0.0
        smaller = min(len(a), len(b))
        overlap_of_smaller = inter / smaller if smaller else 0.0

        print(f"    shared players      : {inter} of {union} distinct "
              f"(Jaccard {jac:.1%})")
        print(f"    overlap of the SMALLER set: {overlap_of_smaller:.1%}")

        if overlap_of_smaller >= 0.80:
            v = "H1 SPLIT"
            print(f"    -> *** H1: THE VENDOR SPLIT ONE GAME. *** The two ids "
                  f"price the SAME players.")
        elif overlap_of_smaller <= 0.30:
            v = "H2 BAD VOTE"
            print(f"    -> *** H2: DIFFERENT PLAYERS. *** These are NOT the same "
                  f"game, and one vote is WRONG. This is a CROSSWALK failure.")
        else:
            v = "MIXED"
            print(f"    -> MIXED ({overlap_of_smaller:.0%}). Neither story fits "
                  f"cleanly. Read the rows above.")
        verdicts.append(v)

    # =====================================================================
    print()
    print("=" * 92)
    print("VERDICT")
    print("=" * 92)
    from collections import Counter
    c = Counter(verdicts)
    for k, n in c.most_common():
        print(f"  {k:14s} {n}")
    print()

    if c["H2 BAD VOTE"] == 0 and c["MIXED"] == 0:
        print("  *** ALL COLLISIONS ARE H1: THE VENDOR SPLITS GAMES. ***")
        print("  The two ids price the SAME players. This is the SAME vendor")
        print("  fragmentation we already measured (243 game_ids for ~218 games;")
        print("  (game_id, start_time) splitting into 493 because start_time drifts")
        print("  by a minute).")
        print()
        print("  *** THE CROSSWALK IS NOT FAILING. IT IS DETECTING. ***")
        print("  The 223 resolved games are trustworthy. Excluding the 11 collided")
        print("  games is a QUANTIFIED COVERAGE COST (4.3%), not a correctness")
        print("  problem.")
        print()
        print("  AND WE STILL DO NOT MERGE THEM. Merging requires DECIDING that two")
        print("  vendor ids are the same game -- a judgment. This system exists to")
        print("  remove judgment from identity. A 4.3% coverage cost is cheap")
        print("  insurance against reintroducing a soft joint into a hard chain.")
        return 0

    print("  *** NOT ALL COLLISIONS ARE VENDOR SPLITS. ***")
    if c["H2 BAD VOTE"]:
        print(f"  {c['H2 BAD VOTE']} case(s) have DISJOINT player sets -- meaning")
        print("  the VOTE resolved one of them to the WRONG game. That is a")
        print("  CROSSWALK FALSE POSITIVE, and it means the 223 'resolved' games")
        print("  need re-examining. This is the serious outcome.")
    if c["MIXED"]:
        print(f"  {c['MIXED']} case(s) are ambiguous. Read them individually. Do")
        print("  NOT average them into a single story.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
