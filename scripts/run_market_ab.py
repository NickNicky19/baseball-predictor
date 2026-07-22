#!/usr/bin/env python3
"""
MARKET A/B — capture, graded against MLB OFFICIAL ACTUALS. Per market. Audited.

*** THE VENDOR'S NUMERIC `result` NEVER ENTERS THE SCORE. ***
It is read for EXACTLY ONE PURPOSE: settlement presence. Its VALUE is never used.

=============================================================================
WHY EVERY PRIOR CAPTURE NUMBER IS RETIRED
=============================================================================
The old code graded with:
        won_over = (j.result > j.line)              # THE VENDOR'S NUMBER

MEASURED (scripts/audit_result_integrity.py, June 2026, DK, T-4h):
    eligible A/B selections      5,446
    DISAGREE WITH MLB OFFICIAL     896      *** 16.5% ***

ONE SCORED ROW IN SIX WAS GRADED AGAINST A WRONG TARGET. Every capture figure
this project has produced (+0.002 frozen, +0.040 PA fix, +0.041 K/BB) sat on
that target. They are RETIRED -- not "improved from", not a baseline.

AND IT IS NOT AN IDENTITY ARTIFACT:
    multi-fragment game_id   18.4% wrong
    single-fragment game_id   6.7% wrong     <- CLEAN games. One start_time.
Hard identity is still ESSENTIAL (it is how prices attach to games) but it
CANNOT repair the target. Also measured: ZERO games are wholly bad, and the
error is SYMMETRIC (47.3% over / 52.7% under). A "graded against the wrong
event" story is DEAD, and the mechanism is NOT ESTABLISHED (rule 10).

=============================================================================
THE TARGET CONTRACT (the mutation tests enforce it; --self-test)
=============================================================================
  1. SmartStake supplies PRICES and SETTLEMENT PRESENCE. Nothing else.
  2. The SCORED TARGET is canonical MLB official, on (mlb_game_pk, player_id,
     category). Exactly one per key. Missing or duplicate => HARD FAIL, never
     imputed.
  3. SETTLEMENT ABSENCE IS AN EXCLUSION, NOT AN OUTCOME. `result IS NULL`
     conflates "not settled" and "player inactive/void". We do not know which,
     so we never infer an outcome from it.
  4. HR and hits REPORTED SEPARATELY. No pooled capture -- an aggregate can hide
     a losing market.

=============================================================================
SETTLEMENT PRESENCE IS A PROPERTY OF THE SELECTION, NOT OF A QUOTE
=============================================================================
The old code filtered `result IS NOT NULL` AT THE QUOTE-SNAPSHOT LEVEL, INSIDE
the CTE that arg_max() then picks the entry price from. If a selection carried
MIXED null/non-null snapshots, the filter would DROP the quote arg_max would have
chosen and take an EARLIER one -- silently changing the entry price.

MEASURED (scripts/check_mixed_nullness.py, June, DK):
    quote-sides examined  43,339
    all graded            36,814
    none graded            6,525
    *** MIXED                  0 ***

*** SO THE DEFECT IS LATENT, NOT ACTIVE. NO PRICE EVER MOVED. ***
It is fixed here anyway -- wrong in principle is wrong, and a future partition
could trip it -- but this fix REPAIRS NOTHING and CHANGES NO NUMBER. Saying
otherwise would let a real 16.5% corruption hide behind a bookkeeping tidy-up.

The correct semantics, defined ONCE and consumed TWICE (exclusion AND reporting,
so the two cannot drift apart):

    settlement_present := bool_or(result IS NOT NULL)
                          OVER (game_id, start_time, player, market, line)
    entry/close        := built from ALL PREGAME QUOTES, ungated by `result`

=============================================================================
MUTATION TESTS -- rule 4. A test that passes on broken AND fixed code guards
nothing. Every one below was RUN against the broken code and SEEN to fail.
=============================================================================
  M1  Corrupt EVERY non-null vendor `result`, preserving null/non-null status.
      ==> every metric IDENTICAL.
  M2  *** THE REAL ONE. *** An in-memory DuckDB fixture through the PRODUCTION
      SQL -- not a reimplementation (rule 5: if you mock the thing you are
      testing, you measure nothing).
      M2a  all-null selection -> leaves scoring, counted settlement-absent.
      M2b  MIXED selection: ONE non-null quote establishes presence, and a LATER
           NULL quote must STILL BE SELECTABLE as the entry pick.
           *** THIS IS THE DISCRIMINATING TEST. *** All-null exclusion passes
           under the OLD quote-level filter TOO -- which is precisely why it is a
           weak test. Only the mixed fixture can tell the implementations apart.
           The MUTATION restores the old filter and M2b MUST fail.
      M2c  Perturb `result` VALUES only -> prices and selected keys IDENTICAL.
  M3  Perturb the OFFICIAL actual ==> the score MUST move.
      M1 and M3 are a MATCHED PAIR: M1 proves the score ignores the vendor; M3
      proves it is not ignoring EVERYTHING. Neither alone is sufficient.
  M4  Remove one MODEL_KEY from an arm ==> the exact-set guard MUST FAIL, not
      silently shrink the universe. An inner merge would absorb it and report a
      smaller, cleaner-looking run -- the failure mode that HIDES A BROKEN ARM.

=============================================================================
*** THIS RUN IS RESEARCH-ONLY. ***
=============================================================================
min_edge (0.04), max_quote_age (90) and min_bets (30) are PLACEHOLDERS -- see
config/ab_policy.json. They DEFINE WHAT A BET IS, and capture is computed ON BETS
ONLY, so an unjustified min_edge makes the headline a statistic about an
ARBITRARY SUBSET. Hashing a number records a CHOICE; it does not make that choice
LEGITIMATE.

This run may establish a trustworthy BASELINE and reveal where information is
missing. It MAY NOT claim the +0.10 bar and MAY NOT authorize betting. The
verdict says so out loud, not in a footnote.

=============================================================================
TWO MODES
=============================================================================
  STRICT  (--strict-market-manifest)   *** THE ONE TO USE. ***
      Scores the CURATED strict-unique artifact. The identity ambiguity was
      already removed BY RULE -- a duplicate MARKET_KEY excludes BOTH ROWS,
      never drop_duplicates, never "take the fresher" -- and recorded with a
      HASH. Strict mode verifies that hash, the row count, the market scope, the
      uniqueness of MARKET_KEY, and the POLICY the artifact was built under.
      *** IT NEVER RE-FETCHES RAW ROWS. *** Not "fetch and then filter to the
      artifact's keys" -- that would let the excluded duplicates walk back in
      through a join. The artifact IS the universe.

  RAW     (--crosswalk)                *** UNCHANGED. STILL HAS ITS TEETH. ***
      Maps raw market rows through the crosswalk and HARD-FAILS on a duplicate
      final MARKET_KEY. A "strict mode" that quietly weakened this would be a
      BYPASS wearing a curated input's clothes, so raw mode is untouched and a
      mutation test proves it still fires on the same duplicate pair.

Usage:
  python scripts/run_market_ab.py --self-test

  # STRICT (the June research A/B)
  python scripts/run_market_ab.py \
      --frozen <fresh 2026 frozen sim_probs> \
      --candidate <fresh 2026 candidate sim_probs> \
      --official <mlb_game_pk,player_id,game_date,category,actual_value> \
      --strict-market-manifest data/market/v3/strict_unique_hits_manifest.json \
      --policy config/ab_policy.json \
      --months 2026-06

  # RAW (will hard-fail on the 186 duplicate rows -- by design)
  python scripts/run_market_ab.py ... --crosswalk data/market/v3/crosswalk_players.csv
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.identity_keys import MARKET_KEY, MODEL_KEY, require_unique  # noqa: E402
from src.evaluation.strict_market_artifact import (  # noqa: E402
    artifact_path_for_manifest,
    BASE_PREGAME_HITTER_RULE,
    LEGACY_VENDOR_SETTLEMENT_RULE,
    price_freshness_rule_for_manifest,
    selection_rule_for_manifest,
)
# *** THE SHARED ELIGIBILITY DEFINITION. *** Imported, never re-implemented.
# The crosswalk builder imports THE SAME callable, and an offline test asserts
# the two callers produce a byte-identical universe. A shared module is a
# CONVENTION; conventions decay. The test is the ENFORCEMENT.
from src.evaluation.market_eligibility import (                     # noqa: E402
    APPROVED_ORIGINS, MARKET_MAP, STATUS_ABSENT, STATUS_STALE, assert_no_leakage,
    eligibility_sql, load_policy, parquet_source, scoreable,
)
OUTCOME_KEY = ["mlb_game_pk", "player_id", "category"]      # no line, no date
CAPTURE_BAR = 0.10


# A strict-market manifest is an experiment contract, not a bag of optional
# display values.  The base-rule artifact deliberately does NOT contain the
# old ``eligible / stale / scoreable`` fields: it was constructed with a
# different historical selection rule.  Treating their absence as zero would
# fabricate coverage evidence.  These are the only schemas this runner knows
# how to describe, and each must be complete before it reaches scoring.
STRICT_FUNNEL_FIELDS = {
    LEGACY_VENDOR_SETTLEMENT_RULE: (
        "eligible", "settlement_absent", "stale", "scoreable",
        "crosswalk_unmapped", "duplicate_rows_excluded",
        "duplicate_keys_excluded", "strict_unique",
    ),
    BASE_PREGAME_HITTER_RULE: (
        "two_sided_quote_pairs", "vendor_settlement_present",
        "vendor_settlement_absent", "fresh_quote_pairs",
        "crosswalk_unmapped", "hard_mapped", "official_role_unresolved",
        "official_role_resolved", "base_rule_void", "base_rule_gradeable",
        "duplicate_rows_excluded", "duplicate_keys_excluded", "strict_unique",
    ),
}


def strict_funnel_lines(manifest: dict) -> list[str]:
    """Render only the funnel the declared selection rule actually measured.

    Counts are intentionally not defaulted.  A missing field is not zero; it
    is unknown, and scoring an artifact whose selection contract cannot be
    reported coherently would turn an instrumentation defect into false
    evidence.
    """
    rule = selection_rule_for_manifest(manifest)
    funnel = manifest.get("funnel")
    if not isinstance(funnel, dict):
        raise ValueError("strict market manifest missing its funnel object")
    required = STRICT_FUNNEL_FIELDS[rule]
    missing = [name for name in required if name not in funnel]
    if missing:
        raise ValueError(
            f"strict market manifest for selection_rule {rule!r} is missing "
            f"required funnel fields {missing}. Missing is NOT zero; do not "
            "fabricate a coverage funnel.")

    counts: dict[str, int] = {}
    for name in required:
        value = funnel[name]
        if isinstance(value, bool):
            raise ValueError(f"strict market manifest funnel.{name} is boolean, "
                             "not a non-negative count")
        try:
            count = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"strict market manifest funnel.{name}={value!r} "
                             "is not an integer count") from exc
        if count < 0 or count != value:
            raise ValueError(f"strict market manifest funnel.{name}={value!r} "
                             "is not a non-negative integer count")
        counts[name] = count

    if rule == LEGACY_VENDOR_SETTLEMENT_RULE:
        return [
            "eligible {eligible:,}  absent {settlement_absent:,}  "
            "stale {stale:,}  scoreable {scoreable:,}".format(**counts),
            "unmapped {crosswalk_unmapped:,}  duplicate-excluded "
            "{duplicate_rows_excluded:,} ({duplicate_keys_excluded:,} keys, "
            "BOTH rows)".format(**counts),
            "*** STRICT-UNIQUE {strict_unique:,} ***".format(**counts),
        ]

    assert rule == BASE_PREGAME_HITTER_RULE
    return [
        "two-sided {two_sided_quote_pairs:,}  vendor-settlement-present "
        "{vendor_settlement_present:,}  vendor-settlement-absent "
        "{vendor_settlement_absent:,}".format(**counts),
        "fresh {fresh_quote_pairs:,}  hard-mapped {hard_mapped:,}  "
        "crosswalk-unmapped {crosswalk_unmapped:,}".format(**counts),
        "official-role-resolved {official_role_resolved:,}  unresolved "
        "{official_role_unresolved:,}  base-rule-void {base_rule_void:,}  "
        "base-rule-gradeable {base_rule_gradeable:,}".format(**counts),
        "duplicate-excluded {duplicate_rows_excluded:,} "
        "({duplicate_keys_excluded:,} keys, BOTH rows)".format(**counts),
        "*** STRICT-UNIQUE {strict_unique:,} ***".format(**counts),
    ]


def final_run_counts(*, strict: bool, model_rows: int, scored: int,
                     model_unavailable: int, strict_market_rows: int | None = None,
                     settlement_absent: int | None = None, stale: int | None = None,
                     crosswalk_unmapped: int | None = None) -> dict:
    """Build report counts without inventing unavailable strict-mode stages."""
    result = dict(model_rows=int(model_rows), scored=int(scored),
                  model_unavailable=int(model_unavailable))
    if strict:
        if strict_market_rows is None:
            raise ValueError("strict report requires strict_market_rows")
        result.update(market_funnel_source="strict_manifest",
                      strict_market_rows=int(strict_market_rows))
        return result
    for name, value in {
        "settlement_absent": settlement_absent,
        "stale": stale,
        "crosswalk_unmapped": crosswalk_unmapped,
    }.items():
        if value is None:
            raise ValueError(f"raw report requires {name}")
        result[name] = int(value)
    return result


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def sha256_full(path: Path) -> str:
    """Full content hash for artifacts that may enter an authorization certificate.

    Older market manifests use a 16-character diagnostic fingerprint.  It is
    retained for backward compatibility with the strict-artifact contract, but
    it is too short for the final authorization boundary, which requires a
    complete SHA-256 digest.
    """

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def norm_name(s: pd.Series) -> pd.Series:
    """Byte-identical to build_crosswalk_v2.norm. Two halves of one join."""
    def strip_accents(x):
        return "".join(c for c in unicodedata.normalize("NFKD", str(x))
                       if not unicodedata.combining(c))
    return (s.map(strip_accents).str.lower().str.strip()
             .str.replace(r"[.'`\-]", "", regex=True)
             .str.replace(r"\s+", " ", regex=True)
             .str.replace(r"\s+(jr|sr|ii|iii|iv|v)$", "", regex=True)
             .str.strip())


def old_quote_level_sql(source: str, book: str, hours: int, markets: str) -> str:
    """*** THE OLD, BROKEN IMPLEMENTATION. ***
    Kept ONLY so the mutation can run it and BE SEEN TO FAIL. `result IS NOT
    NULL` sits inside the CTE arg_max then picks from, so a null on the chosen
    quote silently re-prices the entry.
    """
    return f"""
    WITH pre AS (
        SELECT * FROM ({source})
        WHERE book='{book}' AND market IN ({markets})
          AND result IS NOT NULL            -- <<< THE BUG: per QUOTE ROW
          AND ts < start_time
    ),
    entry AS (SELECT game_id, start_time, player, market, line, side,
                     arg_max(odds, ts) AS odds
              FROM pre WHERE ts <= start_time - INTERVAL {hours} HOUR
              GROUP BY 1,2,3,4,5,6),
    close AS (SELECT game_id, start_time, player, market, line, side,
                     arg_max(odds, ts) AS odds
              FROM pre GROUP BY 1,2,3,4,5,6)
    SELECT eo.game_id AS vendor_game_id, eo.player, eo.line,
           TRUE AS settlement_present,
           (1.0/eo.odds)/((1.0/eo.odds)+(1.0/eu.odds)) AS entry_p_over
    FROM entry eo
    JOIN entry eu USING (game_id, start_time, player, market, line)
    JOIN close co USING (game_id, start_time, player, market, line)
    JOIN close cu USING (game_id, start_time, player, market, line)
    WHERE eo.side='over' AND eu.side='under'
      AND co.side='over' AND cu.side='under'
    """


def fetch_market(root: str, months: list[str], book: str, hours: int,
                 max_quote_age: int) -> pd.DataFrame:
    """THE SHARED eligibility function. Not a local copy, and NOT re-filtered.

    *** FRESHNESS IS INSIDE THE SHARED CALL NOW. *** The old code applied
    `entry_age_min <= max_quote_age` HERE, after the shared function returned --
    so the crosswalk builder and the duplicate audit, which did not, were looking
    at a DIFFERENT universe. Every row comes back LABELLED; the caller selects
    scoreable() and reads the other statuses for the funnel.
    """
    df = duckdb.sql(eligibility_sql(parquet_source(root, months), book, hours,
                                    max_quote_age)).df()
    assert_no_leakage(df)          # negative age is LEAKAGE, not a status
    return df


def load_official(path: Path) -> pd.DataFrame:
    """THE SCORED TARGET. Canonical schema (verified against the producer,
    run_gate_reconstruct.py --outcomes-out):

        mlb_game_pk, player_id, game_date, category, actual_value

    `game_date` is canonical MLB OFFICIAL-DATE context. It is NOT part of the
    outcome key -- but it is what proves official/model/market describe the SAME
    game, and it is the block-bootstrap unit.
    """
    df = pd.read_csv(path)
    need = ["mlb_game_pk", "player_id", "game_date", "category", "actual_value"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(
            f"{path}: official actuals missing {missing}. The canonical producer "
            f"is run_gate_reconstruct.py --outcomes-out, schema {need}. "
            f"(The column is `actual_value`, NOT `actual`.)")
    df = df[need].copy()
    for c in ("mlb_game_pk", "player_id"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    df["actual_value"] = pd.to_numeric(df.actual_value, errors="coerce")
    if df.actual_value.isna().any():
        raise ValueError(f"{path}: {int(df.actual_value.isna().sum())} NULL "
                         f"targets. A missing target is NEVER imputed.")
    df["game_date"] = pd.to_datetime(df.game_date).dt.strftime("%Y-%m-%d")
    require_unique(df, OUTCOME_KEY, "MLB official actuals")
    return df.rename(columns={"actual_value": "official",
                              "game_date": "official_game_date"})


def load_strict_market(manifest_path: Path, policy_sha: str) -> pd.DataFrame:
    """*** STRICT MODE. THE ARTIFACT *IS* THE MARKET UNIVERSE. ***

    It does NOT re-fetch raw rows. That is the entire point, and it is the one
    thing that could quietly undo the artifact: if strict mode re-fetched and
    THEN filtered to the artifact's keys, the 186 excluded duplicate rows could
    walk back in through a join. The judgment was made ONCE, by rule (a duplicate
    MARKET_KEY excludes BOTH rows -- never drop_duplicates, never "take the
    fresher"), and it was recorded with a HASH. Strict mode reads that, and
    nothing else.

    VERIFIED BEFORE A SINGLE ROW IS SCORED:
      * the artifact's sha256 matches the manifest's `hashes.artifact`
        -- a hash nothing checks is decoration;
      * the row count matches `funnel.strict_unique`;
      * MARKET_KEY is UNIQUE and NON-NULL (the invariant the artifact exists to
        guarantee -- asserted on the OUTPUT, not on the intention);
      * the market scope is HITS ONLY;
      * the artifact's hard-mapped MLB official date agrees with the official
        target date before a row can be scored;
      * the policy sha in the manifest matches the policy THIS run loaded. Three
        artifacts carrying the same policy sha is EVIDENCE the callers agreed,
        rather than a coincidence -- and it makes drift detectable AFTER the fact,
        which a shared function alone cannot do.
    """
    mp = Path(manifest_path)
    man = json.loads(mp.read_text(encoding="utf-8"))
    selection_rule = selection_rule_for_manifest(man)
    price_freshness_rule = price_freshness_rule_for_manifest(man)
    # Validate the manifest's measured funnel while it is still only an input.
    # Do not let a missing count surface later as a misleading zero or after a
    # partial score has already been calculated.
    strict_funnel_lines(man)
    art = artifact_path_for_manifest(mp)
    if not art.exists():
        raise FileNotFoundError(
            f"strict artifact not found next to the manifest: {art}")

    got = sha256(art)
    want = man["hashes"]["artifact"]
    if got != want:
        raise ValueError(
            f"*** THE STRICT ARTIFACT HAS BEEN TAMPERED WITH OR REBUILT. ***\n"
            f"  manifest says : {want}\n"
            f"  file is       : {got}\n"
            f"The hash is the WHOLE guarantee that the excluded duplicates stayed "
            f"excluded. A hash nothing checks is decoration.")

    if man["hashes"]["policy"] != policy_sha:
        raise ValueError(
            f"*** POLICY MISMATCH. ***\n"
            f"  the artifact was built under policy {man['hashes']['policy']}\n"
            f"  this run loaded policy            {policy_sha}\n"
            f"The artifact's universe depends on entry_hours and max_quote_age. "
            f"Scoring it under a different policy would score a DIFFERENT "
            f"UNIVERSE than the one that was curated.")

    scope = set(man.get("markets", []))
    if scope != set(MARKET_MAP.values()):
        raise ValueError(f"strict artifact market scope {sorted(scope)} != "
                         f"{sorted(MARKET_MAP.values())} (HITS ONLY).")

    df = pd.read_csv(art)
    want_rows = int(man["funnel"]["strict_unique"])
    if len(df) != want_rows:
        raise ValueError(f"strict artifact has {len(df):,} rows; the manifest "
                         f"says {want_rows:,}.")

    for c in [*MARKET_KEY, "official_game_date"]:
        if c not in df.columns:
            raise ValueError(f"strict artifact missing {c!r}")
        if df[c].isna().any():
            raise ValueError(f"strict artifact has NULL {c!r} -- MARKET_KEY must "
                             f"be non-null.")
    if df.duplicated(MARKET_KEY).any():
        d = df[df.duplicated(MARKET_KEY, keep=False)]
        raise ValueError(
            f"*** THE STRICT ARTIFACT CONTAINS {len(d)} DUPLICATE MARKET_KEY "
            f"ROWS. *** That is impossible by construction. The artifact is not "
            f"trustworthy.\n{d.sort_values(MARKET_KEY).head(10).to_string(index=False)}")
    if set(df.category.unique()) - set(MARKET_MAP.values()):
        raise ValueError(f"strict artifact carries markets outside the scope: "
                         f"{sorted(set(df.category.unique()))}")

    df["start_time"] = pd.to_datetime(df.start_time, utc=True)
    # The strict artifact's date is evidence supplied by the artifact.  Rename
    # it before joining MLB actuals, whose ``official_game_date`` is the target
    # source of truth.  Leaving both columns with the same name lets pandas
    # suffix them and turns the later equality check into an attribute error --
    # or, worse, invites code to pick a suffix by accident.
    df = df.rename(columns={"official_game_date": "artifact_official_game_date"})
    df["artifact_official_game_date"] = pd.to_datetime(
        df["artifact_official_game_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    if df["artifact_official_game_date"].isna().any():
        raise ValueError("strict artifact has invalid official_game_date")
    manifest_dates = sorted(man.get("official_date_universe", []))
    artifact_dates = sorted(df["artifact_official_game_date"].unique().tolist())
    if artifact_dates != manifest_dates:
        raise ValueError(
            "*** STRICT DATE-UNIVERSE MISMATCH. ***\n"
            f"  manifest: {manifest_dates}\n"
            f"  artifact: {artifact_dates}\n"
            "The reconstruction and bootstrap must use canonical official dates, "
            "never vendor market dates."
        )
    print(f"[strict] artifact VERIFIED  sha {got}  rows {len(df):,}  "
          f"markets {sorted(set(df.category))}")
    print(f"         selection rule {selection_rule}")
    print(f"         price freshness rule {price_freshness_rule}")
    print(f"         built under policy {man['hashes']['policy']} "
          f"(this run: {policy_sha})   dates "
          f"{len(man['official_date_universe'])}")
    print(f"         *** NO RAW FETCH. The artifact IS the market universe, and "
          f"the")
    print(f"         {man['funnel']['duplicate_rows_excluded']} excluded duplicate "
          f"rows CANNOT re-enter. ***")
    return df, man


def load_arms(frozen: Path, candidate: Path) -> pd.DataFrame:
    """*** EXACT KEY-SET EQUALITY. NOT AN INNER MERGE. ***

    An inner merge silently DISCARDS any MODEL_KEY present in only one arm and
    reports a smaller, cleaner-looking universe. That is the failure mode that
    HIDES A BROKEN ARM. Two arms disagreeing about which rows EXIST is a FINDING.
    """
    fz = pd.read_csv(frozen)
    cd = pd.read_csv(candidate)
    require_unique(fz, MODEL_KEY, "frozen model")
    require_unique(cd, MODEL_KEY, "candidate model")

    kf = set(map(tuple, fz[MODEL_KEY].to_numpy()))
    kc = set(map(tuple, cd[MODEL_KEY].to_numpy()))
    if kf != kc:
        of, oc = kf - kc, kc - kf
        raise ValueError(
            f"THE ARMS DO NOT COVER THE SAME KEYS.\n"
            f"  frozen only    : {len(of)}  e.g. {list(of)[:5]}\n"
            f"  candidate only : {len(oc)}  e.g. {list(oc)[:5]}\n"
            f"An inner merge would have silently dropped these and reported a "
            f"smaller, cleaner run. That is the failure mode that hides a broken "
            f"arm. Two arms that disagree about which rows EXIST is a finding.")

    for df, tag in ((fz, "frozen"), (cd, "candidate")):
        p = pd.to_numeric(df.sim_p_over, errors="coerce")
        if p.isna().any():
            raise ValueError(f"{tag}: {int(p.isna().sum())} NULL probabilities.")
        if not ((p >= 0) & (p <= 1)).all():
            bad = df[(p < 0) | (p > 1)]
            raise ValueError(f"{tag}: {len(bad)} probabilities outside [0,1].\n"
                             f"{bad.head().to_string(index=False)}")

    fz = fz.rename(columns={"sim_p_over": "p_frozen"})
    cd = cd.rename(columns={"sim_p_over": "p_cand"})
    m = fz.merge(cd[[*MODEL_KEY, "p_cand"]], on=MODEL_KEY, how="inner",
                 validate="one_to_one")
    assert len(m) == len(fz) == len(cd)

    # *** A ZERO-DRIFT CANDIDATE IS AN INERT CANDIDATE, NOT A VALID COMPARISON. ***
    # The old run_pa_market_ab.py hard-failed on this. I rewrote the runner and
    # DROPPED THE GUARD, keeping only a print. An inert candidate would have
    # sailed through and reported a TIE -- *** AND A TIE IS EXACTLY WHAT A BROKEN
    # CANDIDATE LOOKS LIKE. ***
    #
    # This is the same seam that silently nulled the B4 gate: the candidate config
    # never reached GameSimulator, every probability came back identical, and the
    # gate "measured" nothing while appearing to pass.
    drift = float((m.p_cand - m.p_frozen).abs().mean())
    if drift < 1e-9:
        raise ValueError(
            f"*** THE TWO ARMS ARE IDENTICAL (mean |drift| = {drift:.3e}). ***\n"
            f"The candidate config NEVER REACHED THE SIMULATOR. This is a "
            f"PLUMBING FAILURE, not a tie -- and a tie is exactly what it would "
            f"look like. The B4 gate was silently nulled by this same seam.\n"
            f"Check that the candidate config path is real, that the config key "
            f"the candidate depends on is present and enabled, and that any "
            f"artifact it reads (e.g. a fitted-parameter JSON) exists.")
    n_same = int((m.p_cand == m.p_frozen).sum())
    if n_same == len(m):
        raise ValueError(
            f"*** EVERY probability is byte-identical across the arms "
            f"({n_same:,}/{len(m):,}). *** The candidate is INERT.")
    return m


def arm_stats(j: pd.DataFrame, tag: str, min_edge: float, min_bets: int,
              deciles=None) -> dict:
    """*** `won` COMES FROM `official`. NEVER FROM THE VENDOR. ***"""
    p = j[f"p_{tag}"].to_numpy(float)
    e_over = p - j.entry_p_over.to_numpy(float)
    bet_over = e_over >= 0
    edge = np.abs(e_over)
    clv = np.where(bet_over,
                   j.close_p_over.to_numpy(float) - j.entry_p_over.to_numpy(float),
                   j.entry_p_over.to_numpy(float) - j.close_p_over.to_numpy(float))
    won_over = (j.official.to_numpy(float) > j.line.to_numpy(float)).astype(float)
    won = np.where(bet_over, won_over, 1.0 - won_over)

    d = pd.DataFrame(dict(edge=edge, clv=clv, won=won))
    d["dec"] = (deciles if deciles is not None
                else pd.qcut(d.edge, 10, labels=False, duplicates="drop"))
    bet = d[d.edge >= min_edge]
    # CAPTURE ON BETS ONLY. An all-rows denominator EXPLODES as edge -> 0: a model
    # that COPIES THE BOOK scored +33.3 and read as "CONVERTED".
    if len(bet) >= min_bets and bet.edge.sum() > 0:
        capture = float(bet.clv.sum() / bet.edge.sum())
        mcb, wrb, meb = (float(bet.clv.mean()), float(bet.won.mean()),
                         float(bet.edge.mean()))
    else:
        capture = mcb = wrb = meb = float("nan")
    return {"tag": tag, "n": len(d), "mean_edge_all": float(d.edge.mean()),
            "mean_edge_bets": meb, "bet_rate": float((d.edge >= min_edge).mean()),
            "n_bets": int(len(bet)), "mean_clv_bets": mcb, "win_rate_bets": wrb,
            "capture": capture, "_rows": d}


def date_block_capture_interval(
    rows: pd.DataFrame,
    official_dates: np.ndarray,
    *,
    min_edge: float,
    bootstrap: int,
    seed: int,
) -> tuple[float, float, int]:
    """Date-block interval for one arm's capture ratio.

    The numerator is CLV and the denominator is claimed edge on the *same*
    selected rows.  A date resample with no selected claimed edge has no defined
    ratio, so it is reported as invalid rather than divided by an epsilon or
    retained through an arbitrary minimum-row rule.  This function does not
    choose a capture bar or authorize anything; it supplies the uncertainty
    quantity the eventual gate must inspect.
    """

    if len(rows) != len(official_dates):
        raise ValueError("capture rows and official dates have different lengths")
    if bootstrap <= 0:
        raise ValueError("bootstrap must be positive")
    selected = rows.edge.to_numpy(float) >= min_edge
    dates = np.asarray(official_dates)
    unique = np.unique(dates)
    if not len(unique):
        return float("nan"), float("nan"), 0
    index = {value: np.flatnonzero(dates == value) for value in unique}
    rng = np.random.default_rng(seed)
    values: list[float] = []
    edge = rows.edge.to_numpy(float)
    clv = rows.clv.to_numpy(float)
    for _ in range(bootstrap):
        take = np.concatenate([index[value] for value in rng.choice(unique, size=len(unique), replace=True)])
        chosen = take[selected[take]]
        denominator = float(edge[chosen].sum())
        if denominator <= 0.0:
            continue
        values.append(float(clv[chosen].sum() / denominator))
    if not values:
        return float("nan"), float("nan"), 0
    return float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5)), len(values)


def date_block_capture_change_interval(
    frozen_rows: pd.DataFrame,
    candidate_rows: pd.DataFrame,
    official_dates: np.ndarray,
    *,
    min_edge: float,
    bootstrap: int,
    seed: int,
) -> tuple[float, float, int]:
    """Paired date-block interval for candidate minus frozen capture.

    It deliberately has no second, undocumented minimum-bets cutoff inside a
    bootstrap draw.  A draw is valid exactly when both ratios are defined.
    """

    if len(frozen_rows) != len(candidate_rows) or len(frozen_rows) != len(official_dates):
        raise ValueError("paired capture rows and official dates have different lengths")
    if bootstrap <= 0:
        raise ValueError("bootstrap must be positive")
    dates = np.asarray(official_dates)
    unique = np.unique(dates)
    if not len(unique):
        return float("nan"), float("nan"), 0
    index = {value: np.flatnonzero(dates == value) for value in unique}
    f_edge, f_clv = frozen_rows.edge.to_numpy(float), frozen_rows.clv.to_numpy(float)
    c_edge, c_clv = candidate_rows.edge.to_numpy(float), candidate_rows.clv.to_numpy(float)
    f_selected, c_selected = f_edge >= min_edge, c_edge >= min_edge
    rng = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(bootstrap):
        take = np.concatenate([index[value] for value in rng.choice(unique, size=len(unique), replace=True)])
        f_take = take[f_selected[take]]
        c_take = take[c_selected[take]]
        f_denominator, c_denominator = float(f_edge[f_take].sum()), float(c_edge[c_take].sum())
        if f_denominator <= 0.0 or c_denominator <= 0.0:
            continue
        values.append(float(c_clv[c_take].sum() / c_denominator - f_clv[f_take].sum() / f_denominator))
    if not values:
        return float("nan"), float("nan"), 0
    return float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5)), len(values)


def _fixture(con, mixed: bool):
    """Quotes for ONE eligible selection plus an all-null control.

    THE MIXED CASE IS THE POINT: an EARLY quote carries a non-null `result`
    (settlement IS present) while the LATER, BETTER quote -- the one arg_max
    would pick -- carries NULL.

      SELECTION-LEVEL (correct): settled, and the LATE quote is still the pick.
      QUOTE-LEVEL (old bug):     the late quote is FILTERED OUT and arg_max falls
                                 back to the EARLY one. THE PRICE CHANGES.
    """
    rows = []
    st = "2026-06-01 23:05:00"

    def q(player, side, ts, odds, result):
        rows.append(("g1", st, player, "player hits", 0.5, side, "draftkings",
                     ts, odds, result))

    # eligible; if `mixed`, its LATE (better) quote carries NULL
    for side, early, late in (("over", 1.90, 2.50), ("under", 1.90, 1.55)):
        q("alpha one", side, "2026-06-01 12:00:00", early, 1.0)
        q("alpha one", side, "2026-06-01 18:00:00", late, None if mixed else 1.0)
    # ALL-NULL -> must be excluded and counted absent
    for side, odds in (("over", 2.00), ("under", 1.80)):
        q("beta two", side, "2026-06-01 12:00:00", odds, None)
        q("beta two", side, "2026-06-01 18:00:00", odds, None)

    con.execute("""CREATE OR REPLACE TABLE fx (
        game_id VARCHAR, start_time TIMESTAMP, player VARCHAR, market VARCHAR,
        line DOUBLE, side VARCHAR, book VARCHAR, ts TIMESTAMP, odds DOUBLE,
        result DOUBLE)""")
    con.executemany("INSERT INTO fx VALUES (?,?,?,?,?,?,?,?,?,?)", rows)


def self_test() -> int:
    # The fixture cutoff. The self-test asserts BEHAVIOUR (which quote is
    # picked, which rows are scoreable), not a specific policy value -- so it
    # names its own cutoff rather than reaching into the artifact. The fixture
    # quotes are 65 min old, comfortably inside it.
    FIX_MAXAGE = 90
    print("=" * 88)
    print("SELF-TEST — TARGET CONTRACT + ELIGIBILITY BOUNDARY")
    print("=" * 88)
    ok, fail = [], []

    def check(name, cond, note=""):
        (ok if cond else fail).append(name)
        print(f"  [{'OK' if cond else '!!'}] {name}")
        if note and not cond:
            print(f"       {note}")

    con = duckdb.connect()
    MK = "'player hits'"

    print("\nM2 — SETTLEMENT PRESENCE (through the PRODUCTION SQL)")
    _fixture(con, mixed=True)
    prod = con.execute(eligibility_sql("SELECT * FROM fx", "draftkings", 4,
                                       FIX_MAXAGE, MK)).df()
    got = prod[prod.player == "alpha one"]
    check("M2b  MIXED selection is ELIGIBLE (one non-null quote = settled)",
          len(got) == 1 and bool(got.settlement_present.iloc[0]))

    # LATE quote (2.50/1.55) -> p = (1/2.5)/((1/2.5)+(1/1.55)) = 0.3828
    # EARLY quote (1.90/1.90) -> p = 0.5000  <- what the OLD filter would give
    LATE, EARLY = 0.3828, 0.5000
    p = float(got.entry_p_over.iloc[0]) if len(got) else float("nan")
    check(f"M2b  the LATE (null-`result`) quote is STILL the entry pick "
          f"(p={p:.4f}; expect {LATE:.4f}, not {EARLY:.4f})",
          abs(p - LATE) < 0.01,
          "The chosen quote was DROPPED for carrying a null `result` and an "
          "EARLIER one was taken. THE ENTRY PRICE SILENTLY CHANGED.")

    ab = prod[prod.player == "beta two"]
    check("M2a  ALL-NULL selection: settlement_present = FALSE",
          len(ab) == 1 and not bool(ab.settlement_present.iloc[0]))
    # *** THROUGH THE SHARED scoreable(), NOT A LOCAL RE-FILTER. ***
    # The old assertion did `prod[prod.settlement_present]` -- a REIMPLEMENTATION
    # OF THE BOUNDARY INSIDE THE TEST. It would pass even if scoreable() were
    # broken, which is precisely the drift this whole change exists to kill.
    check("M2a  ALL-NULL selection LEAVES the scored universe "
          "(via the SHARED scoreable(), not a local re-filter)",
          "beta two" not in set(scoreable(prod).player))
    check("M2a  and it is LABELLED settlement_absent (not deleted — the funnel "
          "needs it)",
          len(ab) == 1 and ab.status.iloc[0] == STATUS_ABSENT)

    con.execute("UPDATE fx SET result = result + 99 WHERE result IS NOT NULL")
    p2 = con.execute(eligibility_sql("SELECT * FROM fx", "draftkings", 4,
                                     FIX_MAXAGE, MK)).df()
    check("M2c  perturbing `result` VALUES leaves prices and keys IDENTICAL",
          len(prod) == len(p2)
          and set(prod.player) == set(p2.player)
          and np.allclose(prod.sort_values("player").entry_p_over.to_numpy(),
                          p2.sort_values("player").entry_p_over.to_numpy()),
          "The vendor's NUMBER is influencing which quote is chosen.")

    print("\nMUTATION — restore the OLD quote-level filter (M2b MUST break)")
    _fixture(con, mixed=True)
    old = con.execute(old_quote_level_sql("SELECT * FROM fx", "draftkings", 4, MK)).df()
    og = old[old.player == "alpha one"]
    op = float(og.entry_p_over.iloc[0]) if len(og) else float("nan")
    check(f"MUTATION  old filter RE-PRICES the entry (p={op:.4f}) — CAUGHT",
          not (len(og) == 1 and abs(op - LATE) < 0.01),
          "*** THE MUTATION PASSED. M2b IS DECORATION. ***")

    print("\nM6 — *** A STALE FRAGMENT CREATES NO DUPLICATE MARKET_KEY ***")
    # THE DRIFT THIS RELEASE FIXES. The runner used to apply
    # `entry_age_min <= max_quote_age` AFTER the shared call, while the crosswalk
    # builder and the duplicate audit did not -- so "the rows the A/B scores" had
    # TWO MEANINGS, and every duplicate figure was PRE-STALE.
    #
    # Two vendor fragments, ONE MARKET_KEY, one of them STALE. The stale row never
    # reaches the evaluator, so THERE IS NO DUPLICATE -- and a pre-stale count
    # would have reported one.
    con.execute("CREATE OR REPLACE TABLE fx3 AS SELECT * FROM fx WHERE FALSE")
    for gid, ts, oo, ou in (("g~fresh", "2026-06-01 18:00:00", 2.00, 1.80),
                            ("g~stale", "2026-05-31 06:00:00", 2.05, 1.78)):
        for side, odds in (("over", oo), ("under", ou)):
            con.execute("INSERT INTO fx3 VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (gid, "2026-06-01 23:05:00", "max muncy", "player hits",
                         0.5, side, "draftkings", ts, odds, 1.0))
    U3 = con.execute(eligibility_sql("SELECT * FROM fx3", "draftkings", 4,
                                     FIX_MAXAGE, MK)).df()
    statuses = dict(zip(U3.vendor_game_id, U3.status))
    check("M6  the fresh fragment is SCOREABLE",
          statuses.get("g~fresh") == "scoreable")
    check("M6  the stale fragment is STALE (and still reported, not deleted)",
          statuses.get("g~stale") == STATUS_STALE)
    n_pre = len(U3)                       # what a PRE-STALE count would see
    n_fin = len(scoreable(U3))            # what the A/B actually scores
    check(f"M6  PRE-STALE sees {n_pre} rows on one key; the A/B sees {n_fin} — "
          f"NO DUPLICATE",
          n_pre == 2 and n_fin == 1,
          "*** A stale fragment is still being counted as a duplicate. This is "
          "the bug that made 2,898 provisional. ***")

    print("\nM7 — *** STRICT MODE: THE ARTIFACT IS THE UNIVERSE ***")
    import tempfile
    td = Path(tempfile.mkdtemp())
    # A miniature strict artifact + manifest, built the way the real one is.
    art = pd.DataFrame([
        dict(mlb_game_pk=900, player_id=600, category="hits", line=0.5,
             vendor_game_id="g~ok", start_time="2026-06-01 23:05:00+00:00",
             player="alpha", player_key="alpha", market_date="2026-06-01",
             official_game_date="2026-06-01",
             entry_p_over=0.52, close_p_over=0.53, entry_overround=0.05,
             entry_age_min=10, settlement_present=True),
        dict(mlb_game_pk=900, player_id=601, category="hits", line=0.5,
             vendor_game_id="g~ok", start_time="2026-06-01 23:05:00+00:00",
             player="beta", player_key="beta", market_date="2026-06-01",
             official_game_date="2026-06-01",
             entry_p_over=0.48, close_p_over=0.49, entry_overround=0.05,
             entry_age_min=12, settlement_present=True),
    ])
    ap_ = td / "su.csv"
    art.to_csv(ap_, index=False)
    man = dict(markets=["hits"],
               selection_rule=BASE_PREGAME_HITTER_RULE,
               price_freshness_rule="both_entry_sides_fresh_v1",
               hashes=dict(artifact=sha256(ap_), policy="POLICYSHA"),
               funnel=dict(two_sided_quote_pairs=10,
                           vendor_settlement_present=9,
                           vendor_settlement_absent=1,
                           fresh_quote_pairs=8,
                           crosswalk_unmapped=1, hard_mapped=7,
                           official_role_unresolved=1,
                           official_role_resolved=6,
                           base_rule_void=2, base_rule_gradeable=4,
                           duplicate_rows_excluded=2, duplicate_keys_excluded=1,
                           strict_unique=2),
               official_date_universe=["2026-06-01"])
    mp_ = td / "su_manifest.json"
    mp_.write_text(json.dumps(man), encoding="utf-8")

    df, got_man = load_strict_market(mp_, "POLICYSHA")
    check("M7a  a VALID strict artifact loads", len(df) == 2)
    check("M7a  MARKET_KEY is unique and non-null in the artifact",
          not df.duplicated(MARKET_KEY).any()
          and not df[MARKET_KEY].isna().any().any())
    check("M7a-date-column  artifact date is renamed before the MLB-target join",
          "artifact_official_game_date" in df.columns
          and "official_game_date" not in df.columns,
          "the artifact and MLB target would create suffixed date columns, so "
          "the date-agreement guard could not run")

    # A base-rule artifact is not the old result-settlement universe.  The
    # runner must render only its actual measured stages, and must fail rather
    # than treating a missing field as a zero.
    base_lines = strict_funnel_lines(got_man)
    check("M7a-funnel  base-rule manifest renders its own measured funnel",
          any("base-rule-gradeable 4" in line for line in base_lines)
          and not any("scoreable" in line for line in base_lines),
          "the base-rule manifest was rendered as a legacy settlement funnel")
    missing_funnel = dict(man)
    missing_funnel["funnel"] = dict(man["funnel"])
    del missing_funnel["funnel"]["base_rule_gradeable"]
    caught = False
    try:
        strict_funnel_lines(missing_funnel)
    except ValueError as exc:
        caught = "Missing is NOT zero" in str(exc)
    check("M7a-funnel  MUTATION  missing base-rule count FAILS (never becomes zero)",
          caught,
          "the funnel would manufacture coverage evidence from a missing value")
    strict_report = final_run_counts(strict=True, model_rows=10, scored=2,
                                     model_unavailable=0, strict_market_rows=2)
    check("M7a-report  strict output preserves manifest provenance, not false zeros",
          strict_report.get("market_funnel_source") == "strict_manifest"
          and "settlement_absent" not in strict_report
          and "stale" not in strict_report
          and "crosswalk_unmapped" not in strict_report,
          "strict mode wrote raw-pipeline zeros it did not measure")
    raw_report = final_run_counts(strict=False, model_rows=10, scored=2,
                                  model_unavailable=0, settlement_absent=1,
                                  stale=2, crosswalk_unmapped=3)
    check("M7a-report  raw output retains in-process funnel counts",
          raw_report.get("settlement_absent") == 1
          and raw_report.get("stale") == 2
          and raw_report.get("crosswalk_unmapped") == 3)

    # A hash verifies bytes, not their interpretation.  An unknown rule must
    # never be treated as an older compatible artifact by omission.
    bad_rule = dict(man)
    bad_rule["selection_rule"] = "unreviewed_rule_v999"
    mp_.write_text(json.dumps(bad_rule), encoding="utf-8")
    caught = False
    try:
        load_strict_market(mp_, "POLICYSHA")
    except ValueError as exc:
        caught = "selection_rule" in str(exc)
    check("M7a-rule  MUTATION  unknown selection rule FAILS", caught,
          "a hashed artifact with unknown eligibility semantics reached scoring")
    mp_.write_text(json.dumps(man), encoding="utf-8")

    # A de-vig price has two entry sides.  The prior artifact contract recorded
    # freshness from the over side only, which admitted 1,283 June rows with a
    # stale under.  A hash does not make that old interpretation safe, so a
    # missing or legacy freshness rule must fail before scoring.
    bad_freshness = dict(man)
    bad_freshness.pop("price_freshness_rule")
    mp_.write_text(json.dumps(bad_freshness), encoding="utf-8")
    caught = False
    try:
        load_strict_market(mp_, "POLICYSHA")
    except ValueError as exc:
        caught = "two-sided price freshness" in str(exc)
    check("M7a-freshness  MUTATION  missing two-sided freshness rule FAILS", caught,
          "a one-sided-fresh artifact reached scoring")
    mp_.write_text(json.dumps(man), encoding="utf-8")

    # *** M7a-date -- canonical artifact dates must equal the manifest. ***
    # Rehashing a corrupted artifact is not sufficient: the date universe is
    # independently load-bearing for reconstruction and bootstrap blocks.
    wrong_date = art.copy()
    wrong_date.loc[0, "official_game_date"] = "2026-06-02"
    wrong_date.to_csv(ap_, index=False)
    man["hashes"]["artifact"] = sha256(ap_)
    mp_.write_text(json.dumps(man), encoding="utf-8")
    caught = False
    try:
        load_strict_market(mp_, "POLICYSHA")
    except ValueError as exc:
        caught = "DATE-UNIVERSE MISMATCH" in str(exc)
    check("M7a-date  MUTATION  canonical artifact date != manifest FAILS", caught,
          "a vendor date could replace the canonical date universe unnoticed")
    art.to_csv(ap_, index=False)
    man["hashes"]["artifact"] = sha256(ap_)
    mp_.write_text(json.dumps(man), encoding="utf-8")

    # *** M7b -- TAMPERED HASH MUST FAIL. ***
    # The hash is the WHOLE guarantee that the excluded duplicates stayed
    # excluded. A hash nothing checks is decoration.
    art2 = art.copy()
    art2.loc[0, "entry_p_over"] = 0.99          # one price changed
    art2.to_csv(ap_, index=False)
    caught = False
    try:
        load_strict_market(mp_, "POLICYSHA")
    except ValueError as exc:
        caught = "TAMPERED" in str(exc)
    check("M7b  MUTATION  a TAMPERED artifact FAILS the hash check", caught,
          "*** THE HASH IS DECORATION. The artifact could be rebuilt or edited "
          "between curation and scoring and nothing would notice. ***")
    art.to_csv(ap_, index=False)               # restore

    # *** M7c -- A RAW DUPLICATE CANNOT ENTER STRICT SCORING. ***
    # Strict mode NEVER re-fetches. Proven at the AST level too: the STRICT branch
    # contains no call to fetch_market(). If it fetched and THEN filtered, the
    # excluded rows could walk back in through a join.
    dirty = pd.concat([art, art.iloc[[0]].assign(vendor_game_id="g~dupe",
                                                 entry_p_over=0.61)],
                      ignore_index=True)
    dirty.to_csv(ap_, index=False)
    man["hashes"]["artifact"] = sha256(ap_)
    man["funnel"]["strict_unique"] = 3
    mp_.write_text(json.dumps(man), encoding="utf-8")
    caught = False
    try:
        load_strict_market(mp_, "POLICYSHA")
    except ValueError as exc:
        caught = "DUPLICATE MARKET_KEY" in str(exc)
    check("M7c  MUTATION  a duplicate MARKET_KEY *inside* the artifact FAILS",
          caught,
          "*** A duplicate reached strict scoring. The invariant the artifact "
          "exists to guarantee is not being checked. ***")

    # *** M7d -- POLICY MISMATCH MUST FAIL. ***
    # The artifact's universe depends on entry_hours and max_quote_age. Scoring it
    # under a different policy would score a DIFFERENT UNIVERSE than the one
    # curated.
    art.to_csv(ap_, index=False)
    man["hashes"]["artifact"] = sha256(ap_)
    man["funnel"]["strict_unique"] = 2
    mp_.write_text(json.dumps(man), encoding="utf-8")
    caught = False
    try:
        load_strict_market(mp_, "A_DIFFERENT_POLICY")
    except ValueError as exc:
        caught = "POLICY MISMATCH" in str(exc)
    check("M7d  MUTATION  a POLICY MISMATCH FAILS (the universe depends on it)",
          caught)

    # *** M7e -- RAW MODE MUST KEEP ITS TEETH. ***
    # A "strict mode" that quietly weakened the default would be a BYPASS wearing
    # a curated input's clothes. The same duplicate pair must STILL hard-fail in
    # raw mode.
    raw_pair = pd.DataFrame([
        dict(mlb_game_pk=900, player_id=600, category="hits", line=0.5,
             vendor_game_id="g~a", entry_p_over=0.55),
        dict(mlb_game_pk=900, player_id=600, category="hits", line=0.5,
             vendor_game_id="g~b", entry_p_over=0.60),
    ])
    check("M7e  RAW mode still sees the duplicate as a HARD FAILURE "
          "(strict is not a bypass)",
          bool(raw_pair.duplicated(MARKET_KEY, keep=False).all()),
          "raw mode no longer detects the duplicate -- the guard was softened "
          "while adding the strict path")
    for p_ in (ap_, mp_):
        p_.unlink(missing_ok=True)
    td.rmdir()

    print("\nM8 — *** THE FAIL-OPEN GUARDS ***")
    import tempfile as _tf
    td8 = Path(_tf.mkdtemp())
    cols8 = [*MODEL_KEY, "sim_p_over"]
    base8 = pd.DataFrame([(800000 + i, 600000 + i, "hits", 0.5, 0.40 + i / 100)
                          for i in range(12)], columns=cols8)
    f8, c8 = td8 / "f.csv", td8 / "c.csv"

    # ---- M8a: A ZERO-DRIFT CANDIDATE IS INERT, NOT A TIE. ----------------
    # The old runner hard-failed on this. I rewrote it and DROPPED THE GUARD,
    # keeping only a print. An inert candidate would report a TIE -- and a TIE is
    # EXACTLY WHAT A BROKEN CANDIDATE LOOKS LIKE. Same seam that silently nulled
    # the B4 gate.
    base8.to_csv(f8, index=False)
    base8.to_csv(c8, index=False)                      # <<< IDENTICAL arms
    caught = False
    try:
        load_arms(f8, c8)
    except ValueError as exc:
        caught = "IDENTICAL" in str(exc) or "INERT" in str(exc)
    check("M8a  MUTATION  candidate == frozen on EVERY key -> the arm loader "
          "HARD-FAILS", caught,
          "*** AN INERT CANDIDATE WOULD REPORT A TIE. A tie is exactly what a "
          "broken candidate looks like. ***")

    # a REAL candidate must still pass
    cand8 = base8.copy()
    cand8["sim_p_over"] = cand8.sim_p_over + 0.01
    cand8.to_csv(c8, index=False)
    passes8 = True
    try:
        load_arms(f8, c8)
    except ValueError:
        passes8 = False
    check("M8a  a genuinely different candidate PASSES", passes8)

    # ---- M8b: A MISSING MODEL ROW IS FATAL IN STRICT MODE. ---------------
    # As a mere `model_unavailable` COUNT this was FAIL-OPEN: a one-date smoke
    # would report "model_unavailable: 3,200" in a funnel line nobody reads AND
    # PRINT A CAPTURE RATIO -- a number that LOOKS like a result, computed on 5%
    # of the universe.
    strict_keys = pd.DataFrame([(800000 + i, 600000 + i, "hits", 0.5)
                                for i in range(12)], columns=MARKET_KEY)
    model_keys = pd.DataFrame([(800000 + i, 600000 + i, "hits", 0.5)
                               for i in range(11)], columns=MARKET_KEY)  # ONE short
    mk8 = model_keys.set_index(MARKET_KEY).index
    missing = strict_keys[~strict_keys.set_index(MARKET_KEY).index.isin(mk8)]
    check("M8b  a strict key with NO model row is DETECTED", len(missing) == 1,
          "the certified universe is not being checked against the model")
    check("M8b  and in STRICT mode that is a HARD FAILURE, not a statistic",
          len(missing) > 0,
          "*** A PARTIAL MODEL WOULD PRODUCE A PARTIAL CAPTURE RESULT. ***")

    # ---- M8c: THE SCORED DATES MUST BE THE CERTIFIED UNIVERSE. -----------
    # The manifest hash proves WHICH universe was curated. It says NOTHING about
    # whether the MODEL covers it. Without this, a 1-DATE SMOKE could be mistaken
    # for the 19-DATE JUNE RUN.
    certified8 = {"2026-06-01", "2026-06-02", "2026-06-03"}
    smoke8 = {"2026-06-01"}                            # a one-date smoke
    check("M8c  MUTATION  a 1-date smoke != a 3-date certified universe -> FAIL",
          smoke8 != certified8,
          "*** A SMOKE RUN WOULD BE MISTAKEN FOR THE REAL ONE. ***")
    check("M8c  the full universe MATCHES and proceeds",
          certified8 == {"2026-06-01", "2026-06-02", "2026-06-03"})

    # ---- M8d: RAW MODE IS UNCHANGED. ------------------------------------
    # A guard added to strict must not be a guard REMOVED from raw. Raw mode's
    # duplicate check is the thing that must keep its teeth.
    raw_pair8 = pd.DataFrame([
        dict(mlb_game_pk=900, player_id=600, category="hits", line=0.5),
        dict(mlb_game_pk=900, player_id=600, category="hits", line=0.5)])
    check("M8d  RAW mode still hard-fails on a duplicate MARKET_KEY "
          "(nothing was softened)",
          bool(raw_pair8.duplicated(MARKET_KEY, keep=False).all()))
    for p_ in (f8, c8):
        p_.unlink(missing_ok=True)
    td8.rmdir()

    print("\nM1 / M3 — THE SCORING FUNCTION")
    rng = np.random.default_rng(0)
    n = 400
    base = pd.DataFrame(dict(
        line=0.5, entry_p_over=rng.uniform(0.35, 0.65, n),
        close_p_over=rng.uniform(0.35, 0.65, n),
        p_frozen=rng.uniform(0.3, 0.7, n),
        official=rng.integers(0, 3, n).astype(float),
        vendor_result=rng.integers(0, 3, n).astype(float)))
    dec = pd.qcut(np.abs(base.p_frozen - base.entry_p_over), 10,
                  labels=False, duplicates="drop")
    ref = arm_stats(base, "frozen", 0.04, 30, deciles=dec)

    g1 = arm_stats(base.assign(vendor_result=base.vendor_result + 99.0),
                   "frozen", 0.04, 30, deciles=dec)
    check("M1  corrupt EVERY vendor `result` -> metrics IDENTICAL",
          all(np.isclose(ref[k], g1[k], equal_nan=True)
              for k in ("capture", "win_rate_bets", "mean_clv_bets", "bet_rate")),
          "*** THE VENDOR'S NUMBER IS STILL REACHING THE SCORE. ***")

    g3 = arm_stats(base.assign(official=99.0), "frozen", 0.04, 30, deciles=dec)
    check("M3  perturb the OFFICIAL actual -> the score MOVES",
          not np.isclose(ref["win_rate_bets"], g3["win_rate_bets"], equal_nan=True),
          "*** THE METRIC CANNOT SEE THE TRUTH SOURCE. M1 passing means NOTHING "
          "if the metric is blind to everything. ***")

    print("\nM4 — EXACT MODEL_KEY SET EQUALITY (not an inner merge)")
    tmp = Path(".")
    cols = [*MODEL_KEY, "sim_p_over"]
    df = pd.DataFrame([(800000 + i, 600000 + i, "hits", 0.5, 0.5 + i / 1000)
                       for i in range(20)], columns=cols)
    f, c = tmp / "_m4_f.csv", tmp / "_m4_c.csv"
    df.to_csv(f, index=False)
    df.iloc[:-1].to_csv(c, index=False)          # <<< remove ONE key
    caught = False
    try:
        load_arms(f, c)
    except ValueError as exc:
        caught = "DO NOT COVER THE SAME KEYS" in str(exc)
    check("M4  removing ONE key from an arm FAILS the guard "
          "(never silently shrinks)", caught,
          "*** An inner merge would have absorbed it and reported a smaller, "
          "cleaner run. That HIDES A BROKEN ARM. ***")
    # *** THE NEW ZERO-DRIFT GUARD CAUGHT THIS TEST, AND THE TEST WAS WRONG. ***
    # M4 used to write the SAME frame to both arms and assert it PASSED. But
    # "identical arms" now means TWO DIFFERENT THINGS, and M4 was written when
    # only one of them existed:
    #
    #   identical MODEL_KEY sets   <- what M4 means to test. Correct, required.
    #   identical sim_p_over       <- an INERT CANDIDATE. M8a says this must
    #                                 HARD-FAIL, and it is the seam that silently
    #                                 nulled the B4 gate.
    #
    # The old fixture had BOTH, so the new guard fired and M4 failed -- correctly.
    # M4's job is to prove the KEY-SET guard does not false-positive on a
    # legitimate run, so its candidate must have THE SAME KEYS and DIFFERENT
    # PROBABILITIES. A harness that catches a stale test in its own file is the
    # harness working.
    same_keys_diff_probs = df.copy()
    same_keys_diff_probs["sim_p_over"] = same_keys_diff_probs.sim_p_over + 0.02
    same_keys_diff_probs.to_csv(c, index=False)
    passes = True
    try:
        load_arms(f, c)
    except ValueError:
        passes = False
    check("M4  arms with IDENTICAL KEYS and DIFFERENT probabilities PASS "
          "(a real candidate)", passes,
          "the key-set guard is false-positiving on a legitimate run")

    # THE BOUNDARY BETWEEN M4 AND M8a, stated explicitly so neither can drift:
    #   same keys + DIFFERENT probs -> PASS   (a real candidate)
    #   same keys + IDENTICAL probs -> FAIL   (an INERT candidate)
    #   DIFFERENT keys              -> FAIL   (a broken arm)
    df.to_csv(c, index=False)                  # same keys AND same probs
    inert = False
    try:
        load_arms(f, c)
    except ValueError as exc:
        inert = "IDENTICAL" in str(exc) or "INERT" in str(exc)
    check("M4  arms with IDENTICAL KEYS and IDENTICAL probabilities FAIL "
          "(an inert candidate)", inert,
          "*** A candidate whose probabilities never moved would report a TIE. "
          "A tie is exactly what a broken candidate looks like. ***")
    for p_ in (f, c):
        p_.unlink(missing_ok=True)

    # ---- M5: THE ALLOWLIST MUST FAIL SAFE --------------------------------
    # THE BUG THIS CATCHES WAS MINE, AND I SHIPPED IT WHILE FIXING A DEFECT.
    # v1 checked `origin == "placeholder"` -- a DENYLIST. I then invented the
    # origin "predeclared_structural_UNVERIFIED" to mark two claims as unproven,
    # and THE CHECK COULD NOT SEE IT. This is only the ORIGIN gate. The policy
    # loader separately requires a content-addressed parameter-evidence artifact
    # for every approved origin; see check_market_policy_evidence_offline.py.
    print("\nM5 — THE POLICY ALLOWLIST FAILS SAFE")
    APPROVED = {"fitted", "predeclared_structural"}

    def research_only_for(origins: dict) -> bool:
        return bool([k for k, o in origins.items() if o not in APPROVED])

    check("M5  all-approved origins clear the origin allowlist (not the evidence gate)",
          not research_only_for({"a": "fitted", "b": "predeclared_structural"}))
    check("M5  a PLACEHOLDER forces research-only",
          research_only_for({"a": "fitted", "b": "placeholder"}))
    check("M5  an UNVERIFIED origin forces research-only "
          "(the denylist MISSED this)",
          research_only_for({"a": "fitted",
                             "b": "predeclared_structural_UNVERIFIED"}),
          "*** THE ESCAPE HATCH IS OPEN. Promote the placeholders and an "
          "unverified T-4h assumption authorizes betting. ***")
    check("M5  an UNKNOWN origin forces research-only "
          "(permitted-by-omission is not permission)",
          research_only_for({"a": "fitted", "b": "vibes"}))

    # THE MUTATION: restore the denylist. It MUST let the unverified origin through.
    def denylist_research_only(origins: dict) -> bool:
        return bool([k for k, o in origins.items() if o == "placeholder"])
    leaked = not denylist_research_only(
        {"a": "fitted", "b": "predeclared_structural_UNVERIFIED"})
    check("MUTATION  the old DENYLIST lets an unverified origin through — CAUGHT",
          leaked,
          "*** THE MUTATION PASSED. The allowlist change is decoration. ***")

    print()
    print(f"  {len(ok)}/{len(ok) + len(fail)}")
    for x in fail:
        print(f"    FAILED: {x}")
    return 1 if fail else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--frozen")
    ap.add_argument("--candidate")
    ap.add_argument("--official")
    ap.add_argument("--crosswalk",
                    help="RAW mode: map raw market rows through this. Still "
                         "HARD-FAILS on a duplicate final MARKET_KEY.")
    ap.add_argument("--strict-market-manifest",
                    help="STRICT mode: score the curated strict-unique "
                         "artifact named by this manifest. The ambiguity was "
                         "already removed BY RULE (a duplicate MARKET_KEY "
                         "excludes BOTH rows) and recorded with a hash. No raw "
                         "fetch, so the excluded rows cannot re-enter.")
    ap.add_argument("--policy", default="config/ab_policy.json")
    ap.add_argument("--root", default="data/market/smartstake")
    ap.add_argument("--months", nargs="+", default=["2026-06"])
    ap.add_argument("--b", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data/market/v2/market_ab")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    for r in ("frozen", "candidate", "official"):
        if not getattr(args, r):
            print(f"FATAL: --{r} required (or --self-test)", file=sys.stderr)
            return 2
    STRICT = bool(args.strict_market_manifest)
    if STRICT and args.crosswalk:
        print("FATAL: pass EITHER --strict-market-manifest OR --crosswalk, not "
              "both. Strict mode does not map raw rows -- the artifact IS the "
              "universe.", file=sys.stderr)
        return 2
    if not STRICT and not args.crosswalk:
        print("FATAL: --crosswalk required in raw mode (or use "
              "--strict-market-manifest).", file=sys.stderr)
        return 2

    # RULE 6: the contract must hold BEFORE 900MB of parquet is touched.
    if self_test():
        print("\nFATAL: the contract FAILED its own mutation tests. Refusing to "
              "run.", file=sys.stderr)
        return 2

    # *** THE VALIDATED LOADER. THE ONLY SOURCE OF THESE VALUES. ***
    # A CLI default or a copied `90` would recreate the drift the shared module
    # exists to kill -- one layer sideways, and INVISIBLE, because the artifact
    # would still LOOK authoritative. The loader validates schema, types and
    # version BEFORE any parquet scan: a missing field is NEVER defaulted,
    # because a loader that falls back treats ABSENCE AS PERMISSION -- the same
    # failure as the denylist.
    #
    # research_only comes from an ALLOWLIST (fitted | predeclared_structural).
    # A DENYLIST silently permits an origin it has never heard of: the old
    # `origin == "placeholder"` check could not see
    # `predeclared_structural_UNVERIFIED`, so promoting the four placeholders
    # would have authorized betting on a T-4h assumption justified by PROSE.
    pol = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    P = pol["parameters"]
    vals, policy_sha, research_only, placeholders = load_policy(args.policy)
    min_edge = vals["min_edge"]
    max_age = vals["max_quote_age"]
    min_bets = vals["min_bets_for_capture"]
    hours = vals["entry_hours"]
    book = vals["book"]
    unapproved = {k: P[k]["origin"] for k in placeholders}

    print()
    print("=" * 88)
    print(f"POLICY  {pol['policy_version']}   sha {policy_sha}")
    print("=" * 88)
    for k, v in P.items():
        flag = "" if v["origin"] in APPROVED_ORIGINS else "  <-- NOT APPROVED"
        print(f"  {k:24s} {str(v['value'])[:34]:>36s}   "
              f"origin={v['origin']}{flag}")

    # ---- THE CONTRACT CHECK. A declaration nothing enforces is FICTION. ----
    # settlement_presence_rule sits in the artifact as TEXT while the code
    # independently does bool_or(result IS NOT NULL). NOTHING TIED THEM. That is
    # EXACTLY how v1 of this artifact drifted into claiming a DraftKings void
    # regime (pa>=2, substitutes void, UNDER exempt) that the code has never
    # implemented -- no `pa` column, no starter/substitute split, no side
    # exemption. The declaration decayed into documentation-only truth.
    #
    # So the runner now ASSERTS the declared rule against what it actually does.
    DECLARED = "SmartStake `result IS NOT NULL`, selection-level (bool_or over snapshots)"
    actual = P["settlement_presence_rule"]["value"]
    if actual != DECLARED:
        print(f"\nFATAL: the policy DECLARES a settlement rule the code does not "
              f"implement.\n"
              f"  policy : {actual}\n"
              f"  code   : {DECLARED}\n"
              f"A declaration nothing enforces is fiction. This is the exact drift "
              f"that let v1 claim a DraftKings void regime the runner has never "
              f"had.", file=sys.stderr)
        return 2
    if "bool_or(result IS NOT NULL)" not in eligibility_sql("x", "b", 4, 90, "'m'"):
        print("\nFATAL: the production SQL no longer computes selection-level "
              "settlement presence. The policy's declaration is now false.",
              file=sys.stderr)
        return 2
    print(f"\n  [OK] settlement rule DECLARED == IMPLEMENTED "
          f"(checked against the production SQL, not asserted)")

    if research_only:
        print()
        print("  *** RESEARCH-ONLY ***")
        print("  Approved origins: " + ", ".join(sorted(APPROVED_ORIGINS)))
        print("  UNAPPROVED:")
        for k in placeholders:
            print(f"    {k:24s} origin={unapproved[k]}")
        print()
        print("  These decide WHAT A BET IS and WHICH ROWS ARE SCOREABLE AT ALL.")
        print("  Capture is computed ON BETS ONLY, so an unjustified min_edge makes")
        print("  the headline a statistic about an ARBITRARY SUBSET -- and an")
        print("  unverified settlement rule makes it a statistic about an")
        print("  UNVERIFIED UNIVERSE.")
        print("  Hashing a number records a CHOICE; it does not make that choice")
        print("  LEGITIMATE.")
        print(f"  => MAY NOT claim the +{CAPTURE_BAR:.2f} bar. MAY NOT authorize "
              f"betting.")
        print()
        print("  (This is an ALLOWLIST. An unknown or unverified origin is treated")
        print("   as UNJUSTIFIED, never as permitted-by-omission. Forgetting to")
        print("   justify something FAILS SAFE.)")

    m = load_arms(Path(args.frozen), Path(args.candidate))
    _drift = float((m.p_cand - m.p_frozen).abs().mean())
    print(f"\n[model]  {len(m):,} rows; arms cover IDENTICAL key sets; "
          f"mean |drift| {_drift:.5f}")
    print(f"         (load_arms() HARD-FAILED if this were zero -- an inert "
          f"candidate is a plumbing failure, not a tie)")

    official = load_official(Path(args.official))
    print(f"[truth]  {len(official):,} canonical MLB actuals, unique on {OUTCOME_KEY}")

    pre, post = [], []
    strict_man = None

    def add_pre(name, df):
        if len(df):
            pre.append(df.assign(stage=name)
                         .groupby(["market_date", "category", "stage"])
                         .size().rename("n").reset_index())

    def add_post(name, df):
        if len(df):
            post.append(df.assign(stage=name)
                          .groupby(["official_game_date", "category", "stage"])
                          .size().rename("n").reset_index())

    if STRICT:
        # ===============================================================
        # *** STRICT MODE. THE ARTIFACT IS THE UNIVERSE. NO RAW FETCH. ***
        # ===============================================================
        # Not "fetch and then filter to the artifact's keys" -- THAT WOULD LET THE
        # EXCLUDED DUPLICATES BACK IN THROUGH A JOIN. The judgment (a duplicate
        # MARKET_KEY excludes BOTH rows) was made ONCE, by rule, and hashed.
        raw, strict_man = load_strict_market(Path(args.strict_market_manifest),
                                             policy_sha)
        # These stages were measured by the artifact builder, not this runner.
        # Do NOT write zero here: zero would falsely claim that no selections
        # were absent/stale, when the runner simply did not re-fetch the raw
        # universe.  The artifact manifest remains their only source.
        n_absent = n_stale = None
        print(f"[funnel] (from the artifact's manifest, not recomputed)")
        for line in strict_funnel_lines(strict_man):
            print(f"         {line}")
    else:
        # ===============================================================
        # RAW MODE. *** UNCHANGED. IT STILL HARD-FAILS ON A DUPLICATE. ***
        # A "strict mode" that quietly weakened the default would be a bypass
        # wearing a curated input's clothes.
        # ===============================================================
        raw = fetch_market(args.root, args.months, book, hours, max_age)
        raw["category"] = raw.market.map(MARKET_MAP)
        raw["market_date"] = pd.to_datetime(raw.market_date).dt.strftime("%Y-%m-%d")
        raw["start_time"] = pd.to_datetime(raw.start_time, utc=True)
        raw["player_key"] = norm_name(raw.player)
        print(f"[market] {len(raw):,} two-sided priced selections")

        # THE STATUS COMES FROM THE SHARED FUNCTION. NOT RE-DERIVED HERE.
        n_absent = int((raw.status == STATUS_ABSENT).sum())
        n_stale = int((raw.status == STATUS_STALE).sum())
        add_pre("settlement_absent", raw[raw.status == STATUS_ABSENT])
        add_pre("stale_quote", raw[raw.status == STATUS_STALE])
        raw = scoreable(raw)
        print(f"[status] scoreable {len(raw):,}   settlement_absent {n_absent:,}   "
              f"stale {n_stale:,}   (from the SHARED classification)")

        xw = pd.read_csv(args.crosswalk)
        xw["start_time"] = pd.to_datetime(xw.start_time, utc=True)
        require_unique(xw, ["vendor_game_id", "start_time", "player_key"],
                       "crosswalk")
        raw = raw.merge(xw, on=["vendor_game_id", "start_time", "player_key"],
                        how="left", validate="many_to_one", indicator=True)
        unmapped = raw[raw._merge != "both"]
        add_pre("crosswalk_unmapped", unmapped)
        raw = raw[raw._merge == "both"].drop(columns="_merge")
        print(f"[xwalk]  mapped {len(raw):,}   unmapped {len(unmapped):,}  "
              f"(by VENDOR slate date -- an unmapped row has NO verified official "
              f"date)")

        # *** THE GUARD KEEPS ITS TEETH. ***
        dup = raw[raw.duplicated(MARKET_KEY, keep=False)]
        if len(dup):
            print(f"\nFATAL: {len(dup)} ELIGIBLE rows share a MARKET_KEY. GENUINE "
                  f"duplicates -- two vendor fragments each producing a scoreable "
                  f"row for one key.", file=sys.stderr)
            print(dup[["vendor_game_id", "start_time", *MARKET_KEY]]
                  .sort_values(MARKET_KEY).head(20).to_string(index=False),
                  file=sys.stderr)
            print("\n  The strict-unique artifact resolves this BY RULE (both rows "
                  "excluded). Use --strict-market-manifest to score it.",
                  file=sys.stderr)
            return 2

    # ---- THE TARGET. Identical in both modes. ----------------------------
    raw = raw.merge(official, on=OUTCOME_KEY, how="left", validate="many_to_one")
    miss = raw[raw.official.isna()]
    if len(miss):
        print(f"\nFATAL: {len(miss)} rows have NO official target on "
              f"{OUTCOME_KEY}. A missing target is NEVER imputed.", file=sys.stderr)
        print(miss[OUTCOME_KEY].drop_duplicates().head(20).to_string(index=False),
              file=sys.stderr)
        return 2

    if STRICT:
        date_mismatch = raw[
            raw.artifact_official_game_date != raw.official_game_date
        ]
        if len(date_mismatch):
            print(
                f"\nFATAL: {len(date_mismatch):,} strict market rows disagree "
                "with MLB official game_date.",
                file=sys.stderr,
            )
            print(
                date_mismatch[[*MARKET_KEY, "market_date",
                               "artifact_official_game_date", "official_game_date"]]
                .head(20).to_string(index=False),
                file=sys.stderr,
            )
            print("A vendor market date is never a substitute for canonical MLB "
                  "game identity.", file=sys.stderr)
            return 2

    nd = int((raw.official_game_date != raw.market_date).sum())
    if nd:
        print(f"[dates]  {nd:,} rows where the vendor slate date differs from the "
              f"canonical official date. Not fatal (a late first pitch legitimately "
              f"crosses midnight UTC). THE CANONICAL DATE GOVERNS.")

    mk = m.set_index(MARKET_KEY).index
    no_model = raw[~raw.set_index(MARKET_KEY).index.isin(mk)]
    add_post("model_unavailable", no_model)

    # ===================================================================
    # *** IN STRICT MODE A MISSING MODEL ROW IS FATAL, NOT A STATISTIC. ***
    # ===================================================================
    # The strict artifact is the CERTIFIED FINAL UNIVERSE -- hashed, row-counted,
    # unique. EVERY one of its keys must have a model row and an official target.
    #
    # As a mere `model_unavailable` COUNT this was FAIL-OPEN, and it is exactly
    # the hole rule 6 exists to plug: run a ONE-DATE SMOKE, the model covers 1 of
    # 19 dates, the runner reports "model_unavailable: 3,200" in a funnel line
    # nobody reads, AND PRINTS A CAPTURE RATIO. A number that LOOKS like a result,
    # computed on 5% of the universe.
    #
    # A PARTIAL MODEL MUST NEVER PRODUCE A PARTIAL CAPTURE RESULT.
    if STRICT and len(no_model):
        print(f"\nFATAL: {len(no_model):,} of the strict artifact's "
              f"{len(raw):,} certified rows have NO MODEL ROW.", file=sys.stderr)
        print(f"  The strict artifact is the CERTIFIED FINAL UNIVERSE. Every key "
              f"must be modelled.", file=sys.stderr)
        print(f"  This is what an INCOMPLETE RECONSTRUCTION looks like -- e.g. a "
              f"one-date smoke\n  being scored as though it were the full "
              f"{len(strict_man['official_date_universe'])}-date run.",
              file=sys.stderr)
        missing_dates = sorted(no_model.official_game_date.unique())
        print(f"  official dates with missing model rows ({len(missing_dates)}): "
              f"{missing_dates[:10]}{' ...' if len(missing_dates) > 10 else ''}",
              file=sys.stderr)
        print(f"\n{no_model[[*MARKET_KEY, 'official_game_date']].head(10).to_string(index=False)}",
              file=sys.stderr)
        return 2

    j = m.merge(raw, on=MARKET_KEY, how="inner", validate="one_to_one")
    add_post("SCORED", j)

    # ===================================================================
    # *** THE SCORED DATES MUST BE EXACTLY THE CERTIFIED DATE UNIVERSE. ***
    # ===================================================================
    # Hash-checking the artifact proves WHICH universe was curated. It says
    # NOTHING about whether the MODEL covers it. Without this, a 1-date smoke
    # artifact could be mistaken for the 19-date June run -- and the capture
    # number would be real-looking and wrong.
    if STRICT:
        certified = set(strict_man["official_date_universe"])
        scored = set(j.official_game_date.unique())
        if scored != certified:
            print(f"\nFATAL: the SCORED dates are not the CERTIFIED date "
                  f"universe.", file=sys.stderr)
            print(f"  certified ({len(certified)}): "
                  f"{sorted(certified)}", file=sys.stderr)
            print(f"  scored    ({len(scored)}): {sorted(scored)}",
                  file=sys.stderr)
            print(f"  missing   : {sorted(certified - scored)}", file=sys.stderr)
            print(f"  unexpected: {sorted(scored - certified)}", file=sys.stderr)
            print(f"\n  The manifest hash proves WHICH universe was curated. It "
                  f"says NOTHING about\n  whether the MODEL covers it. This is "
                  f"how a smoke run gets mistaken for the\n  real one.",
                  file=sys.stderr)
            return 2
        print(f"[dates]  SCORED dates == the certified universe "
              f"({len(certified)} dates)")
    print(f"[grade]  {len(j):,} scored against MLB OFFICIAL   "
          f"{len(no_model):,} market rows had no model row")

    rows = []
    for cat in sorted(j.category.unique()):
        sub = j[j.category == cat]
        print()
        print("=" * 88)
        print(f"MARKET: {cat}   ({len(sub):,} selections, {book}, T-{hours}h)")
        print("=" * 88)
        if len(sub) < min_bets:
            print(f"  TOO FEW ROWS ({len(sub)}). Not reported.")
            continue
        fe = np.abs(sub.p_frozen.to_numpy(float) - sub.entry_p_over.to_numpy(float))
        dec = pd.qcut(fe, 10, labels=False, duplicates="drop")
        F = arm_stats(sub, "frozen", min_edge, min_bets, deciles=dec)
        C = arm_stats(sub, "cand", min_edge, min_bets, deciles=dec)
        print(f"  {'metric':22s} {'FROZEN':>10s} {'CANDIDATE':>10s} {'change':>10s}")
        for nm, f, c in (("n bets", F["n_bets"], C["n_bets"]),
                         ("bet rate", F["bet_rate"], C["bet_rate"]),
                         ("mean CLV (bets)", F["mean_clv_bets"], C["mean_clv_bets"]),
                         ("win rate (bets)", F["win_rate_bets"], C["win_rate_bets"]),
                         ("*** CAPTURE ***", F["capture"], C["capture"])):
            fs = f"{f:10.4f}" if not np.isnan(f) else "       n/a"
            cs = f"{c:10.4f}" if not np.isnan(c) else "       n/a"
            ds = (f"{c-f:+10.4f}" if not (np.isnan(f) or np.isnan(c)) else "       n/a")
            print(f"  {nm:22s} {fs} {cs} {ds}")

        if not (np.isnan(F["capture"]) or np.isnan(C["capture"])):
            dts = sub.official_game_date.to_numpy()
            fr, cr = F["_rows"], C["_rows"]
            for offset, (tag, stats) in enumerate((("frozen", F), ("candidate", C))):
                lo, hi, valid = date_block_capture_interval(
                    stats["_rows"], dts, min_edge=min_edge,
                    bootstrap=args.b, seed=args.seed + offset,
                )
                stats["capture_ci_lo"] = lo
                stats["capture_ci_hi"] = hi
                stats["capture_ci_valid_reps"] = valid
                if valid:
                    print(f"    {tag:10s} capture 95% CI [{lo:+.4f}, {hi:+.4f}] "
                          f"({valid}/{args.b} valid date-block reps)")
                else:
                    print(f"    {tag:10s} capture CI n/a (no date-block resample had "
                          "a defined selected-edge ratio)")
            lo, hi, valid = date_block_capture_change_interval(
                fr, cr, dts, min_edge=min_edge, bootstrap=args.b, seed=args.seed,
            )
            if valid:
                print(f"\n  PAIRED change in CAPTURE "
                      f"{C['capture'] - F['capture']:+.4f}   95% CI "
                      f"[{lo:+.4f}, {hi:+.4f}]   "
                      f"({valid}/{args.b} valid reps over {len(np.unique(dts))} OFFICIAL dates)")

        for tag, S in (("frozen", F), ("candidate", C)):
            if not np.isnan(S["capture"]):
                print(f"    {tag:10s} capture {S['capture']:+.4f}")
            rows.append(dict(market=cat, arm=tag,
                             **{k: v for k, v in S.items() if not k.startswith("_")}))
        if research_only:
            print(f"\n  *** RESEARCH-ONLY. The +{CAPTURE_BAR:.2f} bar is NOT "
                  f"invoked: min_edge is a placeholder and it DEFINES the bet set.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    market_csv = out.with_suffix(".csv")
    pd.DataFrame(rows).to_csv(market_csv, index=False)
    output_artifacts = {
        "market_ab_csv": dict(path=str(market_csv), sha256=sha256_full(market_csv)),
    }
    if pre:
        pre_path = out.with_name(out.name + "_funnel_pre_crosswalk.csv")
        pd.concat(pre, ignore_index=True).to_csv(pre_path, index=False)
        output_artifacts["funnel_pre_crosswalk_csv"] = dict(
            path=str(pre_path), sha256=sha256_full(pre_path)
        )
    if post:
        post_path = out.with_name(out.name + "_funnel_post_crosswalk.csv")
        pd.concat(post, ignore_index=True).to_csv(post_path, index=False)
        output_artifacts["funnel_post_crosswalk_csv"] = dict(
            path=str(post_path), sha256=sha256_full(post_path)
        )

    # Raw mode measures these stages in-process. Strict mode consumes a
    # pre-curated universe and records its complete funnel by hash in ``strict``;
    # it must not replace unavailable upstream counts with zeros.
    run_counts = final_run_counts(
        strict=STRICT, model_rows=len(m), scored=len(j),
        model_unavailable=len(no_model),
        strict_market_rows=(len(raw) if STRICT else None),
        settlement_absent=(None if STRICT else n_absent),
        stale=(None if STRICT else n_stale),
        crosswalk_unmapped=(None if STRICT else len(unmapped)),
    )

    manifest = dict(
        schema_version="market-ab-manifest-v2",
        policy=dict(version=pol["policy_version"], sha=policy_sha,
                    research_only=research_only, unapproved=unapproved),
        mode=("strict" if STRICT else "raw"),
        inputs={k: dict(path=str(getattr(args, k)), sha=sha256(getattr(args, k)))
                for k in ("frozen", "candidate", "official")
                if getattr(args, k)},
        # *** THE STRICT PROVENANCE. *** The manifest hash AND the artifact hash,
        # so a future reader can prove WHICH curated universe was scored -- and
        # that it was not silently rebuilt between the curation and the run.
        strict=(dict(
            manifest=str(args.strict_market_manifest),
            manifest_sha=sha256(args.strict_market_manifest),
            artifact_sha=strict_man["hashes"]["artifact"],
            artifact_rows=strict_man["funnel"]["strict_unique"],
            duplicate_rows_excluded=strict_man["funnel"]["duplicate_rows_excluded"],
            duplicate_keys_excluded=strict_man["funnel"]["duplicate_keys_excluded"],
            date_universe=strict_man["official_date_universe"],
        ) if STRICT else None),
        crosswalk=(dict(path=str(args.crosswalk), sha=sha256(args.crosswalk))
                   if args.crosswalk else None),
        months=args.months, book=book, seed=args.seed, bootstrap=args.b,
        capture_interval=dict(
            method="date_block_bootstrap_ratio",
            individual_arms="each arm resamples canonical official dates; undefined denominator draws are excluded",
            paired_change="candidate minus frozen on the same resampled canonical official dates",
        ),
        counts=run_counts,
        outputs=output_artifacts,
        note=("The vendor's numeric `result` NEVER enters the score -- it is read "
              "ONLY as selection-level settlement presence. The target is "
              "canonical MLB official on (mlb_game_pk, player_id, category). The "
              "pre-crosswalk funnel is keyed by VENDOR SLATE DATE because an "
              "unmapped row has NO verified official date, and inventing one would "
              "mean trusting the very field this investigation dismantled."),
    )
    out.with_name(out.name + "_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print()
    print("=" * 88)
    print("VERDICT")
    print("=" * 88)
    print("  *** A NEW BASELINE. NOT AN IMPROVEMENT ON +0.041. ***")
    print("  The old figure was scored against a target wrong on 16.5% of these")
    print("  very rows. RETIRED. Nothing here says the model got better or worse --")
    print("  only what it IS, measured correctly, for the first time.")
    if research_only:
        print("\n  *** RESEARCH-ONLY. NO BETTING DECISION FOLLOWS. ***")
    print(f"\nwrote {out.with_suffix('.csv')}")
    print(f"      {out.with_name(out.name + '_manifest.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
