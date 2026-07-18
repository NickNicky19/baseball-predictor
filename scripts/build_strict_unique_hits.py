#!/usr/bin/env python3
"""
THE STRICT-UNIQUE HITS ARTIFACT — the research input for the clean A/B.

*** HITS ONLY -- BECAUSE OF THE BOOK, NOT BECAUSE OF THE MARKET. ***

An earlier version of this docstring said home runs "are not evaluable by this
pipeline and never were", because "an HR prop is a YES/NO market with no under
side, at any book". *** THAT WAS FALSE, AND I NEVER MEASURED IT. ***

MEASURED (scripts/audit_market_availability.py, Mar-Jun 2026):
    player home runs   over 65,168,992   *** under 17,503,855 ***
    TWO-SIDED AT 33 BOOKS (hard_rock, novig, bracco, pinnacle, ...)

The real reason HR yields nothing here: *** DRAFTKINGS DOES NOT POST A TWO-SIDED
HR MARKET. *** DK appears in the availability table for hits and RBIs and NOT
ONCE for home runs. The policy declares book=draftkings. That is a BOOK-SPECIFIC
GAP, not a market-structural one -- a different fact with different implications.

HR is therefore POTENTIALLY EVALUABLE at a book that posts both sides. It is out
of scope here because that is a SEPARATE, GATED decision: the under quotes must
first be verified as the SAME PRODUCT (a Yes/No "No" leg is not an over/under
under), the SAME LINE, and TIME-ALIGNED -- and pairing across books is not a
de-vig at all, it is a SPREAD BETWEEN VENUES. See the note in
src/evaluation/market_eligibility.py.

=============================================================================
WHAT THIS IS, AND WHAT IT IS NOT
=============================================================================
It is a HASHED RESEARCH ARTIFACT with the identity ambiguity ALREADY REMOVED BY
RULE, so the A/B has something clean to score.

It is NOT a change to the raw crosswalk (which stays raw), and NOT a softening of
the runner (which STILL HARD-FAILS on a duplicate MARKET_KEY). Those two must
keep their teeth: the artifact is a curated input, not a bypass.

=============================================================================
*** THE RULE: A DUPLICATE MARKET_KEY EXCLUDES *BOTH* ROWS. ***
=============================================================================
Not drop_duplicates. Not "take the fresher entry". Not "take the one with more
quotes". EVERY one of those is a JUDGMENT about which price was real, and this
system exists to keep judgment OUT OF IDENTITY.

MEASURED (June 2026, DK, T-4h, max_quote_age=90):
    scoreable                 3,657
    hard-mapped               3,572
    duplicate MARKET_KEYs        93   -> 186 rows, BOTH excluded
    STRICT-UNIQUE             3,386   (92.59% of scoreable)

    every one of those 93 keys CONFLICTS on at least one evaluator field:
        entry_p_over  median 0.0046  p95 0.0183  max 0.0499
        close_p_over  median 0.0075  p95 0.0285  max 0.0601
    and max(entry_p_over) = 0.0499 sits just ABOVE min_edge (0.04) -- so on the
    worst key, WHICH FRAGMENT YOU PICK DECIDES WHETHER IT IS A BET AT ALL.

A 7.4% cost, quantified and boring. Boring is the goal.

*** `drop_duplicates` DOES NOT APPEAR IN THIS FILE. *** A silent dedupe is data
loss wearing a tidy face, and it is how v1 of the crosswalk "proved" a uniqueness
property it had actually manufactured (it deduped on the same subset it then
asserted uniqueness on, so the assertion could not fire).

=============================================================================
FORMED ONLY *AFTER* FINAL SCOREABLE ELIGIBILITY AND HARD MAPPING
=============================================================================
Order matters, and getting it wrong is what made every earlier number
provisional:

    eligible  ->  scoreable (settled + FRESH)  ->  hard-mapped  ->  strict-unique

A duplicate computed BEFORE the freshness filter counted rows the evaluator would
have dropped: it reported 1,449 duplicate keys where the real number is 93.
*** 94% OF THEM WERE NEVER DUPLICATES. *** One fragment was stale.

=============================================================================
PROVENANCE
=============================================================================
The manifest hashes: this code, the policy, the raw crosswalk, and the excluded
keys. Three artifacts carrying the same policy sha is EVIDENCE the callers agreed
rather than a coincidence -- and it makes drift detectable AFTER the fact, which
a shared function alone cannot do.

Usage:
  python scripts/build_strict_unique_hits.py --months 2026-06
  python scripts/build_strict_unique_hits.py --months 2026-03 2026-04 2026-05 2026-06
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.market_eligibility import (          # noqa: E402
    MARKET_MAP, STATUS_ABSENT, STATUS_STALE, assert_no_leakage, eligibility_sql,
    load_policy, parquet_source, scoreable,
)

MARKET_KEY = ["mlb_game_pk", "player_id", "category", "line"]
CONSUMER_KEY = ["vendor_game_id", "start_time", "player_key"]
FRAGMENT_KEY = ["vendor_game_id", "start_time"]
# Every field the evaluator consumes. A subset comparison would pass on a bug
# that preserves the subset and changes the rest -- an entry-only check is blind
# to a moved CLOSE, and CLV is (close - entry).
EVAL_FIELDS = ["entry_p_over", "close_p_over", "entry_overround", "entry_age_min",
               "settlement_present"]


def sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def norm(s: object) -> str:
    """Byte-identical to build_crosswalk_v3.norm and run_market_ab.norm_name."""
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s))
                if not unicodedata.combining(c))
    s = s.lower().strip()
    for ch in ".'`-":
        s = s.replace(ch, "")
    return " ".join(p for p in s.split()
                    if p not in ("jr", "sr", "ii", "iii", "iv", "v"))


def attach_canonical_dates(mapped: pd.DataFrame, fragments_path: str | Path) -> pd.DataFrame:
    """Attach the MLB official date already proven by the hard crosswalk.

    ``market_date`` is derived from the vendor timestamp.  It is useful for a
    pre-mapping funnel, but it is *not* a canonical game date: late games can
    cross midnight UTC.  The crosswalk vote already recorded the authoritative
    ``official_date`` for each independently mapped fragment in
    ``crosswalk_fragments.csv``.  Reusing that evidence is a hard handoff, not
    a tolerant time join or a second schedule inference.
    """
    out_source = mapped.copy()
    if "start_time" not in out_source.columns:
        raise ValueError("mapped market rows lack start_time for the canonical-date bridge")
    out_source["start_time"] = pd.to_datetime(out_source["start_time"], utc=True)

    path = Path(fragments_path)
    if not path.is_file():
        raise ValueError(
            f"canonical-date bridge missing: {path}. A strict artifact may not "
            "label vendor market_date as an official MLB date."
        )
    fragments = pd.read_csv(path)
    required = [*FRAGMENT_KEY, "mlb_game_pk", "official_date", "outcome"]
    missing = [column for column in required if column not in fragments.columns]
    if missing:
        raise ValueError(f"{path}: missing canonical-date columns {missing}")
    fragments["start_time"] = pd.to_datetime(fragments["start_time"], utc=True)
    if fragments.duplicated(FRAGMENT_KEY).any():
        dup = fragments[fragments.duplicated(FRAGMENT_KEY, keep=False)]
        raise ValueError(
            f"{path}: duplicate crosswalk fragment identity. Canonical date is "
            f"ambiguous:\n{dup[required].head(10).to_string(index=False)}"
        )

    bridge = fragments.loc[
        fragments["outcome"].eq("mapped"),
        [*FRAGMENT_KEY, "mlb_game_pk", "official_date"],
    ].copy()
    bridge["mlb_game_pk"] = pd.to_numeric(bridge["mlb_game_pk"], errors="coerce")
    bridge["official_date"] = pd.to_datetime(
        bridge["official_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    if bridge[["mlb_game_pk", "official_date"]].isna().any().any():
        raise ValueError(
            f"{path}: a mapped fragment lacks MLB game_pk or official_date. "
            "Do not infer it from the vendor timestamp."
        )
    if bridge.duplicated(FRAGMENT_KEY).any():
        raise ValueError(f"{path}: mapped fragment bridge is not unique")
    n_dates = bridge.groupby("mlb_game_pk")["official_date"].nunique()
    if (n_dates > 1).any():
        bad = n_dates[n_dates > 1].index.tolist()[:10]
        raise ValueError(
            f"{path}: MLB game_pk maps to multiple official dates {bad}. "
            "Canonical identity has not been established."
        )

    out = out_source.merge(
        bridge.rename(columns={"mlb_game_pk": "fragment_mlb_game_pk"}),
        on=FRAGMENT_KEY,
        how="left",
        validate="many_to_one",
    )
    if out[["fragment_mlb_game_pk", "official_date"]].isna().any().any():
        sample = out.loc[
            out["official_date"].isna(), [*FRAGMENT_KEY, "mlb_game_pk"]
        ].head(10)
        raise ValueError(
            "A hard-mapped market row has no canonical official date in the "
            f"crosswalk bridge:\n{sample.to_string(index=False)}"
        )
    if (out["mlb_game_pk"].astype("int64") !=
            out["fragment_mlb_game_pk"].astype("int64")).any():
        bad = out.loc[
            out["mlb_game_pk"].astype("int64") != out["fragment_mlb_game_pk"].astype("int64"),
            [*FRAGMENT_KEY, "mlb_game_pk", "fragment_mlb_game_pk", "official_date"],
        ].head(10)
        raise ValueError(
            "The consumer crosswalk and its canonical-date bridge disagree on "
            f"MLB game identity:\n{bad.to_string(index=False)}"
        )
    return out.drop(columns="fragment_mlb_game_pk").rename(
        columns={"official_date": "official_game_date"}
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/market/smartstake")
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--policy", default="config/ab_policy.json")
    ap.add_argument("--crosswalk", default="data/market/v3/crosswalk_players.csv")
    ap.add_argument(
        "--fragments", default="data/market/v3/crosswalk_fragments.csv",
        help=("crosswalk fragment audit containing the hard-mapped MLB official "
              "date; required to make the strict date universe canonical"),
    )
    ap.add_argument("--out", default="data/market/v3/strict_unique_hits")
    args = ap.parse_args(argv)

    vals, policy_sha, research_only, unapproved = load_policy(args.policy)
    book, hours, maxage = vals["book"], vals["entry_hours"], vals["max_quote_age"]

    print("=" * 96)
    print("STRICT-UNIQUE HITS ARTIFACT")
    print("=" * 96)
    print(f"  policy {policy_sha}   book={book}  T-{hours}h  max_quote_age={maxage}min")
    print(f"  markets: {list(MARKET_MAP.values())}   *** HITS ONLY ***")
    print()
    print("  HOME RUNS ARE ABSENT BECAUSE OF THE BOOK, NOT THE MARKET.")
    print("  MEASURED: HR is two-sided at 33 books (17.5M under quotes). It is")
    print("  DraftKings that does not post a two-sided HR market -- and the policy")
    print("  declares book=draftkings. HR is potentially evaluable elsewhere; that")
    print("  is a separate, gated decision (same product? same line? time-aligned?")
    print("  -- and pairing across books is a SPREAD, not a de-vig).")
    print()
    print("  *** A DUPLICATE MARKET_KEY EXCLUDES BOTH ROWS. *** Never")
    print("  drop_duplicates, never 'take the fresher'. Choosing is a JUDGMENT.")
    print()

    # ---- 1. ELIGIBLE ------------------------------------------------------
    raw = duckdb.sql(eligibility_sql(
        parquet_source(args.root, args.months), book, hours, maxage)).df()
    assert_no_leakage(raw)                # negative age is LEAKAGE, not a status
    raw["start_time"] = pd.to_datetime(raw.start_time, utc=True)
    raw["player_key"] = raw.player.map(norm)
    raw["category"] = raw.market.map(MARKET_MAP)
    raw["market_date"] = pd.to_datetime(raw.market_date).dt.strftime("%Y-%m-%d")

    n_elig = len(raw)
    n_absent = int((raw.status == STATUS_ABSENT).sum())
    n_stale = int((raw.status == STATUS_STALE).sum())

    # ---- 2. SCOREABLE (settled + FRESH) ----------------------------------
    SC = scoreable(raw).copy()
    n_score = len(SC)

    # ---- 3. HARD-MAPPED --------------------------------------------------
    xw = pd.read_csv(args.crosswalk)
    xw["start_time"] = pd.to_datetime(xw.start_time, utc=True)
    if xw.duplicated(CONSUMER_KEY).any():
        print("FATAL: the crosswalk's consumer key is not unique.", file=sys.stderr)
        return 2
    M = SC.merge(xw, on=CONSUMER_KEY, how="inner", validate="many_to_one")
    M = attach_canonical_dates(M, args.fragments)
    n_mapped = len(M)
    n_unmapped = n_score - n_mapped

    # ---- 4. STRICT-UNIQUE. *** BOTH ROWS OF A DUPLICATE GO. *** ----------
    dup_mask = M.duplicated(MARKET_KEY, keep=False)      # keep=False => BOTH
    EX = M[dup_mask].copy()
    U = M[~dup_mask].copy()
    n_strict = len(U)

    print("=" * 96)
    print("THE FUNNEL")
    print("=" * 96)
    print(f"  eligible                 {n_elig:7,d}")
    print(f"    settlement_absent      {n_absent:7,d}   (never scoreable)")
    print(f"    stale                  {n_stale:7,d}   (the evaluator drops these)")
    print(f"  SCOREABLE                {n_score:7,d}")
    print(f"    crosswalk-unmapped     {n_unmapped:7,d}")
    print(f"  HARD-MAPPED              {n_mapped:7,d}")
    print(f"    duplicate MARKET_KEY   {len(EX):7,d}   "
          f"({EX.groupby(MARKET_KEY).ngroups if len(EX) else 0} keys, BOTH rows excluded)")
    print(f"  *** STRICT-UNIQUE        {n_strict:7,d}   "
          f"({n_strict / max(n_score, 1):.2%} of scoreable) ***")

    # ---- the hard invariant ----------------------------------------------
    if U.duplicated(MARKET_KEY).any():
        print("\nFATAL: the strict-unique artifact CONTAINS a duplicate "
              "MARKET_KEY. That is impossible by construction; the artifact is "
              "not trustworthy.", file=sys.stderr)
        return 1
    print(f"\n  [OK] ZERO duplicate MARKET_KEY in the artifact "
          f"(the invariant it exists to guarantee)")

    # ---- what was excluded, and how far apart the prices were --------------
    if len(EX):
        print()
        print("=" * 96)
        print("EXCLUDED — every duplicate MARKET_KEY, BOTH fragments named")
        print("=" * 96)
        g = EX.groupby(MARKET_KEY)
        conflicting = int((g[EVAL_FIELDS].nunique() > 1).any(axis=1).sum())
        print(f"  {g.ngroups} keys, {len(EX)} rows")
        print(f"  keys where at least one EVALUATOR field differs: {conflicting}"
              f"/{g.ngroups}")
        for f in ("entry_p_over", "close_p_over"):
            spans = g[f].agg(lambda s: float(s.max() - s.min()))
            nz = spans[spans > 1e-12]
            if len(nz):
                print(f"    {f:16s} median {nz.median():.6f}   "
                      f"p95 {nz.quantile(.95):.6f}   max {nz.max():.6f}")
        print(f"\n  min_edge = {vals['min_edge']}. Where the entry gap exceeds it,")
        print(f"  WHICH FRAGMENT YOU PICK DECIDES WHETHER THE ROW IS A BET AT ALL.")
        print(f"  That is why choosing is a judgment, and why BOTH rows go.")

    # ---- BY OFFICIAL DATE. Post-mapping, so the official date governs. -----
    # (A pre-mapping row has NO verified official date. Inventing one would mean
    # trusting the vendor's start_time -- the field this whole investigation
    # dismantled.)
    print()
    print("=" * 96)
    print("BY OFFICIAL DATE  (post-mapping: the canonical date governs)")
    print("=" * 96)
    by_date = pd.DataFrame({
        "hard_mapped": M.groupby("official_game_date").size(),
        "excluded_dup": EX.groupby("official_game_date").size() if len(EX) else 0,
        "strict_unique": U.groupby("official_game_date").size(),
    }).fillna(0).astype(int)
    by_date["pct"] = (by_date.strict_unique / by_date.hard_mapped * 100).round(1)
    print("  " + by_date.to_string().replace("\n", "\n  "))

    # ---- emit --------------------------------------------------------------
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = [*MARKET_KEY, "vendor_game_id", "start_time", "player", "player_key",
            "market_date", "official_game_date", *EVAL_FIELDS]
    U[cols].to_csv(out.with_suffix(".csv"), index=False)
    if len(EX):
        EX[cols].to_csv(out.with_name(out.name + "_excluded.csv"), index=False)
    by_date.to_csv(out.with_name(out.name + "_by_date.csv"))

    artifact_sha = sha(out.with_suffix(".csv"))
    manifest = dict(
        _comment=(
            "STRICT-UNIQUE HITS research artifact. HITS ONLY -- because of the "
            "BOOK, not the market: HR is two-sided at 33 books (MEASURED: 17.5M "
            "under quotes), but DRAFTKINGS does not post a two-sided HR market, "
            "and the policy declares book=draftkings. HR is potentially evaluable "
            "at another book; that is a separate, gated decision (is the `under` "
            "the same PRODUCT? the same LINE? time-aligned? -- and pairing across "
            "books is a SPREAD BETWEEN VENUES, not a de-vig). "
            "A duplicate MARKET_KEY EXCLUDES BOTH ROWS: never drop_duplicates, "
            "never 'take the fresher', because choosing which price was real is a "
            "JUDGMENT and this system exists to keep judgment out of identity. "
            "Formed ONLY AFTER final scoreable eligibility (settled + fresh) and "
            "hard mapping -- computing duplicates before the freshness filter "
            "reported 1,449 keys where the real number is 93, because 94% of them "
            "had one STALE fragment the evaluator would never have scored. The raw "
            "crosswalk is unchanged and the runner still HARD-FAILS on duplicates; "
            "this artifact is a curated input, not a bypass."),
        built_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        months=args.months, book=book, entry_hours=hours, max_quote_age=maxage,
        markets=list(MARKET_MAP.values()),
        hashes=dict(
            code=sha(__file__),
            policy=policy_sha,
            crosswalk=sha(args.crosswalk),
            artifact=artifact_sha,
        ),
        funnel=dict(
            eligible=n_elig, settlement_absent=n_absent, stale=n_stale,
            scoreable=n_score, crosswalk_unmapped=n_unmapped,
            hard_mapped=n_mapped,
            duplicate_rows_excluded=int(len(EX)),
            duplicate_keys_excluded=int(EX.groupby(MARKET_KEY).ngroups) if len(EX) else 0,
            strict_unique=n_strict,
            strict_unique_pct_of_scoreable=round(n_strict / max(n_score, 1), 4),
        ),
        # The list of excluded KEYS for the manifest. Built WITHOUT
        # drop_duplicates -- this file BANS that call, and a file that bans a call
        # and then makes it is the unenforced-declaration pattern that has bitten
        # this project repeatedly. The check is mechanical, not a matter of
        # judgment about whether a given call is "harmless": a benign-looking
        # dedupe is indistinguishable from a harmful one at a glance, and that is
        # exactly how v1's dedupe-then-assert slipped through.
        excluded_market_keys=(
            [dict(zip(MARKET_KEY, k)) for k in
             sorted({tuple(r) for r in EX[MARKET_KEY].to_numpy()})]
            if len(EX) else []),
        official_date_universe=sorted(U.official_game_date.unique().tolist()),
        policy_research_only=research_only,
        policy_unapproved=unapproved,
    )
    mpath = out.with_name(out.name + "_manifest.json")
    mpath.write_text(json.dumps(manifest, indent=2, default=str) + "\n",
                     encoding="utf-8")

    print()
    print(f"wrote {out.with_suffix('.csv')}        ({n_strict:,} rows)  "
          f"sha {artifact_sha}")
    if len(EX):
        print(f"      {out.with_name(out.name + '_excluded.csv')}  ({len(EX):,} rows)")
    print(f"      {out.with_name(out.name + '_by_date.csv')}")
    print(f"      {mpath}")
    print()
    print("=" * 96)
    print(f"  DATE UNIVERSE: {len(manifest['official_date_universe'])} dates   "
          f"{manifest['official_date_universe'][0]} .. "
          f"{manifest['official_date_universe'][-1]}")
    print("  *** This manifest is what the fresh reconstruction must cover. ***")
    if research_only:
        print()
        print(f"  RESEARCH-ONLY. Unapproved policy parameters: {', '.join(unapproved)}")
        print("  The A/B may establish a baseline. It MAY NOT claim the +0.10 bar")
        print("  and MAY NOT authorize betting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
