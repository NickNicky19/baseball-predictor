#!/usr/bin/env python3
"""
FRAGMENT DIAGNOSTIC — EVERY vendor fragment, EVERY pair, mapped independently.

*** FACTS ONLY. NO MERGE. NO TIME TOLERANCE. NO POLICY. NOTHING DISCARDED. ***

=============================================================================
WHY THE PREVIOUS DIAGNOSTIC WAS TOO NARROW (it silently lost data)
=============================================================================
It did:
        (ka, A), (kb, B) = list(elig.items())[:2]
-- it took the FIRST TWO fragments and DROPPED THE REST WITHOUT SAYING SO.
MEASURED: m~7e622acb carries FIVE start_times. m~66f6c6e0 carries FOUR. The old
script examined two and reported as though that were the whole picture.

It also read the OLD collision report, built from a SINGLE-DATE view. That is
how the "four one-minute pairs" story arose: v2 filtered to one slate_date
FIRST, so we saw 19:10/19:11 and 20:07/20:08 -- two of FOUR fragments, through a
keyhole. The real spans are ~18 HOURS.

*** I ASSERTED A PATTERN FROM A FILTERED VIEW (rule 9). *** This script reads
the vendor data directly and takes EVERY fragment of EVERY multi-fragment
game_id.

=============================================================================
WHAT THE STABILITY SCAN FOUND (June, 243 game_ids)
=============================================================================
    one start_time      69
    <= 5 min           120
    6-90 min            14      <- UNEXPLAINED. This script exists for these.
    91-400 min           8
    1020-1399 min       32      <- 17 to 23 HOURS under ONE game_id

*** ONLY 69 OF 243 game_ids CARRY A SINGLE start_time. ***
The dataset README says `game_id` is a "stable per-game identity -- group and
grade on this." IT IS NOT. 72% carry more than one start_time; 32 span most of
a day. Those ~23-hour spans are the plausible SOURCE of the ctrl4 leakage
already on record ("entry prices recorded 23 HOURS AFTER first pitch"): the bug
was patched at the consumer, but the DATA still contains what caused it.

*** THE TIME BUCKETS ARE OBSERVED PATTERNS, NOT CAUSES. ***
"<= 5 min", "doubleheader-like", "17-23 hours" describe a CLOCK. They are
recorded and reported. NOTHING is inferred from them. A time pattern is not a
mechanism (rule 10), and no merge is ever performed on one.

=============================================================================
*** THE VOTE MAPS. IT DOES NOT MERGE. ***
=============================================================================
Each fragment is mapped INDEPENDENTLY to an mlb_game_pk by a vote of its own
players against MLB box scores, and must clear the SAME vote/margin boundaries
ON ITS OWN. Two fragments voting for the SAME game_pk is STRONG EVIDENCE ABOUT
THEIR MAPPING -- and it is NOT authority to merge them, and NOT authority to
discard either.

That decision belongs DOWNSTREAM: the A/B filters to its real eligible universe,
then enforces MARKET_KEY uniqueness. A genuine duplicate is a MEASURED hard
failure. Anything else must not be thrown away in advance.

THIS SCRIPT DISCARDS NOTHING AND DECIDES NOTHING.

=============================================================================
THE FACTS, PER PAIR OF MAPPED FRAGMENTS
=============================================================================
  relation                  SAME_GAME | DIFFERENT_GAMES | UNMAPPED
     DIFFERENT_GAMES means the vendor reused ONE game_id across TWO REAL GAMES.
     Those fragments CANNOT collide and are not duplicates of anything.

  1. eligible_key_relation  OVERLAPPING | DISJOINT | NEITHER_ELIGIBLE
     Same eligible MARKET_KEY, on the A/B's EXACT universe (HR/hits, intended
     book, settled, two-sided, valid T-Nh entry AND pregame close)?
     *** DECIDES ONLY WHETHER require_unique() WILL FIRE. Not truth. ***

  2. vendor_claim_relation  AGREE | CONTRADICT | NO_SHARED_ROWS
     Same claimed actual for the same (player, category, line)?
     *** COMPUTED REGARDLESS OF AXIS 1. *** Key disjointness protects the KEY,
     not the TRUTH: two fragments with disjoint keys can still disagree about
     what HAPPENED, and no downstream uniqueness check would EVER see it.
     A CONTRADICTION IS A HARD STOP.

  3. official_relation      MATCH | MISMATCH | OFFICIAL_UNAVAILABLE
     Does each claim match MLB's GAME-KEYED official actual?
     Fragments agreeing proves the VENDOR IS SELF-CONSISTENT, not that it is
     RIGHT -- they can agree and BOTH BE WRONG. Only this axis sees that.
     OFFICIAL_UNAVAILABLE is UNSCORED. *** NEVER agreement. ***

=============================================================================
`result` ENCODING -- PROVEN, NOT ASSUMED (rule 1), AND CORROBORATED
=============================================================================
STEP 0 measures whether the over and under rows of one selection carry the SAME
`result`. MEASURED 2026-06-14: 954/954 SAME => `result` IS THE ACTUAL STATISTIC.
The dataset README independently says the same. TWO SOURCES AGREE.

The SIDE_SPECIFIC branch is kept as a GUARD, not a live path: if a partition
ever graded side-wise, comparing raw values across rows would MANUFACTURE
contradictions out of correct data. The script refuses on an ambiguous answer.

=============================================================================
SANITY -- STATED BEFORE THE RUN (rule 7)
=============================================================================
  * every fragment must clear MIN_VOTES/MIN_MARGIN alone, or it is UNMAPPED and
    reported as such -- never guessed at;
  * the ~23-hour game_ids should map to DIFFERENT game_pks (different days);
  * the <= 5-min fragments should map to the SAME game_pk;
  * *** BUCKET C (6-90 min) IS THE OPEN QUESTION AND I HAVE NO PRIOR. *** It
    could be a schedule change, a rain delay, a short-turnaround doubleheader,
    or something nobody has thought of. I will not invent one (rule 9).

Usage:
  python scripts/diagnose_fragments.py --month 2026-06
  python scripts/diagnose_fragments.py --month 2026-06 --min-span 6 --max-span 90
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.mlb_api import MLBStatsAPI          # noqa: E402

MARKET_MAP = {"player hits": "hits", "player home runs": "home_runs"}
OFFICIAL_FIELD = {"hits": "hits", "home_runs": "home_runs"}

# The SAME boundaries build_crosswalk_v2 uses. Every fragment clears them ALONE.
MIN_VOTES = 8
MIN_MARGIN = 5.0


def norm(s: object) -> str:
    """Byte-identical to build_crosswalk_v2.norm and the A/B's norm_name."""
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s))
                if not unicodedata.combining(c))
    s = s.lower().strip()
    for ch in ".'`-":
        s = s.replace(ch, "")
    return " ".join(p for p in s.split()
                    if p not in ("jr", "sr", "ii", "iii", "iv", "v"))


