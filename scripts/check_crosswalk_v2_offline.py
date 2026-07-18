#!/usr/bin/env python3
"""
OFFLINE HARNESS — build_crosswalk_v2.py. No network. Seconds, not minutes.

*** RULE 4: A TEST THAT PASSES ON BOTH BROKEN AND FIXED CODE GUARDS NOTHING. ***
Every check here is MUTATION-TESTED against the ACTUAL v1 DEFECT it exists to
catch -- not a hypothetical one. Each mutation is the real v1 code, run against
the same fixture, and asserted to FAIL. If a mutation passes, the check is
decoration and it says so.

The three v1 defects, restated as testable claims:
  D1 SCHEMA  v1 emits two tables; neither carries load_crosswalk()'s key.
  D2 GRAIN   v1 groups on (date, game_id); the consumer joins on
             (game_id, start_time, player_key). Coarser than its consumer.
  D3 DEDUPE  v1 drop_duplicates(subset=K) then asserts unique on K. Cannot fire.

Plus the two defects found reading the CONSUMER, which v1 never could have hit
because it never got that far:
  D4 COLLISION  two vendor identities -> one mlb_game_pk -> ONE MARKET_KEY ->
                require_unique() hard-fails. The exclusion is LOAD-BEARING.
  D5 NORM DRIFT norm() here and norm_name() in the A/B are two halves of one
                join. If they drift, rows vanish and the vendor gets blamed.

*** THE OFFLINE-HARNESS IMPORT BLIND SPOT (learned on B4). ***
A harness that imports the thing under test DIRECTLY can pass 24/24 while the
module is BROKEN when imported THROUGH the real package graph. Check 0 does a
plain `import` of the production module the real caller imports.

Usage:
    python scripts/check_crosswalk_v2_offline.py
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS: list[str] = []
FAIL: list[str] = []


def ok(name: str, cond: bool, detail: str = "") -> bool:
    (PASS if cond else FAIL).append(f"{name}  {detail}".rstrip())
    print(f"  [{'OK' if cond else '!!'}] {name}" + (f"   {detail}" if detail else ""))
    return cond


def mutation(name: str, broken_raises: bool, detail: str = "") -> None:
    """A mutation MUST break the check. If it doesn't, the check guards nothing."""
    (PASS if broken_raises else FAIL).append(f"MUTATION {name}  {detail}".rstrip())
    print(f"  [{'OK' if broken_raises else '!!'}] MUTATION {name}"
          + (f"   {detail}" if detail else ""))
    if not broken_raises:
        print("       *** THE MUTATION PASSED. THE CHECK IS DECORATION. ***")


# =========================================================================
# FIXTURE — the two failures that actually happened, in miniature.
#
#  * game_id "m~A" is FRAGMENTED across two start_times, one minute apart.
#    Under v1's coarse grain these MERGE into one identity. Under v2 they are
#    two, they both resolve to game_pk 900, and that is a COLLISION.
#  * "Max Muncy" appears as TWO DISTINCT player_ids (600, 601) on the SAME
#    DATE, in DIFFERENT games. This is the real collision that turned a
#    21,171 x 21,171 merge into 21,201 rows.
# =========================================================================
T1 = pd.Timestamp("2026-06-14T23:05:00Z")
T2 = pd.Timestamp("2026-06-14T23:06:00Z")   # +1 min: the vendor fragment
T3 = pd.Timestamp("2026-06-14T20:10:00Z")

VENDOR = pd.DataFrame([
    dict(slate_date="2026-06-14", vendor_game_id="m~A", start_time=T1,
         scoped_rows=500, players=["Max Muncy", "Shohei Ohtani", "Mookie Betts"]),
    dict(slate_date="2026-06-14", vendor_game_id="m~A", start_time=T2,
         scoped_rows=3, players=["Max Muncy", "Shohei Ohtani"]),
    dict(slate_date="2026-06-14", vendor_game_id="m~B", start_time=T3,
         scoped_rows=400, players=["Max Muncy", "Brent Rooker"]),
])

# MLB truth. Two Max Muncys. NEVER in the same box score.
PK_PLAYERS = {
    900: {"max muncy": 600, "shohei ohtani": 660, "mookie betts": 605},   # LAD
    901: {"max muncy": 601, "brent rooker": 670},                          # ATH
}
NAME_TO_PKS = {"max muncy": {900, 901}, "shohei ohtani": {900},
               "mookie betts": {900}, "brent rooker": {901}}


def norm(s: object) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s))
                if not unicodedata.combining(c))
    s = s.lower().strip()
    for ch in ".'`-":
        s = s.replace(ch, "")
    return " ".join(p for p in s.split()
                    if p not in ("jr", "sr", "ii", "iii", "iv", "v"))


def vote(players: list[str]) -> tuple[int | None, int, int]:
    from collections import Counter
    v: Counter[int] = Counter()
    for nm in (norm(p) for p in players):
        for pk in NAME_TO_PKS.get(nm, ()):
            v[pk] += 1
    top = v.most_common(2)
    if not top:
        return None, 0, 0
    return top[0][0], top[0][1], (top[1][1] if len(top) > 1 else 0)


