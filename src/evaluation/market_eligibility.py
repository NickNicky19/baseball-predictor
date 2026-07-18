"""
THE A/B's ELIGIBLE UNIVERSE. *** ONE DEFINITION. ONE SOURCE OF TRUTH. ***

Every consumer of "which market selections does the evaluator score?" imports
THIS. There is no second copy anywhere, and check_one_denominator_offline.py
asserts the callers produce a byte-identical frame -- INCLUDING PRICES, because a
key-only comparison passes on a bug that preserves identity and changes value.

=============================================================================
*** v2: FRESHNESS IS PART OF ELIGIBILITY. IT LIVES HERE NOW. ***
=============================================================================
v1 of this module was extracted precisely to stop two callers computing two
different denominators. It shipped, an offline test asserted 8/8, and THE DRIFT
WAS STILL THERE -- one filter downstream:

    run_market_ab.py       applied `entry_age_min <= max_quote_age` AFTER calling
                           eligibility_sql()
    build_crosswalk_v3.py  did NOT
    audit_duplicate_keys   did NOT

So "the rows the A/B scores" still had two meanings. I MOVED THE BOUNDARY AND
LEFT A PIECE OF IT BEHIND.

And the evidence was sitting in my own output: the duplicate audit reported
`entry_age_min` differing on 100% of duplicate keys, MEDIAN 582 MINUTES. I read
that as a finding about the vendor. The FIRST thing it actually says is that many
of those rows ARE STALE AND THE RUNNER WOULD HAVE DROPPED THEM BEFORE SCORING --
that my own instrument was miscalibrated. (Caught by Codex.)

*** EVERY NUMBER MEASURED BEFORE THIS FIX IS PRE-STALE AND PROVISIONAL: ***
    97.8% coverage        -> pre-stale HARD-MAPPING coverage, not A/B coverage
    2,898 duplicate rows  -> pre-stale. If one fragment of a pair is stale, IT
                             NEVER REACHES THE EVALUATOR AND THERE IS NO
                             DUPLICATE AT ALL.
    "53% coverage cost"   -> NOT ESTABLISHED. I stated it as though it were.

=============================================================================
THE STATUS IS EXHAUSTIVE, MUTUALLY EXCLUSIVE, AND PIPELINE-ORDERED
=============================================================================
    not settlement_present        ->  settlement_absent
    settled + age > cutoff        ->  stale
    settled + 0 <= age <= cutoff  ->  scoreable

    age < 0                       ->  *** HARD FAIL. NOT A FOURTH STATUS. ***

Order matters: settlement is asked FIRST, because an unsettled row's staleness is
irrelevant -- it was never scoreable regardless. The three states must not
overlap, or the funnel double-counts and stops summing.

NEGATIVE AGE IS LEAKAGE, NOT A CATEGORY. An entry quote cannot post-date its own
horizon. ctrl4 caught entry prices recorded 23 HOURS AFTER FIRST PITCH this way
-- `game_id` is not a stable game key (MEASURED: only 69 of 243 June game_ids
carry a single start_time; 32 span 17-23 HOURS). Demoting that guard to a status
would let the thing it exists to catch pass as a category.

=============================================================================
NOTHING IS DROPPED INSIDE. THE FUNNEL NEEDS THE EXCLUSIONS.
=============================================================================
This module LABELS; it does not delete. A filter that deletes its own evidence
cannot report on it. Callers take scoreable(df) to score, and read the other two
statuses for the coverage funnel.

=============================================================================
SETTLEMENT PRESENCE IS A PROPERTY OF THE SELECTION, NOT OF A QUOTE
=============================================================================
An earlier version filtered `result IS NOT NULL` at the QUOTE-SNAPSHOT level,
INSIDE the CTE arg_max() picks the entry price from. On a selection with MIXED
null/non-null snapshots that DROPS the chosen quote and takes an EARLIER one --
SILENTLY RE-PRICING THE ENTRY. MEASURED: 0 mixed selections in June, so the
defect was LATENT, not active. Fixed anyway; it REPAIRS NOTHING and CHANGES NO
NUMBER, and saying otherwise would let a real 16.5% grading corruption hide behind
a tidy-up.

*** AND `result`'s VALUE IS NEVER SELECTED HERE. *** Not once. It disagrees with
MLB's official actuals on 16.5% of these very rows. It is not truth, and it must
not be able to reach a scorer even by accident. Structure, not discipline.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.evaluation.market_policy_evidence import verify_parameter_evidence

MARKET_MAP = {"player hits": "hits"}
# *** HITS ONLY -- BECAUSE OF THE BOOK, NOT BECAUSE OF THE MARKET. ***
#
# `player home runs` was in this map for the whole build and produced ZERO rows.
# The output said so plainly (0.0% HR coverage) and I read past it twice.
#
# I THEN GOT THE REASON WRONG, AND WROTE THE WRONG REASON HERE. This comment used
# to assert: "an HR prop is a YES/NO market, so there is no under side, and a
# two-sided de-vig cannot produce a single HR row AT ANY BOOK." I reasoned from a
# physical intuition about what a home-run prop *is*, and never measured it.
#
# MEASURED (scripts/audit_market_availability.py, Mar-Jun 2026):
#
#     player home runs      over 65,168,992      under 17,503,855
#
#     TWO-SIDED AT 33 BOOKS. hard_rock 29,405 two-sided selections (14,129
#     scoreable), novig 29,355, bracco 26,551, pinnacle 7,804, and on down.
#
# *** SEVENTEEN MILLION UNDER QUOTES EXIST. THE CLAIM WAS FALSE. ***
#
# THE ACTUAL REASON HR PRODUCES NOTHING HERE:
#     *** DRAFTKINGS DOES NOT POST A TWO-SIDED HR MARKET. ***
# DK appears in the availability table for `player hits` (16,371 scoreable) and
# `player rbis` (15,695) and NOT ONCE for `player home runs`. The policy declares
# book=draftkings, so HR yields nothing. That is a BOOK-SPECIFIC GAP, not a
# market-structural one -- a completely different fact with completely different
# implications, and the difference is the whole point.
#
# WHY THE UNDER SIDE MATTERS EVEN IF YOU WOULD ONLY EVER BET THE OVER:
#     p_over_devig = (1/odds_over) / ((1/odds_over) + (1/odds_under))
# The under quote is an INSTRUMENT, not a wager. Without it you have 1/odds_over,
# which still carries the book's margin -- so you would be comparing the model
# against an INFLATED price and calling the difference "edge". The vig IS the
# thing you are trying to beat. You never place the under; the evaluator needs it
# to know what the over is really worth.
#
# SO HR IS POTENTIALLY EVALUABLE -- AT A BOOK THAT POSTS BOTH SIDES. It is not in
# this map because that is a SEPARATE, GATED DECISION, and three things are
# UNVERIFIED (rule 1):
#   1. IS IT THE SAME PRODUCT? A Yes/No market priced both ways gives you a "No"
#      leg, and a No is NOT an over/under under. It may settle identically -- or
#      not (pushes, void rules, alt-line ladders). A COLUMN NAMED `under` IS NOT
#      AN UNDER SIDE.
#   2. IS IT THE SAME LINE? An alt-line 1.5 is a different bet from a 0.5.
#   3. Pairing an over from book A with an under from book B is NOT A DE-VIG AT
#      ALL -- it is a SPREAD BETWEEN VENUES, across two vig structures and two
#      void regimes.
# And switching the whole evaluation to another book would change the venue for
# hits too, where the entire 3,386-row result is priced against DK.
#
# The door is left HONESTLY OPEN, not falsely nailed shut. Anyone adding HR back
# does it deliberately, after answering 1-3.

STATUS_ABSENT = "settlement_absent"
STATUS_STALE = "stale"
STATUS_SCOREABLE = "scoreable"
STATUSES = (STATUS_ABSENT, STATUS_STALE, STATUS_SCOREABLE)

# The canonical projection every caller must agree on, EXACTLY.
#
# *** THE KEYS ARE NOT ENOUGH. *** A key-only comparison proves the callers select
# the SAME ROWS and says NOTHING about whether they select the SAME PRICES. M2b
# demonstrated exactly that: same selection, same key, entry_p_over 0.3827 vs
# 0.5000. A key-only test passes cleanly on it.
#
#   A TEST THAT CHECKS IDENTITY BUT NOT VALUE PASSES ON A BUG THAT PRESERVES
#   IDENTITY AND CHANGES VALUE.
ELIGIBILITY_PROJECTION = [
    "vendor_game_id", "start_time", "player", "market", "line",
    "entry_over_quote_time", "entry_under_quote_time",
    "close_over_quote_time", "close_under_quote_time",
    "entry_over_odds_decimal", "entry_under_odds_decimal",
    "close_over_odds_decimal", "close_under_odds_decimal",
    "entry_p_over", "close_p_over", "entry_overround", "entry_over_age_min",
    "entry_under_age_min", "entry_age_min",
    "settlement_present", "status",
]

# Exact posted-price fields from the historical vendor parquet.  These are not
# interchangeable with the de-vigged probabilities: the latter support market
# information/capture analysis, while the former are required to calculate the
# economic expected value of a one-sided wager.  Consumers that do not need
# economic analysis may ignore the fields, but no consumer may recreate them
# from the rounded de-vig projection.
RAW_DECIMAL_ODDS_FIELDS = [
    "entry_over_odds_decimal", "entry_under_odds_decimal",
    "close_over_odds_decimal", "close_under_odds_decimal",
]

RAW_QUOTE_TIMESTAMP_FIELDS = [
    "entry_over_quote_time", "entry_under_quote_time",
    "close_over_quote_time", "close_under_quote_time",
]

# Origins that authorize betting. *** AN ALLOWLIST. *** Anything else -- a
# placeholder, an "UNVERIFIED", a typo, an origin nobody anticipated -- forces
# research-only. A DENYLIST silently permits what it has never heard of, and that
# escape hatch was live: promote the placeholders and an unverified T-4h
# assumption would have authorized betting.
APPROVED_ORIGINS = {"fitted", "predeclared_structural"}

# Policy versions this code knows how to read. An UNKNOWN version is a HARD FAIL,
# never a best-effort parse.
KNOWN_POLICY_VERSIONS = {"research-2026-07-13-v2", "research-2026-07-13-v3"}

_REQUIRED_PARAMS: dict[str, type] = {
    "min_edge": float,
    "max_quote_age": int,
    "min_bets_for_capture": int,
    "entry_hours": int,
    "settlement_presence_rule": str,
    "base_pregame_hitter_eligibility_rule": str,
    "book": str,
}


def load_policy(path: str | Path) -> tuple[dict, str, bool, list[str]]:
    """THE ONLY SOURCE OF THE POLICY VALUES. Validated BEFORE any parquet scan.

    Returns (values, sha, research_only, unapproved).

    *** ALL THREE CALLERS READ max_quote_age AND entry_hours FROM HERE. *** A CLI
    default or a copied `90` would recreate the drift this module exists to
    prevent -- one layer sideways, and INVISIBLE, because the artifact would still
    LOOK authoritative. The sha is stamped into every report, so three reports
    carrying the SAME sha is EVIDENCE the callers agreed rather than a coincidence
    -- and it makes drift detectable AFTER the fact, which a shared callable alone
    cannot do.

    A MISSING FIELD IS NOT TOLERATED. A loader that falls back to an implicit
    default treats ABSENCE AS PERMISSION -- the same failure as the denylist, and
    a partial policy that defaults is a copied constant wearing an artifact's
    clothes.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"policy artifact not found: {p}")
    sha = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    pol = json.loads(p.read_text(encoding="utf-8"))

    ver = pol.get("policy_version")
    if ver not in KNOWN_POLICY_VERSIONS:
        raise ValueError(
            f"{p}: UNKNOWN policy_version {ver!r}. Known: "
            f"{sorted(KNOWN_POLICY_VERSIONS)}. An unrecognised policy is a HARD "
            f"FAIL, never a best-effort parse.")

    params = pol.get("parameters")
    if not isinstance(params, dict):
        raise ValueError(f"{p}: `parameters` is missing or is not an object.")

    missing = [k for k in _REQUIRED_PARAMS if k not in params]
    if missing:
        raise ValueError(f"{p}: policy is MISSING required parameters {missing}. "
                         f"A missing field is NEVER defaulted.")

    values: dict = {}
    for k, typ in _REQUIRED_PARAMS.items():
        e = params[k]
        if not isinstance(e, dict) or "value" not in e or "origin" not in e:
            raise ValueError(
                f"{p}: parameter {k!r} must carry both `value` and `origin`.")
        v = e["value"]
        if typ is not str:
            try:
                v = typ(v)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{p}: parameter {k!r} value {e['value']!r} is "
                                 f"not a {typ.__name__}") from exc
        values[k] = v

    if values["max_quote_age"] < 0:
        raise ValueError(f"{p}: max_quote_age must be >= 0.")
    if values["entry_hours"] <= 0:
        raise ValueError(f"{p}: entry_hours must be > 0.")

    # An approved-looking origin with no content-addressed evidence is still
    # research-only.  A bare edit from "placeholder" to "fitted" must never
    # authorize a run; the evidence document binds the exact value, method,
    # protocol hash, and source artifacts.
    project_root = p.resolve().parent.parent
    unapproved: list[str] = []
    for key, entry in params.items():
        origin = entry.get("origin") if isinstance(entry, dict) else None
        if origin not in APPROVED_ORIGINS:
            unapproved.append(key)
            continue
        verified, _ = verify_parameter_evidence(
            project_root=project_root,
            parameter=key,
            value=values.get(key),
            origin=str(origin),
            policy_entry=entry,
        )
        if not verified:
            unapproved.append(key)
    unapproved.sort()
    return values, sha, bool(unapproved), unapproved


