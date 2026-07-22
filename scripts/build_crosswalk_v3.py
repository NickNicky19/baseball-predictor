#!/usr/bin/env python3
"""
BUILD THE CROSSWALK, v3 — a MAPPING. It maps. It does not adjudicate.

    (vendor_game_id, start_time, player_key)  ->  (mlb_game_pk, player_id)

v1 and v2 are PRESERVED as audit evidence and are not overwritten. v3 writes to
data/market/v3/.

=============================================================================
*** THE POLICY v2 ENFORCED WAS WRONG, AND IT WAS MY ARGUMENT. ***
=============================================================================
v2 excluded, GLOBALLY, every mlb_game_pk claimed by more than one vendor
fragment. I argued the exclusion was LOAD-BEARING:

    "MARKET_KEY = (mlb_game_pk, player_id, category, line) carries no date and
     no start_time, so two fragments on one game_pk collapse to the SAME key and
     require_unique() hard-fails. Keeping a collision does not cost coverage --
     IT BREAKS THE RUN."

*** I INFERRED THAT FROM THE SHAPE OF THE KEY AND NEVER MEASURED IT. *** And my
own harness fixture ENCODED THE CONCLUSION: it assumed both fragments emit full
player sets, so of course they duplicated. A test whose fixture assumes the
answer is not a test (rule 4).

WHAT THE MEASUREMENTS ACTUALLY SHOWED:
  * three of the four collided pairs produced ZERO eligible A/B rows. Stubs.
    They duplicate NOTHING.
  * bucket C (14 game_ids, 6-90 min): real fragment + a +/-1-min twin + a
    +60-min STUB carrying ~0.05% of the rows. Same shape, twelve times over.
  * eligible_key_relation across 28 mapped pairs:
        NEITHER_ELIGIBLE 20   OVERLAPPING 8   *** DISJOINT 0 ***
  * the 52.6% resolved rate was an ARTIFACT OF MY POLICY, not a coverage cost.
    18 of 19 fragments mapped cleanly; the exclusion then threw 8 of them away.

So v2 was DISCARDING COMPLETE GAMES to guard against a duplication that mostly
does not exist.

=============================================================================
THE v3 CONTRACT
=============================================================================
  * hard uniqueness on (vendor_game_id, start_time, player_key). THAT IS ALL.
  * multiple fragments mapping to one (mlb_game_pk, player_id) are REPORTED,
    NEVER PREEMPTIVELY REMOVED.
  * *** THE A/B ALONE DECIDES DUPLICATION *** -- after eligibility filtering,
    via require_unique(MARKET_KEY) on the rows that actually survive.
  * a duplicate ELIGIBLE MARKET_KEY hard-fails, WITH BOTH FRAGMENT IDENTITIES
    NAMED, so the failure points at the DATA rather than at itself.
  * a NON-ELIGIBLE STUB MUST COEXIST WITHOUT FAILURE.

The vote MAPS. It does not MERGE, and it does not DISCARD. Two fragments voting
for one game_pk is strong evidence about their MAPPING and is NOT authority to
decide they are one thing.

=============================================================================
COVERAGE IS COMPUTED WITH THE *** SHARED *** ELIGIBILITY FUNCTION
=============================================================================
src/evaluation/market_eligibility.eligibility_sql -- the SAME callable
run_market_ab.py scores with. NOT a local copy.

v2 reported coverage over RAW QUOTE SNAPSHOTS (~75 books x every minute x both
sides) and produced a meaningless 65.9%. A ratio over the wrong denominator is a
claim about a quantity nobody defined (rule 8). Writing a SECOND COPY of the
right denominator would be the same error one layer up -- so there is exactly
ONE definition, and check_one_denominator_offline.py asserts (8/8) that both
callers produce a byte-identical universe, INCLUDING PRICES, because a key-only
comparison passes on a bug that preserves identity and changes value.

=============================================================================
THE VOTE DISTRIBUTION IS REPORTED *** PRE-FILTER ***
=============================================================================
v2 printed "mean 20.3 win votes, worst margin 18.0x, 90% unanimous" and I read
it as "the vote is healthy". *** THOSE WERE ACCEPTED ROWS. *** Acceptance
REQUIRED win_votes >= MIN_VOTES and margin >= MIN_MARGIN.

*** A THRESHOLD-ENFORCED STATISTIC CANNOT TEST THE PREMISE ITS THRESHOLD
*** ENCODES. *** Of course the accepted margins clear the boundary; they were
selected to. The distribution that could FALSIFY the premise is the PRE-FILTER
one -- mapped, thin, ambiguous and unmapped alike -- and v2 never printed it.

v3 reports every fragment with its votes and margin, tagged by outcome, so the
boundaries can be JUDGED against data rather than TRUSTED.

Usage:
  python scripts/build_crosswalk_v3.py --months 2026-06
  python scripts/build_crosswalk_v3.py --months 2026-03 2026-04 2026-05 2026-06
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

from src.data.mlb_api import MLBStatsAPI                       # noqa: E402
from src.evaluation.market_eligibility import (                # noqa: E402
    MARKET_MAP, STATUS_ABSENT, STATUS_STALE, assert_no_leakage, eligibility_sql,
    load_policy, parquet_source, scoreable,
)

# UNCHANGED from v1/v2 ON PURPOSE. v3 changes the POLICY, not the vote. Moving
# both at once would confound them.
MIN_VOTES = 8
MIN_MARGIN = 5.0


def norm(s: object) -> str:
    """MUST stay byte-identical to run_market_ab.norm_name. Halves of one join."""
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s))
                if not unicodedata.combining(c))
    s = s.lower().strip()
    for ch in ".'`-":
        s = s.replace(ch, "")
    return " ".join(p for p in s.split()
                    if p not in ("jr", "sr", "ii", "iii", "iv", "v"))


def build_mlb_index(api: MLBStatsAPI, game_date: str):
    name_to_pks: dict[str, set[int]] = defaultdict(set)
    pk_players: dict[int, dict[str, int]] = {}
    pk_meta: dict[int, dict] = {}
    empty: list[int] = []
    # Calendar membership is not game identity: MLB can list a postponed or
    # suspended game_pk on both its original and eventual official dates.
    # Reuse the canonical API boundary, which requires codedGameState ``F``
    # and feed officialDate == game_date.
    canonical_pks = set(api.get_final_game_pks(game_date))
    for g in api.get_schedule(game_date):
        pk = int(g["gamePk"])
        if pk not in canonical_pks:
            continue
        teams = g.get("teams", {}) or {}
        pk_meta[pk] = dict(
            away=((teams.get("away", {}) or {}).get("team", {}) or {}).get("name", ""),
            home=((teams.get("home", {}) or {}).get("team", {}) or {}).get("name", ""),
            official_date=game_date, game_number=g.get("gameNumber"),
            doubleheader=g.get("doubleHeader"))
        try:
            hitters, pitchers = api.get_game_boxscore_stats(pk)
            ids = set(hitters) | set(pitchers)
        except Exception:                                       # noqa: BLE001
            ids = set()
        if not ids:
            # An EMPTY box score casts NO VOTES, so any fragment mapping to it
            # fails as "no name matched" -- which would send us hunting a name
            # bug that does not exist. A silent skip does not just lose a game;
            # IT MISATTRIBUTES THE FAILURE.
            empty.append(pk)
            continue
        players: dict[str, int] = {}
        for pid in ids:
            try:
                nm = norm(getattr(api.get_player_identity(int(pid)), "name", "") or "")
            except Exception:                                   # noqa: BLE001
                nm = ""
            if not nm:
                continue
            if nm in players and players[nm] != int(pid):
                players[nm] = -1        # poison: impossible within one box score
                continue
            players[nm] = int(pid)
            name_to_pks[nm].add(pk)
        pk_players[pk] = players
    return name_to_pks, pk_players, pk_meta, empty


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/market/smartstake")
    ap.add_argument("--months", nargs="+", required=True)
    ap.add_argument("--policy", default="config/ab_policy.json",
                    help="THE source of book / entry_hours / max_quote_age. A CLI "
                         "default would recreate the drift.")
    ap.add_argument("--outdir", default="data/market/v3")
    args = ap.parse_args(argv)

    # *** ALL THREE CALLERS READ THESE FROM THE POLICY ARTIFACT. ***
    # A copied `90` agrees today and diverges the moment the policy changes -- and
    # the sha in every report is what makes that agreement EVIDENCE rather than a
    # coincidence.
    vals, policy_sha, research_only, unapproved = load_policy(args.policy)
    book = vals["book"]
    entry_hours = vals["entry_hours"]
    max_quote_age = vals["max_quote_age"]

    for m in args.months:
        if not list(Path(args.root).glob(f"mon={m}/*.parquet")):
            print(f"FATAL: no parquet under {args.root}/mon={m}/", file=sys.stderr)
            return 2

    print("=" * 92)
    print(f"CROSSWALK v3 — {args.months}")
    print("=" * 92)
    print("  A MAPPING. It maps. It does NOT adjudicate duplication.")
    print("  Nothing is preemptively excluded. The A/B decides, after eligibility.")
    print(f"  policy {policy_sha}   book={book}  T-{entry_hours}h  "
          f"max_quote_age={max_quote_age}min")
    print()

    # =====================================================================
    # THE DENOMINATOR. *** THE SHARED FUNCTION. NOT A LOCAL COPY. ***
    # =====================================================================
    elig = duckdb.sql(eligibility_sql(
        parquet_source(args.root, args.months), book, entry_hours,
        max_quote_age)).df()
    assert_no_leakage(elig)          # negative age is LEAKAGE, not a status
    elig["start_time"] = pd.to_datetime(elig.start_time, utc=True)
    elig["player_key"] = elig.player.map(norm)
    elig["category"] = elig.market.map(MARKET_MAP)
    elig["market_date"] = pd.to_datetime(elig.market_date).dt.strftime("%Y-%m-%d")

    # *** THE FINAL SCOREABLE SUBSET. *** v3's first run used the PRE-STALE frame
    # (settlement only), because freshness was still applied downstream in the
    # runner alone. That is why 97.8% and 2,898 were provisional: they counted
    # rows the evaluator would have dropped.
    SC = scoreable(elig)
    n_abs = int((elig.status == STATUS_ABSENT).sum())
    n_stale = int((elig.status == STATUS_STALE).sum())
    print(f"[denominator] {len(elig):,} eligible selections")
    print(f"              settlement_absent {n_abs:,}   stale {n_stale:,}   "
          f"*** SCOREABLE {len(SC):,} ***")
    print(f"              via the SHARED eligibility function — the same callable,")
    print(f"              the same policy, and the SAME FINAL SUBSET the A/B scores.")

    # ---- EVERY vendor fragment. NOT just the ones with eligible rows. -----
    # A fragment with zero eligible rows is still a fragment, and it still needs
    # a mapping row if any of its players are ever priced. Restricting the
    # crosswalk to the eligible universe would make the crosswalk's coverage
    # trivially 100% -- a denominator that defines away its own question.
    frags = duckdb.sql(f"""
        WITH raw AS ({parquet_source(args.root, args.months)})
        SELECT game_id AS vendor_game_id, start_time,
               CAST(start_time AT TIME ZONE 'UTC'
                               AT TIME ZONE 'America/New_York' AS DATE) AS slate_date,
               count(DISTINCT player) AS n_players,
               list(DISTINCT player)  AS players
        FROM raw
        GROUP BY 1, 2, 3
        ORDER BY 3, 1, 2
    """).df()
    frags["slate_date"] = pd.to_datetime(frags.slate_date).dt.strftime("%Y-%m-%d")
    frags["start_time"] = pd.to_datetime(frags.start_time, utc=True)
    dates = sorted(frags.slate_date.unique())
    bad = [d for d in dates if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
    if bad:
        print(f"FATAL: malformed date(s) {bad}", file=sys.stderr)
        return 2
    print(f"[vendor]      {len(frags):,} fragments over {len(dates)} slate dates")

    # ---- MLB side ---------------------------------------------------------
    idx = {}
    empties: dict[str, list[int]] = {}
    for d in dates:
        n2p, p2p, meta, empty = build_mlb_index(MLBStatsAPI(season=int(d[:4])), d)
        idx[d] = (n2p, p2p, meta)
        if empty:
            empties[d] = empty
    print(f"[mlb]         indexed {len(dates)} dates"
          + (f"   ({sum(len(v) for v in empties.values())} EMPTY box scores — "
             f"reported, never silently skipped)" if empties else ""))

    # =====================================================================
    # THE VOTE. It MAPS. Every fragment is judged ON ITS OWN.
    # =====================================================================
    rows: list[dict] = []
    mappings: list[dict] = []
    for r in frags.itertuples():
        n2p, p2p, meta = idx[r.slate_date]
        votes: Counter[int] = Counter()
        matched = 0
        names = [norm(p) for p in list(r.players)] if r.players is not None else []
        for nm in names:
            pks = n2p.get(nm)
            if not pks:
                continue
            matched += 1
            for pk in pks:
                votes[pk] += 1
        top = votes.most_common(2)
        wv = top[0][1] if top else 0
        rv = top[1][1] if len(top) > 1 else 0
        margin = (wv / rv) if rv else float("inf")

        # OUTCOME TAGS. Each is a DIFFERENT failure and they are never pooled.
        if not top:
            outcome, pk = "no_name_matched", None
        elif wv < MIN_VOTES:
            outcome, pk = "thin", None          # too few players to vote credibly
        elif margin < MIN_MARGIN:
            outcome, pk = "ambiguous", None     # and we do NOT pick arbitrarily
        else:
            outcome, pk = "mapped", int(top[0][0])

        m = meta.get(pk, {}) if pk else {}
        rows.append(dict(
            vendor_game_id=r.vendor_game_id, start_time=r.start_time,
            slate_date=r.slate_date, n_players=int(r.n_players),
            n_matched=matched,
            match_rate=round(matched / max(1, len(names)), 4),
            # *** PRE-FILTER. Reported for EVERY fragment -- mapped, thin,
            # ambiguous and unmatched alike. An ACCEPTED-ROW distribution is
            # THRESHOLD-ENFORCED and cannot test the premise its threshold
            # encodes. ***
            win_votes=wv, runner_up=rv,
            margin=(None if rv == 0 else round(margin, 2)),
            outcome=outcome, mlb_game_pk=pk,
            official_date=m.get("official_date"),
            matchup=(f"{m.get('away','')} @ {m.get('home','')}" if pk else None),
            game_number=m.get("game_number"), doubleheader=m.get("doubleheader"),
        ))
        if pk is None:
            continue
        for nm, pid in p2p.get(pk, {}).items():
            if pid < 0:
                continue
            mappings.append(dict(
                vendor_game_id=r.vendor_game_id, start_time=r.start_time,
                player_key=nm, mlb_game_pk=pk, player_id=pid,
                slate_date=r.slate_date))          # audit only; not in the key

    F = pd.DataFrame(rows)
    P = pd.DataFrame(mappings)
    if P.empty:
        print("\nFATAL: nothing mapped.", file=sys.stderr)
        return 2

    # =====================================================================
    # VALIDATION. *** THE CONSUMER KEY. THAT IS THE ONLY HARD INVARIANT. ***
    # =====================================================================
    print()
    print("=" * 92)
    print("VALIDATION")
    print("=" * 92)
    dup = P[P.duplicated(["vendor_game_id", "start_time", "player_key"],
                         keep=False)]
    if len(dup):
        print(f"  *** {len(dup)} DUPLICATE CONSUMER KEYS. ***", file=sys.stderr)
        print("  (vendor_game_id, start_time, player_key) is what load_crosswalk()"
              " require_unique()s.", file=sys.stderr)
        print(dup.sort_values(["vendor_game_id", "start_time", "player_key"])
                 .head(12).to_string(index=False), file=sys.stderr)
        print("\n  NOT WRITING THE ARTIFACT.", file=sys.stderr)
        return 1
    print("  [OK] (vendor_game_id, start_time, player_key) is UNIQUE")
    print("       The consumer key. The ONLY hard invariant this artifact owes.")

    # A mapping must be WELL DEFINED even where a fragment repeats a row.
    dis = P.groupby(["mlb_game_pk", "player_key"]).player_id.nunique()
    if (dis > 1).any():
        print(f"\n  *** {int((dis>1).sum())} (game_pk, name) map to MORE THAN ONE "
              f"player_id. *** A plain duplicated() check would NOT see this.",
              file=sys.stderr)
        print(dis[dis > 1].head(12).to_string(), file=sys.stderr)
        return 1
    print("  [OK] every (mlb_game_pk, player_key) agrees on ONE player_id")
    print("       Well defined even where a fragment repeats the row.")

    # =====================================================================
    # MULTI-FRAGMENT game_pks: *** REPORTED. NOT REMOVED. ***
    # =====================================================================
    mf = (F[F.outcome == "mapped"]
          .groupby("mlb_game_pk")
          .agg(fragments=("vendor_game_id", "size"))
          .query("fragments > 1"))
    print()
    print("=" * 92)
    print(f"MULTI-FRAGMENT game_pks — {len(mf)}   *** REPORTED, NOT REMOVED ***")
    print("=" * 92)
    if len(mf):
        print("  v2 EXCLUDED these globally. That policy was WRONG and it was my")
        print("  argument: I inferred 'the exclusion is load-bearing' from the SHAPE")
        print("  of MARKET_KEY and never measured it. MEASURED: three of four")
        print("  collided pairs produced ZERO eligible rows. They duplicate NOTHING.")
        print()
        print("  *** WHETHER THEY ARE A REAL DUPLICATE IS THE A/B's QUESTION, AND")
        print("  IT IS ANSWERED AFTER ELIGIBILITY FILTERING, NOT HERE. ***")
        print()
        # *** THE POST-ELIGIBILITY DIAGNOSTIC. Fragment identity is PRESERVED all
        # the way through, so a hard failure NAMES THE FRAGMENTS THAT CAUSED IT.
        e = SC.merge(P, on=["vendor_game_id", "start_time", "player_key"],
                            how="inner", validate="many_to_one")
        MK = ["mlb_game_pk", "player_id", "category", "line"]
        d = e[e.duplicated(MK, keep=False)]
        if len(d):
            print(f"  *** {len(d)} ELIGIBLE rows share a MARKET_KEY. ***")
            print("  These are GENUINE duplicates: TWO fragments each producing a")
            print("  SCOREABLE row for ONE key. The A/B will HARD-FAIL on them, and")
            print("  it will NAME THE FRAGMENTS -- which is why fragment identity is")
            print("  carried all the way through.")
            print("  " + d.sort_values(MK)[["vendor_game_id", "start_time", *MK]]
                  .head(20).to_string(index=False).replace("\n", "\n  "))
        else:
            print(f"  [OK] ZERO duplicate eligible MARKET_KEYs across "
                  f"{len(mf)} multi-fragment game_pk.")
            print("  Every extra fragment produces NO SCOREABLE ROW, so it")
            print("  duplicates nothing and costs nothing. *** v2 was throwing away")
            print("  complete games to guard against this. ***")
        # *** NOT "stubs". *** That word asserts a CAUSE -- that the vendor emitted
        # a deliberate partial listing -- and no such thing was ever established
        # (rule 10). What was MEASURED is that these fragments produce no scoreable
        # row. Call them what they are.
        extras = int(len(mf) - (d.mlb_game_pk.nunique() if len(d) else 0))
        print(f"\n  multi-fragment game_pks whose extras are NON-SCOREABLE EXTRA")
        print(f"  FRAGMENTS: {extras}/{len(mf)}   (the cause is NOT established)")
    else:
        print("  none")

    # =====================================================================
    # THE PRE-FILTER VOTE DISTRIBUTION. The premise, testable.
    # =====================================================================
    print()
    print("=" * 92)
    print("VOTE DISTRIBUTION — *** PRE-FILTER ***")
    print("=" * 92)
    print("  v2 printed the ACCEPTED rows' votes and margins and I read them as")
    print("  'the vote is healthy'. Acceptance REQUIRED those thresholds. A")
    print("  THRESHOLD-ENFORCED STATISTIC CANNOT TEST ITS OWN THRESHOLD'S PREMISE.")
    print()
    t = (F.groupby("outcome")
           .agg(fragments=("win_votes", "size"),
                votes_min=("win_votes", "min"), votes_med=("win_votes", "median"),
                votes_max=("win_votes", "max"),
                match_rate=("match_rate", "mean"))
           .sort_values("fragments", ascending=False))
    print("  " + t.to_string().replace("\n", "\n  "))
    print(f"\n  boundaries: MIN_VOTES={MIN_VOTES}   MIN_MARGIN={MIN_MARGIN}x")
    fin = F[F.margin.notna()]
    if len(fin):
        print(f"  margins (all fragments with ANY runner-up, n={len(fin)}): "
              f"min {fin.margin.min():.1f}x   median {fin.margin.median():.1f}x")
        near = fin[fin.margin < MIN_MARGIN * 1.5]
        print(f"  fragments within 1.5x of the margin boundary: {len(near)}"
              + ("   *** the boundary is doing real work; judge it. ***"
                 if len(near) else "   (the boundary is nowhere near binding)"))
    print(f"  UNANIMOUS (zero runner-up): {int(F.margin.isna().sum())}/{len(F)}")

    # =====================================================================
    # COVERAGE, over THE A/B's UNIVERSE.
    # =====================================================================
    mapped = SC.merge(
        P[["vendor_game_id", "start_time", "player_key", "mlb_game_pk", "player_id"]],
        on=["vendor_game_id", "start_time", "player_key"], how="left",
        validate="many_to_one", indicator=True)
    cov = mapped._merge == "both"
    print()
    print("=" * 92)
    print("COVERAGE — over the A/B's ELIGIBLE, SCOREABLE universe")
    print("=" * 92)
    print(f"  eligible + settled + FRESH : {len(SC):,}")
    print(f"  HARD-MAPPED        : {int(cov.sum()):,}   "
          f"({cov.mean():.1%})")
    print(f"  unmapped           : {int((~cov).sum()):,}")
    print("  (NOT over raw quote snapshots. v2 reported 65.9% over ~75 books x")
    print("   every minute x both sides -- a denominator nobody defined.)")
    print()
    print("  by market:")
    bm = (mapped.assign(m=cov).groupby("category")
                .agg(eligible=("m", "size"), mapped=("m", "sum")))
    bm["rate"] = (bm.mapped / bm.eligible).map(lambda x: f"{x:.1%}")
    print("    " + bm.to_string().replace("\n", "\n    "))

    # ---- emit --------------------------------------------------------------
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    consumer = P[["vendor_game_id", "start_time", "player_key",
                  "mlb_game_pk", "player_id"]]
    consumer.to_csv(out / "crosswalk_players.csv", index=False)
    F.to_csv(out / "crosswalk_fragments.csv", index=False)   # PRE-FILTER, all
    print(f"\nwrote {out / 'crosswalk_players.csv'}  ({len(consumer):,} rows)")
    print(f"wrote {out / 'crosswalk_fragments.csv'}  ({len(F):,} rows — EVERY "
          f"fragment, with its PRE-FILTER votes and margin)")

    accepted_dates = sorted(F[F.outcome == "mapped"].official_date.dropna().unique())
    report = dict(
        _comment=(
            "Crosswalk v3. A MAPPING: it maps, it does not adjudicate. NOTHING is "
            "preemptively excluded. Multiple fragments mapping to one mlb_game_pk "
            "are REPORTED, never removed -- v2 excluded them globally on my "
            "argument that the exclusion was 'load-bearing', which I INFERRED FROM "
            "THE SHAPE OF MARKET_KEY AND NEVER MEASURED. Measured: three of four "
            "collided pairs produce ZERO eligible rows and duplicate NOTHING. "
            "Duplication is the A/B's question, answered AFTER eligibility "
            "filtering via require_unique(MARKET_KEY), where a real duplicate "
            "hard-fails WITH BOTH FRAGMENT IDENTITIES NAMED. Coverage is computed "
            "with the SHARED eligibility function (src/evaluation/"
            "market_eligibility.py), the same callable the A/B scores with -- not "
            "a second copy that could drift. The vote distribution is reported "
            "PRE-FILTER, because an accepted-row distribution is threshold-enforced "
            "and cannot test the premise its threshold encodes."),
        built_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        months=args.months, book=book, entry_hours=entry_hours,
        max_quote_age=max_quote_age,
        min_votes=MIN_VOTES, min_margin=MIN_MARGIN,
        fragments=int(len(F)),
        outcomes={k: int(v) for k, v in F.outcome.value_counts().items()},
        multi_fragment_game_pks=int(len(mf)),
        eligible_selections=int(len(elig)),
        eligible_settled_fresh=int(len(SC)),
        settlement_absent=n_abs, stale=n_stale,
        policy_sha=policy_sha, research_only=research_only,
        hard_mapped=int(cov.sum()),
        coverage=round(float(cov.mean()), 4),
        empty_boxscores={k: v for k, v in empties.items()},
        accepted_date_universe=accepted_dates,
    )
    (out / "crosswalk_report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wrote {out / 'crosswalk_report.json'}")
    print(f"\n  ACCEPTED DATE UNIVERSE: {len(accepted_dates)} official dates")
    print(f"  {accepted_dates[0]} .. {accepted_dates[-1]}"
          if accepted_dates else "  (none)")
    print("  *** This is what the fresh 2026 reconstruction must cover. ***")
    return 0


if __name__ == "__main__":
    sys.exit(main())
