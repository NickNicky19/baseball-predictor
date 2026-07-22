#!/usr/bin/env python3
"""
THE DUPLICATE MARKET_KEYs — harmless re-post, or a REAL conflicting price path?

*** THIS MEASURES. IT DECIDES NOTHING. NO MERGE. NO SELECTION. ***

=============================================================================
WHAT WAS MEASURED (build_crosswalk_v3.py, June 2026, DK, T-4h)
=============================================================================
    eligible + settled selections           5,569
    hard-mapped                             5,446   (97.8%)
    *** ELIGIBLE ROWS SHARING A MARKET_KEY  2,898 ***

    multi-fragment game_pks                   170
      extras are non-eligible STUBS            71   <- harmless
      extras are ELIGIBLE                      99   <- *** NOT harmless ***

Typical shape:
    m~f944130d  22:45  822723  657041  hits  0.5
    m~f944130d  22:46  822723  657041  hits  0.5     <- SAME id, +1 minute

Same vendor_game_id, one minute apart, and BOTH clear the T-4h horizon. This is
NOT a stub.

*** I PREDICTED ZERO DUPLICATES AND I WAS WRONG. *** The fragment diagnostic
measured eligible_key_relation on FOUR PAIRS FROM ONE DATE (20 NEITHER_ELIGIBLE,
8 OVERLAPPING, 0 DISJOINT) and I generalised "the extras are stubs" to the whole
month. Across June, MORE THAN HALF ARE NOT. Fourth time today I asserted a
pattern from a partial view.

WHAT IT DOES *NOT* MEAN: that v2 was right. v2 excluded ALL 170 -- including the
71 that duplicate nothing -- and it excluded them BEFORE anyone could tell which
was which. v3 is what REVEALED the distinction. The guard is working: it let the
data speak, and the data says ~99 games have a real problem.

=============================================================================
THE QUESTION, AND WHY IT MUST BE ASKED BEFORE ANY POLICY
=============================================================================
run_market_ab.require_unique(MARKET_KEY) will HARD-FAIL on these, correctly: two
fragments each producing a scoreable row for one (game_pk, player_id, category,
line) is a GENUINE AMBIGUITY ABOUT WHICH PRICE TO USE, and the runner must not
guess.

But there are two very different worlds behind that, and they have OPPOSITE
implications:

  EXACT_DUPLICATE   every evaluator-consumed field is EXACTLY equal. The vendor
                    re-posted the same quotes under a second timestamp. The
                    ambiguity is COSMETIC -- either row gives the identical
                    score. A deterministic, equality-CHECKED normalisation could
                    be considered later (with a mutation proving that ONE changed
                    price hard-fails).

  CONFLICTING_PRICE any evaluator-consumed field DIFFERS. The two fragments are
                    two DIFFERENT PRICE PATHS for one selection. *** CHOOSING
                    EITHER ONE IS A JUDGMENT *** -- and this system exists to
                    remove judgment from identity. NO MERGE, NO ARBITRARY
                    SELECTION. Quantify the coverage cost and STOP for a separate
                    decision.

=============================================================================
EVERY FIELD THE EVALUATOR CONSUMES. NOT A SUBSET.
=============================================================================
A comparison on entry_p_over alone would pass on a bug that preserves the entry
price and changes the CLOSE -- and CLV is (close - entry). That is the same
identity-but-not-value failure that has now bitten this project four times.

So: EVERY field that can reach a metric.

    entry_p_over        -> edge          (p_model - entry)
    close_p_over        -> CLV           (close - entry)
    entry_overround     -> a data-quality control
    entry_age_min       -> the staleness filter (ctrl4's leakage guard)
    settlement_present  -> whether the row is scoreable AT ALL

Exact equality. NOT rounded -- a rounded comparison would hide precisely the
drift this exists to catch.

=============================================================================
SANITY -- STATED BEFORE THE RUN (rule 7)
=============================================================================
  If the +1-minute twin is a pure re-post of the same book feed, I EXPECT
  EXACT_DUPLICATE on every field.

  *** BUT I PREDICTED ZERO DUPLICATES AN HOUR AGO AND WAS WRONG BY 2,898. ***
  So that expectation is worth very little, and I state it only so it can be
  falsified rather than quietly abandoned. I have NOT checked. I have no
  reliable prior.

  A MIXED result -- some keys exact, some conflicting -- means BOTH things are
  happening and each needs its own verdict. Do not average them.

Usage:
  python scripts/audit_duplicate_keys.py --months 2026-06
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.market_eligibility import (          # noqa: E402
    MARKET_MAP, STATUS_STALE, assert_no_leakage, eligibility_sql, load_policy,
    parquet_source, scoreable,
)

MK = ["mlb_game_pk", "player_id", "category", "line"]

# *** EVERY FIELD THE EVALUATOR CONSUMES. *** A subset would pass on a bug that
# preserves the subset and changes the rest.
EVAL_FIELDS = [
    "entry_p_over",       # -> edge
    "close_p_over",       # -> CLV
    "entry_overround",    # -> the overround control
    "entry_age_min",      # -> the staleness filter / ctrl4 leakage guard
    "settlement_present",  # -> scoreable at all?
]


def norm(s: object) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s))
                if not unicodedata.combining(c))
    s = s.lower().strip()
    for ch in ".'`-":
        s = s.replace(ch, "")
    return " ".join(p for p in s.split()
                    if p not in ("jr", "sr", "ii", "iii", "iv", "v"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/market/smartstake")
    ap.add_argument("--months", nargs="+", default=["2026-06"])
    ap.add_argument("--policy", default="config/ab_policy.json")
    ap.add_argument("--crosswalk", default="data/market/v3/crosswalk_players.csv")
    ap.add_argument("--out", default="data/market/v3/duplicate_keys.json")
    args = ap.parse_args(argv)

    print("=" * 96)
    print("DUPLICATE MARKET_KEY AUDIT — exact re-post, or a REAL price conflict?")
    print("=" * 96)
    vals, policy_sha, _, _ = load_policy(args.policy)
    book = vals["book"]
    entry_hours = vals["entry_hours"]
    max_quote_age = vals["max_quote_age"]
    print("  MEASUREMENT ONLY. No merge. No selection. No policy.")
    print(f"  evaluator-consumed fields: {EVAL_FIELDS}")
    print(f"  policy {policy_sha}   book={book}  T-{entry_hours}h  "
          f"max_quote_age={max_quote_age}min")
    print()

    # *** THE SHARED FUNCTION, AND THE *FINAL SCOREABLE* SUBSET. ***
    # The first run of this audit used the PRE-STALE frame (settlement only), so
    # it counted duplicate pairs the evaluator would never see. That is why the
    # 2,898 / 1,449 figures were provisional -- and the tell was in its OWN
    # output: entry_age_min differed on 100% of duplicate keys, MEDIAN 582
    # MINUTES. I read that as a finding about the vendor. The first thing it
    # actually said was that MY INSTRUMENT WAS MISCALIBRATED.
    RAW = duckdb.sql(eligibility_sql(
        parquet_source(args.root, args.months), book, entry_hours,
        max_quote_age)).df()
    assert_no_leakage(RAW)
    n_stale = int((RAW.status == STATUS_STALE).sum())
    E = scoreable(RAW).copy()
    E["start_time"] = pd.to_datetime(E.start_time, utc=True)
    E["player_key"] = E.player.map(norm)
    E["category"] = E.market.map(MARKET_MAP)
    E["market_date"] = pd.to_datetime(E.market_date).dt.strftime("%Y-%m-%d")
    print(f"[stale]    {n_stale:,} rows dropped as STALE before any duplicate is "
          f"counted")

    xw = pd.read_csv(args.crosswalk)
    xw["start_time"] = pd.to_datetime(xw.start_time, utc=True)
    J = E.merge(xw, on=["vendor_game_id", "start_time", "player_key"],
                how="inner", validate="many_to_one")
    print(f"[universe] {len(J):,} SCOREABLE (settled + fresh), hard-mapped")

    dup = J[J.duplicated(MK, keep=False)].copy()
    if dup.empty:
        print("\n  No duplicate MARKET_KEY. Nothing to audit.")
        return 0
    n_keys = dup.groupby(MK).ngroups
    print(f"[dupes]    {len(dup):,} rows across {n_keys:,} duplicate MARKET_KEYs")

    # =====================================================================
    # FIELD BY FIELD. Exact equality within each key group.
    # =====================================================================
    g = dup.groupby(MK)
    per_field = {f: int((g[f].nunique() > 1).sum()) for f in EVAL_FIELDS}
    # a key is EXACT only if EVERY evaluator field is identical across its rows
    nuniq = g[EVAL_FIELDS].nunique()
    exact_mask = (nuniq <= 1).all(axis=1)
    n_exact = int(exact_mask.sum())
    n_conflict = int((~exact_mask).sum())

    print()
    print("=" * 96)
    print("WHICH FIELDS DIFFER? (per duplicate MARKET_KEY)")
    print("=" * 96)
    for f in EVAL_FIELDS:
        n = per_field[f]
        bar = "*" * min(50, int(50 * n / max(n_keys, 1)))
        print(f"  {f:20s} {n:6,d}/{n_keys:,}  ({n/max(n_keys,1):6.1%})  {bar}")

    print()
    print(f"  EXACT_DUPLICATE   : {n_exact:6,d}/{n_keys:,}  "
          f"({n_exact/max(n_keys,1):6.1%})   every evaluator field identical")
    print(f"  CONFLICTING_PRICE : {n_conflict:6,d}/{n_keys:,}  "
          f"({n_conflict/max(n_keys,1):6.1%})   at least one field differs")

    # ---- MAGNITUDE. "differs" is not the same as "differs materially". ----
    # A field that differs in the 12th decimal is a float artifact. One that
    # differs by 0.05 in entry_p_over is a different bet. STATE THE SIZE.
    if n_conflict:
        print()
        print("  HOW FAR APART, where they differ:")
        for f in ("entry_p_over", "close_p_over", "entry_age_min",
                  "entry_overround"):
            spans = g[f].agg(lambda s: float(np.nanmax(s) - np.nanmin(s)))
            nz = spans[spans > 1e-12]
            if len(nz):
                print(f"    {f:18s} n={len(nz):5,d}   "
                      f"median {nz.median():.6f}   p95 {nz.quantile(.95):.6f}   "
                      f"max {nz.max():.6f}")

    # =====================================================================
    # BY MARKET, BY DATE, BY GAME. Counts of KEYS, not rows.
    # =====================================================================
    key_tab = (dup.groupby(MK)
                  .agg(rows=("entry_p_over", "size"),
                       official_date=("market_date", "first"),
                       fragments=("vendor_game_id", "nunique"),
                       starts=("start_time", "nunique"))
                  .assign(exact=exact_mask)
                  .reset_index())

    print()
    print("=" * 96)
    print("BY MARKET  (duplicate KEYS, not rows)")
    print("=" * 96)
    t = (key_tab.groupby("category")
                .agg(keys=("exact", "size"), exact=("exact", "sum")))
    t["conflicting"] = t["keys"] - t["exact"]
    print("  " + t.to_string().replace("\n", "\n  "))

    print()
    print("BY GAME  (mlb_game_pk)")
    pg = (key_tab.groupby("mlb_game_pk")
                 .agg(keys=("exact", "size"), exact=("exact", "sum")))
    pg["conflicting"] = pg["keys"] - pg["exact"]
    print(f"  {len(pg):,} games carry a duplicate key")
    print(f"    wholly EXACT       : {int((pg.conflicting == 0).sum()):,}")
    print(f"    any CONFLICT       : {int((pg.conflicting > 0).sum()):,}")

    print()
    print("BY OFFICIAL DATE")
    pd_tab = (key_tab.groupby("official_date")
                     .agg(keys=("exact", "size"), exact=("exact", "sum")))
    pd_tab["conflicting"] = pd_tab["keys"] - pd_tab["exact"]
    print("  " + pd_tab.to_string().replace("\n", "\n  "))

    # ---- the fragment shape: is it always the +/-1 min twin? --------------
    # *** SECONDS, NOT INTEGER MINUTES. ***
    # The first version floored to whole minutes, so a sub-minute separation
    # displayed as "0" -- and all 93 keys reported "0 minutes apart" when their
    # start_times differ by SECONDS. I read an artifact of my own binning as a
    # fact about the data. That is the same family as the entry-age
    # miscalibration: THE INSTRUMENT'S RESOLUTION WAS THE FINDING.
    dup["_sec"] = dup.start_time.astype("int64") // 1_000_000_000
    gap = dup.groupby(MK)._sec.agg(lambda s: int(s.max() - s.min()))
    print()
    print("FRAGMENT SEPARATION (SECONDS between the two start_times)")
    print("  " + gap.value_counts().sort_index().head(12).to_string()
          .replace("\n", "\n  "))
    print(f"  min {int(gap.min())}s   median {gap.median():.0f}s   "
          f"max {int(gap.max())}s")
    print("  *** A TIME PATTERN IS AN OBSERVATION, NOT A MECHANISM (rule 10). ***")
    print("  It is recorded. Nothing is inferred from it, and no merge is ever")
    print("  performed on a clock.")

    # ---- examples of BOTH kinds -------------------------------------------
    if n_conflict:
        bad_keys = key_tab[~key_tab.exact].head(3)
        print()
        print("=" * 96)
        print("EXAMPLES — CONFLICTING")
        print("=" * 96)
        for r in bad_keys.itertuples():
            sel = dup[(dup.mlb_game_pk == r.mlb_game_pk)
                      & (dup.player_id == r.player_id)
                      & (dup.category == r.category)
                      & (dup.line == r.line)]
            print(f"\n  ({r.mlb_game_pk}, {r.player_id}, {r.category}, {r.line})")
            print("  " + sel[["vendor_game_id", "start_time", *EVAL_FIELDS]]
                  .to_string(index=False).replace("\n", "\n  "))

    # =====================================================================
    print()
    print("=" * 96)
    print("VERDICT")
    print("=" * 96)
    rc = 0
    if n_conflict == 0:
        print("  *** EXACT_DUPLICATE on EVERY key. ***")
        print("  The vendor re-posted the SAME quotes under a second timestamp.")
        print("  Either row yields the IDENTICAL score, so the ambiguity is")
        print("  COSMETIC.")
        print()
        print("  *** THIS IS EVIDENCE, NOT A POLICY. *** A deterministic,")
        print("  EQUALITY-CHECKED normalisation could now be CONSIDERED -- and it")
        print("  would need its own mutation proving that ONE CHANGED PRICE")
        print("  HARD-FAILS. 'They were all equal in June' is not a licence to")
        print("  collapse rows without checking equality EVERY TIME.")
    elif n_exact == 0:
        print("  *** CONFLICTING_PRICE on EVERY key. ***")
        print("  These are TWO DIFFERENT PRICE PATHS for one selection.")
        print("  *** CHOOSING EITHER IS A JUDGMENT, and this system exists to")
        print("  REMOVE judgment from identity. NO MERGE. NO ARBITRARY SELECTION. ***")
        print()
        print(f"  COVERAGE COST if these are excluded: {len(dup):,} rows "
              f"({len(dup)/max(len(J),1):.1%} of the mapped universe).")
        print("  STOP. This is a separate decision.")
        rc = 1
    else:
        print("  *** MIXED. BOTH THINGS ARE HAPPENING. ***")
        print(f"    EXACT       {n_exact:,} keys -- a cosmetic re-post")
        print(f"    CONFLICTING {n_conflict:,} keys -- a real price ambiguity")
        print()
        print("  DO NOT AVERAGE THEM and do not write ONE rule for both. The exact")
        print("  ones could be normalised with an equality check; the conflicting")
        print("  ones CANNOT be resolved without a judgment, and that is exactly")
        print("  what this system refuses to make.")
        print()
        print(f"  COVERAGE COST if the CONFLICTING keys are excluded: "
              f"{int(key_tab[~key_tab.exact].rows.sum()):,} rows.")
        rc = 1

    print()
    print("  *** AND NOTE WHAT NONE OF THIS ESTABLISHES: WHY. *** (rule 10)")
    print("  A separation of one minute is a CLOCK, not a mechanism. Nothing here")
    print("  says why the vendor emits two fragments, and nothing is inferred.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    key_tab.to_csv(out.with_suffix(".csv"), index=False)
    json.dump(dict(
        _comment=(
            "Duplicate MARKET_KEY audit. Compares EVERY evaluator-consumed field "
            "(entry_p_over, close_p_over, entry_overround, entry_age_min, "
            "settlement_present) EXACTLY within each duplicate key. A subset "
            "comparison would pass on a bug that preserves the subset and changes "
            "the rest -- e.g. an entry-only check is blind to a moved CLOSE, and "
            "CLV is (close - entry). EXACT_DUPLICATE means the ambiguity is "
            "cosmetic; CONFLICTING_PRICE means two real price paths, where "
            "choosing either is a JUDGMENT this system refuses to make. "
            "MEASUREMENT ONLY: no merge, no selection, no policy. The mechanism is "
            "NOT established (rule 10) and a time separation is a clock, not a "
            "cause."),
        months=args.months, book=book, entry_hours=entry_hours,
        max_quote_age=max_quote_age, policy_sha=policy_sha,
        stale_rows_dropped=n_stale,
        mapped_universe=int(len(J)),
        duplicate_rows=int(len(dup)), duplicate_keys=int(n_keys),
        exact_duplicate_keys=n_exact, conflicting_keys=n_conflict,
        fields_differing=per_field,
        conflicting_rows=int(key_tab[~key_tab.exact].rows.sum()),
        games_with_duplicates=int(len(pg)),
    ), out.open("w", encoding="utf-8"), indent=2, default=str)
    print(f"\nwrote {out}")
    print(f"      {out.with_suffix('.csv')}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
