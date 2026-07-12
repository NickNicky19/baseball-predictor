#!/usr/bin/env python3
"""
BEFORE building the CLV backtest: does SmartStake actually HAVE quotes at the
times you would have bet?

=============================================================================
THE IDEA THIS VALIDATES
=============================================================================
CLV = (your entry price) vs (the closing price), on a bet you placed. You never
placed one -- so I said CLV could not be backfilled. THAT WAS WRONG.

SmartStake is minute-by-minute: "Every row is one book's price for one selection
at one minute." So a bet you never placed can be RECONSTRUCTED:

    your_odds  = DK's price at (first_pitch - N hours)   <- a RECORDED FACT
    close_odds = DK's last quote before first pitch
    result     = the graded outcome

That is real CLV, backfilled. The price you WOULD have gotten is not
hypothetical; it is in the data.

=============================================================================
WHAT COULD BREAK IT -- and this probe tests each  (rule 1/9)
=============================================================================
1. QUOTE DENSITY FAR FROM FIRST PITCH.
   Props are posted LATE -- often only after lineups drop. If DK has no quote at
   T-6h, "bet 6 hours out" is not a strategy you could have executed, and any
   CLV computed from a fabricated price is a lie. MEASURE the coverage at each
   horizon before choosing one.

2. ONE-SIDED QUOTES.
   De-vig needs BOTH over and under from the SAME book at the SAME line at that
   minute. A one-sided quote cannot be de-vigged and must be DROPPED, never
   imputed. Measure how many selections survive.

3. LINE MOVEMENT.
   The LINE itself moves (0.5 -> 1.5), not just the price. An entry at T-4h on
   the 0.5 line and a close on the 1.5 line are DIFFERENT BETS. CLV across them
   is meaningless. Measure how often the main line moves.

4. STALE QUOTES.
   "Last quote at or before T" could be from hours earlier if the book went
   quiet. A price you could not actually have gotten is not an entry price.
   Measure the AGE of the quote we would use.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  coverage at T-1h  : expect HIGH   (>80% of selections)   -- lineups are out
  coverage at T-3h  : expect MEDIUM (40-80%)               -- lineups landing
  coverage at T-6h  : expect LOW    (<40%)                 -- props barely posted
  coverage at T-12h : expect VERY LOW                      -- probably unusable
  median quote age  : expect < 30 min at T-1h; larger further out
  If coverage at T-1h is BELOW 50%, something is wrong with the query, not with
  the market.

Usage:
    python scripts/probe_entry_prices.py --month 2026-06
"""
from __future__ import annotations

import argparse
import sys

import duckdb
import pandas as pd

# Horizons to test, in hours before first pitch. 0 = the close.
HORIZONS = [0, 1, 2, 3, 4, 6, 8, 12]

# Books worth betting. Pinnacle is the sharp benchmark; DK/FD are where you
# would actually place. MEASURED earlier: Pinnacle posts ZERO `player hits`.
BOOKS = ["draftkings", "fanduel", "bet365", "betmgm", "caesars", "fanatics",
         "pinnacle", "novig"]