def quote_pairs_sql(source: str, book: str, entry_hours: int,
                    markets: str | None = None) -> str:
    """Two-sided pregame quote pairs before settlement/freshness classification.

    This is the price-observation boundary shared by the evaluator and
    outcome-blind policy diagnostics.  It intentionally contains no vendor
    result value, no official outcome, and no quote-age cutoff; those are
    separate questions.  A quote-age audit that copied this query would be
    vulnerable to the same denominator drift that previously invalidated
    coverage and duplicate counts.
    """
    markets = markets or ", ".join(f"'{m}'" for m in MARKET_MAP)
    return f"""
    WITH pre AS (
        -- ALL PREGAME QUOTES. *** UNGATED BY `result`. ***
        SELECT * FROM ({source})
        WHERE book = '{book}' AND market IN ({markets}) AND ts < start_time
    ),
    settle AS (
        -- SETTLEMENT PRESENCE, AT THE SELECTION LEVEL. Defined once, used twice.
        SELECT game_id, start_time, player, market, line,
               bool_or(result IS NOT NULL) AS settlement_present
        FROM pre GROUP BY 1,2,3,4,5
    ),
    entry AS (
        -- Chosen from ALL pregame quotes. A null `result` on the chosen row must
        -- NOT remove it from consideration -- that is the old bug.
        SELECT game_id, start_time, player, market, line, side,
               arg_max(odds, ts) AS odds, max(ts) AS q_ts
        FROM pre WHERE ts <= start_time - INTERVAL {entry_hours} HOUR
        GROUP BY 1,2,3,4,5,6
    ),
    close AS (
        SELECT game_id, start_time, player, market, line, side,
               arg_max(odds, ts) AS odds, max(ts) AS q_ts
        FROM pre GROUP BY 1,2,3,4,5,6
    ),
    joined AS (
        SELECT eo.game_id AS vendor_game_id, eo.start_time,
            CAST(eo.start_time AT TIME ZONE 'UTC'
                               AT TIME ZONE 'America/New_York' AS DATE) AS market_date,
            eo.player, eo.market, eo.line, s.settlement_present,
            eo.q_ts AS entry_over_quote_time,
            eu.q_ts AS entry_under_quote_time,
            co.q_ts AS close_over_quote_time,
            cu.q_ts AS close_under_quote_time,
            eo.odds AS entry_over_odds_decimal,
            eu.odds AS entry_under_odds_decimal,
            co.odds AS close_over_odds_decimal,
            cu.odds AS close_under_odds_decimal,
            (1.0/eo.odds) / ((1.0/eo.odds) + (1.0/eu.odds)) AS entry_p_over,
            (1.0/eo.odds) + (1.0/eu.odds) - 1.0             AS entry_overround,
            date_diff('minute', eo.q_ts,
                      eo.start_time - INTERVAL {entry_hours} HOUR) AS entry_over_age_min,
            date_diff('minute', eu.q_ts,
                      eo.start_time - INTERVAL {entry_hours} HOUR) AS entry_under_age_min,
            greatest(
              date_diff('minute', eo.q_ts, eo.start_time - INTERVAL {entry_hours} HOUR),
              date_diff('minute', eu.q_ts, eo.start_time - INTERVAL {entry_hours} HOUR)
            ) AS entry_age_min,
            (1.0/co.odds) / ((1.0/co.odds) + (1.0/cu.odds)) AS close_p_over
        FROM entry eo
        JOIN entry eu USING (game_id, start_time, player, market, line)
        JOIN close co USING (game_id, start_time, player, market, line)
        JOIN close cu USING (game_id, start_time, player, market, line)
        JOIN settle s USING (game_id, start_time, player, market, line)
        WHERE eo.side='over' AND eu.side='under'
          AND co.side='over' AND cu.side='under'
    )
    SELECT * FROM joined
    """