def prove_result_encoding(src: str, scoped: str) -> str:
    df = duckdb.sql(f"""
        WITH s AS (
            SELECT * FROM read_parquet('{src}')
            WHERE market IN ({scoped}) AND result IS NOT NULL
        ),
        sel AS (
            SELECT game_id, start_time, player, market, line,
                   count(DISTINCT side)   AS n_sides,
                   count(DISTINCT result) AS n_results
            FROM s GROUP BY 1,2,3,4,5
        )
        SELECT count(*)                              AS two_sided,
               count(*) FILTER (WHERE n_results = 1) AS same_r,
               count(*) FILTER (WHERE n_results > 1) AS diff_r
        FROM sel WHERE n_sides = 2
    """).df().iloc[0]
    n, same, diff = int(df.two_sided), int(df.same_r), int(df.diff_r)
    print(f"[step 0] two-sided selections {n:,}   same-result {same:,}   "
          f"differing {diff:,}")
    if n == 0:
        print("FATAL: no two-sided selection. Encoding unprovable.",
              file=sys.stderr)
        raise SystemExit(2)
    if diff == 0:
        print("[step 0] `result` IS THE ACTUAL STATISTIC (agrees with the README).")
        return "ACTUAL"
    if same == 0:
        print("[step 0] `result` IS SIDE-SPECIFIC. Normalising via (side, line).")
        return "SIDE_SPECIFIC"
    print(f"FATAL: `result` is neither consistently actual nor consistently "
          f"side-specific ({same:,} vs {diff:,}). AMBIGUOUS. Refusing to guess.",
          file=sys.stderr)
    raise SystemExit(2)