MARKETS = ["player hits", "player home runs", "player bases", "player rbis",
           "player strikeouts"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-06")
    ap.add_argument("--book", default="draftkings",
                    help="book to measure quote density for")
    args = ap.parse_args(argv)

    HF = (f"'hf://datasets/SmartStake/mlb-player-props/"
          f"mon={args.month}/*.parquet'")
    duckdb.sql("INSTALL httpfs; LOAD httpfs;")
    duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

    books = ", ".join(f"'{b}'" for b in BOOKS)
    markets = ", ".join(f"'{m}'" for m in MARKETS)

    # ---- 1. how far ahead of first pitch does each book quote? -------------
    print("=" * 78)
    print("1. QUOTE HORIZON -- how far before first pitch does each book post?")
    print("=" * 78)
    print(duckdb.sql(f"""
        SELECT book,
               count(*)                                          AS quotes,
               round(max(date_diff('minute', ts, start_time)) / 60.0, 1)
                                                                 AS earliest_h,
               round(median(date_diff('minute', ts, start_time)) / 60.0, 1)
                                                                 AS median_h,
               round(min(date_diff('minute', ts, start_time)) / 60.0, 2)
                                                                 AS latest_h
        FROM {HF}
        WHERE book IN ({books}) AND market IN ({markets})
          AND result IS NOT NULL AND ts < start_time
        GROUP BY 1 ORDER BY quotes DESC
    """).df().to_string(index=False))
    print("\n  earliest_h = the EARLIEST a quote exists (hours before first pitch)")
    print("  latest_h   = the LATEST (closest to first pitch) -- ~0 = a real close")

    # ---- 2. COVERAGE at each horizon -- the decisive number ---------------
    print()
    print("=" * 78)
    print(f"2. COVERAGE at each entry horizon  (book = {args.book})")
    print("   Of the selections that have a CLOSE, what share ALSO have a")
    print("   TWO-SIDED quote at or before T-Nh? A one-sided quote cannot be")
    print("   de-vigged and is NOT a usable entry price.")
    print("=" * 78)

    rows = []
    for h in HORIZONS:
        q = f"""
        WITH src AS (
            SELECT * FROM {HF}
            WHERE book = '{args.book}' AND market IN ({markets})
              AND result IS NOT NULL AND ts < start_time
        ),
        -- every selection that has a CLOSE (the denominator)
        closes AS (
            SELECT game_id, player, market, line, side
            FROM src
            GROUP BY 1,2,3,4,5
        ),
        two_sided_close AS (
            SELECT o.game_id, o.player, o.market, o.line
            FROM closes o JOIN closes u
              ON o.game_id=u.game_id AND o.player=u.player
             AND o.market=u.market AND o.line=u.line
            WHERE o.side='over' AND u.side='under'
        ),
        -- the last quote at or before the horizon, per side
        entry AS (
            SELECT game_id, player, market, line, side,
                   arg_max(odds, ts) AS odds,
                   max(ts)           AS entry_ts,
                   any_value(start_time) AS start_time
            FROM src
            WHERE ts <= start_time - INTERVAL {h} HOUR
            GROUP BY 1,2,3,4,5
        ),
        two_sided_entry AS (
            SELECT o.game_id, o.player, o.market, o.line,
                   date_diff('minute', o.entry_ts,
                             o.start_time - INTERVAL {h} HOUR) AS age_min
            FROM entry o JOIN entry u
              ON o.game_id=u.game_id AND o.player=u.player
             AND o.market=u.market AND o.line=u.line
            WHERE o.side='over' AND u.side='under'
        )
        SELECT
            (SELECT count(*) FROM two_sided_close)  AS n_close,
            (SELECT count(*) FROM two_sided_entry)  AS n_entry,
            (SELECT round(median(age_min), 1) FROM two_sided_entry) AS median_age_min,
            (SELECT round(quantile_cont(age_min, 0.9), 1) FROM two_sided_entry) AS p90_age_min
        """
        r = duckdb.sql(q).df().iloc[0]
        n_close = int(r.n_close or 0)
        n_entry = int(r.n_entry or 0)
        cov = n_entry / n_close if n_close else 0.0
        rows.append(dict(horizon_h=h, n_two_sided_close=n_close,
                         n_two_sided_entry=n_entry, coverage=round(cov, 4),
                         median_quote_age_min=r.median_age_min,
                         p90_quote_age_min=r.p90_age_min))
    cov_df = pd.DataFrame(rows)
    print(cov_df.to_string(index=False))
    print("\n  coverage = share of closeable selections that ALSO have a usable")
    print("             two-sided quote at that horizon")
    print("  quote_age = how STALE the quote we would use is, in minutes BEFORE")
    print("              the horizon. A large age means the book went quiet and")
    print("              you could NOT actually have gotten that price then.")

    # ---- 3. does the LINE move? (an entry and a close on different lines
    #         are DIFFERENT BETS -- CLV across them is meaningless) ---------
    print()
    print("=" * 78)
    print("3. LINE MOVEMENT -- does the posted line itself change?")
    print("=" * 78)
    print(duckdb.sql(f"""
        WITH src AS (
            SELECT * FROM {HF}
            WHERE book = '{args.book}' AND market IN ({markets})
              AND result IS NOT NULL AND ts < start_time
        ),
        per_sel AS (
            SELECT game_id, player, market,
                   count(DISTINCT line) AS n_lines
            FROM src GROUP BY 1,2,3
        )
        SELECT n_lines,
               count(*) AS n_player_games,
               round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
        FROM per_sel GROUP BY 1 ORDER BY 1
    """).df().to_string(index=False))
    print("\n  n_lines = how many DISTINCT lines that book posted for that")
    print("            player-game across the whole pre-game window.")
    print("  n_lines > 1 means the LINE moved (e.g. 0.5 -> 1.5). An entry on one")
    print("  line and a close on another are DIFFERENT BETS -- the backtest must")
    print("  match on (player, market, LINE), never just (player, market).")

    # ---- verdict ---------------------------------------------------------
    print()
    print("=" * 78)
    print("VERDICT -- which entry horizon is actually BETTABLE?")
    print("=" * 78)
    usable = cov_df[(cov_df.coverage >= 0.50) & (cov_df.horizon_h > 0)]
    if usable.empty:
        print("  *** NO horizon beyond the close has >=50% two-sided coverage. ***")
        print("  Props are posted too late at this book to reconstruct a real")
        print("  entry price. Either widen to more books, or accept that the only")
        print("  honest 'entry' is very close to first pitch.")
    else:
        best = usable.horizon_h.max()
        print(f"  Usable horizons (>=50% two-sided coverage): "
              f"{sorted(usable.horizon_h.tolist())}")
        print(f"  EARLIEST usable entry: T-{best}h")
        print(f"  -> the CLV backtest can sweep entry times from T-{best}h to the")
        print(f"     close, and MEASURE WHEN to bet. That is a tradeable finding")
        print(f"     that forward-logging would take a season to discover.")
    print()
    print("  A horizon with LOW coverage must NOT be used: fabricating an entry")
    print("  price you could not have gotten produces a CLV number that is a lie.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