def eligibility_sql(source: str, book: str, entry_hours: int,
                    max_quote_age: int, markets: str | None = None) -> str:
    """The A/B's universe, PRE-CROSSWALK, WITH FRESHNESS CLASSIFIED.

    `source` is any SQL producing the vendor schema -- read_parquet() over the
    local partitions in production, an in-memory fixture in the tests. The tests
    therefore run THE REAL FUNCTION rather than a reimplementation (rule 5: mock
    the thing you are testing and you measure nothing).

    *** max_quote_age IS REQUIRED. NO DEFAULT. *** A default here is a second
    definition of the boundary, which is the bug this module exists to kill.
    """
    return f"""
    WITH quote_pairs AS (
        {quote_pairs_sql(source, book, entry_hours, markets)}
    )
    SELECT *,
        -- EXHAUSTIVE, MUTUALLY EXCLUSIVE, PIPELINE-ORDERED.
        -- Settlement FIRST: an unsettled row's staleness is irrelevant, it was
        -- never scoreable regardless.
        CASE
          WHEN NOT settlement_present            THEN '{STATUS_ABSENT}'
          WHEN entry_over_age_min > {max_quote_age}
            OR entry_under_age_min > {max_quote_age} THEN '{STATUS_STALE}'
          ELSE                                        '{STATUS_SCOREABLE}'
        END AS status
    FROM quote_pairs
    """


