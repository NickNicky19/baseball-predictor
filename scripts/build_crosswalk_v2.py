#!/usr/bin/env python3
"""
BUILD THE CROSSWALK, v2 — exact vendor grain, ONE consumer mapping, validated.

    (vendor_game_id, start_time, player_key)  ->  (mlb_game_pk, player_id)

v1 (scripts/build_crosswalk.py) is PRESERVED as historical audit evidence. It is
not edited and its artifacts are not overwritten. v2 writes to data/market/v2/.

=============================================================================
WHY v2 EXISTS — THREE DEFECTS IN v1, ALL VERIFIED AGAINST THE CONSUMER
=============================================================================
run_pa_market_ab.py is the ONLY consumer. v1 cannot feed it. Read from the
consumer BACKWARD, not from the builder forward:

  DEFECT 1 — SCHEMA. load_crosswalk() requires ONE table with exactly
      [vendor_game_id, start_time, player_key, mlb_game_pk, player_id]
    v1 writes TWO tables. crosswalk_games.csv has vendor_game_id but NO
    player_key. crosswalk_players.csv has player_key and player_id but NO
    vendor_game_id and NO start_time. Neither is loadable, and they cannot be
    composed, because v1 never carried start_time per mapping.

  DEFECT 2 — GRAIN. v1 grouped the vendor side on (slate_date, game_id).
    fetch_market() keys entry/close on (vendor_game_id, start_time, ...) and
    joins the crosswalk on (vendor_game_id, start_time, player_key). start_time
    is IN THE KEY because ctrl4 caught REAL OUTCOME LEAKAGE there: 7,455 vendor
    groups carried two start_times, and without start_time in the key, max(ts)
    took a quote from the LATE game while the EARLY game's first pitch was used
    as the horizon -- "entry prices" recorded 23 HOURS AFTER first pitch.
    A builder coarser than its consumer's key cannot serve that key.

  DEFECT 3 — A SELF-DEFEATING ASSERTION. v1 did:
        P = pd.DataFrame(player_rows).drop_duplicates(
                subset=["mlb_game_pk", "player_key"])          # <-- DROPS
        dup = P[P.duplicated(subset=["mlb_game_pk","player_key"], keep=False)]
        if not dup.empty: return 1                             # <-- THEN CHECKS
    The dedupe is on the SAME SUBSET as the check, so `dup` is empty BY
    CONSTRUCTION. The check CANNOT FIRE. v1 then printed
        "[OK] (mlb_game_pk, player_key) is UNIQUE"
    -- a property it MANUFACTURED, not one it VERIFIED. That is rule 4 in its
    purest form: the assertion passes identically on broken and on correct data.
    v1's own docstring says "a silent drop_duplicates is data loss wearing a
    tidy face. There are none." There was one.
    v2 VALIDATES FIRST AND HARD-FAILS. Nothing is ever deduped into looking
    correct.

=============================================================================
*** THE EXACT GRAIN WILL PRODUCE MORE COLLISIONS, NOT FEWER. SAY SO FIRST. ***
=============================================================================
This is the prediction that matters, and it is stated BEFORE the run (rule 7)
so that a drop in coverage cannot be rationalised after the fact.

v1 grouped on (slate_date, vendor_game_id). A vendor id FRAGMENTED across two
start_times was therefore ALREADY MERGED into a single vendor identity before
the vote ever ran. v2 resolves at (slate_date, vendor_game_id, start_time), so
those fragments become TWO vendor identities. Both vote. Both land on the SAME
mlb_game_pk. *** THAT IS THE COLLISION CONDITION. ***

So the 10 collisions v1 found are the ones that survived DESPITE the coarse
grouping. v2 should find MORE. That is the crosswalk DETECTING, and coverage
will FALL. A lower resolved-rate here is the correct outcome, not a regression.

MEASURED, June 2026 (the reason to expect this):
    n_game_ids                243   <- v1's grain
    n_(game_id, start_time)   493   <- v2's grain. TWICE AS MANY.
    real games                ~218

=============================================================================
COLLISIONS: WHAT WE MEASURE, AND WHAT WE REFUSE TO CLAIM
=============================================================================
An audit of v1's 10 collisions found high player-set overlap and a separation of
almost exactly +2h00m01s. That is a MEASUREMENT. It is NOT a cause.

*** WE DO NOT CALL THESE "VENDOR SPLITS". ***  We call them what they are:
    DUPLICATE VENDOR IDENTITIES MAPPING TO ONE mlb_game_pk.
The mechanism (a vendor duplication bug? a re-post? a genuinely different
event?) is UNPROVEN, and rule 10 says a diagnostic isolates WHERE, never WHY.

*** AND THE EXCLUSION IS LOAD-BEARING, NOT A COVERAGE PREFERENCE. ***
MARKET_KEY = (mlb_game_pk, player_id, category, line)   -- VERIFIED in
src/evaluation/identity_keys.py. NO DATE. NO START_TIME.
Two vendor identities on one mlb_game_pk therefore collapse to the SAME
MARKET_KEY, and run_pa_market_ab.py's require_unique(raw, MARKET_KEY) HARD
FAILS. Keeping a collision does not cost coverage -- IT BREAKS THE RUN.
Anyone who later "recovers" these games by merging them will break line 464.

=============================================================================
GLOBAL, NOT PER-DATE  (this is new in v2 and it matters over four months)
=============================================================================
require_unique(raw, MARKET_KEY) runs on the POOLED March-June market frame.
mlb_game_pk is globally unique, so a duplicate vendor identity ANYWHERE in four
months fails the WHOLE run -- not just its own date. Over v1's 18 dates that
risk was small. Over ~110 dates it is not.

Therefore collisions are detected and excluded GLOBALLY (across every date in
the run), not per-date. A single unexcluded collision in April kills a June
result.

=============================================================================
SANITY RANGES -- STATED BEFORE THE RUN (rule 7)
=============================================================================
  vendor identities (date, game_id, start_time)
                          : MORE than (date, game_id). If roughly EQUAL, the
                            exact grain is doing nothing and the premise above
                            is WRONG -- stop and re-measure.
  resolved rate           : 60-90%  (LOWER than v1's 88.1%, by construction)
                            < 50% => the vote is breaking on fragments. STOP.
  win votes (winner)      : 15-25
                            routinely < MIN_VOTES => fragments carry too few
                            players to vote. STOP.
  margin                  : >= 5x, most UNANIMOUS
                            routinely < 3x => the premise is weaker than
                            measured and THE ARTIFACT MUST NOT BE TRUSTED.
                            That is a stop condition, not a tuning opportunity.
  collisions              : MORE than v1's 10. Expected. All excluded, counted.
  dup (mlb_game_pk, player_key)
                          : ZERO. Any => HARD FAIL.

  THE FAILURE THAT WOULD MISLEAD US: a thin fragment (say 3 players) cannot
  clear MIN_VOTES, is excluded as "too few votes" -- but ITS MARKET ROWS STILL
  EXIST in SmartStake. Under --strict-crosswalk the A/B raises; under the report
  mode it becomes a silent coverage hole ATTRIBUTED TO THE WRONG CAUSE. So the
  report counts MARKET ROWS LOST PER REASON, not just games.

Usage:
  python scripts/build_crosswalk_v2.py --months 2026-03 2026-04 2026-05 2026-06
  python scripts/build_crosswalk_v2.py --dates 2026-06-01 2026-06-02
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.mlb_api import MLBStatsAPI          # noqa: E402

# --- DECISION BOUNDARIES ----------------------------------------------------
# Unchanged from v1 ON PURPOSE. v2 changes the GRAIN and the VALIDATION, not the
# vote. Moving both at once would confound them: if coverage falls, we could not
# say whether the grain did it or a boundary did.
#
# MIN_VOTES: MEASURED, real winners get 18-22 votes; runners-up get 0-1.
MIN_VOTES = 8
# MIN_MARGIN: MEASURED, the worst real margin was 18:1 (the Max Muncy case).
MIN_MARGIN = 5.0

# The ONLY markets this milestone evaluates. Declared here so the coverage
# report counts the rows that actually matter. HRR is not a book market at all;
# RBI depends on a component whose structural fix FAILED its gate and is inert;
# total_bases is the next model-version change and needs its own gate.
SCOPED_MARKETS = ("player hits", "player home runs")


def norm(s: object) -> str:
    """Normalise a name for matching.

    *** MUST STAY BYTE-IDENTICAL TO run_pa_market_ab.norm_name(). ***
    They are the two halves of one join. If they drift, the join silently loses
    rows and blames the vendor. check_crosswalk_v2_offline.py asserts this.
    """
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s))
                if not unicodedata.combining(c))
    s = s.lower().strip()
    for ch in ".'`-":
        s = s.replace(ch, "")
    parts = [p for p in s.split()
             if p not in ("jr", "sr", "ii", "iii", "iv", "v")]
    return " ".join(parts)


def dig(obj, *path, default=None):
    cur = obj
    for k in path:
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            return default
        if cur is None:
            return default
    return cur


def build_mlb_side(api: MLBStatsAPI, game_date: str, retries: int = 2):
    """For one date: every game_pk, its metadata, and WHO APPEARED IN IT.

    An EMPTY box score is RETRIED and then REPORTED -- never silently skipped.
    A game with no players casts NO VOTES, so any vendor identity mapping to it
    fails as "no name matched" -- which would send us hunting a name bug that
    does not exist. A silent skip does not just lose a game; it MISATTRIBUTES
    THE FAILURE.
    """
    games = api.get_schedule(game_date)
    name_to_pks: dict[str, set[int]] = defaultdict(set)
    pk_to_players: dict[int, dict[str, int]] = {}
    pk_meta: dict[int, dict] = {}
    empty_pks: list[int] = []

    for g in games:
        pk = int(g["gamePk"])
        pk_meta[pk] = dict(
            game_date=game_date,
            away=dig(g, "teams", "away", "team", "name", default=""),
            home=dig(g, "teams", "home", "team", "name", default=""),
            game_time_utc=g.get("gameDate", ""),
            game_number=g.get("gameNumber"),
            doubleheader=g.get("doubleHeader"),
        )

        ids: set[int] = set()
        for attempt in range(retries + 1):
            try:
                hitters, pitchers = api.get_game_boxscore_stats(pk)
                ids = set(hitters) | set(pitchers)
            except Exception as exc:                       # noqa: BLE001
                if attempt == retries:
                    print(f"    gamePk {pk}: box score FAILED after "
                          f"{retries + 1} attempts ({exc})", file=sys.stderr)
                continue
            if ids:
                break

        if not ids:
            empty_pks.append(pk)
            pk_meta[pk]["n_players"] = 0
            continue

        pk_meta[pk]["n_players"] = len(ids)
        players: dict[str, int] = {}
        for pid in ids:
            try:
                ident = api.get_player_identity(int(pid))
                nm = norm(getattr(ident, "name", "") or "")
            except Exception:                              # noqa: BLE001
                nm = ""
            if not nm:
                continue
            # Within ONE game a normalised name is unique -- two Max Muncys are
            # never in the same box score. If it somehow is not, POISON it. We
            # would rather LOSE the name than silently keep the last writer.
            if nm in players and players[nm] != int(pid):
                print(f"    gamePk {pk}: TWO player_ids share the normalised "
                      f"name {nm!r} ({players[nm]} and {pid}). Impossible within "
                      f"one game. EXCLUDING the name.", file=sys.stderr)
                players[nm] = -1
                continue
            players[nm] = int(pid)
            name_to_pks[nm].add(pk)

        pk_to_players[pk] = players

    return name_to_pks, pk_meta, pk_to_players, empty_pks


def fetch_vendor(months: list[str], dates: list[str] | None) -> pd.DataFrame:
    """The vendor side AT ITS OWN GRAIN: (slate_date, game_id, start_time).

    *** NOT (slate_date, game_id). *** See the module docstring. This is the
    exact key run_pa_market_ab.fetch_market() builds, so the artifact can
    actually serve its consumer.

    scoped_rows counts ONLY the markets this milestone evaluates, so the
    coverage report can say how many HITS/HR ROWS an exclusion actually cost --
    not how many games.
    """
    hf = " UNION ALL ".join(
        f"SELECT * FROM 'hf://datasets/SmartStake/mlb-player-props/"
        f"mon={m}/*.parquet'" for m in months
    )
    scoped = ", ".join(f"'{m}'" for m in SCOPED_MARKETS)
    where = ""
    if dates:
        lst = ", ".join(f"DATE '{d}'" for d in dates)
        where = f"WHERE slate_date IN ({lst})"

    return duckdb.sql(f"""
        WITH src AS (SELECT * FROM ({hf})),
        tagged AS (
            SELECT *,
                   CAST(start_time AT TIME ZONE 'UTC'
                                   AT TIME ZONE 'America/New_York' AS DATE)
                       AS slate_date
            FROM src
        )
        SELECT slate_date,
               game_id                AS vendor_game_id,
               start_time,
               count(DISTINCT player) AS n_players,
               count(*)               AS n_rows,
               count(*) FILTER (WHERE market IN ({scoped})) AS scoped_rows,
               list(DISTINCT player)  AS players
        FROM tagged
        {where}
        GROUP BY 1, 2, 3
        ORDER BY 1, 3, 2
    """).df()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", nargs="+", default=None,
                    help="SmartStake partitions, e.g. 2026-03 2026-04 2026-05 2026-06")
    ap.add_argument("--dates", nargs="+", default=None,
                    help="restrict to these slate dates (still needs --months "
                         "unless the dates imply them)")
    ap.add_argument("--outdir", default="data/market/v2")
    args = ap.parse_args(argv)

    if not args.months and not args.dates:
        print("FATAL: pass --months (and optionally --dates).", file=sys.stderr)
        return 2
    months = args.months or sorted({d[:7] for d in args.dates})

    duckdb.sql("INSTALL httpfs; LOAD httpfs;")
    duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

    vend = fetch_vendor(months, args.dates)
    if vend.empty:
        print("FATAL: SmartStake returned no rows for those partitions.",
              file=sys.stderr)
        return 2

    # *** DuckDB's CAST(... AS DATE) returns a pandas TIMESTAMP, not a str. ***
    # str(Timestamp) -> "2026-06-01 00:00:00", and MLB's schedule endpoint
    # rejects that with an HTTP 400. v1's own comment records failing rule 1
    # TWICE on this exact file (list() -> numpy array; DATE -> Timestamp).
    # NORMALISE EXPLICITLY. Never rely on str() of a type you did not verify.
    vend["slate_date"] = pd.to_datetime(vend["slate_date"]).dt.strftime("%Y-%m-%d")
    vend["start_time"] = pd.to_datetime(vend["start_time"], utc=True)
    dates = sorted(vend.slate_date.unique().tolist())

    bad = [d for d in dates if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
    if bad:
        print(f"FATAL: malformed date(s) {bad}. The schedule endpoint needs "
              f"YYYY-MM-DD and answers anything else with an HTTP 400.",
              file=sys.stderr)
        return 2

    # ---- THE GRAIN CHECK. The premise of v2, tested before anything else. ----
    n_exact = len(vend)
    n_coarse = vend.groupby(["slate_date", "vendor_game_id"]).ngroups
    print("=" * 88)
    print("VENDOR GRAIN")
    print("=" * 88)
    print(f"  (slate_date, game_id)              : {n_coarse:,}   <- v1's grain")
    print(f"  (slate_date, game_id, start_time)  : {n_exact:,}   <- v2's grain")
    print(f"  fragmented identities              : {n_exact - n_coarse:,}")
    if n_exact == n_coarse:
        print("\n  *** THE EXACT GRAIN CHANGES NOTHING. ***")
        print("  Every vendor game_id carries exactly ONE start_time on these")
        print("  dates. The premise for v2's grain fix (and for expecting MORE")
        print("  collisions) is FALSE HERE. That contradicts the June measurement")
        print("  (243 -> 493). Do not proceed on an unexplained contradiction:")
        print("  re-measure before trusting anything downstream.", file=sys.stderr)
        return 2
    print(f"  {len(dates)} dates, {dates[0]} .. {dates[-1]}")

    game_rows: list[dict] = []
    player_rows: list[dict] = []
    report: dict = {"dates": {}, "excluded": [], "collisions": []}
    # Collisions are tracked GLOBALLY, keyed by mlb_game_pk (which is globally
    # unique). A duplicate identity in April must fail the June run -- see the
    # module docstring on pooled require_unique(MARKET_KEY).
    claimed: dict[int, list[dict]] = defaultdict(list)

    print()
    for gd in dates:
        api = MLBStatsAPI(season=int(gd[:4]))
        name_to_pks, pk_meta, pk_to_players, empty_pks = build_mlb_side(api, gd)

        sub = vend[vend.slate_date == gd]
        day = dict(vendor_identities=int(len(sub)), mlb_games=len(pk_meta),
                   empty_boxscores=empty_pks, resolved=0, excluded=0,
                   scoped_rows_total=int(sub.scoped_rows.sum()),
                   scoped_rows_resolved=0, scoped_rows_excluded=0,
                   excluded_by_reason=Counter(),
                   scoped_rows_lost_by_reason=Counter())

        for r in sub.itertuples():
            raw = r.players
            names = [norm(p) for p in list(raw)] if raw is not None else []

            votes: Counter[int] = Counter()
            matched = 0
            for nm in names:
                pks = name_to_pks.get(nm)
                if not pks:
                    continue
                matched += 1
                for pk in pks:
                    votes[pk] += 1

            top = votes.most_common(2)
            win_pk = top[0][0] if top else None
            win_votes = top[0][1] if top else 0
            run_votes = top[1][1] if len(top) > 1 else 0
            margin = (win_votes / run_votes) if run_votes else float("inf")

            # Reasons are TAGGED, not just prose. A thin fragment excluded for
            # "too few votes" and an ambiguous one excluded for "margin" are
            # DIFFERENT FAILURES, and pooling them would misattribute the
            # coverage loss (see the sanity-range note).
            tag = reason = None
            if not top:
                tag = "no_name_matched"
                reason = ("no name matched any box score. If this identity's "
                          "mlb_game_pk is in empty_boxscores, this is a MISSING "
                          "BOX SCORE, not a name problem.")
            elif win_votes < MIN_VOTES:
                tag = "too_few_votes"
                reason = (f"the winning game_pk got only {win_votes} votes "
                          f"(minimum {MIN_VOTES}). Likely a THIN VENDOR FRAGMENT: "
                          f"too few players to vote credibly. Its market rows "
                          f"still exist and are LOST, not absent.")
            elif margin < MIN_MARGIN:
                tag = "thin_margin"
                reason = (f"margin {margin:.1f}x is below {MIN_MARGIN}x "
                          f"({win_votes} vs {run_votes}). AMBIGUOUS -- and we do "
                          f"NOT pick arbitrarily.")

            if reason:
                day["excluded"] += 1
                day["excluded_by_reason"][tag] += 1
                day["scoped_rows_excluded"] += int(r.scoped_rows)
                day["scoped_rows_lost_by_reason"][tag] += int(r.scoped_rows)
                report["excluded"].append(dict(
                    slate_date=gd, vendor_game_id=r.vendor_game_id,
                    start_time=str(r.start_time), n_players=int(r.n_players),
                    scoped_rows=int(r.scoped_rows), matched=matched,
                    votes=dict(votes.most_common(3)), tag=tag, reason=reason,
                ))
                continue

            m = pk_meta[win_pk]
            claimed[win_pk].append(dict(
                slate_date=gd, vendor_game_id=r.vendor_game_id,
                start_time=r.start_time, scoped_rows=int(r.scoped_rows)))
            game_rows.append(dict(
                slate_date=gd,
                vendor_game_id=r.vendor_game_id,
                start_time=r.start_time,
                mlb_game_pk=win_pk,
                away=m["away"], home=m["home"],
                game_number=m["game_number"], doubleheader=m["doubleheader"],
                mlb_game_time_utc=m["game_time_utc"],
                n_vendor_players=int(r.n_players),
                scoped_rows=int(r.scoped_rows),
                n_matched=matched,
                match_rate=round(matched / max(1, len(names)), 4),
                win_votes=win_votes,
                runner_up_votes=run_votes,
                # The MARGIN is written into the artifact so MIN_MARGIN can be
                # second-guessed WITH DATA rather than trusted.
                margin=(None if run_votes == 0 else round(margin, 2)),
            ))

            # *** THE CONSUMER MAPPING. This is the whole point of v2. ***
            # (vendor_game_id, start_time, player_key) -> (mlb_game_pk, player_id)
            # Unique BY CONSTRUCTION: two players with the same normalised name
            # are never in the same box score, and one vendor identity resolves
            # to exactly one game_pk.
            for nm, pid in pk_to_players.get(win_pk, {}).items():
                if pid < 0:
                    continue                     # poisoned within-game collision
                player_rows.append(dict(
                    vendor_game_id=r.vendor_game_id,
                    start_time=r.start_time,
                    player_key=nm,
                    mlb_game_pk=win_pk,
                    player_id=pid,
                    slate_date=gd,               # audit only; NOT in the key
                ))

            day["resolved"] += 1
            day["scoped_rows_resolved"] += int(r.scoped_rows)

        day["excluded_by_reason"] = dict(day["excluded_by_reason"])
        day["scoped_rows_lost_by_reason"] = dict(day["scoped_rows_lost_by_reason"])
        report["dates"][gd] = day
        print(f"  {gd}  vendor {day['vendor_identities']:3d}  "
              f"mlb {day['mlb_games']:2d}  resolved {day['resolved']:3d}  "
              f"excluded {day['excluded']:3d}  "
              f"empty_box {len(empty_pks)}  "
              f"hits/hr rows {day['scoped_rows_resolved']:6,d}"
              f"/{day['scoped_rows_total']:6,d}")

    # =====================================================================
    # COLLISIONS -- GLOBAL, and EXCLUDED. This is load-bearing, not a policy.
    # =====================================================================
    coll = {pk: v for pk, v in claimed.items() if len(v) > 1}
    if coll:
        print()
        print("=" * 88)
        print(f"COLLISIONS — {len(coll)} mlb_game_pk claimed by >1 vendor identity")
        print("=" * 88)
        print("  MEASURED: duplicate vendor identities mapping to ONE mlb_game_pk.")
        print("  *** WE DO NOT CLAIM A CAUSE. *** An audit of v1's collisions found")
        print("  high player overlap and a ~+2h00m01s separation. That is a")
        print("  MEASUREMENT, not a mechanism (rule 10). Calling them 'vendor")
        print("  splits' would assert something never established.")
        print()
        print("  THE EXCLUSION IS NECESSARY, NOT PREFERRED:")
        print("    MARKET_KEY = (mlb_game_pk, player_id, category, line)")
        print("    -- NO date, NO start_time (verified in identity_keys.py).")
        print("  Two vendor identities on one mlb_game_pk collapse to the SAME")
        print("  MARKET_KEY, and run_pa_market_ab's require_unique() HARD FAILS.")
        print("  Keeping a collision does not cost coverage -- IT BREAKS THE RUN.")
        print()
        lost = 0
        for pk, ids in sorted(coll.items()):
            rows = sum(i["scoped_rows"] for i in ids)
            lost += rows
            print(f"  game_pk {pk}  ({len(ids)} identities, {rows:,} hits/hr rows)")
            for i in ids:
                print(f"      {i['slate_date']}  {i['vendor_game_id']}  "
                      f"{i['start_time']}")
            report["collisions"].append(dict(
                mlb_game_pk=int(pk), scoped_rows_lost=int(rows),
                identities=[dict(slate_date=i["slate_date"],
                                 vendor_game_id=i["vendor_game_id"],
                                 start_time=str(i["start_time"]))
                            for i in ids],
            ))
        bad_pks = set(coll)
        before = len(game_rows)
        game_rows = [g for g in game_rows if g["mlb_game_pk"] not in bad_pks]
        player_rows = [p for p in player_rows if p["mlb_game_pk"] not in bad_pks]
        print(f"\n  EXCLUDED {before - len(game_rows)} vendor identities "
              f"({lost:,} hits/hr market rows). Never guessed at.")
        report["collision_scoped_rows_lost"] = int(lost)

    if not game_rows:
        print("\nFATAL: nothing resolved.", file=sys.stderr)
        return 2

    G = pd.DataFrame(game_rows)
    P = pd.DataFrame(player_rows)

    # =====================================================================
    # *** VALIDATE. NEVER DEDUPE. *** (v1's defect 3.)
    # =====================================================================
    # v1 called drop_duplicates(subset=[...]) and THEN asserted uniqueness on
    # the SAME subset -- an assertion that could not fail. v2 checks the RAW
    # frame and HARD FAILS. If this ever fires, the artifact is WRONG and the
    # right response is to understand why, not to dedupe it away.
    #
    # *** AND THE INVARIANTS HOLD AT DIFFERENT GRAINS. FOUND BY THE HARNESS. ***
    # I first wrote this block asserting BOTH keys on the same frame, and it
    # passed -- but only because collisions had ALREADY been excluded above.
    # THAT IS ORDERING, NOT VERIFICATION -- the identical defect as v1's, one
    # layer up. The two invariants are NOT the same claim:
    #
    #   (vendor_game_id, start_time, player_key)  UNIQUE on the RAW frame.
    #       The consumer key. Must hold unconditionally.
    #
    #   (mlb_game_pk, player_key)                 UNIQUE only POST-EXCLUSION.
    #       On the RAW frame a FRAGMENTED vendor id re-emits the SAME mapping
    #       once per identity (m~A@23:05 and m~A@23:06 both yield
    #       (900,"max muncy")->600). That is ONE mapping twice, not two Muncys
    #       -- the mapping is well defined, the ROW is repeated. The invariant
    #       is restored by the collision exclusion, and it is stated HERE as
    #       depending on it, rather than being quietly true.
    #
    # v1 printed "(mlb_game_pk, player_key) is UNIQUE" as an unconditional fact.
    # It is not. It is a CONSEQUENCE of excluding collisions.
    print()
    print("=" * 88)
    print("VALIDATION (before any dedupe -- there is none)")
    print("=" * 88)
    rc = 0
    P_kept = P[~P.mlb_game_pk.isin(set(coll))] if coll else P
    for frame, keys, what in (
        (P, ["vendor_game_id", "start_time", "player_key"],
         "THE CONSUMER KEY -- load_crosswalk() require_unique()s exactly this. "
         "RAW frame; must hold unconditionally."),
        (P_kept, ["mlb_game_pk", "player_key"],
         "(game, name) -> player_id -- the Max Muncy guarantee. POST-EXCLUSION: "
         "it holds BECAUSE collisions were removed, and only then."),
        (G, ["vendor_game_id", "start_time"],
         "one vendor identity -> one mlb_game_pk"),
        (G, ["mlb_game_pk"],
         "one mlb_game_pk -> one vendor identity (collisions excluded)"),
    ):
        dup = frame[frame.duplicated(subset=keys, keep=False)]
        if dup.empty:
            print(f"  [OK]   unique on {keys}")
            print(f"         {what}")
        else:
            rc = 1
            print(f"\n  *** {len(dup)} DUPLICATE ROWS on {keys} ***",
                  file=sys.stderr)
            print(f"  {what}", file=sys.stderr)
            print("  This should be IMPOSSIBLE. The artifact is NOT trustworthy.",
                  file=sys.stderr)
            print(dup.sort_values(keys).head(12).to_string(index=False),
                  file=sys.stderr)

    # The mapping must be WELL DEFINED even where a fragment repeats the row:
    # every duplicated (game_pk, name) must agree on player_id. If two rows
    # disagreed, that would be a genuine identity failure -- and it would be
    # INVISIBLE to a plain duplicated() check.
    disagree = P.groupby(["mlb_game_pk", "player_key"]).player_id.nunique()
    bad_map = disagree[disagree > 1]
    if bad_map.empty:
        print(f"  [OK]   every (mlb_game_pk, player_key) agrees on ONE player_id")
        print(f"         the MAPPING is well defined even where the ROW repeats")
    else:
        rc = 1
        print(f"\n  *** {len(bad_map)} (mlb_game_pk, player_key) map to MORE THAN "
              f"ONE player_id ***", file=sys.stderr)
        print("  A plain duplicated() check would NOT see this. It is a genuine "
              "identity failure.", file=sys.stderr)
        print(bad_map.head(12).to_string(), file=sys.stderr)

    if rc:
        print("\n  *** NOT WRITING THE CONSUMER ARTIFACT. ***", file=sys.stderr)
        print("  A crosswalk that cannot prove its own key is not a crosswalk.",
              file=sys.stderr)
        return 1

    # =====================================================================
    print()
    print("=" * 88)
    print("THE CROSSWALK (v2)")
    print("=" * 88)
    n_vendor, n_res = int(len(vend)), int(len(G))
    rows_total = int(vend.scoped_rows.sum())
    rows_res = int(G.scoped_rows.sum())
    print(f"  vendor identities   : {n_vendor:,}   (exact grain)")
    print(f"  RESOLVED            : {n_res:,}  ({n_res / n_vendor:.1%})")
    print(f"  excluded (vote)     : {len(report['excluded']):,}")
    print(f"  excluded (collision): {sum(len(c['identities']) for c in report['collisions']):,}")
    print(f"  player mappings     : {len(P):,}")
    print()
    print(f"  *** HITS/HR MARKET ROWS COVERED : {rows_res:,} / {rows_total:,} "
          f"({rows_res / max(rows_total, 1):.1%}) ***")
    print("  (rows, not games -- an exclusion costs ROWS, and that is what the")
    print("   A/B actually loses)")
    print()
    print(f"  match rate  : mean {G.match_rate.mean():.1%}  min {G.match_rate.min():.1%}")
    print(f"  win votes   : mean {G.win_votes.mean():.1f}  min {int(G.win_votes.min())}")
    fin = G[G.margin.notna()]
    unanimous = len(G) - len(fin)
    print(f"  UNANIMOUS   : {unanimous:,} of {len(G):,} "
          f"({unanimous / len(G):.0%}) had ZERO runner-up votes")
    if not fin.empty:
        print(f"  worst margin: {fin.margin.min():.1f}x   (boundary {MIN_MARGIN}x)")

    # ---- the sanity ranges, JUDGED (rule 7) ------------------------------
    print()
    print("  SANITY (declared before the run):")
    checks = [
        ("resolved rate 60-90%", 0.60 <= n_res / n_vendor <= 0.90,
         f"{n_res / n_vendor:.1%}"),
        ("mean win votes 15-25", 15 <= G.win_votes.mean() <= 25,
         f"{G.win_votes.mean():.1f}"),
        ("worst margin >= 3x", fin.empty or fin.margin.min() >= 3.0,
         "n/a (all unanimous)" if fin.empty else f"{fin.margin.min():.1f}x"),
    ]
    for name, ok, got in checks:
        print(f"    [{'OK' if ok else '!!'}] {name:24s} {got}")
    if not all(ok for _, ok, _ in checks):
        print()
        print("  *** A SANITY RANGE WAS MISSED. *** The artifact is WRITTEN (so it")
        print("  can be inspected) but it is NOT CERTIFIED. Read the misses above")
        print("  before any consumer trusts this. A missed range is a STOP")
        print("  CONDITION, not a tuning opportunity.", file=sys.stderr)
        rc = 1

    if report["excluded"]:
        print()
        print("  EXCLUDED BY REASON (hits/hr rows lost):")
        agg = Counter()
        rows_by = Counter()
        for e in report["excluded"]:
            agg[e["tag"]] += 1
            rows_by[e["tag"]] += e["scoped_rows"]
        for tag, n in agg.most_common():
            print(f"    {tag:18s} {n:4d} identities   {rows_by[tag]:7,d} rows")

    # ---- emit ------------------------------------------------------------
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    # THE CONSUMER TABLE. Exactly load_crosswalk()'s required columns, in order.
    consumer = P[["vendor_game_id", "start_time", "player_key",
                  "mlb_game_pk", "player_id"]]
    paths = {
        "crosswalk_players.csv": consumer,     # <- the one the A/B reads
        "crosswalk_games.csv": G,              # <- audit: votes, margins
        "crosswalk_players_audit.csv": P,      # <- audit: + slate_date
    }
    for name, df in paths.items():
        df.to_csv(out / name, index=False)
        print(f"\nwrote {out / name}  ({len(df):,} rows)")

    report["_comment"] = (
        "SmartStake (vendor_game_id, start_time, player_key) -> (mlb_game_pk, "
        "player_id), resolved by a VOTE of the vendor identity's own players "
        "against MLB box scores. SmartStake carries NO TEAMS and NO PLAYER IDS. "
        "v2 resolves at the EXACT VENDOR GRAIN -- (slate_date, game_id, "
        "start_time) -- because that is the key run_pa_market_ab.fetch_market() "
        "builds, and because start_time is in that key for a reason: ctrl4 caught "
        "entry prices recorded 23 HOURS AFTER first pitch when it was absent. "
        "COLLISIONS (one mlb_game_pk claimed by >1 vendor identity) are EXCLUDED "
        "GLOBALLY. That exclusion is NECESSARY, not preferred: MARKET_KEY = "
        "(mlb_game_pk, player_id, category, line) carries no date and no "
        "start_time, so two identities on one game_pk collapse to the SAME key "
        "and require_unique() hard-fails the run. The CAUSE of the duplication is "
        "NOT established and is not claimed. v2 NEVER dedupes before validating: "
        "v1 dropped duplicates on the same subset it then asserted uniqueness on, "
        "so the assertion could not fire."
    )
    report["provenance"] = dict(
        built_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        months=months, dates=dates,
        min_votes=MIN_VOTES, min_margin=MIN_MARGIN,
        scoped_markets=list(SCOPED_MARKETS),
        vendor_identities_exact=n_vendor,
        vendor_identities_coarse=int(n_coarse),
        n_resolved=n_res,
        n_player_mappings=int(len(P)),
        scoped_rows_total=rows_total,
        scoped_rows_resolved=rows_res,
        scoped_row_coverage=round(rows_res / max(rows_total, 1), 4),
        certified=(rc == 0),
    )
    rp = out / "crosswalk_report.json"
    with rp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2, default=str)
        f.write("\n")
    print(f"wrote {rp}")

    print()
    print("=" * 88)
    print("THE MARKET KEY IS HARD" if rc == 0 else "*** NOT CERTIFIED ***")
    print("=" * 88)
    print("  (vendor_game_id, start_time, player_key) -> (mlb_game_pk, player_id)")
    print()
    print("  MARKET_KEY = (mlb_game_pk, player_id, category, line)")
    print("  -- the SAME key the model emits. No name join, no date join, no")
    print("     tolerance window, no drop_duplicates.")
    print()
    print("  SCOPE: hits and home_runs ONLY. Not HRR (no book market anywhere).")
    print("         Not RBI (its structural dependency is unresolved -- the HRR")
    print("         fix FAILED its gate and is inert). Not total_bases (the next")
    print("         model-version change; it needs its own gate).")
    return rc


if __name__ == "__main__":
    sys.exit(main())