def claim_sql(encoding: str) -> str:
    if encoding == "ACTUAL":
        return "CAST(result AS DOUBLE)"
    return """
        CASE
          WHEN lower(CAST(side AS VARCHAR))='over'
               AND lower(CAST(result AS VARCHAR)) IN ('win','won','w','true','1') THEN 1.0
          WHEN lower(CAST(side AS VARCHAR))='over'
               AND lower(CAST(result AS VARCHAR)) IN ('loss','lost','l','false','0') THEN 0.0
          WHEN lower(CAST(side AS VARCHAR))='under'
               AND lower(CAST(result AS VARCHAR)) IN ('win','won','w','true','1') THEN 0.0
          WHEN lower(CAST(side AS VARCHAR))='under'
               AND lower(CAST(result AS VARCHAR)) IN ('loss','lost','l','false','0') THEN 1.0
          ELSE NULL
        END
    """


def build_mlb_index(api: MLBStatsAPI, game_date: str):
    """For ONE date: name -> {game_pk}, game_pk -> {name: player_id}, meta."""
    name_to_pks: dict[str, set[int]] = defaultdict(set)
    pk_players: dict[int, dict[str, int]] = {}
    pk_meta: dict[int, dict] = {}
    for g in api.get_schedule(game_date):
        pk = int(g["gamePk"])
        teams = g.get("teams", {}) or {}
        pk_meta[pk] = dict(
            away=((teams.get("away", {}) or {}).get("team", {}) or {}).get("name", ""),
            home=((teams.get("home", {}) or {}).get("team", {}) or {}).get("name", ""),
            game_number=g.get("gameNumber"),
            doubleheader=g.get("doubleHeader"),
            official_date=game_date,
        )
        try:
            hitters, pitchers = api.get_game_boxscore_stats(pk)
            ids = set(hitters) | set(pitchers)
        except Exception:                                      # noqa: BLE001
            ids = set()
        players: dict[str, int] = {}
        for pid in ids:
            try:
                nm = norm(getattr(api.get_player_identity(int(pid)), "name", "") or "")
            except Exception:                                  # noqa: BLE001
                nm = ""
            if not nm:
                continue
            if nm in players and players[nm] != int(pid):
                players[nm] = -1        # poison: impossible within one game
                continue
            players[nm] = int(pid)
            name_to_pks[nm].add(pk)
        pk_players[pk] = players
    return name_to_pks, pk_players, pk_meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/market/smartstake",
                    help="LOCAL parquet root -- reproducible, not a live remote")
    ap.add_argument("--month", required=True)
    ap.add_argument("--book", default="draftkings")
    ap.add_argument("--entry-hours", type=int, default=4)
    ap.add_argument("--min-span", type=int, default=None)
    ap.add_argument("--max-span", type=int, default=None)
    ap.add_argument("--out", default="data/market/v2/fragment_diagnosis.json")
    args = ap.parse_args(argv)

    if not list(Path(args.root).glob(f"mon={args.month}/*.parquet")):
        print(f"FATAL: no parquet under {args.root}/mon={args.month}/",
              file=sys.stderr)
        return 2
    src = f"{args.root}/mon={args.month}/*.parquet"
    scoped = ", ".join(f"'{m}'" for m in MARKET_MAP)

    print("=" * 92)
    print(f"FRAGMENT DIAGNOSTIC — {args.month}   (local: {args.root})")
    print("=" * 92)
    print("  FACTS ONLY. No merge. No time tolerance. No policy. Nothing discarded.")
    print("  EVERY fragment of EVERY multi-fragment game_id. EVERY pair.")
    print()

    encoding = prove_result_encoding(src, scoped)
    CLAIM = claim_sql(encoding)

    span_filter = ""
    if args.min_span is not None:
        span_filter += f" AND span_min >= {args.min_span}"
    if args.max_span is not None:
        span_filter += f" AND span_min <= {args.max_span}"

    frags = duckdb.sql(f"""
        WITH s AS (
            SELECT *,
                   CAST(start_time AT TIME ZONE 'UTC'
                                   AT TIME ZONE 'America/New_York' AS DATE)
                       AS slate_date
            FROM read_parquet('{src}')
        ),
        spans AS (
            SELECT game_id,
                   count(DISTINCT start_time) AS n_frags,
                   date_diff('minute', min(start_time), max(start_time)) AS span_min
            FROM s GROUP BY 1
        ),
        keep AS (
            SELECT game_id, n_frags, span_min FROM spans
            WHERE n_frags > 1 {span_filter}
        )
        SELECT s.game_id, s.start_time, s.slate_date,
               k.n_frags, k.span_min,
               count(DISTINCT s.player) AS n_players,
               count(*) FILTER (WHERE s.market IN ({scoped})) AS scoped_rows,
               list(DISTINCT s.player) AS players
        FROM s JOIN keep k USING (game_id)
        GROUP BY 1,2,3,4,5
        ORDER BY k.span_min DESC, s.game_id, s.start_time
    """).df()

    if frags.empty:
        print("No multi-fragment game_id in range. Nothing to diagnose.")
        return 0

    frags["slate_date"] = pd.to_datetime(frags.slate_date).dt.strftime("%Y-%m-%d")
    gids = frags.game_id.unique().tolist()
    print(f"\n[scope] {len(gids)} game_ids   {len(frags)} fragments   "
          f"spans {int(frags.span_min.min())}-{int(frags.span_min.max())} min")

    dates = sorted(frags.slate_date.unique())
    idx: dict[str, tuple] = {}
    for d in dates:
        idx[d] = build_mlb_index(MLBStatsAPI(season=int(d[:4])), d)
    print(f"[mlb]   indexed {len(dates)} slate dates")

    # ---- MAP EVERY FRAGMENT INDEPENDENTLY --------------------------------
    mapped: list[dict] = []
    for r in frags.itertuples():
        name_to_pks, pk_players, pk_meta = idx[r.slate_date]
        votes: Counter[int] = Counter()
        matched = 0
        names = [norm(p) for p in list(r.players)] if r.players is not None else []
        for nm in names:
            pks = name_to_pks.get(nm)
            if not pks:
                continue
            matched += 1
            for pk in pks:
                votes[pk] += 1
        top = votes.most_common(2)
        wv = top[0][1] if top else 0
        rv = top[1][1] if len(top) > 1 else 0
        margin = (wv / rv) if rv else float("inf")
        okay = bool(top) and wv >= MIN_VOTES and margin >= MIN_MARGIN
        win = int(top[0][0]) if okay else None
        meta = pk_meta.get(win, {}) if okay else {}
        mapped.append(dict(
            game_id=r.game_id, start_time=r.start_time, slate_date=r.slate_date,
            span_min=int(r.span_min), n_frags=int(r.n_frags),
            n_players=int(r.n_players), scoped_rows=int(r.scoped_rows),
            mlb_game_pk=win, win_votes=wv, runner_up=rv,
            margin=(None if rv == 0 else round(margin, 2)),
            matched=matched, mapped=okay,
            official_date=meta.get("official_date"),
            matchup=(f"{meta.get('away','')} @ {meta.get('home','')}" if okay else None),
            game_number=meta.get("game_number"),
            doubleheader=meta.get("doubleheader"),
        ))
    M = pd.DataFrame(mapped)
    n_unmapped = int((~M.mapped).sum())
    print(f"[vote]  mapped {int(M.mapped.sum())}/{len(M)} fragments"
          + (f"   ({n_unmapped} UNMAPPED — reported, never guessed at)"
             if n_unmapped else ""))

    # ---- eligible A/B rows, PER FRAGMENT ---------------------------------
    def eligible(gid: str, st, pk: int, sdate: str) -> pd.DataFrame:
        df = duckdb.sql(f"""
            WITH s AS (
                SELECT *, {CLAIM} AS claim FROM read_parquet('{src}')
                WHERE game_id = '{gid}' AND start_time = TIMESTAMP '{st}'
                  AND market IN ({scoped}) AND book = '{args.book}'
                  AND result IS NOT NULL AND ts < start_time
            ),
            e AS (SELECT player, market, line, side, arg_max(odds, ts) AS odds
                  FROM s WHERE ts <= start_time - INTERVAL {args.entry_hours} HOUR
                  GROUP BY 1,2,3,4),
            c AS (SELECT player, market, line, side, arg_max(odds, ts) AS odds,
                         any_value(claim) AS claim
                  FROM s GROUP BY 1,2,3,4)
            SELECT eo.player, eo.market, eo.line, co.claim
            FROM e eo
            JOIN e eu USING (player, market, line)
            JOIN c co USING (player, market, line)
            JOIN c cu USING (player, market, line)
            WHERE eo.side='over' AND eu.side='under'
              AND co.side='over' AND cu.side='under'
        """).df()
        if df.empty:
            return df
        pmap = idx[sdate][1].get(pk, {})
        df["player_key"] = df.player.map(norm)
        df["category"] = df.market.map(MARKET_MAP)
        df["player_id"] = [pmap.get(k) for k in df.player_key]
        return df[df.player_id.notna() & (df.player_id != -1)].copy()

    elig: dict[tuple, pd.DataFrame] = {}
    for r in M[M.mapped].itertuples():
        elig[(r.game_id, r.start_time)] = eligible(
            r.game_id, r.start_time, int(r.mlb_game_pk), r.slate_date)

    # ---- EVERY PAIR within a game_id -------------------------------------
    pair_rows: list[dict] = []
    for gid in gids:
        sub = M[M.game_id == gid]
        for a, b in combinations(sub.itertuples(), 2):
            gap = int(abs((pd.Timestamp(b.start_time)
                           - pd.Timestamp(a.start_time)).total_seconds()) // 60)
            base = dict(game_id=gid, span_min=int(a.span_min), gap_min=gap,
                        frag_a=str(a.start_time), frag_b=str(b.start_time),
                        pk_a=a.mlb_game_pk, pk_b=b.mlb_game_pk,
                        date_a=a.slate_date, date_b=b.slate_date)
            if not (a.mapped and b.mapped):
                pair_rows.append(dict(base, relation="UNMAPPED"))
                continue
            if a.mlb_game_pk != b.mlb_game_pk:
                # The vendor reused ONE game_id across TWO REAL GAMES.
                # These CANNOT collide and are not duplicates of anything.
                pair_rows.append(dict(base, relation="DIFFERENT_GAMES"))
                continue

            A, B = elig[(gid, a.start_time)], elig[(gid, b.start_time)]

            def keys(df):
                return set() if df.empty else {
                    (int(x.player_id), str(x.category), float(x.line))
                    for x in df.itertuples()}
            KA, KB = keys(A), keys(B)
            shared = KA & KB
            a1 = ("NEITHER_ELIGIBLE" if (not KA or not KB)
                  else ("OVERLAPPING" if shared else "DISJOINT"))

            cmp_df = duckdb.sql(f"""
                WITH x AS (SELECT DISTINCT player, market, line, {CLAIM} AS ca
                           FROM read_parquet('{src}')
                           WHERE game_id='{gid}'
                             AND start_time=TIMESTAMP '{a.start_time}'
                             AND market IN ({scoped}) AND result IS NOT NULL),
                     y AS (SELECT DISTINCT player, market, line, {CLAIM} AS cb
                           FROM read_parquet('{src}')
                           WHERE game_id='{gid}'
                             AND start_time=TIMESTAMP '{b.start_time}'
                             AND market IN ({scoped}) AND result IS NOT NULL)
                SELECT player, market, line, ca, cb
                FROM x JOIN y USING (player, market, line)
                WHERE ca IS NOT NULL AND cb IS NOT NULL
            """).df()
            n_sh = len(cmp_df)
            dis = cmp_df[cmp_df.ca != cmp_df.cb] if n_sh else cmp_df
            a2 = ("NO_SHARED_ROWS" if n_sh == 0
                  else ("CONTRADICT" if len(dis) else "AGREE"))

            pair_rows.append(dict(
                base, relation="SAME_GAME",
                eligible_key_relation=a1, eligible_a=len(KA), eligible_b=len(KB),
                shared_keys=len(shared), vendor_claim_relation=a2,
                vendor_shared=n_sh, vendor_contradictions=int(len(dis))))
    P = pd.DataFrame(pair_rows)

    # ---- AXIS 3: EVERY mapped fragment vs MLB OFFICIAL --------------------
    off_rows: list[dict] = []
    for r in M[M.mapped].itertuples():
        df = elig[(r.game_id, r.start_time)]
        if df.empty:
            continue
        pk = int(r.mlb_game_pk)
        try:
            hitters, _ = MLBStatsAPI(season=int(r.slate_date[:4])) \
                .get_game_boxscore_stats(pk)
        except Exception:                                      # noqa: BLE001
            hitters = {}
        for q in df.itertuples():
            stat = hitters.get(int(q.player_id))
            truth = getattr(stat, OFFICIAL_FIELD[q.category], None) if stat else None
            if truth is None or pd.isna(q.claim):
                v = "OFFICIAL_UNAVAILABLE"
            elif encoding == "ACTUAL":
                v = "MATCH" if float(q.claim) == float(truth) else "MISMATCH"
            else:
                good = ((q.claim == 1.0 and truth > q.line) or
                        (q.claim == 0.0 and truth < q.line))
                v = "MATCH" if good else "MISMATCH"
            off_rows.append(dict(
                game_id=r.game_id, start_time=str(r.start_time), mlb_game_pk=pk,
                player=q.player, player_id=int(q.player_id), category=q.category,
                line=float(q.line), vendor_claim=float(q.claim),
                official=(None if truth is None else float(truth)), verdict=v))
    O = pd.DataFrame(off_rows)

    # =====================================================================
    print()
    print("=" * 92)
    print("FACTS")
    print("=" * 92)
    print("\nFRAGMENTS (time spans are OBSERVATIONS, never causes):")
    cols = ["game_id", "start_time", "span_min", "n_frags", "mlb_game_pk",
            "official_date", "matchup", "game_number", "doubleheader",
            "win_votes", "margin", "scoped_rows", "mapped"]
    print(M[cols].to_string(index=False, max_rows=100))

    rc = 0
    if len(P):
        print("\nPAIR RELATIONS:")
        print("  " + P.relation.value_counts().to_string().replace("\n", "\n  "))
        same = P[P.relation == "SAME_GAME"]
        if len(same):
            for ax in ("eligible_key_relation", "vendor_claim_relation"):
                print(f"\n  {ax}:")
                print("    " + same[ax].value_counts().to_string()
                      .replace("\n", "\n    "))
            bad = same[same.vendor_claim_relation == "CONTRADICT"]
            if len(bad):
                print("\n  *** FRAGMENTS OF ONE game_id CONTRADICT EACH OTHER "
                      "ABOUT WHAT HAPPENED. HARD STOP. ***")
                print(bad[["game_id", "frag_a", "frag_b", "vendor_shared",
                           "vendor_contradictions"]].to_string(index=False))
                rc = 2
        dg = P[P.relation == "DIFFERENT_GAMES"]
        if len(dg):
            print(f"\n  {len(dg)} pair(s) map to DIFFERENT game_pks — the vendor "
                  f"reused ONE game_id across TWO REAL GAMES.")
            print("  These cannot collide and are NOT duplicates. gap_min range: "
                  f"{int(dg.gap_min.min())}-{int(dg.gap_min.max())}")

    if len(O):
        print("\nAXIS 3 — VENDOR CLAIM vs MLB OFFICIAL:")
        print("  " + O.verdict.value_counts().to_string().replace("\n", "\n  "))
        mm = O[O.verdict == "MISMATCH"]
        if len(mm):
            print(f"\n  *** {len(mm)} VENDOR CLAIMS DISAGREE WITH THE OFFICIAL "
                  f"BOX SCORE. ***")
            print("  The README says outcomes come FROM official box scores.")
            print("  A MATERIAL DATA-QUALITY FINDING, distinct from any fragment")
            print("  contradiction. These rows MUST NOT silently become evaluable.")
            print(mm.head(20).to_string(index=False))
            rc = max(rc, 1)

    if n_unmapped:
        print(f"\n  {n_unmapped} fragment(s) UNMAPPED (below MIN_VOTES={MIN_VOTES} "
              f"or MIN_MARGIN={MIN_MARGIN}x). Reported, not guessed at.")

    print()
    print("  NOTHING WAS DISCARDED AND NOTHING WAS MERGED. The vote MAPPED each")
    print("  fragment; it did not decide that any two are one thing. That belongs")
    print("  to the A/B, on eligible rows, via require_unique(MARKET_KEY) — where a")
    print("  genuine duplicate is a MEASURED hard failure, not a preemptive")
    print("  exclusion.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    M.to_csv(out.with_name("fragment_map.csv"), index=False)
    if len(P):
        P.to_csv(out.with_name("fragment_pairs.csv"), index=False)
    if len(O):
        O.to_csv(out.with_name("fragment_official.csv"), index=False)

    payload = dict(
        _comment=(
            "Fragment diagnostic. FACTS ONLY. Every fragment of every "
            "multi-fragment game_id is mapped INDEPENDENTLY by a player vote and "
            "must clear MIN_VOTES/MIN_MARGIN on its own; THE VOTE MAPS AND DOES "
            "NOT MERGE. Every PAIR within a game_id is compared. Time spans are "
            "OBSERVED PATTERNS, never causes, and no merge is performed on one. "
            "Fragments mapping to DIFFERENT game_pks are DIFFERENT_GAMES -- the "
            "vendor reused one game_id across two real games -- and cannot "
            "collide. Axis 2 is computed REGARDLESS of axis 1, because key "
            "disjointness protects the KEY and not the TRUTH. Axis 3 is strictly "
            "stronger than axis 2: two fragments can agree with each other and "
            "both be wrong. OFFICIAL_UNAVAILABLE is UNSCORED, never agreement. "
            "NOTHING IS DISCARDED HERE; downstream eligibility + "
            "require_unique(MARKET_KEY) expose genuine duplication."
        ),
        month=args.month, root=args.root, book=args.book,
        entry_hours=args.entry_hours, result_encoding=encoding,
        span_filter=dict(min=args.min_span, max=args.max_span),
        min_votes=MIN_VOTES, min_margin=MIN_MARGIN,
        n_game_ids=len(gids), n_fragments=int(len(M)), n_unmapped=n_unmapped,
        pair_relations=(P.relation.value_counts().to_dict() if len(P) else {}),
        official=(O.verdict.value_counts().to_dict() if len(O) else {}),
    )
    with out.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2, default=str)
        f.write("\n")
    print(f"\nwrote {out}")
    print(f"      {out.with_name('fragment_map.csv')}")
    if len(P):
        print(f"      {out.with_name('fragment_pairs.csv')}")
    if len(O):
        print(f"      {out.with_name('fragment_official.csv')}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
