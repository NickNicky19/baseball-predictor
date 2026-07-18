#!/usr/bin/env python3
"""
STAGE 1 — THE DECISIVE TEST.

    *** DO THE PLAYERS IN ONE SmartStake game_id RESOLVE TO EXACTLY TWO
        MLB TEAMS? ***

Everything rests on this. If yes, the crosswalk is buildable and free. If no,
the strict market A/B is genuinely blocked and we stop pretending otherwise.

=============================================================================
WHY THIS IS THE ONLY PATH  (established, not assumed)
=============================================================================
SmartStake's schema, MEASURED (probe_smartstake_schema.py, DESCRIBE):
    game_id, start_time, player, market, line, side, book, ts, odds, result,
    won, mon
*** NO TEAMS. NO PLAYER IDS. ***

And its event identity is unreliable at EVERY granularity:
    n_game_ids           243     <- a MATCHUP key
    n_(game_id,start)    493     <- OVER-SPLITS: start_time drifts by a MINUTE
    n_start_times        390     <- also fragmented
June has ~218 real games. None of the three counts is right.

And a web search for a free alternative turned up NOTHING. Every free MLB odds
archive (SportsBookReview, Odds Shark, Princeton's catalog, Kaggle) is
GAME-LEVEL -- moneyline/spread/total. Player props are orders of magnitude more
data and nobody archives them for free. From a scraper repo that went looking:
"it's pretty hard to get free odds data... there's not really any free datasets
online."

*** SmartStake is not one option among several. It is the ONLY free historical
    player-prop dataset that exists. That is why it is worth fixing rather than
    replacing. ***

=============================================================================
THE INSIGHT
=============================================================================
    We do not need SmartStake to tell us the teams.
    *** THE PLAYERS *ARE* THE TEAMS. ***

    ~25-36 named players in one vendor game_id sit on exactly TWO MLB rosters.
    MLB's schedule tells us which gamePk has those two teams that day.

    And we do not use a ROSTER -- we use the BOX SCORE. get_game_boxscore_stats
    returns the players who ACTUALLY APPEARED. A roster includes players who did
    not play; SmartStake prices players who are IN the lineup. The box score is
    the tighter, more discriminating match, and it is the same call
    reconstruct_objects already makes.

    DOUBLEHEADERS: the same two teams twice. MLB marks them with an EXPLICIT
    gameNumber (1/2) -- so we ORDER them by MLB's own field, never by comparing
    SmartStake's drifting timestamp. The minute-level drift that fragments
    (game_id, start_time) is irrelevant when the two games are HOURS apart.

    MAX MUNCY: once the gamePk is known, so are the two box scores. Two Max
    Muncys are never in the same game. (gamePk, normalized_name) -> player_id is
    unique BY CONSTRUCTION, and the cross-join that turned 21,171 + 21,171 into
    21,201 becomes STRUCTURALLY IMPOSSIBLE.

=============================================================================
HOW THIS CAN FAIL, AND WHY EACH FAILURE IS REPORTED, NOT PAPERED OVER
=============================================================================
  F1  A vendor game's players resolve to THREE OR MORE teams.
      -> the vendor game_id is not a game. The premise is dead.

  F2  They resolve to ONE team, or ZERO.
      -> too few names matched. Report the match rate; if it is low, the NAME
         normalisation is the problem, not the premise.

  F3  Two teams, but MLB has TWO gamePks for that matchup (a doubleheader) and
      we cannot tell which.
      -> SOLVABLE: order by gameNumber and by start_time at the HOUR level.
         Reported separately, because it is a DIFFERENT problem from F1/F2.

  F4  A gamePk gets claimed by TWO vendor game_ids.
      -> a collision. FAIL LOUDLY. Never pick one arbitrarily.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  players per vendor game_id      : 20 - 40   (both lineups + subs)
  name match rate to the box score: > 90%     (the roster join measured 99.6%)
  teams per vendor game_id        : EXACTLY 2
  gamePks matched per vendor game : EXACTLY 1  (or 2 for a doubleheader matchup,
                                                which the start-time hour then
                                                separates)

  If the team count is not 2 for the overwhelming majority of games, STOP. The
  premise is wrong and no amount of tuning fixes it.

Usage:
    python scripts/probe_crosswalk.py --date 2026-06-14
    python scripts/probe_crosswalk.py --date 2026-06-14 --verbose
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.mlb_api import MLBStatsAPI          # noqa: E402


def norm(s: object) -> str:
    """Normalise a name for matching. Measured 99.6% against the MLB roster."""
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-06-14")
    ap.add_argument("--month", default=None,
                    help="the SmartStake partition; defaults to the date's month")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    month = args.month or args.date[:7]
    HF = (f"'hf://datasets/SmartStake/mlb-player-props/"
          f"mon={month}/*.parquet'")

    # =====================================================================
    # 1. MLB's side: every game that day, its two teams, and WHO PLAYED
    # =====================================================================
    api = MLBStatsAPI(season=int(args.date[:4]))
    games = api.get_schedule(args.date)
    if not games:
        print(f"FATAL: MLB has no games on {args.date}.", file=sys.stderr)
        return 2

    print("=" * 88)
    print(f"MLB — {args.date}: {len(games)} games")
    print("=" * 88)

    # name -> the gamePk(s) that name appeared in. Built from BOX SCORES, not
    # rosters: SmartStake prices players who are IN the lineup, and a box score
    # is exactly who appeared.
    name_to_pks: dict[str, set[int]] = defaultdict(set)
    pk_meta: dict[int, dict] = {}
    empty_boxscores: list[int] = []

    for g in games:
        pk = int(g["gamePk"])
        pk_meta[pk] = dict(
            away=dig(g, "teams", "away", "team", "name", default="?"),
            home=dig(g, "teams", "home", "team", "name", default="?"),
            away_id=dig(g, "teams", "away", "team", "id"),
            home_id=dig(g, "teams", "home", "team", "id"),
            game_date=g.get("gameDate", ""),
            game_number=g.get("gameNumber"),
            doubleheader=g.get("doubleHeader"),
        )
        try:
            hitters, pitchers = api.get_game_boxscore_stats(pk)
        except Exception as exc:
            print(f"  gamePk {pk}: box score FAILED ({exc})", file=sys.stderr)
            hitters, pitchers = {}, {}
        ids = set(hitters) | set(pitchers)
        pk_meta[pk]["n_players"] = len(ids)

        # *** A BOX SCORE WITH ZERO PLAYERS IS A FAILURE, NOT A SKIP. ***
        # MEASURED on 2026-06-14: gamePk 824424 (Tigers @ Guardians) returned
        # 0 players while every other game returned 26-35.
        #
        # Why this MUST be loud: a game with no players contributes NO VOTES. Any
        # vendor game that maps to it would then fail with "F2 NO MATCH" -- and
        # F2 is defined as "the NAME NORMALISATION is broken". We would have gone
        # hunting a name bug that does not exist.
        #
        # A silent skip does not just lose one game. It MISATTRIBUTES the failure.
        if not ids:
            empty_boxscores.append(pk)
        for pid in ids:
            try:
                ident = api.get_player_identity(int(pid))
                nm = norm(getattr(ident, "name", "") or "")
            except Exception:
                nm = ""
            if nm:
                name_to_pks[nm].add(pk)

        m = pk_meta[pk]
        print(f"  gamePk {pk}  {m['away'][:20]:20s} @ {m['home'][:20]:20s}  "
              f"g#{m['game_number']}  DH={m['doubleheader']}  "
              f"{m.get('n_players', 0)} players")

    if not name_to_pks:
        print("FATAL: no box-score players resolved. Cannot build the crosswalk.",
              file=sys.stderr)
        return 2

    if empty_boxscores:
        print()
        print("  " + "!" * 80)
        print(f"  *** {len(empty_boxscores)} GAME(S) RETURNED AN EMPTY BOX SCORE: "
              f"{empty_boxscores}")
        print("  These contribute ZERO votes. Any vendor game that belongs to one")
        print("  will report 'F2 NO MATCH' -- which is DEFINED as a name-matching")
        print("  failure. It is NOT. It is a missing box score.")
        print("  Do NOT read an F2 on these games as a name bug.")
        print("  " + "!" * 80)

    print(f"\n  {len(name_to_pks)} distinct player names across all box scores")
    ambiguous = {n: pks for n, pks in name_to_pks.items() if len(pks) > 1}
    if ambiguous:
        print(f"  {len(ambiguous)} name(s) appear in MORE THAN ONE game today "
              f"(a doubleheader, or two players sharing a name):")
        for n, pks in list(ambiguous.items())[:5]:
            print(f"    {n!r} -> gamePks {sorted(pks)}")

    # =====================================================================
    # 2. SmartStake's side: the players in each vendor game_id
    # =====================================================================
    duckdb.sql("INSTALL httpfs; LOAD httpfs;")
    duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

    vend = duckdb.sql(f"""
        SELECT game_id,
               min(start_time)                AS first_start,
               max(start_time)                AS last_start,
               count(DISTINCT player)         AS n_players,
               list(DISTINCT player)          AS players
        FROM {HF}
        WHERE CAST(start_time AT TIME ZONE 'UTC'
                              AT TIME ZONE 'America/New_York' AS DATE)
              = DATE '{args.date}'
        GROUP BY game_id
        ORDER BY first_start
    """).df()

    print()
    print("=" * 88)
    print(f"SmartStake — {args.date}: {len(vend)} vendor game_ids")
    print("=" * 88)
    if vend.empty:
        print("FATAL: SmartStake has no games on this date.", file=sys.stderr)
        return 2

    # =====================================================================
    # 3. *** THE DECISIVE TEST *** — do the players resolve to TWO teams?
    # =====================================================================
    print()
    print("=" * 88)
    print("*** THE DECISIVE TEST: do a vendor game's players resolve to EXACTLY")
    print("    ONE MLB gamePk? ***")
    print("=" * 88)
    print(f"  {'vendor game_id':14s} {'n':>3s} {'matched':>8s} "
          f"{'gamePks hit':32s} {'verdict'}")
    print("  " + "-" * 86)

    results = []
    claimed: dict[int, list[str]] = defaultdict(list)

    for r in vend.itertuples():
        # DuckDB's list() aggregate comes back as a NUMPY ARRAY, not a Python
        # list. `array or []` raises ValueError -- numpy refuses to truthiness-
        # test a multi-element array.
        #
        # RULE 1, and I failed it: I asserted what list() returns instead of
        # checking. Coerce EXPLICITLY rather than relying on truthiness.
        raw = r.players
        if raw is None:
            names = []
        else:
            names = [norm(p) for p in list(raw)]
        hits: Counter[int] = Counter()
        matched = 0
        for nm in names:
            pks = name_to_pks.get(nm)
            if not pks:
                continue
            matched += 1
            # A name in a doubleheader appears in BOTH gamePks. Count it for
            # each -- the VOTE across all ~25 players is what disambiguates,
            # because the two games of a doubleheader have DIFFERENT lineups.
            for pk in pks:
                hits[pk] += 1

        rate = matched / max(1, len(names))
        top = hits.most_common(3)
        top_str = "  ".join(f"{pk}:{c}" for pk, c in top) if top else "(none)"

        if not top:
            verdict = "F2 NO MATCH"
        elif len(top) == 1 or (len(top) > 1 and top[0][1] > 2 * top[1][1]):
            # A CLEAR winner: either one gamePk, or one with more than twice the
            # votes of the runner-up. The 2x margin is a DECISION BOUNDARY, not a
            # fitted number -- it is stated here and reported, and the actual
            # margins are printed so it can be judged.
            verdict = "OK"
            claimed[top[0][0]].append(r.game_id)
        else:
            verdict = "F3 AMBIGUOUS"

        results.append(dict(game_id=r.game_id, n=len(names), matched=matched,
                            rate=rate, top=top, verdict=verdict,
                            first_start=r.first_start))
        print(f"  {r.game_id:14s} {len(names):3d} {rate:7.1%} {top_str:32s} "
              f"{verdict}")

    # =====================================================================
    # 4. COLLISIONS — two vendor games claiming the same gamePk
    # =====================================================================
    print()
    print("=" * 88)
    print("COLLISIONS — does any gamePk get claimed by MORE THAN ONE vendor id?")
    print("=" * 88)
    coll = {pk: v for pk, v in claimed.items() if len(v) > 1}
    if coll:
        for pk, ids in coll.items():
            m = pk_meta.get(pk, {})
            print(f"  *** gamePk {pk} ({m.get('away')} @ {m.get('home')}) claimed "
                  f"by {len(ids)} vendor ids: {ids}")
        print()
        print("  This is F4. NEVER pick one arbitrarily. Either the vendor splits")
        print("  one game across two ids, or the vote is wrong. Investigate.")
    else:
        print("  None. Every matched gamePk is claimed by exactly one vendor id.")

    # =====================================================================
    print()
    print("=" * 88)
    print("VERDICT")
    print("=" * 88)
    ok = sum(1 for r in results if r["verdict"] == "OK")
    amb = sum(1 for r in results if r["verdict"] == "F3 AMBIGUOUS")
    nom = sum(1 for r in results if r["verdict"] == "F2 NO MATCH")
    mean_rate = sum(r["rate"] for r in results) / len(results)

    if empty_boxscores:
        print(f"  *** {len(empty_boxscores)} EMPTY BOX SCORE(S): {empty_boxscores} ***")
        print(f"      Vendor games belonging to these CANNOT resolve, and their")
        print(f"      F2 is a MISSING BOX SCORE, not a name bug. Discount them.")
        print()
    print(f"  vendor games      : {len(results)}")
    print(f"  resolved cleanly  : {ok}  ({ok/len(results):.1%})")
    print(f"  ambiguous (F3)    : {amb}")
    print(f"  no match  (F2)    : {nom}")
    print(f"  collisions (F4)   : {len(coll)}")
    print(f"  mean name-match   : {mean_rate:.1%}   "
          f"(the roster join measured 99.6%)")
    print()
    print("  SANITY (stated BEFORE the run): resolved cleanly should be ~100%,")
    print("  name-match > 90%, collisions ZERO.")
    print()

    if ok == len(results) and not coll and mean_rate > 0.90:
        print("  *** THE CROSSWALK WORKS. ***")
        print("  Every vendor game resolves to exactly one MLB gamePk, by a vote")
        print("  of its own players. No teams needed -- THE PLAYERS ARE THE TEAMS.")
        print()
        print("  This makes the strict market join buildable and FREE:")
        print("    vendor game_id           -> mlb gamePk        (this vote)")
        print("    (gamePk, player_name)    -> player_id         (the box score)")
        print("    -> MARKET_KEY = (gamePk, player_id, category, line)")
        print()
        print("  And the Max Muncy cross-join becomes STRUCTURALLY IMPOSSIBLE:")
        print("  two Max Muncys are never in the same box score.")
        return 0

    print("  *** NOT CLEAN. Read the failures above. ***")
    print("  F2 (no match)  -> the NAME NORMALISATION is the problem, not the premise.")
    print("  F3 (ambiguous) -> a doubleheader. Order by MLB's gameNumber and by")
    print("                    start_time at the HOUR level (the two games are")
    print("                    hours apart; the minute-drift is irrelevant).")
    print("  F4 (collision) -> two vendor ids for one game. Investigate. Never")
    print("                    pick one arbitrarily.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