def parquet_source(root: str, months: list[str]) -> str:
    """The production `source`: the LOCAL parquet partitions. Reproducible, no
    rate limit, and the artifact cannot shift when the vendor re-publishes."""
    return " UNION ALL ".join(
        f"SELECT * FROM read_parquet('{root}/mon={m}/*.parquet')" for m in months)


def assert_no_leakage(df) -> None:
    """*** NEGATIVE AGE IS LEAKAGE, NOT A STATUS. HARD FAIL. ***

    An entry quote cannot post-date its own horizon. ctrl4 caught entry prices
    recorded 23 HOURS AFTER FIRST PITCH this way. Making it a fourth status would
    let the thing the guard exists to catch pass as a category.
    """
    age_fields = ["entry_over_age_min", "entry_under_age_min"]
    missing_ages = [c for c in age_fields if c not in df.columns]
    if missing_ages:
        raise ValueError(
            "market quote projection is missing side-specific entry ages "
            f"{missing_ages}; two-sided de-vig freshness cannot be inferred "
            "from one side"
        )
    bad = df[(df[age_fields] < 0).any(axis=1)]
    if len(bad):
        cols = ["vendor_game_id", "start_time", "player", *age_fields]
        raise ValueError(
            f"{len(bad)} rows with NEGATIVE side-specific entry age "
            f"(min {float(bad[age_fields].min().min()):.0f}). An entry quote cannot "
            f"post-date its own horizon. *** THIS IS LEAKAGE (ctrl4), NOT A "
            f"CATEGORY. ***\n{bad[cols].head(10).to_string(index=False)}")

    # The provider's historical quotes are decimal odds.  Validate the raw
    # payout inputs at the shared boundary, before any artifact can claim an
    # economic conclusion.  A non-finite or <=1 quote is not a conservative
    # value; it is an invalid payout observation and must not be coerced.
    missing_odds = [c for c in RAW_DECIMAL_ODDS_FIELDS if c not in df.columns]
    if missing_odds:
        raise ValueError(
            "market quote projection is missing exact raw payout fields "
            f"{missing_odds}; economic analysis cannot recreate them from "
            "de-vigged probabilities"
        )
    import numpy as np
    import pandas as pd
    odds = df[RAW_DECIMAL_ODDS_FIELDS].apply(pd.to_numeric, errors="coerce")
    invalid = (
        ~np.isfinite(odds.to_numpy()).all(axis=1)
        | (odds <= 1.0).any(axis=1).to_numpy()
    )
    if invalid.any():
        bad_odds = df.loc[invalid, ["vendor_game_id", "start_time", "player",
                                    *RAW_DECIMAL_ODDS_FIELDS]].head(10)
        raise ValueError(
            f"{int(invalid.sum())} row(s) have invalid decimal payout odds. "
            "Exact posted prices must be finite and > 1.\n"
            f"{bad_odds.to_string(index=False)}"
        )


