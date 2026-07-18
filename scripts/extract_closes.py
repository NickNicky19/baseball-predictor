#!/usr/bin/env python3
"""
Extract de-vigged CLOSING probabilities from the SmartStake MLB prop dataset.

This is the BENCHMARK ARM of the market verdict: the sharpest publicly available
forecast of each prop. Our model must beat it to have any edge. This script is
READ-ONLY and is NEVER used as a training signal -- fitting the model to the
close and then evaluating against the close is training on the test set (the
same circularity that killed option (iii) in the HRR kernel discussion).

WHAT "CLOSING" MEANS HERE
  The last quote from a book STRICTLY BEFORE first pitch:
      arg_max(odds, ts) WHERE ts < start_time
  The `ts < start_time` filter is not cosmetic. Books keep quoting during the
  game (live betting); a post-first-pitch quote has partial knowledge of the
  outcome. Including it would leak the result into the "closing" price and make
  the benchmark look artificially sharp -- i.e. make our model look artificially
  bad. Hard filter, no exceptions.

DE-VIGGING
  Two-way multiplicative (the standard sharp method):
      p_over = (1/odds_over) / (1/odds_over + 1/odds_under)
  This requires BOTH sides of the same selection from the SAME book at the SAME
  line. A one-sided quote CANNOT be de-vigged and is DROPPED, never imputed.
  Raw implied probability (1/odds) carries the book's margin and is NOT a
  probability -- comparing our model to a vigged price would be comparing to a
  number that sums to ~1.05 across the two sides, which flatters us for free.

MARKETS
  SmartStake market names -> our PropCategory:
      'player hits'      -> hits
      'player home runs' -> home_runs
      'player bases'     -> total_bases   (not yet in our model -- see notes)
      'player rbis'      -> rbi           (not yet in our model)
      'player strikeouts'-> strikeouts
  NOTE: there is NO hits+runs+RBIs market. `hrr` -- our flagship graded category
  -- is not a bettable product and cannot be validated against any close.

OUTPUT
  One row per (game_date, player, market, line, book) with a de-vigged p_over
  and the graded result. Joins to our projections on
  (game_date, norm(player), category, line).

Usage:
  python scripts/extract_closes.py --month 2026-05 --out data/market/closes_2026-05.parquet
  python scripts/extract_closes.py --month all   --out data/market/closes_all.parquet
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

import duckdb
import pandas as pd

# SmartStake market name -> our category name
MARKET_MAP = {
    "player hits": "hits",
    "player home runs": "home_runs",
    "player bases": "total_bases",
    "player rbis": "rbi",
    "player strikeouts": "strikeouts",
    "player batting walks": "walks",
}

# Books to extract. Pinnacle is THE benchmark (sharpest sportsbook; measured
# overround 0.0697 with mean == median, the signature of a well-behaved book).
#
# CORRECTION (measured, 2026-05): the "exchanges" do NOT run near-zero vig in
# this data, contrary to the usual assumption. Mean overround by venue:
#     novig      0.0501   <- the only genuinely tight one
#     bet365     0.0633
#     pinnacle   0.0697
#     sporttrade 0.0721
#     prophetx   0.0777   <- WORST of any venue, and it produced 1,521 of the
#                            1,631 dropped rows, with overrounds up to 0.99
# Do NOT treat exchange prices as cleaner probabilities here. Either these are
# outer bid/ask rather than mid, or the last-quote pick is catching stale
# one-sided liquidity. Pinnacle remains the benchmark where it posts.
#
# DK/FD/etc are included because they are where a bet would ACTUALLY be placed
# -- the edge that pays is (our model) vs (the price we can get), not vs the
# sharpest price in the world.
#
# COVERAGE GOTCHA (measured): Pinnacle posts ONLY home_runs, total_bases, and
# strikeouts. It has ZERO rows for hits, rbi, and walks. For those categories
# the benchmark must fall back to draftkings (0.0701) or bet365 (0.0633).
BENCHMARK_BOOKS = [
    "pinnacle", "ps3838",                      # sharp
    "sporttrade", "novig", "prophetx",         # exchanges, near-zero vig
    "draftkings", "fanduel", "fanatics",       # where we would bet
    "betmgm", "caesars", "bet365",
]


def norm_name(s: pd.Series) -> pd.Series:
    """Normalize a player name for joining. Verified 99.6% match rate against
    the MLB lineup roster (269/270) -- strips accents, punctuation, suffixes."""
    def strip_accents(x: object) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFKD", str(x))
            if not unicodedata.combining(c)
        )
    return (
        s.map(strip_accents).str.lower().str.strip()
         .str.replace(r"[.'`\-]", "", regex=True)
         .str.replace(r"\s+", " ", regex=True)
         .str.replace(r"\s+(jr|sr|ii|iii|iv|v)$", "", regex=True)
         .str.strip()
    )


def build_query(hf_glob: str) -> str:
    books = ", ".join(f"'{b}'" for b in BENCHMARK_BOOKS)
    markets = ", ".join(f"'{m}'" for m in MARKET_MAP)
    return f"""
    WITH src AS (
        SELECT * FROM {hf_glob}
        WHERE book IN ({books})
          AND market IN ({markets})
          AND result IS NOT NULL          -- graded only
          AND ts < start_time             -- STRICTLY pre-first-pitch (no leakage)
    ),
    closing AS (                          -- last quote per selection per book
        SELECT
            book, market, game_id, player, line, side,
            any_value(start_time)          AS start_time,
            arg_max(odds, ts)              AS close_odds,
            max(ts)                        AS close_ts,
            any_value(result)              AS result,
            any_value(won)                 AS won,
            count(*)                       AS n_quotes
        FROM src
        GROUP BY book, market, game_id, player, line, side
    ),
    two_way AS (                          -- both sides required for de-vig
        SELECT
            o.game_id, o.player, o.market, o.line, o.book,
            o.start_time,
            o.close_odds                   AS odds_over,
            u.close_odds                   AS odds_under,
            o.close_ts                     AS close_ts_over,
            u.close_ts                     AS close_ts_under,
            o.result                       AS result,
            o.won                          AS won_over,
            -- multiplicative two-way de-vig
            (1.0 / o.close_odds) / ((1.0 / o.close_odds) + (1.0 / u.close_odds))
                                           AS p_over_devig,
            -- the book's margin. MEASURED: 0.05-0.08 across ALL venues,
            -- exchanges included (see the note on BENCHMARK_BOOKS above).
            (1.0 / o.close_odds) + (1.0 / u.close_odds) - 1.0
                                           AS overround
        FROM closing o
        JOIN closing u
          ON  o.book    = u.book
          AND o.market  = u.market
          AND o.game_id = u.game_id
          AND o.player  = u.player
          AND o.line    = u.line
        WHERE o.side = 'over' AND u.side = 'under'
    )
    SELECT
        game_id,
        -- SLATE DATE. start_time is UTC; MLB assigns a game to the date of its
        -- LOCAL first pitch, and US/Eastern is the reference. This is a proper
        -- timezone conversion (DST-aware), NOT an hour offset.
        --
        -- MEASURED (probe_hours.py, May 2026): the date mapping was never the
        -- problem. All three candidate mappings agree --
        --     raw UTC     31 dates, 12.1 games/date
        --     minus 8h    30 dates, 12.5 games/date
        --     US/Eastern  30 dates, 12.5 games/date
        -- and May 2026 has 376 distinct games, not the ~465 a full MLB month
        -- would have. SmartStake covers ~80% of games. An earlier "-8 HOUR fix"
        -- was chasing a bug that did not exist (the premise -- that we should
        -- see ~15 games/date -- was wrong) and it made the spread WORSE
        -- (min 2 / max 21). US/Eastern is correct by construction; use it.
        CAST(start_time AT TIME ZONE 'UTC'
                        AT TIME ZONE 'America/New_York' AS DATE)  AS game_date,
        player,
        market,
        line,
        book,
        odds_over,
        odds_under,
        p_over_devig,
        overround,
        result,
        won_over,
        close_ts_over
    FROM two_way
    """


def main(argv=None) -> int:
    # This extractor emits the vendor's numeric result beside close prices and
    # groups by a non-canonical vendor game identity. Its only consumer was the
    # retired market verdict path. Keep the implementation for auditability,
    # but never mint a new close/target artifact that could be mistaken for the
    # canonical game-keyed market universe.
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.evaluation.retired_market_evaluators import retired_market_evaluator_exit
    return retired_market_evaluator_exit(Path(__file__).name)

    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-05",
                    help="'2026-05' for one month, or 'all' for the whole dataset")
    ap.add_argument("--out", required=True, help="output parquet path")
    ap.add_argument("--min-overround", type=float, default=-0.02,
                    help="sanity floor on the two-way overround (negative = arb)")
    ap.add_argument("--max-overround", type=float, default=0.35,
                    help="sanity ceiling; a book quoting >35%% margin is a data error")
    args = ap.parse_args(argv)

    base = "hf://datasets/SmartStake/mlb-player-props"
    hf_glob = (f"'{base}/**/*.parquet'" if args.month == "all"
               else f"'{base}/mon={args.month}/*.parquet'")

    duckdb.sql("INSTALL httpfs; LOAD httpfs;")
    duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

    print(f"extracting closes from {hf_glob}")
    print(f"  books:   {', '.join(BENCHMARK_BOOKS)}")
    print(f"  markets: {', '.join(MARKET_MAP)}")
    print("  filter:  graded rows only, ts < start_time (no post-first-pitch leakage)")

    df = duckdb.sql(build_query(hf_glob)).df()
    if df.empty:
        print("NO ROWS. Check the month partition exists and books/markets match.",
              file=sys.stderr)
        return 2

    print(f"\nraw two-way closes: {len(df):,}")

    # ---------------- assertions: fail loudly, never silently ----------------
    fails = []

    # 1. de-vigged probability must be a probability
    bad_p = df[(df.p_over_devig <= 0) | (df.p_over_devig >= 1)]
    if len(bad_p):
        fails.append(f"{len(bad_p)} rows with p_over_devig outside (0,1)")

    # 2. overround sanity. Books ~0.02-0.08; exchanges ~0. A negative overround
    #    means arbitrage (possible but rare); a huge one means bad data.
    bad_or = df[(df.overround < args.min_overround) | (df.overround > args.max_overround)]
    if len(bad_or):
        print(f"\n  WARNING: dropping {len(bad_or):,} rows with implausible overround "
              f"(outside [{args.min_overround}, {args.max_overround}])")
        print(bad_or.groupby("book")["overround"]
                    .agg(["count", "min", "max"]).to_string())
        df = df.drop(bad_or.index)

    # 3. `won_over` must agree with result vs line -- if it doesn't, either our
    #    grading convention or theirs is wrong, and every Brier downstream is junk.
    chk = df[df.won_over.notna()].copy()
    chk["implied_won"] = chk["result"] > chk["line"]
    disagree = chk[chk["won_over"].astype(bool) != chk["implied_won"]]
    if len(disagree):
        fails.append(
            f"{len(disagree)} rows where won_over disagrees with (result > line). "
            f"Grading convention mismatch -- STOP, do not use this data."
        )
        print("\n  sample disagreements:")
        print(disagree[["player", "market", "line", "result", "won_over"]]
              .head(10).to_string(index=False))

    # 4. SLATE-DATE SANITY.
    #    MEASURED: SmartStake covers ~12.1 games/date (376 games in May 2026),
    #    NOT the ~15/day MLB actually plays -- the dataset is ~80% complete.
    #    An earlier version of this assertion used a 13.0 floor derived from the
    #    WRONG premise that we should see 15/day; it hard-failed on perfectly good
    #    data. The floor is now set below the dataset's true coverage, so it fires
    #    only on a CATASTROPHIC misdating (games spilling across a date boundary
    #    would collapse this well below 10), not on ordinary incompleteness.
    gpd = df.groupby("game_date")["game_id"].nunique()
    if len(gpd) and gpd.mean() < 10.0:
        fails.append(
            f"mean games/date = {gpd.mean():.1f} (this dataset covers ~12/day). "
            f"Below 10 means the slate-date mapping is broken -- games are "
            f"spilling across the date boundary. Min date has {gpd.min()} games."
        )

    if fails:
        print("\n*** ASSERTION FAILURES ***", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        return 1

    # ---------------- normalize + emit ----------------
    df["category"] = df["market"].map(MARKET_MAP)
    df["player_key"] = norm_name(df["player"])
    df["game_date"] = pd.to_datetime(df["game_date"]).dt.strftime("%Y-%m-%d")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)

    print(f"\nwrote {len(df):,} de-vigged closes -> {out}")
    print("\nrows by category x book:")
    print(df.pivot_table(index="category", columns="book", values="p_over_devig",
                         aggfunc="count", fill_value=0).to_string())
    print("\noverround by book (MEASURED 0.05-0.08 everywhere; exchanges are NOT tighter):")
    print(df.groupby("book")["overround"]
            .agg(n="count", mean="mean", median="median")
            .round(4).sort_values("mean").to_string())
    gpd = df.groupby("game_date")["game_id"].nunique()
    print(f"\ndates: {df.game_date.min()} .. {df.game_date.max()} "
          f"({df.game_date.nunique()} days)")
    print(f"distinct games: {df.game_id.nunique()}  "
          f"({gpd.mean():.1f}/date, min {gpd.min()}, max {gpd.max()}) "
          f"-- MLB is ~15/day")
    print(f"distinct players: {df.player_key.nunique()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