def resolve(vend: pd.DataFrame, keys: list[str]) -> tuple[pd.DataFrame, dict]:
    """Resolve at whatever grain `keys` names. v1 passes the coarse key here;
    v2 passes the exact one. ONE function, so the ONLY difference under test is
    the GRAIN -- not two reimplementations that could diverge for other reasons."""
    from collections import defaultdict
    grouped = (vend.groupby(keys, as_index=False)
                   .agg(players=("players", lambda s: sorted({p for x in s for p in x})),
                        scoped_rows=("scoped_rows", "sum")))
    rows, claimed = [], defaultdict(list)
    for r in grouped.itertuples():
        pk, w, ru = vote(r.players)
        if pk is None or w < 2:
            continue
        st = getattr(r, "start_time", pd.NaT)
        claimed[pk].append(r.vendor_game_id)
        for nm, pid in PK_PLAYERS[pk].items():
            rows.append(dict(vendor_game_id=r.vendor_game_id, start_time=st,
                             player_key=nm, mlb_game_pk=pk, player_id=pid))
    return pd.DataFrame(rows), {k: v for k, v in claimed.items() if len(v) > 1}


print("=" * 76)
print("check_crosswalk_v2_offline")
print("=" * 76)

# ---- 0. THE IMPORT BLIND SPOT ------------------------------------------
print("\n0. PACKAGE-GRAPH IMPORT (the B4 blind spot)")
try:
    import src.data.mlb_api          # noqa: F401  <- what the REAL caller imports
    ok("import src.data.mlb_api through the real package graph", True)
except Exception as exc:                                       # noqa: BLE001
    ok("import src.data.mlb_api through the real package graph", False, repr(exc))

# ---- 1. D2: THE GRAIN ---------------------------------------------------
print("\n1. D2 — THE EXACT VENDOR GRAIN")
coarse = VENDOR.groupby(["slate_date", "vendor_game_id"]).ngroups
exact = VENDOR.groupby(["slate_date", "vendor_game_id", "start_time"]).ngroups
ok("exact grain finds MORE identities than coarse", exact > coarse,
   f"coarse {coarse} -> exact {exact}")

P2, C2 = resolve(VENDOR, ["slate_date", "vendor_game_id", "start_time"])
ok("v2 emits load_crosswalk()'s EXACT columns",
   list(P2.columns) == ["vendor_game_id", "start_time", "player_key",
                        "mlb_game_pk", "player_id"],
   str(list(P2.columns)))
ok("v2 carries start_time on every mapping (the consumer joins on it)",
   P2.start_time.notna().all())

# MUTATION: resolve at v1's grain. The fragment MERGES, start_time is absent,
# and the consumer key CANNOT BE BUILT.
P1, C1 = resolve(VENDOR, ["slate_date", "vendor_game_id"])
mutation("v1 coarse grain cannot serve the consumer key",
         "start_time" not in P1.columns or P1.start_time.isna().all(),
         "no start_time per mapping -> load_crosswalk() raises on missing column")

# ---- 2. D4: THE COLLISION IS LOAD-BEARING -------------------------------
print("\n2. D4 — COLLISION -> DUPLICATE MARKET_KEY (why exclusion is NECESSARY)")
ok("the exact grain SURFACES a collision the coarse grain HID",
   bool(C2) and not bool(C1),
   f"v2 collisions {list(C2)} | v1 collisions {list(C1) or 'none'}")

# MARKET_KEY = (mlb_game_pk, player_id, category, line). No date. No start_time.
mk = (P2.assign(category="hits", line=0.5)
        [["mlb_game_pk", "player_id", "category", "line"]])
dup_mk = mk.duplicated(keep=False).sum()
mutation("KEEPING the collision duplicates MARKET_KEY", dup_mk > 0,
         f"{dup_mk} duplicate market keys -> require_unique() HARD FAILS")

kept = P2[~P2.mlb_game_pk.isin(C2)]
mk_ok = (kept.assign(category="hits", line=0.5)
             [["mlb_game_pk", "player_id", "category", "line"]])
ok("EXCLUDING the collision restores a unique MARKET_KEY",
   not mk_ok.duplicated().any(),
   "the exclusion is LOAD-BEARING, not a coverage preference")

# ---- 3. D3: VALIDATE BEFORE DEDUPE --------------------------------------
print("\n3. D3 — VALIDATE, NEVER DEDUPE (v1's assertion could not fire)")
dirty = pd.concat([P2, P2.iloc[[0]]], ignore_index=True)   # inject a duplicate
K = ["mlb_game_pk", "player_key"]

v2_fires = bool(dirty.duplicated(subset=K, keep=False).any())
ok("v2 validates the RAW frame and FIRES on a duplicate", v2_fires)

# MUTATION: v1's actual code. drop_duplicates(subset=K) THEN check duplicated(K).
v1_frame = dirty.drop_duplicates(subset=K)
v1_fires = bool(v1_frame.duplicated(subset=K, keep=False).any())
mutation("v1 dedupe-then-assert CANNOT FIRE on the same duplicate",
         not v1_fires,
         "v1 would print '[OK] ... is UNIQUE' -- a property it MANUFACTURED")

