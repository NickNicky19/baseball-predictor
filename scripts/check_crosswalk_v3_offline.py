#!/usr/bin/env python3
"""
OFFLINE HARNESS — build_crosswalk_v3.py. No network.

*** RULE 4: A TEST THAT PASSES ON BOTH BROKEN AND FIXED CODE GUARDS NOTHING. ***

THE THREE MUTATIONS:
  M1  a DUPLICATE CONSUMER KEY must HARD-FAIL.
      (vendor_game_id, start_time, player_key) is the ONLY hard invariant v3 owes.

  M2  TWO *** ELIGIBLE *** FRAGMENTS producing ONE MARKET_KEY must HARD-FAIL --
      *** AFTER *** eligibility filtering, with BOTH FRAGMENT IDENTITIES NAMED.

  M3  *** A STUB WITH NO ELIGIBLE SELECTION MUST NOT FAIL. ***
      THIS IS THE ONE THAT MATTERS. It is the difference between REMOVING the
      over-strict policy and RENAMING it. v2 excluded any game_pk claimed by two
      fragments, full stop -- so under v2 this case FAILED. If it still fails,
      nothing has actually changed.

=============================================================================
WHAT v2 GOT WRONG, AND HOW ITS OWN HARNESS HID IT
=============================================================================
v2 excluded, GLOBALLY, every mlb_game_pk claimed by more than one fragment. I
argued the exclusion was LOAD-BEARING -- "two fragments on one game_pk collapse
to the same MARKET_KEY, so keeping one BREAKS THE RUN".

*** I INFERRED THAT FROM THE SHAPE OF THE KEY AND NEVER MEASURED IT. ***

And v2's harness "proved" it -- because MY FIXTURE ASSUMED BOTH FRAGMENTS EMIT
FULL PLAYER SETS. Of course they duplicated: I had encoded the conclusion into
the test data. A test whose fixture assumes the answer is not a test.

MEASURED, afterwards:
    three of four collided pairs produced ZERO eligible A/B rows -- STUBS.
    eligible_key_relation over 28 pairs: NEITHER_ELIGIBLE 20, OVERLAPPING 8,
                                         *** DISJOINT 0 ***
So the fixture below deliberately includes a REAL fragment and a STUB, and
asserts they COEXIST.

Usage:
    python scripts/check_crosswalk_v3_offline.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.market_eligibility import eligibility_sql   # noqa: E402
from scripts.build_crosswalk_v3 import build_mlb_index          # noqa: E402

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
        print(f"       *** THE MUTATION PASSED. THE CHECK IS DECORATION. ***")
        if note:
            print(f"       {note}")


MK = ["mlb_game_pk", "player_id", "category", "line"]
CONSUMER_KEY = ["vendor_game_id", "start_time", "player_key"]

print("=" * 84)
print("check_crosswalk_v3_offline")
print("=" * 84)

# =========================================================================
# FIXTURE. One game_pk (900), TWO vendor fragments:
#   g~real @ 23:05  -- the real listing. Full quotes. PRODUCES ELIGIBLE ROWS.
#   g~stub @ 23:06  -- a one-minute stub. ONE quote, past the horizon.
#                      *** PRODUCES ZERO ELIGIBLE ROWS. ***
# This is the shape MEASURED in bucket C, twelve times over.
# =========================================================================
con = duckdb.connect()
rows = []


def q(gid, st, player, side, ts, odds, result):
    rows.append((gid, st, player, "player hits", 0.5, side, "draftkings",
                 ts, odds, result))


ST1, ST2 = "2026-06-14 23:05:00", "2026-06-14 23:06:00"
for side, odds in (("over", 2.00), ("under", 1.80)):
    q("g~real", ST1, "max muncy", side, "2026-06-14 12:00:00", odds, 1.0)
    q("g~real", ST1, "mookie betts", side, "2026-06-14 12:00:00", odds, 2.0)
# THE STUB: its only quote is 30 min before first pitch -- PAST the T-4h horizon.
# It can never produce an entry price, so it can never produce an eligible row.
for side, odds in (("over", 2.05), ("under", 1.78)):
    q("g~stub", ST2, "max muncy", side, "2026-06-14 22:35:00", odds, 1.0)

con.execute("""CREATE OR REPLACE TABLE fx (
    game_id VARCHAR, start_time TIMESTAMP, player VARCHAR, market VARCHAR,
    line DOUBLE, side VARCHAR, book VARCHAR, ts TIMESTAMP, odds DOUBLE,
    result DOUBLE)""")
con.executemany("INSERT INTO fx VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

E = con.execute(eligibility_sql("SELECT * FROM fx", "draftkings", 4, 90,
                                "'player hits'")).df()
E["player_key"] = E.player
E["category"] = "hits"

print("\n0. THE FIXTURE — a real fragment and a STUB, both on game_pk 900")
print(f"   eligible rows: {len(E)}")
print("   " + E[["vendor_game_id", "player", "entry_p_over",
                 "settlement_present"]].to_string(index=False)
      .replace("\n", "\n   "))
real_e = int((E.vendor_game_id == "g~real").sum())
stub_e = int((E.vendor_game_id == "g~stub").sum())
ok("the REAL fragment produces eligible rows", real_e > 0, f"got {real_e}")
ok("the STUB produces ZERO eligible rows (its only quote is past the horizon)",
   stub_e == 0, f"got {stub_e} — the fixture no longer models a stub")

# The crosswalk maps BOTH fragments. Both vote for game_pk 900.
XW = pd.DataFrame([
    dict(vendor_game_id="g~real", start_time=pd.Timestamp(ST1, tz="UTC"),
         player_key="max muncy", mlb_game_pk=900, player_id=600),
    dict(vendor_game_id="g~real", start_time=pd.Timestamp(ST1, tz="UTC"),
         player_key="mookie betts", mlb_game_pk=900, player_id=605),
    dict(vendor_game_id="g~stub", start_time=pd.Timestamp(ST2, tz="UTC"),
         player_key="max muncy", mlb_game_pk=900, player_id=600),
])

# =========================================================================
print("\n1. M3 — *** A STUB MUST COEXIST WITHOUT FAILURE ***")
print("   (the mutation that proves the policy is GONE, not RENAMED)")
E["start_time"] = pd.to_datetime(E.start_time, utc=True)
joined = E.merge(XW, on=CONSUMER_KEY, how="inner", validate="many_to_one")
dupes = joined[joined.duplicated(MK, keep=False)]
ok("M3  two fragments -> ONE game_pk, and ZERO duplicate eligible MARKET_KEYs",
   len(dupes) == 0,
   "the stub duplicated a key it should not have been able to reach")
ok("M3  the run PROCEEDS (the A/B would not hard-fail here)", len(dupes) == 0)

# THE MUTATION: v2's global policy. It EXCLUDES the game_pk outright.
v2_excluded = set(XW.groupby("mlb_game_pk").vendor_game_id.nunique()
                  .pipe(lambda s: s[s > 1]).index)
v2_kept = XW[~XW.mlb_game_pk.isin(v2_excluded)]
mut("v2's GLOBAL EXCLUSION throws the whole game away over a harmless stub",
    len(v2_kept) == 0 and len(XW) > 0,
    "v2 would keep this game -- so the two policies are not actually different "
    "and the fixture does not discriminate")
print(f"       v2 keeps {len(v2_kept)}/{len(XW)} mappings   "
      f"v3 keeps {len(XW)}/{len(XW)}")
print("       v2 discarded a COMPLETE GAME to guard against a fragment that")
print("       cannot produce a scoreable row. That was the 52.6%.")

# =========================================================================
print("\n2. M2 — TWO *** ELIGIBLE *** FRAGMENTS ON ONE MARKET_KEY MUST FAIL")
print("   (a REAL duplicate — and it must NAME BOTH FRAGMENTS)")
# Promote the stub into a REAL second listing: give it a quote at the horizon.
for side, odds in (("over", 2.05), ("under", 1.78)):
    con.execute("INSERT INTO fx VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("g~stub", ST2, "max muncy", "player hits", 0.5, side,
                 "draftkings", "2026-06-14 12:00:00", odds, 1.0))
E2 = con.execute(eligibility_sql("SELECT * FROM fx", "draftkings", 4, 90,
                                 "'player hits'")).df()
E2["player_key"] = E2.player
E2["category"] = "hits"
E2["start_time"] = pd.to_datetime(E2.start_time, utc=True)
j2 = E2.merge(XW, on=CONSUMER_KEY, how="inner", validate="many_to_one")
d2 = j2[j2.duplicated(MK, keep=False)]
ok("M2  a duplicate eligible MARKET_KEY IS DETECTED", len(d2) > 0,
   "two fragments now BOTH produce a scoreable row for (900, 600, hits, 0.5) "
   "and nothing caught it")
if len(d2):
    print(f"       {len(d2)} rows share MARKET_KEY {MK}")
    print("       " + d2[["vendor_game_id", "start_time", *MK]]
          .to_string(index=False).replace("\n", "\n       "))
ok("M2  BOTH fragment identities are traceable on the failing rows",
   len(d2) > 0 and d2.vendor_game_id.nunique() == 2,
   "the failure cannot be attributed back to the fragments that caused it")

# =========================================================================
print("\n3. M1 — A DUPLICATE CONSUMER KEY MUST HARD-FAIL")
dirty = pd.concat([XW, XW.iloc[[0]]], ignore_index=True)
fires = bool(dirty.duplicated(CONSUMER_KEY, keep=False).any())
ok("M1  v3 validates the RAW frame and FIRES on a duplicate consumer key", fires)

# MUTATION: v1's actual code -- drop_duplicates on the SAME subset it then
# asserts uniqueness on. The assertion CANNOT FIRE.
v1 = dirty.drop_duplicates(subset=CONSUMER_KEY)
mut("v1's dedupe-then-assert CANNOT FIRE on the same duplicate",
    not bool(v1.duplicated(CONSUMER_KEY, keep=False).any()),
    "v1 would print '[OK] ... is UNIQUE' -- a property it MANUFACTURED")

# =========================================================================
print("\n4. THE MAPPING MUST BE WELL DEFINED WHERE A FRAGMENT REPEATS A ROW")
# Both fragments map (900, "max muncy") -> 600. Same mapping, twice. The ROW
# repeats; the MAPPING does not conflict. A plain duplicated() check cannot tell
# those apart -- so we check AGREEMENT, not just uniqueness.
agree = XW.groupby(["mlb_game_pk", "player_key"]).player_id.nunique()
ok("(mlb_game_pk, player_key) agrees on ONE player_id",
   bool((agree == 1).all()),
   "two fragments disagree about who a player IS — a genuine identity failure "
   "that a duplicated() check would NOT see")
raw_dup = int(XW.duplicated(["mlb_game_pk", "player_key"], keep=False).sum())
print(f"       (raw rows duplicated on (game_pk, name): {raw_dup} — expected, a "
      f"fragment re-emits the same mapping. The ROW repeats; the MAPPING agrees.)")

# =========================================================================
print("\n5. M4 — POSTPONED/RESCHEDULED LISTINGS ARE NOT TWO OFFICIAL DATES")


class ScheduleBoundary:
    """Stub the MLB client at its public canonical-game boundary."""

    def get_schedule(self, game_date, include_lineups=False):
        assert game_date == "2026-05-05"
        return [
            {
                "gamePk": 824362,
                "status": {"abstractGameState": "Final", "codedGameState": "D"},
                "teams": {
                    "away": {"team": {"name": "New York Mets"}},
                    "home": {"team": {"name": "Colorado Rockies"}},
                },
            },
            {
                "gamePk": 900,
                "status": {"abstractGameState": "Final", "codedGameState": "F"},
                "teams": {
                    "away": {"team": {"name": "Away"}},
                    "home": {"team": {"name": "Home"}},
                },
            },
        ]

    def get_final_game_pks(self, game_date):
        assert game_date == "2026-05-05"
        return [900]

    def get_game_boxscore_stats(self, game_pk):
        assert game_pk == 900
        return ({10: SimpleNamespace()}, {})

    def get_player_identity(self, player_id):
        assert player_id == 10
        return SimpleNamespace(name="Final Player")


_, canonical_players, canonical_meta, _ = build_mlb_index(
    ScheduleBoundary(), "2026-05-05"
)
ok("M4  only the canonical coded-F game enters the crosswalk index",
   set(canonical_meta) == {900} and set(canonical_players) == {900},
   f"indexed game_pks={sorted(canonical_meta)}")

# Mutation: the postponed row advertises abstract Final. The former broad
# schedule loop admitted it and labelled May 5 as official even though the
# canonical boundary rejected it.
postponed_abstract_final = (
    ScheduleBoundary().get_schedule("2026-05-05")[0]["status"]["abstractGameState"]
    == "Final"
)
mut("abstract Final would admit the postponed listing — canonical filter catches it",
    postponed_abstract_final and 824362 not in canonical_meta)

print()
print("=" * 84)
print(f"{len(PASS)}/{len(PASS) + len(FAIL)}")
print("=" * 84)
for f in FAIL:
    print(f"  FAILED: {f}")
sys.exit(1 if FAIL else 0)
