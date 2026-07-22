#!/usr/bin/env python3
"""
DOES MIXED NULL/NON-NULL `result` SNAPSHOTTING EXIST?  (rule 6: measure, then patch)

*** NEITHER CODEX NOR I HAVE CHECKED THIS. The settlement fix is CONDITIONAL on
*** the answer, and I am not going to patch a bug I have not proven exists.

=============================================================================
THE DEFECT UNDER TEST
=============================================================================
run_market_ab.fetch_market() applies settlement presence AT THE QUOTE-SNAPSHOT
LEVEL:

    src AS (SELECT * FROM raw
            WHERE book = ... AND market IN (...)
              AND result IS NOT NULL          <-- HERE, per QUOTE ROW
              AND ts < start_time),
    entry AS (SELECT ..., arg_max(odds, ts) AS odds FROM src WHERE ts <= ...)

`arg_max(odds, ts)` then picks the entry/close price FROM THE SURVIVORS.

*** IF A SELECTION HAS MIXED NULL/NON-NULL `result` SNAPSHOTS, THE FILTER
*** SILENTLY CHANGES WHICH QUOTE IS CHOSEN AS THE ENTRY PRICE. ***
The last quote before T-4h might be dropped for having a null `result`, and an
EARLIER quote is taken instead -- a different price, on a selection that was
never supposed to be re-priced by a settlement flag. Settlement is a property of
the SELECTION (did this prop settle?), not of a QUOTE (did this minute's snapshot
happen to carry the graded value yet?).

The absence counter has the same defect from the other side: it counts
`result IS NULL` rows, so a selection with SOME null snapshots is counted as
absent WHILE ALSO being scored. Double-counted, and inconsistently.

=============================================================================
THE CORRECT SEMANTICS (if the defect is real)
=============================================================================
    settlement_present := bool_or(result IS NOT NULL)
                          OVER (game_id, start_time, player, market, line)

Build entry/close from ALL PREGAME QUOTES. Then exclude on the SELECTION-LEVEL
fact, and report absence from THE SAME FACT. One definition, used twice --
rather than two definitions that can disagree.

=============================================================================
BUT FIRST: IS IT REAL? THREE POSSIBLE ANSWERS, THREE DIFFERENT ACTIONS
=============================================================================
  A. ZERO mixed selections.
     -> the quote-level filter is BEHAVIOURALLY IDENTICAL to the selection-level
        one on this data. The defect is LATENT, not active. Still fix it (it is
        wrong in principle and a future partition could trip it), but NO PRICE
        EVER MOVED and no prior number is invalidated by it.

  B. Mixed selections exist, but the LAST PREGAME QUOTE is never null.
     -> `arg_max(odds, ts)` picks the same row either way. Prices unaffected;
        only the ABSENCE COUNT is wrong (double-counting). Narrower fix.

  C. Mixed selections exist AND a null lands on the chosen quote.
     -> *** PRICES ACTUALLY MOVED. *** The entry price used in every capture
        figure was, for those rows, not the price the model would have seen.
        This is a real, active corruption of the A/B input.

  These are NOT the same finding and must not be pooled. The query below
  separates them.

=============================================================================
SANITY -- STATED BEFORE THE RUN (rule 7)
=============================================================================
  The README says `result` is "Null if the game had not settled OR THE PLAYER
  WAS INACTIVE". Both are properties of the SELECTION, not of a minute. So I
  EXPECT case A -- a selection's `result` should be uniformly null or uniformly
  non-null across its snapshots.

  *** IF THAT EXPECTATION IS WRONG, THE VENDOR IS BACKFILLING `result` INTO
  *** EARLIER SNAPSHOTS ASYNCHRONOUSLY, and that is a fact about the data nobody
  *** has recorded. It would also mean the graded value was PRESENT ON SOME
  *** PREGAME ROWS -- which is worth knowing on its own.

  I have NO PRIOR on the magnitude. I will not invent one.

Usage:
  python scripts/check_mixed_nullness.py --month 2026-06
  python scripts/check_mixed_nullness.py --months 2026-03 2026-04 2026-05 2026-06
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

MARKET_MAP = {"player hits": "hits", "player home runs": "home_runs"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/market/smartstake")
    ap.add_argument("--months", nargs="+", default=["2026-06"])
    ap.add_argument("--book", default="draftkings")
    ap.add_argument("--entry-hours", type=int, default=4)
    args = ap.parse_args(argv)

    for m in args.months:
        if not list(Path(args.root).glob(f"mon={m}/*.parquet")):
            print(f"FATAL: no parquet under {args.root}/mon={m}/", file=sys.stderr)
            return 2

    src = " UNION ALL ".join(
        f"SELECT * FROM read_parquet('{args.root}/mon={m}/*.parquet')"
        for m in args.months)
    markets = ", ".join(f"'{m}'" for m in MARKET_MAP)

    print("=" * 92)
    print("MIXED NULL/NON-NULL `result` — does the quote-level filter move prices?")
    print("=" * 92)
    print(f"  months {args.months}   book {args.book}   markets {list(MARKET_MAP)}")
    print()
    print("  EXPECTED (rule 7): case A -- ZERO mixed selections. The README says")
    print("  `result` is null when the game had not settled OR the player was")
    print("  inactive. BOTH are properties of the SELECTION, not of a minute.")
    print("  If mixed selections exist, the vendor is BACKFILLING `result` into")
    print("  earlier snapshots -- a fact about this data nobody has recorded.")
    print()

    # A SELECTION is (game_id, start_time, player, market, line). Its snapshots
    # are the per-minute quote rows. We ask, per selection:
    #   * are ALL its pregame snapshots graded, NONE of them, or SOME?
    #   * and if SOME -- does a null land on the quote arg_max WOULD HAVE PICKED?
    #     That is the only case where a PRICE actually moves.
    q = duckdb.sql(f"""
        WITH raw AS ({src}),
        pre AS (
            SELECT game_id, start_time, player, market, line, side, ts, odds, result
            FROM raw
            WHERE book = '{args.book}' AND market IN ({markets})
              AND ts < start_time
        ),
        sel AS (
            SELECT game_id, start_time, player, market, line, side,
                   count(*)                                   AS snapshots,
                   count(*) FILTER (WHERE result IS NOT NULL) AS graded,
                   count(*) FILTER (WHERE result IS NULL)     AS ungraded,
                   -- the quote the A/B's entry logic WOULD pick, ignoring result
                   arg_max(result, ts) FILTER
                       (WHERE ts <= start_time - INTERVAL {args.entry_hours} HOUR)
                                                              AS entry_pick_result,
                   count(*) FILTER
                       (WHERE ts <= start_time - INTERVAL {args.entry_hours} HOUR)
                                                              AS entry_snapshots,
                   -- the quote the CLOSE logic would pick
                   arg_max(result, ts)                        AS close_pick_result
            FROM pre
            GROUP BY 1,2,3,4,5,6
        )
        SELECT
            count(*)                                                  AS sides,
            count(*) FILTER (WHERE graded = snapshots)                AS all_graded,
            count(*) FILTER (WHERE graded = 0)                        AS none_graded,
            count(*) FILTER (WHERE graded > 0 AND ungraded > 0)       AS MIXED,
            -- CASE C: the chosen ENTRY quote is null while OTHER snapshots are
            -- graded => the filter DROPS the chosen quote and takes an earlier
            -- one. THE PRICE MOVES.
            count(*) FILTER (WHERE graded > 0 AND ungraded > 0
                             AND entry_snapshots > 0
                             AND entry_pick_result IS NULL)           AS entry_price_moves,
            count(*) FILTER (WHERE graded > 0 AND ungraded > 0
                             AND close_pick_result IS NULL)           AS close_price_moves
        FROM sel
    """).df().iloc[0]

    sides = int(q.sides)
    mixed = int(q.MIXED)
    print(f"  quote-sides examined        : {sides:,}")
    print(f"    ALL snapshots graded      : {int(q.all_graded):,}")
    print(f"    NONE graded               : {int(q.none_graded):,}")
    print(f"    *** MIXED ***             : {mixed:,}")
    print()

    if mixed == 0:
        print("  *** CASE A: ZERO MIXED SELECTIONS. ***")
        print("  Every selection's `result` is uniformly null or uniformly non-null")
        print("  across its pregame snapshots -- exactly as the README implies.")
        print()
        print("  CONSEQUENCE: the quote-level filter is BEHAVIOURALLY IDENTICAL to")
        print("  a selection-level one ON THIS DATA. The defect is LATENT, not")
        print("  active. NO PRICE EVER MOVED, and no prior figure is invalidated")
        print("  BY THIS (they remain invalidated by the vendor-`result` grading,")
        print("  which is a separate and much larger problem).")
        print()
        print("  STILL FIX IT: it is wrong in principle, and a future partition")
        print("  could trip it. But fix it as HYGIENE, not as a repair -- and do")
        print("  not claim it changed a number, because it did not.")
        return 0

    em, cm = int(q.entry_price_moves), int(q.close_price_moves)
    print(f"  *** MIXED SELECTIONS EXIST. The README's implication is WRONG. ***")
    print(f"  The vendor backfills `result` into earlier snapshots asynchronously.")
    print()
    print(f"  DOES A PRICE ACTUALLY MOVE?")
    print(f"    entry quote would be DROPPED (null on the arg_max pick) : {em:,}")
    print(f"    close quote would be DROPPED                            : {cm:,}")
    print()
    if em == 0 and cm == 0:
        print("  *** CASE B: MIXED, BUT NO PRICE MOVES. ***")
        print("  The last pregame quote is ALWAYS graded, so arg_max(odds, ts) picks")
        print("  the SAME row with or without the filter. PRICES ARE UNAFFECTED.")
        print("  Only the ABSENCE COUNT is wrong: a selection with SOME null")
        print("  snapshots is counted as settlement-absent WHILE ALSO being scored.")
        print("  Narrower fix -- but still fix both, from ONE definition.")
        return 1

    print("  *** CASE C: PRICES ACTUALLY MOVED. ***")
    print(f"  On {em:,} entry sides and {cm:,} close sides, the quote-level filter")
    print("  DISCARDS the quote arg_max would have chosen and takes an EARLIER one.")
    print("  *** THE ENTRY PRICE USED IS NOT THE PRICE THE MODEL WOULD HAVE SEEN. ***")
    print("  This is an ACTIVE corruption of the A/B's input, independent of the")
    print("  vendor-`result` grading bug, and it must be fixed before any rerun.")

    print("\n  worst offenders:")
    duckdb.sql(f"""
        WITH raw AS ({src}),
        pre AS (
            SELECT game_id, start_time, player, market, line, side, ts, result
            FROM raw
            WHERE book = '{args.book}' AND market IN ({markets})
              AND ts < start_time
        )
        SELECT game_id, player, market, line, side,
               count(*) AS snapshots,
               count(*) FILTER (WHERE result IS NULL) AS ungraded,
               arg_max(result, ts) FILTER
                   (WHERE ts <= start_time - INTERVAL {args.entry_hours} HOUR)
                   AS entry_pick_result
        FROM pre
        GROUP BY 1,2,3,4,5,6
        HAVING count(*) FILTER (WHERE result IS NULL) > 0
           AND count(*) FILTER (WHERE result IS NOT NULL) > 0
        ORDER BY ungraded DESC
        LIMIT 15
    """).show()
    return 2


if __name__ == "__main__":
    sys.exit(main())