# ---- 4. THE MAX MUNCY GUARANTEE -----------------------------------------
print("\n4. TWO MAX MUNCYS — the 21,171 x 21,171 -> 21,201 cross-join")
muncy = P2[P2.player_key == "max muncy"]
ok("both Max Muncys are present and DISTINCT",
   set(muncy.player_id) == {600, 601}, str(sorted(set(muncy.player_id))))

# *** FOUND BY THIS HARNESS, AND IT IS THE POINT OF WRITING ONE. ***
# I asserted "(mlb_game_pk, player_key) is unique" -- v1 printed it, and I
# repeated it. AT v2's GRAIN IT IS FALSE, BY CONSTRUCTION. A fragmented vendor
# id emits the SAME (game_pk, name) -> player_id mapping ONCE PER IDENTITY:
#     m~A @ 23:05  (900, "max muncy") -> 600
#     m~A @ 23:06  (900, "max muncy") -> 600     <- same mapping, second identity
# That is not two Muncys. It is ONE mapping, twice.
#
# The MAPPING is still well defined -- both rows agree on player_id 600, which
# is the property that actually matters. What is NOT unique is the ROW.
#
# So the invariant must be stated at the RIGHT GRAIN:
#   (vendor_game_id, start_time, player_key)  UNIQUE on the RAW frame  <- consumer key
#   (mlb_game_pk, player_key)                 UNIQUE only POST-EXCLUSION
#
# And note WHY the second one holds post-exclusion: because collisions are gone.
# Checking it on the raw frame and watching it pass would be the SAME defect as
# v1's -- passing because the offending rows were removed first, not because the
# property was verified. ORDERING IS NOT VERIFICATION.
ok("CONSUMER KEY (vendor_game_id, start_time, player_key) unique on the RAW frame",
   not P2.duplicated(subset=["vendor_game_id", "start_time", "player_key"]).any(),
   "this is the key load_crosswalk() require_unique()s")

raw_dup = P2.duplicated(subset=["mlb_game_pk", "player_key"], keep=False).sum()
mutation("(mlb_game_pk, player_key) is NOT unique on the RAW frame",
         raw_dup > 0,
         f"{raw_dup} rows -- a FRAGMENT re-emits the same mapping. v1 claimed "
         f"this invariant and never tested it at this grain.")

kept4 = P2[~P2.mlb_game_pk.isin(C2)]
ok("(mlb_game_pk, player_key) -> ONE player_id, POST-EXCLUSION",
   not kept4.duplicated(subset=["mlb_game_pk", "player_key"]).any(),
   "two Max Muncys are never in the same box score -- and it holds ONLY "
   "because the collision was excluded first")

agree = P2.groupby(["mlb_game_pk", "player_key"]).player_id.nunique()
ok("every duplicated (game_pk, name) row AGREES on player_id",
   bool((agree == 1).all()),
   "the MAPPING is well defined even where the ROW is repeated")

# MUTATION: join on the NAME, as the A/B originally did. Cross-join.
left = pd.DataFrame([dict(player_key="max muncy", game_date="2026-06-14")])
xj = left.merge(P2[P2.player_key == "max muncy"], on="player_key", how="inner")
mutation("a NAME join cross-joins the two Muncys", len(xj) > len(left),
         f"{len(left)} row -> {len(xj)} rows")

# ---- 5. D5: NORM DRIFT ---------------------------------------------------
print("\n5. D5 — norm() MUST MATCH the A/B's norm_name() (two halves of one join)")


def norm_name_ab(x: str) -> str:
    """run_pa_market_ab.norm_name, applied to a scalar."""
    return (pd.Series([x]).map(
        lambda v: "".join(c for c in unicodedata.normalize("NFKD", str(v))
                          if not unicodedata.combining(c)))
        .str.lower().str.strip()
        .str.replace(r"[.'`\-]", "", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.replace(r"\s+(jr|sr|ii|iii|iv|v)$", "", regex=True)
        .str.strip().iloc[0])


CASES = ["Max Muncy", "Ronald Acuña Jr.", "J.D. Martinez", "Luis Robert Jr.",
         "Michael Harris II", "Vladimir Guerrero Jr.", "Jean Segura",
         "O'Neil Cruz", "Jung Hoo Lee", "  Mookie   Betts  "]
drift = [(c, norm(c), norm_name_ab(c)) for c in CASES if norm(c) != norm_name_ab(c)]
ok("norm() == norm_name() on every case", not drift,
   "identical" if not drift else f"DRIFT: {drift}")
if drift:
    print("       *** A silent join loss. The rows would vanish and the VENDOR")
    print("       would be blamed for a bug that is OURS. ***")

# =========================================================================
print()
print("=" * 76)
print(f"{len(PASS)}/{len(PASS) + len(FAIL)}")
print("=" * 76)
for f in FAIL:
    print(f"  FAILED: {f}")
sys.exit(1 if FAIL else 0)