def fetch(con, root: str, months: list[str], book: str, entry_hours: int,
          max_quote_age: int):
    """THE ONE CALL EVERY CONSUMER MAKES. Labelled, not filtered."""
    sql = eligibility_sql(parquet_source(root, months), book, entry_hours,
                          max_quote_age)
    df = con.sql(sql).df() if hasattr(con, "sql") else con.execute(sql).df()
    assert_no_leakage(df)
    return df


def scoreable(df):
    """THE FINAL SUBSET THE EVALUATOR ACTUALLY SCORES.

    *** ALL THREE CALLERS USE THIS. *** The runner, the crosswalk builder and the
    duplicate audit. None re-applies a freshness filter of its own -- that drift is
    exactly what made 97.8% and 2,898 provisional.
    """
    return df[df.status == STATUS_SCOREABLE]


def fresh_quote_pairs(df, max_quote_age: int):
    """Return fresh two-sided quotes without using vendor settlement presence.

    This exists for a separate research artifact whose grading boundary is an
    official sportsbook rule.  It is deliberately *not* the legacy ``scoreable``
    subset: an official-starting hitter with PA may be gradeable even if the
    vendor omitted its own result.  Numeric vendor results remain unavailable to
    both paths.
    """
    if max_quote_age < 0:
        raise ValueError("max_quote_age must be non-negative")
    assert_no_leakage(df)
    required = ["entry_over_age_min", "entry_under_age_min"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            "freshness requires both two-sided quote ages; missing "
            f"{missing}. A fresh over plus a stale under cannot form a valid "
            "de-vigged entry price."
        )
    return df[
        df.entry_over_age_min.le(max_quote_age)
        & df.entry_under_age_min.le(max_quote_age)
    ].copy()


