#!/usr/bin/env python3
"""
RESULT-INTEGRITY AUDIT — is the vendor's `result` column the truth it claims?

*** THIS MEASURES. IT DOES NOT FIX. ***
The remedy is architectural and belongs in a separate patch: SmartStake supplies
PRICES and SETTLEMENT PRESENCE; MLB game-keyed official actuals supply the SCORED
TARGET. This script quantifies what that change is worth and what it repairs.

=============================================================================
WHY THIS EXISTS
=============================================================================
The dataset README says:
    "outcomes are from official box scores"
    `result` | "The player's actual stat for that market."

MEASURED on 2026-06-08, game_pk 824025 (HOU @ LAA), via `m~13d6623a`:

    player              vendor result     MLB official
    Christian Walker         0.0              2.0
    Mike Trout               1.0              0.0
    Yordan Alvarez           2.0              0.0
    Isaac Paredes            0.0              1.0
    Cam Smith                0.0              2.0

The MLB lookup was VERIFIED, not assumed (rule 1): get_game_boxscore_stats
returns dict[int, HittingStatsSnapshot] with a real `.hits` field and games=1, and
the lines are internally coherent (Trout 0-for-4, 1 BB, 2 K, pa=5). *** MLB IS
RIGHT. THE VENDOR IS WRONG ON THIS GAME. ***

Codex independently established that 113 of those selections carry ONE STABLE
non-null `result` across every stored snapshot -- no churn, no conflicting
values. So the wrong number was there FROM THE START and never moved. That RULES
OUT "the vendor corrected or corrupted it mid-stream". It does NOT establish a
mechanism (rule 10), and none is claimed here.

=============================================================================
THE QUESTION THIS ANSWERS
=============================================================================
Is corrupted `result` confined to MULTI-FRAGMENT game_ids -- in which case it is
one more symptom of the `game_id`-is-not-a-game-key failure -- or does it also
hit CLEAN single-start_time games, in which case *** THE VENDOR'S GRADING IS
UNRELIABLE AS SUCH *** and no amount of identity work repairs it.

Those have very different implications and must not be conflated.

=============================================================================
TWO UNIVERSES. THEY ANSWER DIFFERENT QUESTIONS. DO NOT POOL THEM.
=============================================================================
  1. TRUTH-AUDIT UNIVERSE
     EVERY hard-mapped HR/hits selection with a stable non-null vendor `result`,
     REGARDLESS of book / entry horizon / two-sidedness.
     -> measures VENDOR RESULT INTEGRITY. How wrong is the column, in itself?

  2. A/B-IMPACT UNIVERSE
     ONLY the exact eventual eligible rows: the intended book, settled,
     two-sided, valid T-Nh entry AND pregame close.
     -> measures HOW MUCH corrupted grading WOULD HAVE CONTAMINATED the capture
        study. This is the number that says whether every prior capture figure
        was scored against a partly-false target.

  *** UNIVERSE 2 IS A SUBSET OF UNIVERSE 1, AND ITS MISMATCH RATE COULD DIFFER.
  *** Reporting only one would answer a question nobody asked.

=============================================================================
SPLITS (each isolates a DIFFERENT candidate explanation)
=============================================================================
  * single-start_time game_id  vs  multi-fragment game_id
        -> is this an identity problem or a grading problem?
  * mapped selection           vs  unmapped-player row
        -> is it OUR name resolution, or THEIR data?
  * stable result              vs  no-result
        -> no-result rows are EXCLUDED by `result IS NOT NULL` and cannot
           explain the mismatches away. Counted anyway, so nobody assumes.
  * game level                 vs  selection level
        -> 91 bad selections inside ONE game is a very different finding from
           91 scattered across ninety. THE CONCENTRATION IS THE SIGNAL.

=============================================================================
SANITY -- STATED BEFORE THE RUN (rule 7)
=============================================================================
  If the corruption is an IDENTITY artifact:
      mismatch rate on single-start_time game_ids  ~= 0
      mismatch rate on multi-fragment game_ids     >  0, concentrated in whole
                                                      games
  If the corruption is a GRADING problem:
      BOTH populations show mismatches, and identity work does not help.

  *** I HAVE NOT CHECKED WHICH. I have no prior and will not invent one. ***

  I also asserted, from twenty rows, that the mismatches looked TEAM-INVERTED
  (one club understated, the other overstated). *** TWENTY ROWS IS A SLICE, AND
  I HAVE ALREADY ASSERTED A PATTERN FROM A PARTIAL VIEW TWICE IN THIS PROJECT. ***
  This script computes the direction across ALL mismatches and lets the number
  speak. My eyeball is not evidence.

Usage:
  python scripts/audit_result_integrity.py --month 2026-06
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.mlb_api import MLBStatsAPI          # noqa: E402

MARKET_MAP = {"player hits": "hits", "player home runs": "home_runs"}
OFFICIAL_FIELD = {"hits": "hits", "home_runs": "home_runs"}
MIN_VOTES = 8
MIN_MARGIN = 5.0


def norm(s: object) -> str:
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
    for g in api.get_schedule(game_date):
        pk = int(g["gamePk"])
        teams = g.get("teams", {}) or {}
        pk_meta[pk] = dict(
            away=((teams.get("away", {}) or {}).get("team", {}) or {}).get("name", ""),
            home=((teams.get("home", {}) or {}).get("team", {}) or {}).get("name", ""),
            away_id=((teams.get("away", {}) or {}).get("team", {}) or {}).get("id"),
            home_id=((teams.get("home", {}) or {}).get("team", {}) or {}).get("id"),
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
                players[nm] = -1
                continue
            players[nm] = int(pid)
            name_to_pks[nm].add(pk)
        pk_players[pk] = players
    return name_to_pks, pk_players, pk_meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/market/smartstake")
    ap.add_argument("--month", required=True)
    ap.add_argument("--book", default="draftkings")
    ap.add_argument("--entry-hours", type=int, default=4)
    ap.add_argument("--out", default="data/market/v2/result_integrity.json")
    args = ap.parse_args(argv)

    src = f"{args.root}/mon={args.month}/*.parquet"
    if not list(Path(args.root).glob(f"mon={args.month}/*.parquet")):
        print(f"FATAL: no parquet under {src}", file=sys.stderr)
        return 2
    scoped = ", ".join(f"'{m}'" for m in MARKET_MAP)

    print("=" * 96)
    print(f"RESULT-INTEGRITY AUDIT — {args.month}")
    print("=" * 96)
    print("  The README says outcomes come FROM OFFICIAL BOX SCORES.")
    print("  We have MEASURED one game where they do not. This asks: how far does")
    print("  that go, and is it an IDENTITY artifact or a GRADING problem?")
    print("  MEASUREMENT ONLY. The fix is architectural and lives elsewhere.")
    print()

    # ---- every vendor selection, with its own result-stability recorded ----
    # A selection is (game_id, start_time, player, market, line). `result` is
    # taken across ALL its snapshots so that CHURN is visible rather than
    # collapsed by an any_value().
    sel = duckdb.sql(f"""
        WITH s AS (
            SELECT *,
                   CAST(start_time AT TIME ZONE 'UTC'
                                   AT TIME ZONE 'America/New_York' AS DATE)
                       AS slate_date
            FROM read_parquet('{src}')
            WHERE market IN ({scoped})
        ),
        frag AS (
            SELECT game_id, count(DISTINCT start_time) AS n_frags
            FROM read_parquet('{src}') GROUP BY 1
        )
        SELECT s.game_id, s.start_time, s.slate_date, s.player, s.market, s.line,
               f.n_frags,
               count(DISTINCT s.result)                       AS n_distinct_results,
               count(*) FILTER (WHERE s.result IS NOT NULL)   AS n_graded_rows,
               max(s.result)                                  AS vendor_result,
               count(*)                                       AS n_rows
        FROM s JOIN frag f ON f.game_id = s.game_id
        GROUP BY 1,2,3,4,5,6,7
    """).df()
    sel["slate_date"] = pd.to_datetime(sel.slate_date).dt.strftime("%Y-%m-%d")
    sel["category"] = sel.market.map(MARKET_MAP)
    sel["player_key"] = sel.player.map(norm)

    # result-stability classes -- stated, not assumed
    sel["result_class"] = "stable"
    sel.loc[sel.n_graded_rows == 0, "result_class"] = "no_result"
    sel.loc[sel.n_distinct_results > 1, "result_class"] = "CHURN"

    n_churn = int((sel.result_class == "CHURN").sum())
    print(f"[selections] {len(sel):,}   "
          f"stable {int((sel.result_class=='stable').sum()):,}   "
          f"no_result {int((sel.result_class=='no_result').sum()):,}   "
          f"CHURN {n_churn:,}")
    if n_churn:
        print(f"  *** {n_churn} selections carry MORE THAN ONE distinct `result`. ***")
        print("  A selection whose graded outcome CHANGES is a separate defect from")
        print("  one that is simply wrong. Excluded from the truth audit and")
        print("  reported on its own -- never averaged in.")

    # ---- the A/B's exact eligible universe (universe 2) -------------------
    elig = duckdb.sql(f"""
        WITH s AS (
            SELECT * FROM read_parquet('{src}')
            WHERE market IN ({scoped}) AND book = '{args.book}'
              AND result IS NOT NULL AND ts < start_time
        ),
        e AS (SELECT game_id, start_time, player, market, line, side
              FROM s WHERE ts <= start_time - INTERVAL {args.entry_hours} HOUR
              GROUP BY 1,2,3,4,5,6),
        c AS (SELECT game_id, start_time, player, market, line, side
              FROM s GROUP BY 1,2,3,4,5,6)
        SELECT DISTINCT eo.game_id, eo.start_time, eo.player, eo.market, eo.line
        FROM e eo
        JOIN e eu USING (game_id, start_time, player, market, line)
        JOIN c co USING (game_id, start_time, player, market, line)
        JOIN c cu USING (game_id, start_time, player, market, line)
        WHERE eo.side='over' AND eu.side='under'
          AND co.side='over' AND cu.side='under'
    """).df()
    elig["is_eligible"] = True
    sel = sel.merge(elig, on=["game_id", "start_time", "player", "market", "line"],
                    how="left")
    sel["is_eligible"] = sel.is_eligible.fillna(False)
    print(f"[eligible]   {int(sel.is_eligible.sum()):,} of {len(sel):,} selections "
          f"survive the A/B universe ({args.book}, two-sided, T-{args.entry_hours}h "
          f"entry + close)")

    # ---- map every FRAGMENT to a game_pk (the vote MAPS; it never merges) --
    dates = sorted(sel.slate_date.unique())
    idx = {}
    for d in dates:
        idx[d] = build_mlb_index(MLBStatsAPI(season=int(d[:4])), d)
    print(f"[mlb]        indexed {len(dates)} slate dates")

    frag_players = (sel.groupby(["game_id", "start_time", "slate_date"])
                       .player_key.apply(lambda s: sorted(set(s))).reset_index())
    frag_pk: dict[tuple, int | None] = {}
    for r in frag_players.itertuples():
        name_to_pks, _, _ = idx[r.slate_date]
        votes: dict[int, int] = defaultdict(int)
        for nm in r.player_key:
            for pk in name_to_pks.get(nm, ()):
                votes[pk] += 1
        top = sorted(votes.items(), key=lambda kv: -kv[1])[:2]
        wv = top[0][1] if top else 0
        rv = top[1][1] if len(top) > 1 else 0
        ok = bool(top) and wv >= MIN_VOTES and (rv == 0 or wv / rv >= MIN_MARGIN)
        frag_pk[(r.game_id, r.start_time)] = int(top[0][0]) if ok else None

    sel["mlb_game_pk"] = [frag_pk.get((g, t)) for g, t in
                          zip(sel.game_id, sel.start_time)]
    sel["fragment_class"] = ["single" if n == 1 else "multi" for n in sel.n_frags]

    # ---- resolve player_id, then compare against MLB OFFICIAL --------------
    box: dict[int, dict] = {}
    truths: list[float | None] = []
    pids: list[int | None] = []
    for r in sel.itertuples():
        pk = r.mlb_game_pk
        if pk is None or pd.isna(pk):
            pids.append(None)
            truths.append(None)
            continue
        pk = int(pk)
        if pk not in box:
            try:
                hitters, _ = MLBStatsAPI(season=int(r.slate_date[:4])) \
                    .get_game_boxscore_stats(pk)
            except Exception:                                  # noqa: BLE001
                hitters = {}
            box[pk] = hitters
        pmap = idx[r.slate_date][1].get(pk, {})
        pid = pmap.get(r.player_key)
        if pid is None or pid < 0:
            pids.append(None)
            truths.append(None)
            continue
        stat = box[pk].get(int(pid))
        val = getattr(stat, OFFICIAL_FIELD[r.category], None) if stat else None
        pids.append(int(pid))
        truths.append(None if val is None else float(val))
    sel["player_id"] = pids
    sel["official"] = truths

    sel["mapped"] = sel.player_id.notna() & sel.mlb_game_pk.notna()
    sel["scoreable"] = (sel.mapped & sel.official.notna()
                        & (sel.result_class == "stable"))
    sel["mismatch"] = sel.scoreable & (sel.vendor_result != sel.official)

    # =====================================================================
    def report(df: pd.DataFrame, title: str) -> dict:
        print()
        print("=" * 96)
        print(title)
        print("=" * 96)
        sc = df[df.scoreable]
        if sc.empty:
            print("  no scoreable selection in this universe")
            return {}
        n, bad = len(sc), int(sc.mismatch.sum())
        print(f"  scoreable selections : {n:,}")
        print(f"  MISMATCH vs MLB      : {bad:,}   ({bad / n:.1%})")

        print("\n  BY FRAGMENT CLASS  (identity artifact, or grading problem?)")
        g = (sc.groupby("fragment_class")
               .agg(n=("mismatch", "size"), bad=("mismatch", "sum")))
        g["rate"] = (g.bad / g.n).map(lambda x: f"{x:.1%}")
        print("    " + g.to_string().replace("\n", "\n    "))

        print("\n  BY CATEGORY")
        g2 = (sc.groupby("category")
                .agg(n=("mismatch", "size"), bad=("mismatch", "sum")))
        g2["rate"] = (g2.bad / g2.n).map(lambda x: f"{x:.1%}")
        print("    " + g2.to_string().replace("\n", "\n    "))

        # *** CONCENTRATION IS THE SIGNAL. *** 91 bad selections inside ONE game
        # is a completely different finding from 91 scattered across ninety.
        pg = (sc.groupby("mlb_game_pk")
                .agg(n=("mismatch", "size"), bad=("mismatch", "sum")))
        pg["rate"] = pg.bad / pg.n
        n_games = len(pg)
        clean = int((pg.bad == 0).sum())
        partial = int(((pg.bad > 0) & (pg.rate < 0.9)).sum())
        whole = int((pg.rate >= 0.9).sum())
        print(f"\n  BY GAME  ({n_games} games)")
        print(f"    fully clean (0 bad)        : {clean}")
        print(f"    partially bad (<90%)       : {partial}")
        print(f"    ESSENTIALLY WHOLLY BAD >=90%: {whole}")
        if whole:
            print("    *** A game where ~EVERY selection is wrong is not noise. ***")
            print("    That is the whole game graded against something else.")
            print("    " + pg[pg.rate >= 0.9].sort_values("rate", ascending=False)
                  .head(12).to_string().replace("\n", "\n    "))

        # The DIRECTION, computed -- not eyeballed from twenty rows.
        mm = sc[sc.mismatch]
        if len(mm):
            over = int((mm.vendor_result > mm.official).sum())
            under = int((mm.vendor_result < mm.official).sum())
            print(f"\n  DIRECTION of the error (computed over ALL {len(mm):,} "
                  f"mismatches, not eyeballed)")
            print(f"    vendor OVERSTATED  : {over:,}  ({over/len(mm):.1%})")
            print(f"    vendor UNDERSTATED : {under:,}  ({under/len(mm):.1%})")
            if abs(over - under) / len(mm) < 0.10:
                print("    -> roughly SYMMETRIC. Not a systematic over- or under-count.")
            else:
                print("    -> ASYMMETRIC. The error has a direction; that is a clue,")
                print("       and it is still not a mechanism (rule 10).")

        return dict(n=n, mismatch=bad, rate=round(bad / n, 4),
                    games=n_games, games_clean=clean,
                    games_partial=partial, games_wholly_bad=whole)

    u1 = report(sel, "UNIVERSE 1 — TRUTH AUDIT (every hard-mapped selection, "
                     "regardless of eligibility)")
    u2 = report(sel[sel.is_eligible],
                f"UNIVERSE 2 — A/B IMPACT (only {args.book}, two-sided, "
                f"T-{args.entry_hours}h entry + close)")

    # ---- the splits Codex asked for, stated plainly -----------------------
    print()
    print("=" * 96)
    print("COVERAGE SPLITS")
    print("=" * 96)
    for name, col in (("result_class", "result_class"),
                      ("fragment_class", "fragment_class")):
        t = sel.groupby(col).agg(
            selections=("mapped", "size"),
            mapped=("mapped", "sum"),
            scoreable=("scoreable", "sum"),
            eligible=("is_eligible", "sum"))
        print(f"\n  by {name}:")
        print("    " + t.to_string().replace("\n", "\n    "))
    n_unmapped = int((~sel.mapped).sum())
    print(f"\n  unmapped-player rows: {n_unmapped:,} "
          f"({n_unmapped/len(sel):.1%}) — OUR name resolution, not their data. "
          f"Excluded from every rate above.")

    print()
    print("=" * 96)
    print("WHAT THIS DOES AND DOES NOT ESTABLISH")
    print("=" * 96)
    print("  ESTABLISHED: the vendor's numeric `result` disagrees with MLB's")
    print("  game-keyed official actuals on a MEASURED subset of otherwise clean,")
    print("  hard-mapped, stably-graded selections. MLB was verified directly.")
    print()
    print("  NOT ESTABLISHED: WHY (rule 10). A stable-but-wrong value rules out")
    print("  mid-stream corruption; it does not name a mechanism, and none is")
    print("  claimed.")
    print()
    print("  THE REMEDY DOES NOT DEPEND ON THE MECHANISM:")
    print("    SmartStake supplies PRICES and SETTLEMENT PRESENCE.")
    print("    MLB game-keyed official actuals supply the SCORED TARGET.")
    print("  The vendor's numeric `result` must never enter won_over, Brier, or")
    print("  capture. TWO TRUTH SOURCES IN ONE PIPELINE IS THE BUG, whichever one")
    print("  happens to be wrong.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sel.to_csv(out.with_name("result_integrity_rows.csv"), index=False)
    payload = dict(
        _comment=(
            "Vendor `result` vs MLB game-keyed official actuals. TWO UNIVERSES, "
            "never pooled: (1) TRUTH AUDIT -- every hard-mapped selection with a "
            "stable non-null vendor result, regardless of eligibility, measuring "
            "the column's integrity in itself; (2) A/B IMPACT -- only the exact "
            "eventual eligible rows, measuring how far corrupted grading would "
            "have contaminated the capture study. Splits: single vs multi-fragment "
            "game_id (identity artifact or grading problem), mapped vs unmapped "
            "(our name resolution or their data), stable vs no-result vs CHURN, "
            "and GAME level vs SELECTION level (concentration is the signal). "
            "The MLB lookup was VERIFIED, not assumed. MEASUREMENT ONLY: the "
            "mechanism is NOT established and is not claimed."
        ),
        month=args.month, book=args.book, entry_hours=args.entry_hours,
        universe_1_truth_audit=u1, universe_2_ab_impact=u2,
        n_selections=int(len(sel)), n_unmapped=n_unmapped, n_churn=n_churn,
    )
    with out.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2, default=str)
        f.write("\n")
    print(f"\nwrote {out}")
    print(f"      {out.with_name('result_integrity_rows.csv')}")
    return 1 if (u1.get("mismatch", 0) or u2.get("mismatch", 0)) else 0


if __name__ == "__main__":
    sys.exit(main())
