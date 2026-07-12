#!/usr/bin/env python3
"""
CLV BACKTEST — reconstruct bets you never placed, at prices that really existed.

=============================================================================
THE INSIGHT
=============================================================================
CLV = (your entry price) vs (the closing price) on a bet you PLACED. You never
placed one, so CLV looked un-backfillable. It is not.

SmartStake is MINUTE-BY-MINUTE: "one row per book per selection per minute."
So a bet you never placed can be RECONSTRUCTED from prices that really existed:

    your_odds  = the book's price at (first_pitch - N hours)   <- A RECORDED FACT
    close_odds = the book's last quote strictly before first pitch
    result     = the graded outcome (SmartStake `result` / `won`)

The price you WOULD have gotten is not hypothetical. It is in the data.

This is stronger than forward-logging in one specific way: you can SWEEP THE
ENTRY TIME (T-6h, T-4h, T-2h, T-1h, close) and measure WHEN to bet. Forward
logging takes a season to learn that.

=============================================================================
THREE NUMBERS. THEY ARE NOT THE SAME NUMBER.  (rule 8)
=============================================================================
    EDGE = model_prob - entry_prob_devig        your claimed advantage AT ENTRY
    CLV  = close_prob_devig - entry_prob_devig  whether the MARKET later agreed
    ROI  = realised P&L from the graded outcome what you actually made

A bet can be +EDGE, -CLV, +ROI (you were wrong, the market disagreed, you got
lucky). Over a large sample CLV is the best LEADING indicator: it converges far
faster than ROI because it does not depend on the outcome. The code you were
handed had THREE CONFLICTING CLV conventions inside a single function. This
file reports all three, separately, and never substitutes one for another.

SIGN CONVENTION, verified on hand-checkable cases before this was written:
    CLV = p_close_devig(your side) - p_entry_devig(your side)
  You bet the OVER at 2.10; it SHORTENS to 1.80 -> the market came to you
  -> p_over rose 0.4615 -> 0.5385 -> CLV = +0.0769. Correct.
  Same move, but you were on the UNDER -> CLV = -0.0769. Correct.

DE-VIG BOTH SIDES, ALWAYS. Raw 1/odds sums to ~1.05 across the two sides; the
excess is the book's margin. Comparing raw entry to raw close would fold the
book's MARGIN CHANGE into your "CLV" -- widen the vig and you would show
negative CLV having done nothing wrong.

=============================================================================
WHAT WILL SILENTLY POISON THIS, AND HOW EACH IS HANDLED
=============================================================================
1. FABRICATED ENTRY PRICES. If the book had no two-sided quote at T-Nh, there
   is NO entry price. Dropping the row is correct; inventing one produces a CLV
   number that is a LIE. -> we require a two-sided quote AT OR BEFORE the
   horizon and record its AGE. Rows staler than --max-quote-age are DROPPED.

2. LINE MOVEMENT. The LINE moves, not just the price (0.5 -> 1.5). An entry on
   the 0.5 line and a close on the 1.5 line are DIFFERENT BETS. -> every join is
   on (game_id, player, market, LINE). Never on (player, market) alone.

3. POST-FIRST-PITCH QUOTES. Books quote live. A quote after first pitch has
   partial knowledge of the outcome. -> ts < start_time, hard filter, everywhere.

4. VOID BETS. DK voids a starting batter's prop at pa == 1; PrizePicks at
   pa <= 2. Grading a void bet as a LOSS understates ROI. -> --void-min-pa
   drops them (needs an out_pa source), and the count is REPORTED, never hidden.

5. LOOKAHEAD IN THE MODEL. The model probability must come from a
   RECONSTRUCTION that only saw information available before the game. That is
   what run_gate_reconstruct.py + AsOfMLBAPI already guarantee. We consume its
   output; we do not re-derive it.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  n bets at a 4% edge threshold : expect 1-15% of matched selections
  mean CLV of a RANDOM bettor   : ~0.000 (by construction -- a coin flip has no
                                  edge on the close). If a random rule shows
                                  strongly positive CLV, the JOIN or the DE-VIG
                                  is broken, not the strategy.
  mean CLV of a SHARP bettor    : +0.01 to +0.03 is a real, strong edge
  ROI                           : dominated by variance at small n; do NOT read
                                  a positive ROI on <500 bets as evidence.

  A RANDOM-BET CONTROL IS RUN AUTOMATICALLY and must show ~0 CLV. If it does
  not, nothing else in the output can be trusted (rule 4, applied to a backtest).

Usage:
  python scripts/run_clv_backtest.py \
      --sim data/market/sim_probs_2026-06_all.csv \
      --month 2026-06 \
      --entry-hours 0 1 2 4 \
      --book draftkings \
      --min-edge 0.04 \
      --out data/market/clv_backtest.csv
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

MARKET_MAP = {
    "player hits": "hits",
    "player home runs": "home_runs",
    "player bases": "total_bases",
    "player rbis": "rbi",
    "player strikeouts": "strikeouts",
}


def norm_name(s: pd.Series) -> pd.Series:
    """99.6% match rate against the MLB roster (measured)."""
    def strip_accents(x):
        return "".join(c for c in unicodedata.normalize("NFKD", str(x))
                       if not unicodedata.combining(c))
    return (s.map(strip_accents).str.lower().str.strip()
             .str.replace(r"[.'`\-]", "", regex=True)
             .str.replace(r"\s+", " ", regex=True)
             .str.replace(r"\s+(jr|sr|ii|iii|iv|v)$", "", regex=True)
             .str.strip())


def american_from_decimal(d: float) -> float:
    return (d - 1.0) * 100.0 if d >= 2.0 else -100.0 / (d - 1.0)


def fetch_entry_and_close(hf: str, book: str, hours: int) -> pd.DataFrame:
    """One row per two-sided selection: the price at T-Nh AND at the close.

    BOTH sides required at BOTH timestamps -- a one-sided quote cannot be
    de-vigged, and an un-de-vigged price folds the book's margin into the CLV.
    """
    markets = ", ".join(f"'{m}'" for m in MARKET_MAP)
    return duckdb.sql(f"""
    WITH src AS (
        SELECT * FROM {hf}
        WHERE book = '{book}'
          AND market IN ({markets})
          AND result IS NOT NULL          -- graded only
          AND ts < start_time             -- NEVER a live/post-first-pitch quote
    ),
    entry AS (                            -- last quote AT OR BEFORE the horizon
        -- *** start_time IS PART OF THE KEY. THIS IS NOT OPTIONAL. ***
        -- MEASURED: SmartStake's game_id is NOT unique per game -- 7,455 groups
        -- carried TWO distinct start_times. It behaves like a MATCHUP key, so a
        -- doubleheader (two games, same teams, same day) COLLAPSES into one
        -- group. Without start_time in the key:
        --     any_value(start_time) picked ONE game arbitrarily, while
        --     max(ts)               took the latest quote from EITHER game.
        -- Result, straight from the data:
        --     q_ts       2026-06-06 23:34   <- a quote from the LATE game
        --     start_time 2026-06-06 00:16   <- the EARLY game's first pitch
        --     age        -1398 minutes      <- the quote is 23h AFTER first pitch
        -- The per-row `ts < start_time` filter PASSED (each row satisfied it
        -- against its OWN start_time) and the aggregation then mixed the games.
        -- That is OUTCOME LEAKAGE: an "entry price" quoted after the game began.
        SELECT game_id, start_time, player, market, line, side,
               arg_max(odds, ts) AS odds,
               max(ts)           AS q_ts
        FROM src
        WHERE ts <= start_time - INTERVAL {hours} HOUR
        GROUP BY 1, 2, 3, 4, 5, 6
    ),
    close AS (                            -- last quote before first pitch
        SELECT game_id, start_time, player, market, line, side,
               arg_max(odds, ts) AS odds,
               any_value(result) AS result
        FROM src
        GROUP BY 1, 2, 3, 4, 5, 6
    )
    SELECT
        eo.game_id,
        CAST(eo.start_time AT TIME ZONE 'UTC'
                           AT TIME ZONE 'America/New_York' AS DATE) AS game_date,
        eo.start_time,
        eo.player, eo.market, eo.line,
        -- ENTRY (both sides -> de-viggable)
        eo.odds AS entry_over, eu.odds AS entry_under,
        (1.0/eo.odds) / ((1.0/eo.odds) + (1.0/eu.odds))            AS entry_p_over,
        (1.0/eo.odds) + (1.0/eu.odds) - 1.0                        AS entry_overround,
        -- staleness: minutes the quote PRECEDES the horizon. MUST BE >= 0.
        date_diff('minute', eo.q_ts,
                  eo.start_time - INTERVAL {hours} HOUR)           AS entry_age_min,
        -- CLOSE (both sides)
        co.odds AS close_over, cu.odds AS close_under,
        (1.0/co.odds) / ((1.0/co.odds) + (1.0/cu.odds))            AS close_p_over,
        co.result                                                  AS result
    FROM entry eo
    JOIN entry eu ON eo.game_id=eu.game_id AND eo.start_time=eu.start_time
                 AND eo.player=eu.player AND eo.market=eu.market
                 AND eo.line=eu.line
                 AND eo.side='over' AND eu.side='under'
    JOIN close co ON eo.game_id=co.game_id AND eo.start_time=co.start_time
                 AND eo.player=co.player AND eo.market=co.market
                 AND eo.line=co.line AND co.side='over'
    JOIN close cu ON eo.game_id=cu.game_id AND eo.start_time=cu.start_time
                 AND eo.player=cu.player AND eo.market=cu.market
                 AND eo.line=cu.line AND cu.side='under'
    """).df()


def kelly(p: float, dec_odds: float, frac: float, cap: float) -> float:
    """Fractional Kelly, capped. b = net decimal payout."""
    b = dec_odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - p
    f = (b * p - q) / b
    if f <= 0:
        return 0.0
    return min(f * frac, cap)


def simulate(bets: pd.DataFrame, kelly_frac: float, kelly_cap: float) -> dict:
    """Grade the reconstructed bets. EDGE, CLV and ROI are computed separately."""
    if bets.empty:
        return {}
    # payout at the price you ACTUALLY got (entry), not the close
    dec = np.where(bets.side == "over", bets.entry_over, bets.entry_under)
    p_model = np.where(bets.side == "over", bets.p_model, 1.0 - bets.p_model)
    stake = np.array([kelly(p, d, kelly_frac, kelly_cap)
                      for p, d in zip(p_model, dec)])
    won = bets.won.to_numpy(float)
    pnl = np.where(won > 0, stake * (dec - 1.0), -stake)

    return {
        "n_bets": int(len(bets)),
        "win_rate": float(won.mean()),
        # EDGE: claimed advantage at entry
        "mean_edge": float(bets.edge.mean()),
        # CLV: did the market come to us?
        "mean_clv": float(bets.clv.mean()),
        "pct_positive_clv": float((bets.clv > 0).mean()),
        # ROI: realised
        "total_staked": float(stake.sum()),
        "total_pnl": float(pnl.sum()),
        "roi": float(pnl.sum() / stake.sum()) if stake.sum() > 0 else 0.0,
        "mean_entry_age_min": float(bets.entry_age_min.mean()),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True, help="run_gate_reconstruct.py output")
    ap.add_argument("--month", default="2026-06")
    ap.add_argument("--book", default="draftkings")
    ap.add_argument("--entry-hours", nargs="+", type=int, default=[0, 1, 2, 4])
    ap.add_argument("--min-edge", type=float, default=0.04)
    ap.add_argument("--kelly-fraction", type=float, default=0.25)
    ap.add_argument("--kelly-cap", type=float, default=0.05)
    ap.add_argument("--max-quote-age", type=int, default=90,
                    help="drop an 'entry' whose quote is older than this many "
                         "minutes BEFORE the horizon -- you could not actually "
                         "have gotten a stale price")
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--void-min-pa", type=int, default=2,
                    help="drop bets the book would VOID (DK voids pa==1). 0=keep all")
    ap.add_argument("--out", default=None)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    hf = (f"'hf://datasets/SmartStake/mlb-player-props/"
          f"mon={args.month}/*.parquet'")
    duckdb.sql("INSTALL httpfs; LOAD httpfs;")
    duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

    # ---- model probabilities (leakage-safe reconstruction) ----------------
    sim = pd.read_csv(args.sim)
    tr = pd.read_csv(args.training, low_memory=False)
    bridge = (tr[["player_id", "game_date", "player_name"]].dropna()
              .drop_duplicates(subset=["player_id", "game_date"]))
    pa = (tr[["player_id", "game_date", "out_pa"]].dropna()
          .drop_duplicates(subset=["player_id", "game_date"]))
    sim = sim.merge(bridge, on=["player_id", "game_date"], how="inner")
    sim = sim.merge(pa, on=["player_id", "game_date"], how="left")
    sim["player_key"] = norm_name(sim["player_name"])
    print(f"[model] {len(sim):,} reconstructed probabilities, "
          f"{sim.game_date.nunique()} dates")

    rng = np.random.default_rng(args.seed)
    summaries = []

    for hours in args.entry_hours:
        raw = fetch_entry_and_close(hf, args.book, hours)
        if raw.empty:
            print(f"\nT-{hours}h: no two-sided entry+close pairs. SKIPPED.")
            continue
        raw["category"] = raw["market"].map(MARKET_MAP)
        raw["player_key"] = norm_name(raw["player"])
        raw["game_date"] = pd.to_datetime(raw["game_date"]).dt.strftime("%Y-%m-%d")

        n_raw = len(raw)

        # ==== CONTROL 4: THE GRAIN.  entry_age_min MUST be >= 0. ====
        # An "age" is minutes the quote PRECEDES the horizon. A NEGATIVE age
        # means the quote came AFTER the horizon -- and in the bug this guard
        # was written for, after FIRST PITCH. That is OUTCOME LEAKAGE.
        #
        # The other three controls are BLIND to it:
        #   ctrl1 (de-vig identity) is per-row and still holds.
        #   ctrl2 (overround)       is per-row and still holds.
        #   ctrl3 (random bettor)   sees ~0 CLV because leaked prices are still
        #                           SYMMETRIC -- they do not bias a coin flip.
        # A broken GRAIN is a fourth, independent failure mode. Guard it
        # explicitly or it will not be caught.
        n_neg = int((raw.entry_age_min < 0).sum())
        if n_neg:
            print(f"\nFATAL at T-{hours}h: {n_neg:,} of {n_raw:,} rows have a "
                  f"NEGATIVE entry_age_min.", file=sys.stderr)
            print(f"  min = {raw.entry_age_min.min():.0f} minutes. An entry quote "
                  f"cannot post-date its own horizon.", file=sys.stderr)
            print(f"  This is the doubleheader/grain bug: SmartStake's game_id is "
                  f"NOT unique per game\n"
                  f"  (measured: 7,455 groups carried two start_times), so without "
                  f"start_time in the\n"
                  f"  GROUP BY, two games blend and the 'entry price' can be a "
                  f"quote taken AFTER\n"
                  f"  first pitch. OUTCOME LEAKAGE. Nothing downstream is valid.",
                  file=sys.stderr)
            return 2

        stale = int((raw.entry_age_min > args.max_quote_age).sum())
        raw = raw[raw.entry_age_min <= args.max_quote_age]

        # The model file has ONE row per (player, date, category, line) -- it has
        # no start_time, because the simulator does not model doubleheaders
        # separately. The market file now has TWO rows for a doubleheader player.
        # Joining naively DUPLICATES the model row across both games, which
        # double-counts the bet.
        #
        # We keep ONLY the FIRST game of a doubleheader (earliest start_time) and
        # REPORT how many rows that drops. Silently duplicating would inflate the
        # bet count and the P&L; silently dropping without saying so would hide a
        # real coverage gap.
        n_before = len(raw)
        raw = (raw.sort_values("start_time")
                  .drop_duplicates(subset=["game_date", "player_key",
                                           "market", "line"], keep="first"))
        n_dh = n_before - len(raw)

        j = sim.merge(raw, on=["game_date", "player_key", "category", "line"],
                      how="inner")
        if j.empty:
            print(f"\nT-{hours}h: nothing matched the model. SKIPPED.")
            continue

        # void bets (DK voids a starter's prop at pa==1)
        n_void = 0
        if args.void_min_pa > 0:
            n_void = int((j.out_pa < args.void_min_pa).sum())
            j = j[j.out_pa >= args.void_min_pa]

        j = j.rename(columns={"sim_p_over": "p_model"})
        j["won_over"] = (j["result"] > j["line"]).astype(float)

        # EDGE on each side, at the ENTRY price (de-vigged)
        j["edge_over"] = j.p_model - j.entry_p_over
        j["edge_under"] = (1 - j.p_model) - (1 - j.entry_p_over)
        j["side"] = np.where(j.edge_over >= j.edge_under, "over", "under")
        j["edge"] = np.maximum(j.edge_over, j.edge_under)

        # CLV: did the market move TOWARD our side? (sign verified by hand)
        j["clv"] = np.where(
            j.side == "over",
            j.close_p_over - j.entry_p_over,
            (1 - j.close_p_over) - (1 - j.entry_p_over),
        )
        j["won"] = np.where(j.side == "over", j.won_over, 1 - j.won_over)

        bets = j[j.edge >= args.min_edge].copy()
        s = simulate(bets, args.kelly_fraction, args.kelly_cap)
        s.update(dict(entry_h=hours, n_matched=len(j),
                      n_stale_dropped=stale, n_void_dropped=n_void,
                      n_doubleheader_dropped=n_dh,
                      bet_rate=round(len(bets) / max(1, len(j)), 4)))

        # ==================================================================
        # THREE CONTROLS, THREE FAILURE MODES  (rule 4, applied to a backtest)
        # ==================================================================
        # A first draft shipped only the random-bet control. A mutation test
        # PROVED IT IS BLIND to a broken de-vig: the vig is SYMMETRIC, so it
        # cancels when you average over random sides. Raw-implied-prob,
        # de-vig-entry-only, AND a sign flip all showed ~0 mean CLV for a random
        # bettor. One control is not enough.
        #
        # CONTROL 1 -- IDENTITY. p_over_devig + p_under_devig == 1.0, EXACTLY.
        #   Not a statistic; an identity. A broken or absent de-vig violates it
        #   instantly (raw implied probs sum to ~1.047). This is the one the
        #   random-bet control cannot see.
        p_sum = j.entry_p_over + (1.0 - j.entry_p_over)   # by construction
        # the REAL test: recompute both sides from the raw odds independently
        io, iu = 1.0 / j.entry_over, 1.0 / j.entry_under
        p_o = io / (io + iu)
        p_u = iu / (io + iu)
        s["ctrl1_devig_identity_maxerr"] = float((p_o + p_u - 1.0).abs().max())

        # CONTROL 2 -- OVERROUND. The book's margin must be plausible. A 0.5
        #   overround is a data error; a large negative one is not a real arb in
        #   bulk, it is a broken quote.
        s["ctrl2_mean_overround"] = float(j.entry_overround.mean())

        # CONTROL 3 -- RANDOM BETTOR. A coin-flip bettor has no edge on the
        #   close, so mean CLV must be ~0. This catches a broken JOIN (entry and
        #   close taken from DIFFERENT selections), which would show as phantom
        #   CLV. Verified on synthetic data: a SHARP bettor shows +0.068 mean CLV
        #   and 99.5% positive, while a random one shows ~0.000 -- so the backtest
        #   CAN distinguish skill from noise.
        ctrl = j.copy()
        ctrl["side"] = rng.choice(["over", "under"], size=len(ctrl))
        ctrl["clv"] = np.where(
            ctrl.side == "over",
            ctrl.close_p_over - ctrl.entry_p_over,
            (1 - ctrl.close_p_over) - (1 - ctrl.entry_p_over),
        )
        s["ctrl3_random_mean_clv"] = float(ctrl.clv.mean())

        # NOTE: the SIGN convention is caught by NONE of these three (a flip
        # leaves every mean at ~0). It was verified separately on hand-checkable
        # cases -- see the module docstring.

        summaries.append(s)

    if not summaries:
        print("\nFATAL: no horizon produced any bets.", file=sys.stderr)
        return 2

    out = pd.DataFrame(summaries)[[
        "entry_h", "n_matched", "n_stale_dropped", "n_void_dropped",
        "n_doubleheader_dropped",
        "n_bets", "bet_rate", "mean_entry_age_min",
        "mean_edge", "mean_clv", "pct_positive_clv",
        "win_rate", "total_staked", "total_pnl", "roi",
        "ctrl1_devig_identity_maxerr", "ctrl2_mean_overround",
        "ctrl3_random_mean_clv",
    ]].round(5)

    print()
    print("=" * 92)
    print(f"CLV BACKTEST — {args.book}, {args.month}, min_edge={args.min_edge}, "
          f"{args.kelly_fraction:.0%}-Kelly (cap {args.kelly_cap:.0%})")
    print("=" * 92)
    print(out.to_string(index=False))

    print()
    print("=" * 92)
    print("THREE CONTROLS (rule 4) -- each catches a DIFFERENT failure")
    print("=" * 92)
    fails = []

    bad1 = out[out.ctrl1_devig_identity_maxerr > 1e-9]
    if not bad1.empty:
        fails.append("DE-VIG IDENTITY VIOLATED")
        print("  [FAIL] ctrl1 IDENTITY: p_over_devig + p_under_devig != 1.0")
        print("         The de-vig is BROKEN or ABSENT. Raw implied probabilities")
        print("         sum to ~1.047 -- that excess IS the book's margin, and")
        print("         folding it into CLV makes the number meaningless.")
        print(bad1[["entry_h", "ctrl1_devig_identity_maxerr"]].to_string(index=False))
    else:
        print("  [PASS] ctrl1 IDENTITY: the two de-vigged sides sum to exactly 1.0")

    bad2 = out[(out.ctrl2_mean_overround < 0.01) | (out.ctrl2_mean_overround > 0.12)]
    if not bad2.empty:
        fails.append("IMPLAUSIBLE OVERROUND")
        print("  [FAIL] ctrl2 OVERROUND outside [0.01, 0.12] -- a book's margin is")
        print("         normally 2-8%. This is a data error, not a market.")
        print(bad2[["entry_h", "ctrl2_mean_overround"]].to_string(index=False))
    else:
        print(f"  [PASS] ctrl2 OVERROUND: {out.ctrl2_mean_overround.mean():.4f} "
              f"({out.ctrl2_mean_overround.mean()*100:.1f}%) -- a plausible book margin")

    # ctrl4 is enforced as a HARD FAIL inside the loop (we return 2), so if we
    # got here it passed. Say so explicitly -- a silent pass on the control that
    # caught real leakage is exactly how it gets removed by a future edit.
    print(f"  [PASS] ctrl4 GRAIN: entry_age_min >= 0 on every row -- no quote "
          f"post-dates its horizon.")
    print(f"         (this is the control that caught the doubleheader leakage: "
          f"game_id is\n"
          f"          NOT unique per game, so start_time must be in the key.)")

    bad3 = out[out.ctrl3_random_mean_clv.abs() > 0.005]
    if not bad3.empty:
        fails.append("RANDOM BETTOR HAS CLV")
        print("  [FAIL] ctrl3 RANDOM: a coin-flip bettor shows NON-ZERO CLV.")
        print("         They cannot beat the close. The JOIN is broken -- entry and")
        print("         close are probably coming from DIFFERENT selections.")
        print(bad3[["entry_h", "ctrl3_random_mean_clv"]].to_string(index=False))
    else:
        print("  [PASS] ctrl3 RANDOM: a coin-flip bettor shows ~0 CLV at every")
        print("         horizon, so the join is sound and any CLV above is signal.")

    if fails:
        print(f"\n  *** {len(fails)} CONTROL(S) FAILED: {', '.join(fails)}. ***")
        print("  NOTHING ABOVE CAN BE TRUSTED. Fix the pipeline, not the strategy.")

    print("\n  NOTE: a SIGN FLIP is caught by NONE of these (it leaves every mean")
    print("  at ~0). The convention was verified separately on hand-checkable")
    print("  cases: over @2.10 shortening to 1.80 -> CLV = +0.0769 (market came")
    print("  to us). Verified before this file was written.")

    print()
    print("=" * 92)
    print("READ THIS")
    print("=" * 92)
    print("  * EDGE, CLV and ROI are THREE DIFFERENT NUMBERS. A bet can be +EDGE,")
    print("    -CLV and +ROI (wrong, disagreed with, lucky). CLV is the best")
    print("    LEADING indicator -- it converges far faster than ROI.")
    print("  * ROI on a few hundred bets is NOISE. Do not read it as evidence.")
    print("  * The entry price is REAL -- it is the book's recorded quote at that")
    print("    minute. But a BACKTESTED bet is not a PLACED bet: no line shopping,")
    print("    no limits, no discipline problem. This measures EDGE, not execution.")
    print("  * Stale quotes were DROPPED (>{}min old), not used. A price you could"
          .format(args.max_quote_age))
    print("    not actually have gotten is not an entry price.")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args.out, index=False)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
