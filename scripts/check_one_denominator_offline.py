#!/usr/bin/env python3
"""
ONE DENOMINATOR — every caller scores THE SAME ROWS, at THE SAME PRICES.

*** AN ENFORCEMENT, NOT A CONVENTION. ***
A shared module gives one definition TODAY. This test gives one definition
PERMANENTLY. Without it, someone adds a filter to one caller six weeks from now,
the denominators silently diverge, and the failure is INVISIBLE -- because both
numbers still LOOK like coverage.

=============================================================================
*** AND IT ALREADY HAPPENED ONCE, AFTER THE MODULE WAS EXTRACTED. ***
=============================================================================
v1 of this test passed 8/8. The drift was STILL THERE, one filter downstream:

    run_market_ab.py       applied `entry_age_min <= max_quote_age` AFTER the
                           shared call
    build_crosswalk_v3.py  did NOT
    audit_duplicate_keys   did NOT

I extracted the boundary and LEFT A PIECE OF IT BEHIND -- then wrote a test that
only checked the part I had moved. The test was measuring MY FIX rather than THE
PROPERTY.

*** SO THE EQUALITY IS NOW ASSERTED ON THE FINAL `scoreable` SUBSET, *** which is
what the evaluator actually scores.

THE COST OF THAT MISS: every duplicate number was PRE-STALE.
    97.8% coverage      -> pre-stale hard-mapping coverage
    2,898 / 1,449 dupes -> pre-stale. If one fragment is STALE it never reaches
                           the evaluator and THERE IS NO DUPLICATE AT ALL.
    "53% coverage cost" -> NOT ESTABLISHED.

Usage:
    python scripts/check_one_denominator_offline.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.market_eligibility import (          # noqa: E402
    ELIGIBILITY_PROJECTION, RAW_DECIMAL_ODDS_FIELDS, STATUS_ABSENT,
    STATUS_SCOREABLE, STATUS_STALE, assert_no_leakage, canonical,
    eligibility_sql, load_policy, scoreable,
)
from src.evaluation.market_economics import (            # noqa: E402
    expected_profit_per_unit_decimal,
)

PASS: list[str] = []
FAIL: list[str] = []


def ok(name: str, cond: bool, note: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else '!!'}] {name}")
    if note and not cond:
        print(f"       {note}")


def mut(name: str, broke: bool, note: str = "") -> None:
    (PASS if broke else FAIL).append(f"MUTATION {name}")
    print(f"  [{'OK' if broke else '!!'}] MUTATION {name}")
    if not broke:
        print("       *** THE MUTATION PASSED. THE CHECK IS DECORATION. ***")
        if note:
            print(f"       {note}")


MK = ["mlb_game_pk", "player_id", "category", "line"]
CK = ["vendor_game_id", "start_time", "player_key"]
BOOK, HOURS, MAXAGE = "draftkings", 4, 90
MKT = "'player hits'"


def fixture(con):
    """Every case the callers must handle IDENTICALLY.

    alpha -- eligible + FRESH. Its LATE quote carries a NULL `result`; under the
             old quote-level filter that quote is DROPPED and THE ENTRY PRICE
             MOVES, while the KEY stays the same.
    beta  -- ALL-NULL -> settlement_absent. Present, never scoreable, NOT deleted.
    delta -- settled, but both entry quotes are ANCIENT -> STALE.
    epsilon -- settled with a fresh over but stale under.  The old one-sided
               freshness check would score it, despite de-vig consuming both.
    """
    rows = []
    st = "2026-06-01 23:05:00"                    # horizon = 19:05

    def q(gid, player, side, ts, odds, result):
        rows.append((gid, st, player, "player hits", 0.5, side, BOOK, ts, odds,
                     result))

    for side, early, late in (("over", 1.90, 2.50), ("under", 1.90, 1.55)):
        q("g1", "alpha one", side, "2026-06-01 12:00:00", early, 1.0)
        q("g1", "alpha one", side, "2026-06-01 18:00:00", late, None)   # age 65
    for side, odds in (("over", 2.00), ("under", 1.80)):
        q("g1", "beta two", side, "2026-06-01 12:00:00", odds, None)
        q("g1", "beta two", side, "2026-06-01 18:00:00", odds, None)
    for side, odds in (("over", 1.80), ("under", 2.00)):
        q("g2", "delta four", side, "2026-06-01 06:00:00", odds, 0.0)   # age 785
    q("g3", "epsilon five", "over", "2026-06-01 18:00:00", 1.80, 1.0)  # age 65
    q("g3", "epsilon five", "under", "2026-06-01 12:00:00", 2.00, 1.0) # age 425

    con.execute("""CREATE OR REPLACE TABLE fx (
        game_id VARCHAR, start_time TIMESTAMP, player VARCHAR, market VARCHAR,
        line DOUBLE, side VARCHAR, book VARCHAR, ts TIMESTAMP, odds DOUBLE,
        result DOUBLE)""")
    con.executemany("INSERT INTO fx VALUES (?,?,?,?,?,?,?,?,?,?)", rows)


def universe(con, book=BOOK, hours=HOURS, maxage=MAXAGE):
    """THE SHARED FUNCTION. Every caller makes exactly this call."""
    return con.execute(
        eligibility_sql("SELECT * FROM fx", book, hours, maxage, MKT)).df()


print("=" * 86)
print("check_one_denominator_offline")
print("=" * 86)

con = duckdb.connect()
fixture(con)
U = universe(con)

print("\n0. THE SHARED FUNCTION LABELS EVERY ROW (exhaustive, exclusive, ordered)")
print("   " + U[["vendor_game_id", "player", "entry_p_over", "entry_age_min",
                 "settlement_present", "status"]]
      .to_string(index=False).replace("\n", "\n   "))
ok("every row is LABELLED (exhaustive)", U.status.notna().all())
ok("alpha  -> SCOREABLE  (settled, fresh)",
   U[U.player == "alpha one"].status.iloc[0] == STATUS_SCOREABLE)
ok("beta   -> SETTLEMENT_ABSENT  (never scoreable, and NOT deleted)",
   U[U.player == "beta two"].status.iloc[0] == STATUS_ABSENT)
ok("delta  -> STALE  (settled, but the entry quote is ancient)",
   U[U.player == "delta four"].status.iloc[0] == STATUS_STALE)
eps = U[U.player == "epsilon five"].iloc[0]
ok("epsilon -> STALE (fresh over cannot rescue a stale under used in de-vig)",
   eps.status == STATUS_STALE
   and eps.entry_over_age_min <= MAXAGE < eps.entry_under_age_min)
ok("scoreable() yields ONLY the scoreable rows",
   set(scoreable(U).player) == {"alpha one"}, str(set(scoreable(U).player)))

# THE MUTATION: retain the original one-sided freshness condition. It would
# classify epsilon as scoreable even though its under price is 425 minutes old.
# This is not a hypothetical: 1,283/3,020 rows in the first June baseline had
# this exact shape, so the two-sided test must fail on the old logic.
old_one_side = U[(U.settlement_present) & (U.entry_over_age_min <= MAXAGE)]
mut("one-sided freshness wrongly admits epsilon", "epsilon five" in set(old_one_side.player),
    "the fixture no longer distinguishes fresh-over/stale-under from two-sided freshness")

# =========================================================================
print("\n1. THE CALLERS AGREE — on the FINAL scoreable subset, FULL projection")
R = canonical(scoreable(universe(con)))          # runner
B = canonical(scoreable(universe(con)))          # crosswalk / duplicate audit
ok("identical row count", len(R) == len(B))
ok("identical EXACT projection (values, not just keys)", R.equals(B),
   "TWO DEFINITIONS OF ELIGIBLE EXIST.")

pre_stale = canonical(universe(con))
mut("a caller that IGNORES `status` sees a DIFFERENT universe",
    len(pre_stale) != len(R),
    "freshness is not changing the universe -- the fixture does not model it")
print(f"       pre-stale {len(pre_stale)} rows | FINAL scoreable {len(R)} rows")
print("       *** THIS IS THE v1 DRIFT. It is what made 97.8% and 2,898 ***")
print("       *** provisional. ***")

# =========================================================================
print("\n2. D2 — SAME KEYS, DIFFERENT PRICE (a key-set test CANNOT see it)")
old = con.execute(f"""
    WITH pre AS (SELECT * FROM fx
                 WHERE book='{BOOK}' AND market IN ({MKT})
                   AND result IS NOT NULL              -- <<< THE OLD BUG
                   AND ts < start_time),
    entry AS (SELECT game_id, start_time, player, market, line, side,
                     arg_max(odds, ts) AS odds
              FROM pre WHERE ts <= start_time - INTERVAL {HOURS} HOUR
              GROUP BY 1,2,3,4,5,6),
    close AS (SELECT game_id, start_time, player, market, line, side,
                     arg_max(odds, ts) AS odds
              FROM pre GROUP BY 1,2,3,4,5,6)
    SELECT eo.game_id AS vendor_game_id, eo.player,
           (1.0/eo.odds)/((1.0/eo.odds)+(1.0/eu.odds)) AS entry_p_over
    FROM entry eo
    JOIN entry eu USING (game_id, start_time, player, market, line)
    JOIN close co USING (game_id, start_time, player, market, line)
    JOIN close cu USING (game_id, start_time, player, market, line)
    WHERE eo.side='over' AND eu.side='under'
      AND co.side='over' AND cu.side='under'
