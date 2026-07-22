#!/usr/bin/env python3
"""
OFFLINE HARNESS — the strict-unique hits artifact.

*** RULE 4: A TEST THAT PASSES ON BOTH BROKEN AND FIXED CODE GUARDS NOTHING. ***

THE THREE MUTATIONS:
  M1  A CONFLICTING DUPLICATE MARKET_KEY EXCLUDES *** BOTH *** ROWS.
      The mutation restores `drop_duplicates`, which KEEPS ONE -- and it must
      produce a DIFFERENT artifact. If it does not, "both rows" is decoration.

  M2  FRESH + STALE ON ONE MARKET_KEY IS *** NOT *** A DUPLICATE.
      The stale row is reported ONLY as stale; the fresh row SURVIVES and stays
      scoreable. A pre-stale computation would exclude BOTH and lose a clean row.
      *** THIS IS THE DRIFT THAT MADE 1,449 OUT OF 1,449 KEYS "CONFLICTING" WHEN
      THE REAL NUMBER IS 93. ***

  M3  THE EMITTED ARTIFACT CONTAINS ZERO DUPLICATE MARKET_KEY.
      The invariant it exists to guarantee. Asserted on the OUTPUT, not on the
      intention.

=============================================================================
WHY `drop_duplicates` IS BANNED, NOT JUST DISCOURAGED
=============================================================================
It KEEPS ONE ROW. Which one? Whichever pandas happened to see first. That is an
ARBITRARY CHOICE about WHICH PRICE WAS REAL -- and the prices differ:

    entry_p_over  median 0.0046   p95 0.0183   max 0.0499     (MEASURED, June)
    min_edge      0.04

*** ON THE WORST KEY THE GAP EXCEEDS min_edge, SO THE CHOICE DECIDES WHETHER THE
*** ROW IS A BET AT ALL. ***

And it is how v1 of the crosswalk "proved" a uniqueness property it had
MANUFACTURED: it deduped on the same subset it then asserted uniqueness on, so
the assertion COULD NOT FIRE. A silent dedupe is data loss wearing a tidy face.

Usage:
    python scripts/check_strict_unique_offline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.market_eligibility import (          # noqa: E402
    STATUS_SCOREABLE, STATUS_STALE, scoreable,
)

PASS: list[str] = []
FAIL: list[str] = []
MK = ["mlb_game_pk", "player_id", "category", "line"]


def ok(name, cond, note=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK' if cond else '!!'}] {name}")
    if note and not cond:
        print(f"       {note}")


def mut(name, broke, note=""):
    (PASS if broke else FAIL).append(f"MUTATION {name}")
    print(f"  [{'OK' if broke else '!!'}] MUTATION {name}")
    if not broke:
        print("       *** THE MUTATION PASSED. THE CHECK IS DECORATION. ***")
        if note:
            print(f"       {note}")


# =========================================================================
# FIXTURE — the three cases, at the grain the artifact is actually built on
# (post-scoreable, post-mapping).
#
#   key A : TWO fragments, BOTH fresh, CONFLICTING prices -> BOTH must go.
#   key B : TWO fragments, one FRESH one STALE            -> NOT a duplicate.
#   key C : ONE fragment                                  -> survives untouched.
# =========================================================================
M = pd.DataFrame([
    # key A -- both scoreable, prices differ by 0.05 (ABOVE min_edge 0.04)
    dict(mlb_game_pk=900, player_id=600, category="hits", line=0.5,
         vendor_game_id="g~a1", entry_p_over=0.5500, close_p_over=0.5600,
         status=STATUS_SCOREABLE),
    dict(mlb_game_pk=900, player_id=600, category="hits", line=0.5,
         vendor_game_id="g~a2", entry_p_over=0.6000, close_p_over=0.6100,
         status=STATUS_SCOREABLE),
    # key B -- one scoreable, one STALE
    dict(mlb_game_pk=900, player_id=601, category="hits", line=0.5,
         vendor_game_id="g~b1", entry_p_over=0.4800, close_p_over=0.4900,
         status=STATUS_SCOREABLE),
    dict(mlb_game_pk=900, player_id=601, category="hits", line=0.5,
         vendor_game_id="g~b2", entry_p_over=0.5200, close_p_over=0.5300,
         status=STATUS_STALE),
    # key C -- clean
    dict(mlb_game_pk=900, player_id=602, category="hits", line=0.5,
         vendor_game_id="g~c1", entry_p_over=0.5100, close_p_over=0.5150,
         status=STATUS_SCOREABLE),
])

print("=" * 88)
print("check_strict_unique_offline")
print("=" * 88)

# *** THE ORDER IS THE POINT. *** scoreable FIRST, duplicates SECOND.
# Reversing it is exactly the bug that made 1,449 keys "conflict" when 93 do.
SC = scoreable(M)
dup_mask = SC.duplicated(MK, keep=False)          # keep=False => BOTH
EX = SC[dup_mask]
U = SC[~dup_mask]

print("\n0. THE ORDER: scoreable -> hard-mapped -> strict-unique")
print(f"   rows {len(M)}  ->  scoreable {len(SC)}  ->  strict-unique {len(U)}")
print("   " + SC[["mlb_game_pk", "player_id", "vendor_game_id", "entry_p_over",
                  "status"]].to_string(index=False).replace("\n", "\n   "))

# =========================================================================
print("\n1. M1 — A CONFLICTING DUPLICATE EXCLUDES *** BOTH *** ROWS")
a = U[(U.player_id == 600)]
ok("M1  key A (two fresh, conflicting prices) is ENTIRELY excluded", len(a) == 0,
   f"{len(a)} row(s) survived — one price was CHOSEN over the other")
ok("M1  both of key A's fragments are in the EXCLUDED set",
   set(EX[EX.player_id == 600].vendor_game_id) == {"g~a1", "g~a2"})

# THE MUTATION: drop_duplicates. It KEEPS ONE -- an arbitrary choice about which
# price was real, on a pair whose entry gap (0.05) EXCEEDS min_edge (0.04).
dd = SC.drop_duplicates(MK, keep="first")
kept = dd[dd.player_id == 600]
mut("`drop_duplicates` KEEPS one row — an arbitrary choice of price",
    len(kept) == 1 and len(dd) != len(U),
    "drop_duplicates produced the SAME artifact — the fixture does not "
    "discriminate, so 'both rows' is untested")
if len(kept):
    print(f"       it kept {kept.vendor_game_id.iloc[0]} at "
          f"entry {float(kept.entry_p_over.iloc[0]):.4f}; the other was "
          f"{float(EX[(EX.player_id==600) & (EX.vendor_game_id!=kept.vendor_game_id.iloc[0])].entry_p_over.iloc[0]):.4f}")
    print(f"       gap 0.0500 > min_edge 0.04 -> THE CHOICE DECIDES WHETHER THIS")
    print(f"       ROW IS A BET AT ALL.")

# =========================================================================
print("\n2. M2 — FRESH + STALE IS *** NOT *** A DUPLICATE")
b = U[U.player_id == 601]
ok("M2  the FRESH row SURVIVES", len(b) == 1 and b.vendor_game_id.iloc[0] == "g~b1",
   "the fresh row was excluded — a stale fragment is still being counted as a "
   "duplicate")
ok("M2  the STALE row never entered the scoreable universe",
   "g~b2" not in set(SC.vendor_game_id))

# THE MUTATION: compute duplicates BEFORE the freshness filter.
# *** THIS IS THE BUG THAT MADE 1,449 KEYS "CONFLICT" WHEN 93 DO. ***
pre_stale_dup = M.duplicated(MK, keep=False)
pre_U = M[~pre_stale_dup]
lost = "g~b1" not in set(pre_U.vendor_game_id)
mut("computing duplicates BEFORE freshness LOSES the clean fresh row",
    lost,
    "the fixture does not model the pre-stale over-count")
print(f"       pre-stale: {int(pre_stale_dup.sum())} 'duplicate' rows | "
      f"post-stale: {len(EX)}")
print("       94% of the original 1,449 keys were EXACTLY this — one stale "
      "fragment,")
print("       and no duplicate at all.")

# =========================================================================
print("\n3. M3 — THE ARTIFACT CONTAINS ZERO DUPLICATE MARKET_KEY")
ok("M3  zero duplicate MARKET_KEY in the emitted artifact",
   not U.duplicated(MK).any())
ok("M3  the clean single-fragment key survives untouched",
   set(U[U.player_id == 602].vendor_game_id) == {"g~c1"})
ok("M3  the artifact is a SUBSET of scoreable (nothing invented)",
   set(U.vendor_game_id) <= set(SC.vendor_game_id))

# =========================================================================
print("\n4. `drop_duplicates` MUST NOT APPEAR IN THE BUILDER (AST, not grep)")
builder = Path(__file__).resolve().parents[1] / "scripts" / "build_strict_unique_hits.py"
if builder.exists():
    import ast as _ast
    tree = _ast.parse(builder.read_text(encoding="utf-8"))
    # *** AST, NOT STRING MATCHING. *** The file DOCUMENTS the ban in prose, so a
    # grep cannot tell a real call from the sentence forbidding it. The syntax
    # tree can. (And a live `drop_duplicates` DID survive the first draft of this
    # builder, in the manifest's excluded-keys list -- harmless in effect, but the
    # rule is mechanical precisely because a benign-looking dedupe is
    # indistinguishable from a harmful one at a glance.)
    calls = [n for n in _ast.walk(tree)
             if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
             and n.func.attr == "drop_duplicates"]
    ok("M4  ZERO drop_duplicates CALLS in the builder (AST-verified)",
       len(calls) == 0,
       f"live calls at line(s) {[c.lineno for c in calls]} — a silent dedupe is "
       f"data loss wearing a tidy face")
else:
    print("  (builder not found at scripts/ — skipping the source check)")

print()
print("=" * 88)
print(f"{len(PASS)}/{len(PASS) + len(FAIL)}")
print("=" * 88)
for f in FAIL:
    print(f"  FAILED: {f}")
sys.exit(1 if FAIL else 0)
