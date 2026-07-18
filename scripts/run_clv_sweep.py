#!/usr/bin/env python3
"""
CLV SWEEP — the one diagnostic that says whether there is a model here at all.

=============================================================================
THE CHART THAT MATTERS: THE CAPTURE RATIO
=============================================================================
    CLV / EDGE  --  what SHARE of its claimed edge does the model actually
                    capture in the closing line?

If the model claims a 10% edge and the market moves 5% toward it, it captured
HALF of what it claimed. If it claims 10% and the market moves 0.2%, it captured
2% -- and that is MEAN-REVERSION to the book, not insight.

*** RAW CLV IS NOT A VALID TEST, AND I NEARLY SHIPPED IT AS ONE. ***
An earlier draft plotted raw mean CLV by edge decile and declared "slopes up ->
real edge". VALIDATED ON FOUR SYNTHETIC WORLDS WITH KNOWN TRUTH, it FAILED:

    world                                  raw-CLV verdict    TRUTH
    SHARP (knows the true probability)     UP                 UP     ok
    BIASED (+7pp, ZERO information)        DOWN               FLAT   WRONG
    NOISE (random probabilities)           UP                 FLAT   WRONG
    ANTI-SKILL (systematically backwards)  UP                 DOWN   WRONG

It told us to BET a model that was systematically WRONG. The reason: |edge| is
large exactly where the model's estimate is FAR FROM THE BOOK -- i.e. where its
ERROR is large. The close then mean-reverts toward the book, and that registers
as positive CLV. |edge| was measuring model NOISE, not model confidence.

The CAPTURE RATIO separates them cleanly:

    world                                  capture (top 3 deciles)
    SHARP (knows the true probability)     +0.804      <- captures 80% of claim
    BIASED (+7pp, ZERO information)        -0.014
    NOISE (random probabilities)           +0.020
    ANTI-SKILL (systematically backwards)  +0.018

Two orders of magnitude apart. A real edge captures a large, STABLE share across
every decile. A biased/noisy/backwards model captures ~2% -- pure mean-reversion.

This is a FALSIFIABLE test of whether the edge is real or the bias wearing a
costume, and it is worth more than any ROI number.

=============================================================================
WHY A SWEEP, AND NOT ONE NUMBER  (rule 3)
=============================================================================
A single (book, market, threshold, entry-time) result is one cell of a large
grid. Reporting the best cell is p-hacking. Reporting the WHOLE grid shows
whether the signal is STRUCTURED (rises with edge, concentrates in one market,
strengthens with lead time -- i.e. behaves like a real edge) or SCATTERED
(a few lucky cells in noise).

  edge threshold : does CLV RISE as we get pickier? A real edge must.
  book           : which book is softest? DK/FD are soft; Pinnacle is sharp.
                   MEASURED: Pinnacle posts ZERO `player hits`.
  market         : is the model good at ANYTHING specifically?
  entry time     : MEASURED already -- CLV at T-6h is ~9x CLV at T-1h.
  line           : better on the fat part of the distribution or the tail?

=============================================================================
PERFORMANCE — FETCH ONCE, SWEEP IN MEMORY  (rule 6)
=============================================================================
A naive sweep re-queries SmartStake for every (book x market x hour x
threshold) cell -- hundreds of round-trips against a 621M-row remote dataset.
This fetches ONE frame per (book, entry-hour) and sweeps markets, thresholds,
lines and deciles IN MEMORY. That is ~n_books x n_hours queries, not
n_books x n_hours x n_markets x n_thresholds.

=============================================================================
EVERY CONTROL FROM run_clv_backtest.py STILL APPLIES
=============================================================================
  ctrl1 IDENTITY  : p_over_devig + p_under_devig == 1.0 exactly
  ctrl2 OVERROUND : the book's margin must be plausible (2-10%)
  ctrl3 RANDOM    : a coin-flip bettor must show ~0 CLV (catches a broken join)
  ctrl4 GRAIN     : entry_age_min >= 0 on EVERY row.
                    *** THIS ONE CAUGHT REAL OUTCOME LEAKAGE. ***
                    SmartStake's game_id is NOT unique per game -- 7,455 groups
                    carried two start_times (doubleheaders). Without start_time
                    in the key, max(ts) took a quote from the LATE game while
                    any_value(start_time) took the EARLY game's first pitch,
                    producing "entry prices" recorded 23 HOURS AFTER the game
                    began. ctrl1/2/3 ALL PASSED on that fully-leaked data --
                    they are per-row or symmetric, and blind to a broken grain.
                    A control guards the failure it was designed for and NOTHING
                    ELSE.

=============================================================================
SANITY RANGES — stated BEFORE the run (rule 7)
=============================================================================
  random bettor CLV        : ~0.000 at every cell. If not, the join is broken.
  CLV of a REAL edge       : rises with the edge threshold, and is POSITIVE
  bet_rate at 4% threshold : a SANE model bets 5-15% of the board.
                             The current model bets 74% -- that is the bias,
                             not selectivity. Expect it, and do not celebrate it.
  ROI                      : NOISE below ~2,000 bets per cell. Do not read it.

Usage:
  python scripts/run_clv_sweep.py \
      --sim data/market/sim_probs_2026-06_all.csv \
      --months 2026-06 \
      --books draftkings bet365 \
      --entry-hours 1 4 6 \
      --edges 0.02 0.04 0.06 0.08 0.10 \
      --out data/market/clv_sweep.csv
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MARKET_MAP = {
    "player hits": "hits",
    "player home runs": "home_runs",
    "player bases": "total_bases",
    "player rbis": "rbi",
    "player strikeouts": "strikeouts",
}


def norm_name(s: pd.Series) -> pd.Series:
    def strip_accents(x):
        return "".join(c for c in unicodedata.normalize("NFKD", str(x))
                       if not unicodedata.combining(c))
    return (s.map(strip_accents).str.lower().str.strip()
             .str.replace(r"[.'`\-]", "", regex=True)
             .str.replace(r"\s+", " ", regex=True)
             .str.replace(r"\s+(jr|sr|ii|iii|iv|v)$", "", regex=True)
             .str.strip())


def fetch(months: list[str], book: str, hours: int) -> pd.DataFrame:
    """ONE query per (book, hour). Markets/thresholds/lines swept in memory.

    start_time IS IN THE KEY. SmartStake's game_id is a MATCHUP key, not a game
    key -- 7,455 groups carried two start_times. Omitting start_time blends a
    doubleheader's two games and yields entry prices recorded AFTER first pitch.
    """
    glob = " UNION ALL ".join(
        f"SELECT * FROM 'hf://datasets/SmartStake/mlb-player-props/"
        f"mon={m}/*.parquet'" for m in months
    )
    markets = ", ".join(f"'{m}'" for m in MARKET_MAP)
    return duckdb.sql(f"""
    WITH all_months AS ({glob}),
    src AS (
        SELECT * FROM all_months
        WHERE book = '{book}' AND market IN ({markets})
          AND result IS NOT NULL AND ts < start_time
    ),
    entry AS (
        SELECT game_id, start_time, player, market, line, side,
               arg_max(odds, ts) AS odds, max(ts) AS q_ts
        FROM src
        WHERE ts <= start_time - INTERVAL {hours} HOUR
        GROUP BY 1,2,3,4,5,6
    ),
    close AS (
        SELECT game_id, start_time, player, market, line, side,
               arg_max(odds, ts) AS odds, any_value(result) AS result
        FROM src
        GROUP BY 1,2,3,4,5,6
    )
    SELECT
        eo.game_id, eo.start_time,
        CAST(eo.start_time AT TIME ZONE 'UTC'
                           AT TIME ZONE 'America/New_York' AS DATE) AS game_date,
        eo.player, eo.market, eo.line,
        eo.odds AS entry_over, eu.odds AS entry_under,
        (1.0/eo.odds) / ((1.0/eo.odds) + (1.0/eu.odds))  AS entry_p_over,
        (1.0/eo.odds) + (1.0/eu.odds) - 1.0              AS entry_overround,
        date_diff('minute', eo.q_ts,
                  eo.start_time - INTERVAL {hours} HOUR) AS entry_age_min,
        co.odds AS close_over, cu.odds AS close_under,
        (1.0/co.odds) / ((1.0/co.odds) + (1.0/cu.odds))  AS close_p_over,
        co.result
    FROM entry eo
    JOIN entry eu USING (game_id, start_time, player, market, line)
    JOIN close co USING (game_id, start_time, player, market, line)
    JOIN close cu USING (game_id, start_time, player, market, line)
    WHERE eo.side='over' AND eu.side='under'
      AND co.side='over' AND cu.side='under'
    """).df()


def kelly_vec(p: np.ndarray, dec: np.ndarray, frac: float, cap: float) -> np.ndarray:
    b = dec - 1.0
    q = 1.0 - p
    with np.errstate(divide="ignore", invalid="ignore"):
        f = np.where(b > 0, (b * p - q) / b, 0.0)
    f = np.where(np.isfinite(f) & (f > 0), f, 0.0)
    return np.minimum(f * frac, cap)


def main(argv=None) -> int:
    # This legacy sweep scores SmartStake's numeric result and collapses market
    # identity at player/date. It cannot be a valid input to any current market
    # decision. Keep the source for auditability, but make execution fail closed.
    from src.evaluation.retired_market_evaluators import retired_market_evaluator_exit
    return retired_market_evaluator_exit(Path(__file__).name)

    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--months", nargs="+", default=["2026-06"])
    ap.add_argument("--books", nargs="+", default=["draftkings"])
    ap.add_argument("--entry-hours", nargs="+", type=int, default=[1, 4, 6])
    ap.add_argument("--edges", nargs="+", type=float,
                    default=[0.02, 0.04, 0.06, 0.08, 0.10])
    ap.add_argument("--kelly-fraction", type=float, default=0.25)
    ap.add_argument("--kelly-cap", type=float, default=0.05)
    ap.add_argument("--max-quote-age", type=int, default=90)
    ap.add_argument("--void-min-pa", type=int, default=2)
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--out", default="data/market/clv_sweep.csv")
    ap.add_argument("--decile-out", default="data/market/clv_by_edge_decile.csv")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true",
                    help="RULE 6: fetch ONE (book, hour), report the match rate, "
                         "and exit. Two minutes instead of twenty. Run this FIRST "
                         "-- a full sweep that produces zero matches because the "
                         "model file has the wrong categories is twenty minutes "
                         "you do not get back.")
    args = ap.parse_args(argv)

    duckdb.sql("INSTALL httpfs; LOAD httpfs;")
    duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

    sim = pd.read_csv(args.sim)
    tr = pd.read_csv(args.training, low_memory=False)
    bridge = (tr[["player_id", "game_date", "player_name"]].dropna()
              .drop_duplicates(subset=["player_id", "game_date"]))
    pa = (tr[["player_id", "game_date", "out_pa"]].dropna()
          .drop_duplicates(subset=["player_id", "game_date"]))
    sim = sim.merge(bridge, on=["player_id", "game_date"], how="inner")
    sim = sim.merge(pa, on=["player_id", "game_date"], how="left")
    sim["player_key"] = norm_name(sim["player_name"])
    print(f"[model] {len(sim):,} probabilities, {sim.game_date.nunique()} dates, "
          f"{sorted(sim.category.unique())}")

    rng = np.random.default_rng(args.seed)
    cells: list[dict] = []
    decile_rows: list[dict] = []

    for book in args.books:
        for hours in args.entry_hours:
            raw = fetch(args.months, book, hours)
            if raw.empty:
                print(f"  {book} T-{hours}h: no data. SKIP.")
                continue

            # ---- ctrl4 GRAIN: HARD FAIL. This caught real leakage. --------
            n_neg = int((raw.entry_age_min < 0).sum())
            if n_neg:
                print(f"\nFATAL {book} T-{hours}h: {n_neg:,} rows with NEGATIVE "
                      f"entry_age_min (min {raw.entry_age_min.min():.0f}).\n"
                      f"  An entry quote cannot post-date its own horizon. This is "
                      f"the doubleheader/grain bug -- start_time must be in the "
                      f"GROUP BY and every JOIN. OUTCOME LEAKAGE.",
                      file=sys.stderr)
                return 2

            raw = raw[raw.entry_age_min <= args.max_quote_age]
            raw["category"] = raw["market"].map(MARKET_MAP)
            raw["player_key"] = norm_name(raw["player"])
            raw["game_date"] = pd.to_datetime(raw.game_date).dt.strftime("%Y-%m-%d")

            # doubleheader: the model has ONE row per player-date (it does not
            # model DHs separately); the market now has TWO. Keep the FIRST game
            # and report the drop -- silently duplicating would double-count.
            n_before = len(raw)
            raw = (raw.sort_values("start_time")
                      .drop_duplicates(subset=["game_date", "player_key",
                                               "market", "line"], keep="first"))
            n_dh = n_before - len(raw)

            j = sim.merge(raw, on=["game_date", "player_key", "category", "line"],
                          how="inner")
            if j.empty:
                print(f"  {book} T-{hours}h: nothing matched. SKIP.")
                continue

            if args.void_min_pa > 0:
                j = j[(j.out_pa.isna()) | (j.out_pa >= args.void_min_pa)]

            j = j.rename(columns={"sim_p_over": "p_model"})
            j["won_over"] = (j.result > j.line).astype(float)
            j["edge_over"] = j.p_model - j.entry_p_over
            j["edge_under"] = -j.edge_over            # (1-p)-(1-q) == q-p
            j["side"] = np.where(j.edge_over >= j.edge_under, "over", "under")
            j["edge"] = np.maximum(j.edge_over, j.edge_under)
            j["clv"] = np.where(j.side == "over",
                                j.close_p_over - j.entry_p_over,
                                j.entry_p_over - j.close_p_over)
            j["won"] = np.where(j.side == "over", j.won_over, 1 - j.won_over)
            j["dec_odds"] = np.where(j.side == "over", j.entry_over, j.entry_under)
            j["p_side"] = np.where(j.side == "over", j.p_model, 1 - j.p_model)

            # ---- controls 1-3 (per book/hour) ---------------------------
            io, iu = 1.0 / j.entry_over, 1.0 / j.entry_under
            ident = float(((io/(io+iu)) + (iu/(io+iu)) - 1.0).abs().max())
            orr = float(j.entry_overround.mean())
            ctrl_side = rng.choice(["over", "under"], size=len(j))
            ctrl_clv = float(np.where(ctrl_side == "over",
                                      j.close_p_over - j.entry_p_over,
                                      j.entry_p_over - j.close_p_over).mean())

            print(f"  {book:12s} T-{hours}h: {len(j):6,d} matched  "
                  f"(dropped {n_dh} DH)  ctrl: ident={ident:.1e} "
                  f"orr={orr:.4f} rand_clv={ctrl_clv:+.5f}")

            if args.dry_run:
                print()
                print("=" * 84)
                print("DRY RUN (rule 6) -- one cell fetched, nothing swept")
                print("=" * 84)
                print(f"  market rows fetched : {len(raw):,}")
                print(f"  matched to model    : {len(j):,}")
                print(f"  match rate          : {len(j)/max(1,len(raw)):.1%}")
                print(f"  categories matched  : {sorted(j.category.unique())}")
                print(f"  dates               : {j.game_date.nunique()}")
                print(f"  mean |edge|         : {j.edge.mean():.4f}")
                print(f"  mean entry_age_min  : {j.entry_age_min.mean():.1f}  "
                      f"(MUST be >= 0)")
                print()
                if len(j) < 200:
                    print("  *** LOW MATCH COUNT. A full sweep would produce thin,")
                    print("  unreliable cells. Check the model file's categories and")
                    print("  date range BEFORE spending twenty minutes on the sweep.")
                else:
                    print("  Looks healthy. Re-run without --dry-run for the full sweep.")
                return 0

            # ---- collect for the POOLED decile curve --------------------
            # RULE 4 / DEFECT FOUND ON AUDIT: an earlier draft ran pd.qcut PER
            # (book, hour) and then pooled the buckets by weighted mean. That is
            # WRONG -- decile 9 for DK at T-1h is not the same edge range as
            # decile 9 for bet365 at T-6h, so the pooled curve would MIX
            # INCOMPARABLE BUCKETS and could manufacture (or erase) a slope.
            # The deciles must be cut ONCE, on the POOLED edges, after the loop.
            decile_rows.append(j[["edge", "clv", "won"]].assign(
                book=book, entry_h=hours))

            # ---- the grid: market x line x edge threshold ---------------
            for cat in sorted(j.category.unique()):
                for line in sorted(j[j.category == cat].line.unique()):
                    base = j[(j.category == cat) & (j.line == line)]
                    if len(base) < 100:
                        continue
                    for e in args.edges:
                        b = base[base.edge >= e]
                        if len(b) < 30:
                            continue
                        stake = kelly_vec(b.p_side.to_numpy(float),
                                          b.dec_odds.to_numpy(float),
                                          args.kelly_fraction, args.kelly_cap)
                        pnl = np.where(b.won.to_numpy(float) > 0,
                                       stake * (b.dec_odds.to_numpy(float) - 1.0),
                                       -stake)
                        cells.append(dict(
                            book=book, entry_h=hours, category=cat, line=float(line),
                            min_edge=e, n_pool=len(base), n_bets=len(b),
                            bet_rate=round(len(b) / len(base), 4),
                            mean_edge=round(float(b.edge.mean()), 5),
                            mean_clv=round(float(b.clv.mean()), 5),
                            pct_positive_clv=round(float((b.clv > 0).mean()), 4),
                            win_rate=round(float(b.won.mean()), 4),
                            staked=round(float(stake.sum()), 2),
                            pnl=round(float(pnl.sum()), 2),
                            roi=round(float(pnl.sum() / stake.sum()), 4)
                                if stake.sum() > 0 else 0.0,
                            ctrl1_identity=ident, ctrl2_overround=round(orr, 5),
                            ctrl3_random_clv=round(ctrl_clv, 5),
                        ))

    if not cells:
        print("\nFATAL: no cell produced bets.", file=sys.stderr)
        return 2

    grid = pd.DataFrame(cells)

    # ---- POOLED decile curve (see the audit note in the loop) -------------
    pool = pd.concat(decile_rows, ignore_index=True)
    # NOTE (rule 8): `edge` is |signed edge| -- edge_under == -edge_over exactly,
    # so we always bet the favoured side and edge >= 0 on every row. These are
    # therefore deciles of the FAVOURED-SIDE edge magnitude, which is the right
    # thing: it asks "when the model is more sure, is it more right?"
    pool["edge_decile"] = pd.qcut(pool.edge, 10, labels=False, duplicates="drop")
    dec = (pool.groupby("edge_decile")
           .agg(n=("edge", "size"),
                mean_edge=("edge", "mean"),
                mean_clv=("clv", "mean"),
                pct_positive_clv=("clv", lambda s: float((s > 0).mean())),
                win_rate=("won", "mean"))
           .reset_index())

    # =====================================================================
    # THE HEADLINE
    # =====================================================================
    print()
    print("=" * 84)
    print("*** THE CAPTURE RATIO ***  --  CLV / EDGE, by edge decile")
    print("=" * 84)
    print("  If the model claims a 10% edge and the market moves 5% toward it, the")
    print("  model CAPTURED HALF of what it claimed. If it claims 10% and the market")
    print("  moves 0.2%, it captured 2% -- that is MEAN-REVERSION to the book, not")
    print("  insight.")
    print()
    curve = dec.copy()
    curve["clv_per_edge"] = curve.mean_clv / curve.mean_edge.clip(lower=1e-6)
    print(curve.round(5).to_string(index=False))

    lo3 = float(curve[curve.edge_decile <= 2].clv_per_edge.mean())
    hi3 = float(curve[curve.edge_decile >= 7].clv_per_edge.mean())
    overall = float(curve.clv_per_edge.mean())

    # RULE 7 -- the boundary, justified by MEASUREMENT, not taste.
    # Validated on four synthetic worlds with KNOWN truth (see the module
    # docstring): a SHARP model that knows the true probability captures ~0.80
    # of its claimed edge, and holds that ratio across every decile. A model with
    # ZERO information (+7pp on the book's own price -- exactly this model's
    # measured failure mode), a NOISE model, and an ANTI-SKILL model ALL capture
    # <= 0.02. The gap is two orders of magnitude, so a 0.10 boundary is nowhere
    # near either population.
    CAPTURE_REAL = 0.10

    print()
    print(f"  bottom 3 deciles : {lo3:+.3f}")
    print(f"  top 3 deciles    : {hi3:+.3f}")
    print(f"  overall          : {overall:+.3f}")
    print()
    print("  REFERENCE (synthetic worlds, KNOWN truth):")
    print("    SHARP model (knows the true probability)   captures +0.80")
    print("    BIASED model (+7pp, ZERO information)      captures -0.01")
    print("    NOISE model  (random probabilities)        captures +0.02")
    print("    ANTI-SKILL   (systematically backwards)    captures +0.02")
    print()
    if hi3 >= CAPTURE_REAL:
        print("  *** THE MODEL CAPTURES A REAL SHARE OF ITS CLAIMED EDGE. ***")
        print(f"  On its most confident bets it captures {hi3:+.1%} of what it claims.")
        print("  The market moves TOWARD the model where the model is most sure.")
        print("  That is what genuine information looks like.")
    else:
        print("  *** THE MODEL CAPTURES ALMOST NOTHING OF ITS CLAIMED EDGE. ***")
        print(f"  On its most confident bets it captures only {hi3:+.1%}.")
        print("  A model with ZERO information -- one that simply adds a constant to")
        print("  the book's own price -- captures about the same. The 'edge' is the")
        print("  +6.9pp OVERCONFIDENCE, not insight. The market is not moving toward")
        print("  the model; the model is just far from the book, and the book is right.")
        print()
        print("  DO NOT BET THIS. Fix the calibration first, then re-run this exact")
        print("  sweep. If the capture ratio rises above ~0.10 while the bet rate")
        print("  falls to 5-15%, THAT is the moment the model became real.")

    # ---- WHY RAW CLV IS NOT ENOUGH (this cost a rewrite) ------------------
    slope = float(np.polyfit(curve.edge_decile, curve.mean_clv, 1)[0])
    print()
    print("  (raw mean CLV slope per decile: {:+.6f} -- reported for reference "
          "ONLY.".format(slope))
    print("   RAW CLV IS NOT A VALID TEST: a NOISE model shows RISING raw CLV,")
    print("   because a large |edge| is a large ERROR, and the close mean-reverts")
    print("   toward the book -- which registers as CLV. Validated: raw CLV said")
    print("   'UP' for the noise model AND the anti-skill model. The capture ratio")
    print("   is the statistic that separates them.)")

    # ---- controls ------------------------------------------------------
    print()
    print("=" * 84)
    print("CONTROLS (rule 4) -- four failures, four guards")
    print("=" * 84)
    fails = []
    if (grid.ctrl1_identity > 1e-9).any():
        fails.append("ctrl1 IDENTITY: de-vig broken (sides do not sum to 1.0)")
    if ((grid.ctrl2_overround < 0.01) | (grid.ctrl2_overround > 0.12)).any():
        fails.append("ctrl2 OVERROUND: implausible book margin")
    if (grid.ctrl3_random_clv.abs() > 0.005).any():
        fails.append("ctrl3 RANDOM: a coin-flip bettor shows CLV -> broken JOIN")
    print("  ctrl4 GRAIN passed (enforced as a hard exit inside the loop) -- no "
          "quote post-dates its horizon.")
    if fails:
        for f in fails:
            print(f"  [FAIL] {f}")
        print("\n  *** NOTHING ABOVE CAN BE TRUSTED. Fix the pipeline. ***")
    else:
        print(f"  [PASS] ctrl1 identity max err {grid.ctrl1_identity.max():.1e}")
        print(f"  [PASS] ctrl2 overround {grid.ctrl2_overround.mean():.4f}")
        print(f"  [PASS] ctrl3 random-bettor CLV "
              f"{grid.ctrl3_random_clv.abs().max():+.5f} (must be ~0)")

    # ---- does CLV rise with the THRESHOLD? ------------------------------
    print()
    print("=" * 84)
    print("CLV vs the EDGE THRESHOLD -- a real edge gets BETTER as you get pickier")
    print("=" * 84)
    thr = (grid.groupby("min_edge")
           .apply(lambda g: pd.Series({
               "n_bets": int(g.n_bets.sum()),
               "bet_rate": float((g.bet_rate * g.n_pool).sum() / g.n_pool.sum()),
               "mean_clv": float((g.mean_clv * g.n_bets).sum() / g.n_bets.sum()),
               "win_rate": float((g.win_rate * g.n_bets).sum() / g.n_bets.sum()),
               "roi": float(g.pnl.sum() / g.staked.sum()) if g.staked.sum() else 0.0,
           }), include_groups=False)
           .reset_index())
    print(thr.round(5).to_string(index=False))
    print("\n  A SANE model bets 5-15% of the board at a 4% threshold. If bet_rate")
    print("  is ~0.7, that is the +6.9pp OVERCONFIDENCE, not selectivity.")

    # ---- best/worst cells, honestly labelled ---------------------------
    print()
    print("=" * 84)
    print("BY MARKET  (is the model good at ANYTHING specifically?)")
    print("=" * 84)
    bym = (grid.groupby("category")
           .apply(lambda g: pd.Series({
               "n_bets": int(g.n_bets.sum()),
               "mean_clv": float((g.mean_clv * g.n_bets).sum() / g.n_bets.sum()),
               "win_rate": float((g.win_rate * g.n_bets).sum() / g.n_bets.sum()),
               "roi": float(g.pnl.sum() / g.staked.sum()) if g.staked.sum() else 0.0,
           }), include_groups=False)
           .reset_index())
    print(bym.round(5).to_string(index=False))

    print()
    print("=" * 84)
    print("READ THIS BEFORE BELIEVING ANY CELL")
    print("=" * 84)
    print("  * The single BEST cell in a grid this size is p-hacking. What matters")
    print("    is whether the signal is STRUCTURED -- rising with edge, concentrated")
    print("    in one market, strengthening with lead time -- or SCATTERED.")
    print("  * ROI is NOISE below ~2,000 bets per cell. CLV converges far faster")
    print("    because it does not depend on the outcome.")
    print("  * The entry price is a RECORDED FACT (the book's quote at that minute),")
    print("    but a BACKTESTED bet is not a PLACED bet: no line shopping, no limits,")
    print("    no discipline problem. This measures EDGE, not execution.")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    grid.to_csv(args.out, index=False)
    dec.to_csv(args.decile_out, index=False)
    print(f"\nwrote {args.out}  ({len(grid)} cells)")
    print(f"wrote {args.decile_out}  (the CLV-vs-edge curve)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