""").df()

# *** ALPHA ALONE. *** Both implementations agree the row EXISTS and disagree
# ONLY about its price. Pooling beta in would let the test pass via an unrelated
# DROPPED ROW -- and a test that passes for the wrong reason stops working the
# moment that reason changes. (That defect was in v1 of this file, and the
# harness caught it.)
oa = old[old.player == "alpha one"]
ra = R[R.player == "alpha one"]
ok("D2  a KEY-ONLY test would PASS this (alpha exists in BOTH) — the point",
   len(oa) == 1 and len(ra) == 1)
p_new = float(ra.entry_p_over.iloc[0])
p_old = float(oa.entry_p_over.iloc[0])
print(f"       correct {p_new:.6f}   |   old quote-level filter {p_old:.6f}   |   "
      f"delta {abs(p_new - p_old):.6f}")
mut("the FULL-projection comparison CATCHES the price drift",
    abs(p_new - p_old) > 1e-9,
    "the price did not move; the fixture no longer models the bug")

# =========================================================================
print("\n2b. E1 — EXACT POSTED PAYOUTS SURVIVE THE SHARED BOUNDARY")
alpha = U[U.player == "alpha one"].iloc[0]
ok("E1  raw decimal entry/close odds are retained alongside de-vig probabilities",
   set(RAW_DECIMAL_ODDS_FIELDS).issubset(U.columns)
   and float(alpha.entry_over_odds_decimal) == 2.50
   and float(alpha.entry_under_odds_decimal) == 1.55
   and float(alpha.close_over_odds_decimal) == 2.50
   and float(alpha.close_under_odds_decimal) == 1.55,
   "raw payout values were dropped or re-derived; wager economics is no longer auditable")

# Same de-vig probability does NOT imply the same real wager economics.  This
# is a structural counterexample, not a policy recommendation: decimal 1.90/1.90
# and 2.50/2.50 both de-vig to .5, but a .52 model probability loses at the
# first posted price and wins at the second.  A projection containing only .5
# cannot distinguish those states.
low_ev = expected_profit_per_unit_decimal(0.52, 1.90)
high_ev = expected_profit_per_unit_decimal(0.52, 2.50)
fair_low = (1.0 / 1.90) / ((1.0 / 1.90) + (1.0 / 1.90))
fair_high = (1.0 / 2.50) / ((1.0 / 2.50) + (1.0 / 2.50))
ok("E1  equal de-vig probabilities can have opposite posted-price EV",
   abs(fair_low - fair_high) < 1e-12
   and low_ev < 0.0 < high_ev,
   "the fixture no longer distinguishes capture probability from payout EV")

# THE MUTATION: stripping raw payout fields must hard-fail the shared boundary.
# Without this, an enriched artifact could silently regress to fair probabilities
# only and later pretend its selection/staking arithmetic was price-aware.
caught = False
try:
    assert_no_leakage(U.drop(columns=RAW_DECIMAL_ODDS_FIELDS))
except ValueError:
    caught = True
mut("removing raw payout fields HARD-FAILS (they cannot be recreated from p_fair)",
    caught,
    "the boundary accepted an economics-blind quote projection")

# =========================================================================
print("\n3. D3 — *** FRESH + STALE ON ONE MARKET_KEY IS NOT A DUPLICATE ***")
print("   (the mutation that proves the freshness fix)")
con.execute("CREATE OR REPLACE TABLE fx2 AS SELECT * FROM fx WHERE FALSE")
for gid, ts, oo, ou in (("g~fresh", "2026-06-01 18:00:00", 2.00, 1.80),
                        ("g~stale", "2026-05-31 06:00:00", 2.05, 1.78)):
    for side, odds in (("over", oo), ("under", ou)):
        con.execute("INSERT INTO fx2 VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (gid, "2026-06-01 23:05:00", "max muncy", "player hits", 0.5,
                     side, BOOK, ts, odds, 1.0))

U2 = con.execute(eligibility_sql("SELECT * FROM fx2", BOOK, HOURS, MAXAGE,
                                 MKT)).df()
U2["player_key"] = U2.player
U2["category"] = "hits"
U2["start_time"] = pd.to_datetime(U2.start_time, utc=True)
print("   " + U2[["vendor_game_id", "entry_age_min", "status"]]
      .to_string(index=False).replace("\n", "\n   "))

XW = pd.DataFrame([
    dict(vendor_game_id=g,
         start_time=pd.Timestamp("2026-06-01 23:05:00", tz="UTC"),
         player_key="max muncy", mlb_game_pk=900, player_id=600)
    for g in ("g~fresh", "g~stale")])

pre = U2.merge(XW, on=CK, how="inner", validate="many_to_one")
n_pre = int(pre.duplicated(MK, keep=False).sum())
fin = scoreable(U2).merge(XW, on=CK, how="inner", validate="many_to_one")
n_fin = int(fin.duplicated(MK, keep=False).sum())

print(f"\n       PRE-STALE duplicate rows on MARKET_KEY : {n_pre}   "
      f"<- what 2,898 was counting")
print(f"       FINAL     duplicate rows on MARKET_KEY : {n_fin}   "
      f"<- what the A/B would actually see")
mut("PRE-STALE counting reports a duplicate that DOES NOT EXIST for the A/B",
    n_pre > 0 and n_fin == 0,
    "the fixture does not demonstrate the pre-stale over-count")
ok("D3  the stale fragment creates NO final duplicate", n_fin == 0)
ok("D3  the stale fragment appears ONLY in the stale funnel",
   set(U2[U2.status == STATUS_STALE].vendor_game_id) == {"g~stale"}
   and set(scoreable(U2).vendor_game_id) == {"g~fresh"})

# =========================================================================
print("\n4. D4 — THE POLICY LOADER: *** ABSENCE IS NOT PERMISSION ***")
tmp = Path("_policy_test.json")
good = dict(policy_version="research-2026-07-13-v2", parameters={
    k: dict(value=v, origin="placeholder") for k, v in (
        ("min_edge", 0.04), ("max_quote_age", 90), ("min_bets_for_capture", 30),
        ("entry_hours", 4), ("settlement_presence_rule", "x"),
        ("base_pregame_hitter_eligibility_rule", "official_starter AND official_pa >= 1"),
        ("book", "dk"))})
tmp.write_text(json.dumps(good), encoding="utf-8")
vals, sha, ro, unapproved = load_policy(tmp)
ok("D4  a VALID policy loads and yields a sha",
   vals["max_quote_age"] == 90 and len(sha) == 16)
ok("D4  placeholders force research_only", ro and len(unapproved) == 7)

for name, mutate in (
    ("a MISSING field", lambda d: d["parameters"].pop("max_quote_age")),
    ("a BAD TYPE",
     lambda d: d["parameters"]["max_quote_age"].update(value="ninety")),
    ("an UNKNOWN policy_version", lambda d: d.update(policy_version="vibes-1")),
    ("a parameter with no `origin`",
     lambda d: d["parameters"]["min_edge"].pop("origin")),
):
    d = json.loads(json.dumps(good))
    mutate(d)
    tmp.write_text(json.dumps(d), encoding="utf-8")
    caught = False
    try:
        load_policy(tmp)
    except (ValueError, KeyError):
        caught = True
    mut(f"{name} HARD-FAILS (never an implicit default)", caught,
        "*** THE LOADER FELL BACK TO A DEFAULT. Absence became permission — the "
        "same failure as the denylist. ***")
tmp.unlink(missing_ok=True)

print()
print("=" * 86)
print(f"{len(PASS)}/{len(PASS) + len(FAIL)}")
print("=" * 86)
for f in FAIL:
    print(f"  FAILED: {f}")
sys.exit(1 if FAIL else 0)
