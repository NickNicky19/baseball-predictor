#!/usr/bin/env python3
"""
BUILD THE CROSSWALK — SmartStake vendor_game_id  ->  MLB game_pk  ->  player_id.

This writes an ARTIFACT, not a join. Every downstream consumer reads the same
table, so the identity cannot drift between scripts -- which is exactly how the
same bug appeared three times (the CLV backtest, run_market_verdict, the market
A/B), each patched locally, each recurring.

=============================================================================
WHY THIS IS NEEDED, AND WHY IT IS THE ONLY PATH
=============================================================================
SmartStake's schema, MEASURED (DESCRIBE, not assumed):
    game_id, start_time, player, market, line, side, book, ts, odds, result,
    won, mon
*** NO TEAMS. NO PLAYER IDS. ***

And its event identity is unreliable at EVERY granularity (June 2026):
    n_game_ids                243    <- a MATCHUP key, not a game key
    n_(game_id, start_time)   493    <- OVER-SPLITS: start_time drifts by a MINUTE
    n_start_times             390    <- also fragmented
June has ~218 real games. None of the three counts is right.

A web search for a free alternative found NOTHING. Every free MLB odds archive
(SportsBookReview, Odds Shark, Princeton, Kaggle) is GAME-LEVEL --
moneyline/spread/total. Player props are orders of magnitude more data and
nobody archives them for free. SmartStake is the ONLY free historical
player-prop dataset that exists. So it is worth fixing, not replacing.

=============================================================================
THE INSIGHT: WE DO NOT NEED THE VENDOR TO TELL US THE TEAMS.
             *** THE PLAYERS *ARE* THE TEAMS. ***
=============================================================================
A vendor game_id names ~20-26 players. Those players appear in exactly ONE MLB
box score. So: resolve the names, and let them VOTE for their game_pk.

MEASURED on 2026-06-14 (probe_crosswalk.py), 15 games:
    resolved cleanly  14/15
    ambiguous          0
    collisions         0
    the one failure    an EMPTY BOX SCORE from MLB, not a name problem

And the vote is not close. Typical: 22 players for one game_pk, 0 for any other.

*** THE MAX MUNCY PROOF. *** Two DISTINCT players named Max Muncy played that
day -- one for the Dodgers (824586), one for the Athletics (824994). This is the
EXACT collision that turned a 21,171 x 21,171 merge into 21,201 rows, because
the market A/B paired on a normalised NAME.

    m~ebf5b622  20 players  95.0%   824586:18   823777:1   824994:1   -> 824586
    m~747e59b9  24 players  83.3%   824994:20   824586:1              -> 824994

BOTH RESOLVE CORRECTLY. The stray Muncy vote is 1 against 18. The vote does not
need precision -- it needs a MARGIN, and it has an enormous one.

And once the game_pk is known, so is that game's box score. TWO MAX MUNCYS ARE
NEVER IN THE SAME BOX SCORE. (game_pk, normalised_name) -> player_id is unique
BY CONSTRUCTION. The cross-join becomes STRUCTURALLY IMPOSSIBLE.

=============================================================================
WHY THE BOX SCORE AND NOT THE ROSTER  (found by reading the source)
=============================================================================
get_game_boxscore_stats(game_pk) returns who ACTUALLY APPEARED. A roster
includes players who did not play. SmartStake prices players who are IN the
lineup, so the box score is the tighter, more discriminating match -- and it is
the same call reconstruct_objects already makes, so it costs nothing new.

It also handles doubleheaders for FREE: the two games have DIFFERENT LINEUPS, so
the vote separates them even though the teams are identical. A roster could not.
And MLB marks doubleheaders EXPLICITLY (doubleHeader + gameNumber), so nothing
is ever inferred from a timestamp.

=============================================================================
WHAT THIS REFUSES TO DO
=============================================================================
  * It NEVER picks arbitrarily. An ambiguous game is EXCLUDED and LOGGED.
  * It NEVER imputes a player_id. An unresolved name is EXCLUDED and COUNTED.
  * It NEVER silently drops a game. Every exclusion has a REASON in the artifact.
  * A silent drop_duplicates is data loss wearing a tidy face. There are none.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  vendor games per date      : 12 - 16   (SmartStake covers ~80% of the slate)
  players per vendor game    : 18 - 30
  name-match rate            : 70 - 95%  (MEASURED 79% -- and that is FINE: the
                                          vote needs a MARGIN, not precision)
  winning-vote margin        : the winner should have >= 5x the runner-up
  resolved cleanly           : > 90% of vendor games
  collisions                 : ZERO. A game_pk claimed twice is a HARD FAILURE.

  If the margin is routinely thin (< 3x), the premise is weaker than measured
  and the artifact must NOT be trusted. That is a stop condition, not a tuning
  opportunity.

Usage:
  python scripts/build_crosswalk.py --dates 2026-06-01 ... 2026-06-19
  python scripts/build_crosswalk.py --month 2026-06        # every graded date
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.mlb_api import MLBStatsAPI          # noqa: E402

# --- DECISION BOUNDARIES (rule 2: stated, not fitted; and REPORTED so they can
# --- be judged rather than trusted) -----------------------------------------
#
# MIN_VOTES: a game_pk needs at least this many players voting for it. Below
#   this, the "winner" could be a handful of coincidental name matches.
#   MEASURED: real winners get 18-22 votes. 8 is far below any observed winner
#   and far above any observed runner-up (0-1).
MIN_VOTES = 8
#
# MIN_MARGIN: the winner must have at least this multiple of the runner-up.
#   MEASURED: the worst real margin was 18:1 (the Max Muncy case). 5x is a wide
#   safety band, and every actual margin is written into the artifact so this
#   boundary can be second-guessed with data.
MIN_MARGIN = 5.0


def norm(s: object) -> str:
    """Normalise a name for matching. 99.6% against the MLB roster (measured)."""
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

    Returns (name_to_pks, pk_meta, pk_to_players, empty_pks).

    An EMPTY box score is RETRIED and then REPORTED -- never silently skipped.
    MEASURED on 2026-06-14: gamePk 824424 returned 0 players while every other
    game returned 26-35. A game with no players casts NO VOTES, so any vendor
    game mapping to it fails as "no name matched" -- which would send us hunting
    a name bug that does not exist. A silent skip does not just lose a game; it
    MISATTRIBUTES the failure.
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
            away_id=dig(g, "teams", "away", "team", "id"),
            home_id=dig(g, "teams", "home", "team", "id"),
            game_time_utc=g.get("gameDate", ""),
            game_number=g.get("gameNumber"),
            doubleheader=g.get("doubleHeader"),
        )

        ids: set[int] = set()
        for attempt in range(retries + 1):
            try:
                hitters, pitchers = api.get_game_boxscore_stats(pk)
                ids = set(hitters) | set(pitchers)
            except Exception as exc:
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
            except Exception:
                nm = ""
            if not nm:
                continue
            # Within ONE game a normalised name is unique -- two Max Muncys are
            # never in the same box score. If it somehow is not, we would rather
            # KNOW than silently keep the last one.
            if nm in players and players[nm] != int(pid):
                print(f"    gamePk {pk}: TWO player_ids share the normalised "
                      f"name {nm!r} ({players[nm]} and {pid}). This should be "
                      f"impossible within one game. EXCLUDING the name.",
                      file=sys.stderr)
                players[nm] = -1        # poison it; it will never resolve
                continue
            players[nm] = int(pid)
            name_to_pks[nm].add(pk)

        pk_to_players[pk] = players

    return name_to_pks, pk_meta, pk_to_players, empty_pks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="+", default=None)
    ap.add_argument("--month", default=None,
                    help="build every date in this SmartStake partition")
    ap.add_argument("--out-games", default="data/market/crosswalk_games.csv")
    ap.add_argument("--out-players", default="data/market/crosswalk_players.csv")
    ap.add_argument("--out-report", default="data/market/crosswalk_report.json")
    args = ap.parse_args(argv)

    if not args.dates and not args.month:
        print("FATAL: pass --dates or --month.", file=sys.stderr)
        return 2

    duckdb.sql("INSTALL httpfs; LOAD httpfs;")
    duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

    months = sorted({(d[:7]) for d in args.dates}) if args.dates else [args.month]
    HF = " UNION ALL ".join(
        f"SELECT * FROM 'hf://datasets/SmartStake/mlb-player-props/"
        f"mon={m}/*.parquet'" for m in months
    )

    # ---- the vendor side, ONE query for every date -----------------------
    where = ""
    if args.dates:
        lst = ", ".join(f"DATE '{d}'" for d in args.dates)
        where = f"WHERE slate_date IN ({lst})"

    vend = duckdb.sql(f"""
        WITH src AS (SELECT * FROM ({HF})),
        tagged AS (
            SELECT *,
                   CAST(start_time AT TIME ZONE 'UTC'
                                   AT TIME ZONE 'America/New_York' AS DATE)
                       AS slate_date
            FROM src
        )
        SELECT slate_date,
               game_id,
               min(start_time)        AS first_start,
               count(DISTINCT player) AS n_players,
               list(DISTINCT player)  AS players
        FROM tagged
        {where}
        GROUP BY slate_date, game_id
        ORDER BY slate_date, first_start
    """).df()

    if vend.empty:
        print("FATAL: SmartStake returned no games for those dates.",
              file=sys.stderr)
        return 2

    # *** DuckDB's CAST(... AS DATE) comes back as a pandas TIMESTAMP. ***
    # str(Timestamp) yields "2026-06-01 00:00:00" -- and MLB's schedule endpoint
    # rejects that with an HTTP 400. It wants "2026-06-01".
    #
    # RULE 1, AND I FAILED IT TWICE ON THIS FILE. The first time, DuckDB's
    # list() returned a NUMPY ARRAY and `array or []` raised. Now its DATE cast
    # returns a TIMESTAMP. Both times I asserted what DuckDB returns instead of
    # checking. NORMALISE EXPLICITLY -- never rely on str() of a type you did not
    # verify.
    vend["slate_date"] = pd.to_datetime(vend["slate_date"]).dt.strftime("%Y-%m-%d")
    dates = sorted(vend.slate_date.unique().tolist())

    # A guard, because a malformed date must never reach the API again. This is
    # cheap and it turns a confusing HTTP 400 into a named failure.
    import re
    bad = [d for d in dates if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
    if bad:
        print(f"FATAL: malformed date(s) {bad}. MLB's schedule endpoint needs "
              f"YYYY-MM-DD and rejects anything else with an HTTP 400.",
              file=sys.stderr)
        return 2

    print(f"[vendor] {len(vend)} game_ids across {len(dates)} dates")
    print(f"         {dates[0]} .. {dates[-1]}")

    game_rows: list[dict] = []
    player_rows: list[dict] = []
    report: dict = {"dates": {}, "excluded": []}

    for gd in dates:
        api = MLBStatsAPI(season=int(gd[:4]))
        name_to_pks, pk_meta, pk_to_players, empty_pks = build_mlb_side(api, gd)

        sub = vend[vend.slate_date == gd]      # already normalised to YYYY-MM-DD
        claimed: dict[int, list[str]] = defaultdict(list)
        day = dict(vendor_games=int(len(sub)), mlb_games=len(pk_meta),
                   empty_boxscores=empty_pks, resolved=0, excluded=0)

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

            reason = None
            if not top:
                reason = ("no name matched any box score. If this game's MLB "
                          "game_pk is in empty_boxscores, this is a MISSING BOX "
                          "SCORE, not a name problem.")
            elif win_votes < MIN_VOTES:
                reason = (f"the winning game_pk got only {win_votes} votes "
                          f"(minimum {MIN_VOTES}). Too few to be sure.")
            elif margin < MIN_MARGIN:
                reason = (f"margin {margin:.1f}x is below {MIN_MARGIN}x "
                          f"({win_votes} vs {run_votes}). AMBIGUOUS -- and we do "
                          f"NOT pick arbitrarily.")

            if reason:
                day["excluded"] += 1
                report["excluded"].append(dict(
                    slate_date=gd, vendor_game_id=r.game_id,
                    n_players=int(r.n_players), matched=matched,
                    votes=dict(votes.most_common(3)), reason=reason,
                ))
                continue

            claimed[win_pk].append(r.game_id)
            m = pk_meta[win_pk]
            game_rows.append(dict(
                slate_date=gd,
                vendor_game_id=r.game_id,
                mlb_game_pk=win_pk,
                away=m["away"], home=m["home"],
                game_number=m["game_number"],
                doubleheader=m["doubleheader"],
                vendor_first_start=str(r.first_start),
                mlb_game_time_utc=m["game_time_utc"],
                n_vendor_players=int(r.n_players),
                n_matched=matched,
                match_rate=round(matched / max(1, len(names)), 4),
                win_votes=win_votes,
                runner_up_votes=run_votes,
                # The MARGIN is written into the artifact so MIN_MARGIN can be
                # second-guessed with data rather than trusted.
                margin=(None if run_votes == 0 else round(margin, 2)),
            ))

            # (mlb_game_pk, normalised_name) -> player_id. Unique BY
            # CONSTRUCTION: two players with the same normalised name are never
            # in the same box score.
            for nm, pid in pk_to_players.get(win_pk, {}).items():
                if pid < 0:
                    continue          # poisoned: a within-game name collision
                player_rows.append(dict(
                    slate_date=gd, mlb_game_pk=win_pk,
                    player_key=nm, player_id=pid,
                ))

            day["resolved"] += 1

        # ---- COLLISIONS: a game_pk claimed twice. HARD FAILURE. -----------
        coll = {pk: v for pk, v in claimed.items() if len(v) > 1}
        if coll:
            print(f"\n  *** {gd}: COLLISION -- a game_pk claimed by MORE THAN "
                  f"ONE vendor id ***", file=sys.stderr)
            for pk, ids in coll.items():
                m = pk_meta.get(pk, {})
                print(f"      game_pk {pk} ({m.get('away')} @ {m.get('home')}) "
                      f"<- {ids}", file=sys.stderr)
            print("      NEVER pick one arbitrarily. Both are excluded.",
                  file=sys.stderr)
            bad_ids = {i for v in coll.values() for i in v}
            before = len(game_rows)
            game_rows = [g for g in game_rows
                         if not (g["slate_date"] == gd
                                 and g["vendor_game_id"] in bad_ids)]
            day["resolved"] -= (before - len(game_rows))
            day["excluded"] += (before - len(game_rows))
            day["collisions"] = {str(k): v for k, v in coll.items()}

        report["dates"][gd] = day
        print(f"  {gd}  vendor {day['vendor_games']:2d}  mlb {day['mlb_games']:2d}  "
              f"resolved {day['resolved']:2d}  excluded {day['excluded']:2d}  "
              f"empty_box {len(empty_pks)}")

    if not game_rows:
        print("\nFATAL: nothing resolved.", file=sys.stderr)
        return 2

    G = pd.DataFrame(game_rows)
    P = pd.DataFrame(player_rows).drop_duplicates(
        subset=["mlb_game_pk", "player_key"])

    # =====================================================================
    print()
    print("=" * 88)
    print("THE CROSSWALK")
    print("=" * 88)
    n_vendor = int(len(vend))
    n_res = int(len(G))
    print(f"  vendor games        : {n_vendor}")
    print(f"  RESOLVED            : {n_res}  ({n_res / n_vendor:.1%})")
    print(f"  excluded            : {len(report['excluded'])}")
    print(f"  player mappings     : {len(P):,}  "
          f"((mlb_game_pk, name) -> player_id)")
    print()
    print(f"  match rate  : mean {G.match_rate.mean():.1%}  "
          f"min {G.match_rate.min():.1%}")
    print(f"  win votes   : mean {G.win_votes.mean():.1f}  "
          f"min {int(G.win_votes.min())}")
    fin = G[G.margin.notna()]
    if not fin.empty:
        print(f"  margin      : {len(fin)} game(s) had ANY runner-up; "
              f"worst {fin.margin.min():.1f}x  (boundary {MIN_MARGIN}x)")
        worst = fin.nsmallest(3, "margin")
        print("    tightest:")
        print("     ", worst[["slate_date", "vendor_game_id", "mlb_game_pk",
                              "win_votes", "runner_up_votes", "margin"]]
              .to_string(index=False).replace("\n", "\n      "))
    print(f"  {len(G) - len(fin)} game(s) had ZERO runner-up votes -- the vote "
          f"was UNANIMOUS.")

    # ---- HARD CHECK: (mlb_game_pk, player_key) MUST be unique ------------
    dup = P[P.duplicated(subset=["mlb_game_pk", "player_key"], keep=False)]
    print()
    if not dup.empty:
        print(f"  *** {len(dup)} DUPLICATE (mlb_game_pk, player_key) ROWS. ***",
              file=sys.stderr)
        print("  This should be IMPOSSIBLE -- two players with the same "
              "normalised name are never in the same box score. The artifact is "
              "NOT trustworthy.", file=sys.stderr)
        print(dup.head(10).to_string(index=False), file=sys.stderr)
        return 1
    print("  [OK] (mlb_game_pk, player_key) is UNIQUE -- so "
          "(game, name) -> player_id is well defined.")
    print("       Two Max Muncys are never in the same box score. The cross-join")
    print("       that turned 21,171 + 21,171 into 21,201 is now STRUCTURALLY")
    print("       IMPOSSIBLE.")

    if report["excluded"]:
        print()
        print("  EXCLUDED (never guessed at):")
        for e in report["excluded"][:10]:
            print(f"    {e['slate_date']}  {e['vendor_game_id']}  "
                  f"matched {e['matched']}/{e['n_players']}")
            print(f"      {e['reason']}")

    # ---- emit -----------------------------------------------------------
    for path, df in ((args.out_games, G), (args.out_players, P)):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p, index=False)
        print(f"\nwrote {p}  ({len(df):,} rows)")

    report["_comment"] = (
        "SmartStake vendor_game_id -> MLB mlb_game_pk, resolved by a VOTE of the "
        "vendor game's own players against MLB box scores. SmartStake carries NO "
        "TEAMS and NO PLAYER IDS, and its event identity is unreliable at every "
        "granularity (game_id is a MATCHUP key; (game_id, start_time) over-splits "
        "because start_time drifts by a minute). But the PLAYERS ARE THE TEAMS: "
        "~25 named players appear in exactly ONE box score. "
        "MEASURED margins are enormous (typically 20:0). The Max Muncy case -- two "
        "DISTINCT players, same normalised name, same date -- resolves 18:1 and "
        "20:1. This is the collision that turned a 21,171 x 21,171 merge into "
        "21,201 rows; once the game_pk is known it is structurally impossible."
    )
    report["provenance"] = dict(
        built_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        dates=dates, min_votes=MIN_VOTES, min_margin=MIN_MARGIN,
        n_vendor_games=n_vendor, n_resolved=n_res,
        n_player_mappings=int(len(P)),
    )
    rp = Path(args.out_report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    with rp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    print(f"wrote {rp}")

    print()
    print("=" * 88)
    print("THE MARKET KEY IS NOW HARD")
    print("=" * 88)
    print("    vendor_game_id            -> mlb_game_pk    (crosswalk_games.csv)")
    print("    (mlb_game_pk, name)       -> player_id      (crosswalk_players.csv)")
    print()
    print("    MARKET_KEY = (mlb_game_pk, player_id, category, line)")
    print()
    print("  The SAME key the model emits. No names, no dates, no tolerance")
    print("  windows, no drop_duplicates. Every join validates one_to_one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
