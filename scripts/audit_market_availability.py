#!/usr/bin/env python3
"""
MARKET AVAILABILITY — what can this evaluator actually score, and where?

*** THIS MEASURES. IT PROPOSES NOTHING. ***
No merge across books. No manufactured under side. No fix.

=============================================================================
WHY IT EXISTS: THE PIPELINE CLAIMED A MARKET IT CANNOT EVALUATE
=============================================================================
Every artifact in this build said "HR and hits, reported separately, never
pooled". MEASURED, June:

    by market:
                eligible  mapped   rate
      category
      hits          3657    3572  97.7%
      <no home_runs row at all>

*** ZERO HR ROWS. IT WAS IN MY OWN OUTPUT AND I READ PAST IT TWICE. ***

*** I THEN INVENTED A MECHANISM, AND THIS AUDIT PROVED IT WRONG. ***

I wrote, in four files, that "an HR prop is a YES/NO market, so there is no under
side, at ANY book -- it is not a two-sided market anywhere." I reasoned from a
physical intuition about what a home-run prop *is*, and NEVER MEASURED IT.

WHAT THIS AUDIT MEASURED (Mar-Jun 2026):

    player home runs      over 65,168,992      *** under 17,503,855 ***

    TWO-SIDED AT 33 BOOKS:
        hard_rock  29,405 two-sided selections (14,129 scoreable)
        novig      29,355
        bracco     26,551
        pinnacle    7,804
        ... and on down.

*** SEVENTEEN MILLION UNDER QUOTES. THE CLAIM WAS FALSE. ***

THE ACTUAL REASON HR PRODUCED NOTHING:
    *** DRAFTKINGS DOES NOT POST A TWO-SIDED HR MARKET. ***
DK appears in this table for `player hits` (16,371 scoreable) and `player rbis`
(15,695) and NOT ONCE for `player home runs`. The policy declares
book=draftkings. A BOOK-SPECIFIC GAP -- not a market-structural one. Different
fact, different implications, and the difference is the entire point of measuring
instead of asserting (rule 9).

AND NOTE WHY THE UNDER SIDE MATTERS EVEN TO SOMEONE WHO WOULD ONLY EVER BET THE
OVER:
        p_over_devig = (1/odds_over) / ((1/odds_over) + (1/odds_under))
The under quote is an INSTRUMENT, not a wager. Without it you have 1/odds_over,
which still carries the book's margin -- so you would compare the model against
an INFLATED price and call the difference "edge". THE VIG IS THE THING YOU ARE
TRYING TO BEAT.

=============================================================================
WHAT THIS AUDIT DOES
=============================================================================
It walks the ACTUAL EVALUATOR CONTRACT, per (book, product, side), and reports
where each stage loses its rows:

    quotes  ->  pregame  ->  BOTH SIDES  ->  valid ENTRY at T-Nh
            ->  valid CLOSE  ->  settlement present  ->  FRESH  ->  SCOREABLE

Counting raw rows would answer a question nobody asked: a book can have millions
of HR quotes and still produce ZERO scoreable selections, because every one of
them is an over with no under to pair with. *** THE CONTRACT IS THE DENOMINATOR,
NOT THE ROW COUNT. ***

=============================================================================
*** A COLUMN NAMED `under` IS NOT AN UNDER SIDE. *** (rule 1)
=============================================================================
If some book DOES show a two-sided HR market, that is a FINDING, not a solution.
Before it could ever be used it would have to be verified as:
    * the SAME PRODUCT (a Yes/No priced both ways is not an over/under ladder);
    * the SAME LINE (an alt-line 1.5 is a different bet from a 0.5);
    * TIME-ALIGNED (a stale under from book B paired with a fresh over from book
      A yields a probability NEITHER BOOK EVER OFFERED).
And pairing ACROSS books is not a de-vig at all -- it is a SPREAD BETWEEN VENUES.

None of that is done here. This audit reports what EXISTS.

=============================================================================
SANITY -- STATED BEFORE THE RUN (rule 7)
=============================================================================
  I PREDICTED: "HR is ONE-SIDED AT EVERY BOOK, because that is what the market
  is. If a two-sided HR market appears anywhere, I am WRONG about the product."

  *** IT APPEARED AT 33 BOOKS. I WAS WRONG. ***

  This is the FIFTH prediction I got wrong on this vendor in one session:
      "the extras are stubs"        -> more than half were not
      "zero duplicate MARKET_KEYs"  -> 2,898 (pre-stale) / 186 (real)
      "exact re-posts"              -> 1,449/1,449 CONFLICTING
      "different market histories"  -> most were simply STALE
      "HR is one-sided everywhere"  -> two-sided at 33 books
  Every one was a MECHANISM inferred from a partial view. The measurements were
  right and the stories were wrong, every time. The expectations above are stated
  so they can be FALSIFIED, not so they can be trusted -- and this one was.

Usage:
  python scripts/audit_market_availability.py --months 2026-03 2026-04 2026-05 2026-06
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.market_eligibility import load_policy, parquet_source  # noqa: E402

# EVERY market in the vendor, not just the ones we currently evaluate. The point
# is to find out what IS there, which means not pre-filtering to what we expect.
ALL_MARKETS = ("player hits", "player home runs", "player bases", "player rbis",
               "player strikeouts", "player batting walks")


def draftkings_hr_status(hr: pd.DataFrame) -> str:
    """Describe the measured DK HR state without equating a raw row with coverage."""
    dk = hr[hr.book == "draftkings"]
    if dk.empty:
        return "ABSENT — no DraftKings HR row meets the reporting threshold"
    if len(dk) != 1:
        raise ValueError("expected one DraftKings / player-home-runs funnel row")
    row = dk.iloc[0]
    if int(row["SCOREABLE"]) == 0:
        return ("PRESENT IN RAW FUNNEL, BUT ZERO SCOREABLE TWO-SIDED HR "
                "SELECTIONS AT THE DECLARED CONTRACT")
    return (f"{int(row['SCOREABLE']):,} SCOREABLE TWO-SIDED HR SELECTIONS "
            "IN THIS EXPORT")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/market/smartstake")
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--policy", default="config/ab_policy.json")
    ap.add_argument("--min-quotes", type=int, default=1000,
                    help="only report (book, market) pairs above this, to keep "
                         "the table readable. REPORTING boundary only -- it "
                         "changes no measurement.")
    ap.add_argument("--out", default="data/market/v3/market_availability.json")
    args = ap.parse_args(argv)

    vals, policy_sha, _, _ = load_policy(args.policy)
    hours, maxage = vals["entry_hours"], vals["max_quote_age"]

    print("=" * 100)
    print(f"MARKET AVAILABILITY — {args.months}")
    print("=" * 100)
    print(f"  policy {policy_sha}   T-{hours}h entry   max_quote_age={maxage}min")
    print()
    print("  THE EVALUATOR CONTRACT, stage by stage. NOT a raw row count -- a book")
    print("  can carry MILLIONS of HR quotes and produce ZERO scoreable selections,")
    print("  because every one is an OVER with no UNDER to pair with.")
    print()
    print("  MEASUREMENT ONLY. No merge across books. No manufactured under side.")
    print()

    src = parquet_source(args.root, args.months)
    mk = ", ".join(f"'{m}'" for m in ALL_MARKETS)

    # ---- STAGE 0: does a SIDE even exist? --------------------------------
    # The decisive question for HR, asked directly.
    sides = duckdb.sql(f"""
        SELECT market, side, count(*) AS quotes,
               count(DISTINCT book) AS books,
               count(DISTINCT (game_id, start_time, player, line)) AS selections
        FROM ({src})
        WHERE market IN ({mk})
        GROUP BY 1, 2 ORDER BY 1, 2
    """).df()
    print("=" * 100)
    print("STAGE 0 — DOES A SIDE EVEN EXIST?  (the decisive question)")
    print("=" * 100)
    piv = sides.pivot_table(index="market", columns="side", values="quotes",
                            fill_value=0).astype("int64")
    print("  quotes by market x side:")
    print("  " + piv.to_string().replace("\n", "\n  "))

    one_sided = []
    for m in piv.index:
        row = piv.loc[m]
        have = {s for s in row.index if row[s] > 0}
        if have != {"over", "under"}:
            one_sided.append((m, sorted(have)))
    print()
    if one_sided:
        for m, have in one_sided:
            print(f"  *** {m}: SIDES PRESENT IN THIS DATA = {have} ***")
            if "under" not in have:
                # *** REPORT THE ABSENCE. DO NOT ASSERT A MARKET STRUCTURE. ***
                # An earlier version of this line printed "IT IS NOT A TWO-SIDED
                # MARKET" -- the exact claim this script DISPROVED for home runs
                # (17.5M under quotes, 33 books). All that is MEASURED here is
                # that no under quote appears IN THIS VENDOR'S EXPORT, for these
                # months. Absence of evidence is not evidence of absence, and the
                # vendor's coverage is not the market (rule 9).
                print(f"      NO UNDER QUOTE APPEARS IN THIS EXPORT for these")
                print(f"      months. A two-sided de-vig therefore cannot be built")
                print(f"      FROM THIS DATA.")
                print(f"      *** THAT IS A FACT ABOUT THE EXPORT, NOT ABOUT THE")
                print(f"      MARKET. *** Whether the product is genuinely")
                print(f"      one-sided is NOT established here, and asserting it")
                print(f"      would repeat the error this audit exists to correct.")
    else:
        print("  every market carries BOTH sides somewhere in this export.")

    # ---- STAGE 1..N: the contract, per (book, market) --------------------
    print()
    print("=" * 100)
    print("THE CONTRACT, PER (book, market)")
    print("=" * 100)
    funnel = duckdb.sql(f"""
        WITH raw AS (SELECT * FROM ({src}) WHERE market IN ({mk})),
        pre AS (SELECT * FROM raw WHERE ts < start_time),
        sel AS (
            SELECT book, market, game_id, start_time, player, line,
                   count(DISTINCT side) AS n_sides,
                   bool_or(result IS NOT NULL) AS settled,
                   count(*) FILTER (WHERE side='over'
                        AND ts <= start_time - INTERVAL {hours} HOUR) AS entry_over,
                   count(*) FILTER (WHERE side='under'
                        AND ts <= start_time - INTERVAL {hours} HOUR) AS entry_under,
                   count(*) FILTER (WHERE side='over')  AS close_over,
                   count(*) FILTER (WHERE side='under') AS close_under,
                   date_diff('minute',
                       max(ts) FILTER (WHERE side='over'
                            AND ts <= start_time - INTERVAL {hours} HOUR),
                       start_time - INTERVAL {hours} HOUR) AS entry_age
            FROM pre GROUP BY 1,2,3,4,5,6
        )
        SELECT book, market,
            count(*)                                              AS selections,
            count(*) FILTER (WHERE n_sides = 2)                   AS both_sides,
            count(*) FILTER (WHERE entry_over > 0 AND entry_under > 0)
                                                                  AS valid_entry,
            count(*) FILTER (WHERE entry_over > 0 AND entry_under > 0
                             AND close_over > 0 AND close_under > 0)
                                                                  AS valid_close,
            count(*) FILTER (WHERE entry_over > 0 AND entry_under > 0
                             AND close_over > 0 AND close_under > 0
                             AND settled)                         AS settled_too,
            count(*) FILTER (WHERE entry_over > 0 AND entry_under > 0
                             AND close_over > 0 AND close_under > 0
                             AND settled AND entry_age <= {maxage})
                                                                  AS SCOREABLE
        FROM sel GROUP BY 1,2
        HAVING count(*) >= {args.min_quotes}
        ORDER BY SCOREABLE DESC, selections DESC
    """).df()

    if funnel.empty:
        print(f"  no (book, market) pair above --min-quotes {args.min_quotes}")
    else:
        show = funnel.head(40).copy()
        show["pct"] = (show.SCOREABLE / show.selections * 100).round(1)
        print("  " + show.to_string(index=False).replace("\n", "\n  "))
        print()
        print("  selections -> both_sides -> valid_entry -> valid_close -> "
              "settled -> SCOREABLE")

    # ---- HR, specifically -------------------------------------------------
    print()
    print("=" * 100)
    print("HOME RUNS — CAN ANY BOOK SUPPORT A TWO-SIDED DE-VIG?")
    print("=" * 100)
    hr = funnel[funnel.market == "player home runs"]
    if hr.empty:
        print("  no book reaches the reporting threshold for HR.")
    else:
        two_sided = hr[hr.both_sides > 0]
        if two_sided.empty:
            print(f"  {len(hr)} book(s) carry HR quotes, and NOT ONE produces a")
            print(f"  two-sided selection IN THIS EXPORT.")
            print(f"  total HR selections: {int(hr.selections.sum()):,}   "
                  f"both_sides: 0   SCOREABLE: 0")
            print()
            print("  *** THAT IS A FACT ABOUT THIS EXPORT, NOT ABOUT THE MARKET. ***")
            print("  It does NOT establish that HR is a one-sided product -- that is")
            print("  precisely the claim this audit was built to test, and asserting")
            print("  it from an absence would be the same error again.")
        else:
            print(f"  *** HR IS TWO-SIDED AT {len(two_sided)} BOOKS. ***")
            print(f"  I PREDICTED NONE. The prediction was wrong (rule 9).")
            print()
            print("  " + two_sided.head(15).to_string(index=False)
                  .replace("\n", "\n  "))
            print()
            dk = hr[hr.book == "draftkings"]
            print(f"  DRAFTKINGS in this table: {draftkings_hr_status(hr)}")
            # Raw presence is not the same as scoreable coverage; handled above.
            print("  The policy declares book=draftkings, so it has no HR rows "
                  "for THIS two-sided evaluator contract.")
            print(f"  *** A BOOK-SPECIFIC GAP, NOT A MARKET-STRUCTURAL ONE. ***")
            print()
            print("  *** AND THIS IS A FINDING, NOT A SOLUTION. *** Before any of it")
            print("  could be used, three things are UNVERIFIED (rule 1):")
            print("    1. SAME PRODUCT?  A Yes/No market priced both ways gives a")
            print("       'No' leg -- and a No is NOT an over/under under. It may")
            print("       settle identically, or not (pushes, voids, alt-lines).")
            print("       *** A COLUMN NAMED `under` IS NOT AN UNDER SIDE. ***")
            print("    2. SAME LINE?     An alt-line 1.5 is a different bet from 0.5.")
            print("    3. TIME-ALIGNED?  A stale under paired with a fresh over")
            print("       yields a probability NEITHER BOOK EVER OFFERED.")
            print("  And pairing ACROSS books is not a de-vig at all -- it is a")
            print("  SPREAD BETWEEN VENUES, across two vig structures and two void")
            print("  regimes. Switching venue for HR would also change it for hits,")
            print("  where the entire result is priced against DK.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    funnel.to_csv(out.with_suffix(".csv"), index=False)
    json.dump(dict(
        _comment=(
            "Market availability at the ACTUAL evaluator contract (both sides -> "
            "valid entry at T-Nh -> valid close -> settlement presence -> "
            "freshness -> scoreable), per book x market. NOT a raw row count: a "
            "book can carry millions of HR quotes and produce ZERO scoreable "
            "selections at one venue while other venues do produce two-sided HR "
            "rows. This export can establish only book/product/side availability "
            "under this evaluator contract; it cannot establish that an HR product "
            "is intrinsically one-sided or that an `under` side has identical "
            "settlement semantics. MEASUREMENT ONLY -- no merge across books, no "
            "manufactured under side. A column named `under` is not an under side."),
        months=args.months, policy_sha=policy_sha,
        entry_hours=hours, max_quote_age=maxage,
        sides_by_market=piv.to_dict(),
        one_sided_markets=[m for m, _ in one_sided],
        funnel=funnel.to_dict("records"),
    ), out.open("w", encoding="utf-8"), indent=2, default=str)
    print(f"\nwrote {out}")
    print(f"      {out.with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