def policy_source_quote_pairs(df):
    """Return the uncensored quote-pair source used to *fit* freshness.

    No current ``max_quote_age`` is applied here. Applying the inherited cutoff
    before policy fitting would censor the evidence and make it impossible for
    the fit to confirm or reject that cutoff. Negative ages remain ctrl4
    leakage and hard-fail; missing side ages cannot define a two-sided entry.
    """
    assert_no_leakage(df)
    required = ["entry_over_age_min", "entry_under_age_min"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            "policy-source freshness evidence requires both side ages; missing "
            f"{missing}"
        )
    if df[required].isna().any().any():
        raise ValueError("policy-source freshness evidence has a missing side age")
    return df.copy()


def canonical(df):
    """Normalise for EXACT comparison between callers.

    UTC timestamps. Numerics UNTOUCHED -- not rounded, because a rounded
    comparison would hide precisely the price drift this exists to catch. Sorted,
    index reset. Compared on VALUES from the shared function, never on display
    strings.
    """
    import pandas as pd
    out = df[ELIGIBILITY_PROJECTION].copy()
    out["start_time"] = pd.to_datetime(out.start_time, utc=True)
    for column in RAW_QUOTE_TIMESTAMP_FIELDS:
        out[column] = pd.to_datetime(out[column], utc=True)
    out["settlement_present"] = out.settlement_present.astype(bool)
    return (out.sort_values(["vendor_game_id", "start_time", "player",
                             "market", "line"])
               .reset_index(drop=True))
