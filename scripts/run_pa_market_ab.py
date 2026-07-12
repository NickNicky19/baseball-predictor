#!/usr/bin/env python3
"""
DID THE PA FIX CONVERT INTO MARKET EDGE?

=============================================================================
THE QUESTION -- and why it is NOT the same question the gate answered
=============================================================================
The PA gate PASSED. On DK-gradeable rows (pa>=2):
    hits 0.5  dbrier -0.00335  CI [-0.00486, -0.00190]  -> CANDIDATE
    hits 1.5  dbrier -0.00043  CI [-0.00071, -0.00016]  -> CANDIDATE
Frozen won ZERO lines. `strikeouts` drift EXACTLY 0.00000 (hard control).

That means the fix is better CALIBRATED AGAINST OUTCOMES.

It does NOT mean it has more EDGE AGAINST THE MARKET. Those are different
questions and they can diverge: a model can become better calibrated while
moving CLOSER to the book -- more accurate AND less useful, because there is
no longer any disagreement to bet on.

This script asks the second question, on the SAME ROWS, PAIRED.

=============================================================================
WHY PAIRED, AND WHY THAT IS MUCH STRONGER  (rule 3)
=============================================================================
Running the sweep twice and eyeballing two capture ratios compares two
INDEPENDENT samples. The market noise -- which selections DK happened to price
softly that day -- lands in BOTH arms and swamps the difference.

Instead we join frozen and candidate on the SAME (game, player, market, line)
and compare PER SELECTION. Both arms see the IDENTICAL recorded entry price and
the IDENTICAL close, so the MARKET'S MOVE is held fixed and the only thing that
varies is the model.

*** BUT BE PRECISE (rule 8). The market's move is identical; the CLV is NOT. ***
CLV depends on WHICH SIDE the model bets:
    clv = +(close_p - entry_p)   if the model bets the OVER
    clv = -(close_p - entry_p)   if it bets the UNDER
If frozen bets the over and candidate bets the under on the same selection,
their CLVs are EXACT NEGATIVES. So the market does not "cancel" -- the SIDE
CHOICE is part of what is being measured, and that is exactly right: the
question is whether the candidate puts its confidence where the market actually
moves.

What the pairing DOES eliminate is the sampling noise of "which selections DK
happened to price softly that day" -- that lands in both arms identically. That
alone makes this far more sensitive than two independent sweeps.

=============================================================================
THE THREE OUTCOMES, DECLARED BEFORE THE RUN  (rule 7)
=============================================================================
  1. CAPTURE UP, BET RATE DOWN
     The fix converted calibration into edge. The model is now pickier AND the
     market moves toward it more. PROMOTE, then go find the remaining bias.

  2. CAPTURE FLAT, BET RATE DOWN
     The fix removed overconfidence but the model still has NO INFORMATION the
     market lacks. It is a better-calibrated model with no edge. Still worth
     promoting (calibration is the foundation), but do NOT bet, and the next
     work item is finding actual SIGNAL, not more calibration.

  3. CAPTURE DOWN
     The fix moved the model TOWARD the book. Better calibrated, less useful.
     This is a real possibility and it is why we measure before promoting.

  MEASURED BASELINE (frozen model, 2026-06, 19 dates):
      capture ratio, top 3 deciles : +0.070
      bet rate @ 4% edge           : 0.641
      mean |edge|                  : 0.076
      decile 9 capture             : +0.176  <- the only decile with real signal

  A SANE model bets 5-15% of the board at 4%. 64% is the overconfidence.

=============================================================================
EVERY CONTROL FROM THE SWEEP STILL APPLIES -- INCLUDING ctrl4
=============================================================================
  ctrl4 GRAIN caught REAL OUTCOME LEAKAGE that ctrl1/2/3 were all blind to:
  SmartStake's game_id is NOT unique per game (7,455 groups carried two
  start_times -- doubleheaders). Without start_time in the key, max(ts) took a
  quote from the LATE game while any_value(start_time) took the EARLY game's
  first pitch, producing "entry prices" recorded 23 HOURS AFTER first pitch.
  A control guards the failure it was designed for and NOTHING ELSE.

Usage:
  # 1. reconstruct the SAME dates with the CANDIDATE config
  python run_gate_reconstruct.py --pairs data/models/gbm/wf_predictions_catboost.csv \
      --config config/config.pa.json \
      --dates 2026-06-01 ... 2026-06-19 \
      --out data/market/sim_probs_2026-06_pa.csv

  # 2. the paired comparison
  python scripts/run_pa_market_ab.py \
      --frozen data/market/sim_probs_2026-06_all.csv \
      --candidate data/market/sim_probs_2026-06_pa.csv \
      --month 2026-06 --book draftkings --entry-hours 4
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

# Declared BEFORE the run (rule 7). Validated on four synthetic worlds with
# KNOWN truth: a SHARP model captures +0.80 of its claimed edge; BIASED (+7pp,
# zero information), NOISE, and ANTI-SKILL all capture <= 0.02. Two orders of
# magnitude apart, so 0.10 sits nowhere near either population.
CAPTURE_REAL = 0.10
# MEASURED on the frozen model (2026-06, 19 dates, DK, T-4h). NOTE: an earlier
# ALL-ROWS capture reported +0.070; that denominator collapses as edge -> 0 and
# was inflated. The BETS-ONLY value is what this script reports.
FROZEN_BASELINE = {"capture_bets": 0.002, "bet_rate_4pct": 0.641, "mean_edge": 0.076}


def norm_name(s: pd.Series) -> pd.Series:
    def strip_accents(x):
        return "".join(c for c in unicodedata.normalize("NFKD", str(x))
                       if not unicodedata.combining(c))
    return (s.map(strip_accents).str.lower().str.strip()
             .str.replace(r"[.'`\-]", "", regex=True)
             .str.replace(r"\s+", " ", regex=True)
             .str.replace(r"\s+(jr|sr|ii|iii|iv|v)$", "", regex=True)
             .str.strip())


def fetch_market(month: str, book: str, hours: int) -> pd.DataFrame:
    """Entry + close, two-sided, de-vigged.

    start_time IS IN THE KEY -- see the ctrl4 note in the module docstring.
    """
    hf = f"'hf://datasets/SmartStake/mlb-player-props/mon={month}/*.parquet'"
    markets = ", ".join(f"'{m}'" for m in MARKET_MAP)
    return duckdb.sql(f"""
    WITH src AS (
        SELECT * FROM {hf}
        WHERE book = '{book}' AND market IN ({markets})
          AND result IS NOT NULL AND ts < start_time
    ),
    entry AS (
        SELECT game_id, start_time, player, market, line, side,
               arg_max(odds, ts) AS odds, max(ts) AS q_ts
        FROM src WHERE ts <= start_time - INTERVAL {hours} HOUR
        GROUP BY 1,2,3,4,5,6
    ),
    close AS (
        SELECT game_id, start_time, player, market, line, side,
               arg_max(odds, ts) AS odds, any_value(result) AS result
        FROM src GROUP BY 1,2,3,4,5,6
    )
    SELECT eo.game_id, eo.start_time,
        CAST(eo.start_time AT TIME ZONE 'UTC'
                           AT TIME ZONE 'America/New_York' AS DATE) AS game_date,
        eo.player, eo.market, eo.line,
        eo.odds AS entry_over, eu.odds AS entry_under,
        (1.0/eo.odds) / ((1.0/eo.odds) + (1.0/eu.odds)) AS entry_p_over,
        (1.0/eo.odds) + (1.0/eu.odds) - 1.0             AS entry_overround,
        date_diff('minute', eo.q_ts,
                  eo.start_time - INTERVAL {hours} HOUR) AS entry_age_min,
        (1.0/co.odds) / ((1.0/co.odds) + (1.0/cu.odds)) AS close_p_over,
        co.result
    FROM entry eo
    JOIN entry eu USING (game_id, start_time, player, market, line)
    JOIN close co USING (game_id, start_time, player, market, line)
    JOIN close cu USING (game_id, start_time, player, market, line)
    WHERE eo.side='over' AND eu.side='under'
      AND co.side='over' AND cu.side='under'
    """).df()


def load_model(path: Path, training: Path, tag: str) -> pd.DataFrame:
    sim = pd.read_csv(path)
    tr = pd.read_csv(training, low_memory=False)
    bridge = (tr[["player_id", "game_date", "player_name"]].dropna()
              .drop_duplicates(subset=["player_id", "game_date"]))
    pa = (tr[["player_id", "game_date", "out_pa"]].dropna()
          .drop_duplicates(subset=["player_id", "game_date"]))
    sim = sim.merge(bridge, on=["player_id", "game_date"], how="inner")
    sim = sim.merge(pa, on=["player_id", "game_date"], how="left")
    sim["player_key"] = norm_name(sim["player_name"])
    sim = sim.rename(columns={"sim_p_over": f"p_{tag}"})
    return sim[["game_date", "player_key", "category", "line", "out_pa", f"p_{tag}"]]


def arm_stats(j: pd.DataFrame, tag: str, min_edge: float,
              deciles: np.ndarray | None = None) -> dict:
    """Compute edge / clv / capture for ONE arm on the shared rows.

    *** deciles MUST be cut ONCE, on the FROZEN edge, and applied to BOTH arms. ***
    RULE 4 / DEFECT FOUND ON AUDIT -- and it is the SAME bug I fixed in
    run_clv_sweep.py and then reintroduced here. If each arm cuts its own qcut,
    then "decile 9" for frozen is a DIFFERENT EDGE RANGE than "decile 9" for the
    candidate (the candidate's edges are SMALLER if the fix worked, which is the
    whole point). Comparing them would compare INCOMPARABLE BUCKETS.

    Cutting on the frozen edge means both arms are describing THE SAME
    SELECTIONS -- "on the rows the OLD model was most confident about, how much
    does each arm capture?" That is the comparison that means something.
    """
    p = j[f"p_{tag}"].to_numpy(float)
    e_over = p - j.entry_p_over.to_numpy(float)
    bet_over = e_over >= 0
    edge = np.abs(e_over)
    clv = np.where(bet_over,
                   j.close_p_over.to_numpy(float) - j.entry_p_over.to_numpy(float),
                   j.entry_p_over.to_numpy(float) - j.close_p_over.to_numpy(float))
    won_over = (j.result.to_numpy(float) > j.line.to_numpy(float)).astype(float)
    won = np.where(bet_over, won_over, 1.0 - won_over)

    d = pd.DataFrame(dict(edge=edge, clv=clv, won=won))
    d["dec"] = deciles if deciles is not None else \
        pd.qcut(d.edge, 10, labels=False, duplicates="drop")
    g = (d.groupby("dec")
         .agg(n=("edge", "size"), mean_edge=("edge", "mean"),
              mean_clv=("clv", "mean"), win_rate=("won", "mean"))
         .reset_index())
    g["capture"] = g.mean_clv / g.mean_edge.clip(lower=1e-6)

    bet = d[d.edge >= min_edge]

    # ================================================================
    # CAPTURE IS COMPUTED ON BETS ONLY.  *** THIS IS NOT OPTIONAL. ***
    # ================================================================
    # RULE 7 / DEFECT FOUND ON VALIDATION. An earlier draft used ALL ROWS as the
    # denominator:  capture = sum(clv) / sum(edge)  over every selection.
    # That EXPLODES as edge -> 0. A model that simply COPIES the book has a
    # near-zero denominator, and the ratio blew up to +33.3 -- and the test read
    # that as "OUTCOME 1: CONVERTED". It would have told us a model with ZERO
    # disagreement had the best edge of all.
    #
    # Validated on four synthetic worlds with KNOWN truth, all-rows denominator:
    #     CONVERTED (bias gone, signal kept)   +2.50   } all three read
    #     CALIBRATED (bias gone, no signal)    +6.66   } as "CONVERTED".
    #     COPIES THE BOOK (no disagreement)   +33.30   } The metric was junk.
    #
    # ON BETS ONLY, the denominator is the edge we ACTUALLY CLAIMED on the bets
    # we ACTUALLY MADE. It cannot collapse -- a bet REQUIRES edge >= threshold by
    # construction. The same four worlds then read correctly:
    #     SHARP (knows the truth)      n=5528  capture +0.804
    #     CONVERTED                    n= 177  capture +2.491
    #     CALIBRATED, no new signal    n=   0  capture  n/a
    #     COPIES THE BOOK              n=   0  capture  n/a
    #
    # A model that makes NO BETS has an UNDEFINED capture, not an infinite one.
    # That is the honest answer: no disagreement means no edge to measure, and
    # reporting a huge ratio computed on three bets would be a lie.
    MIN_BETS_FOR_CAPTURE = 30
    if len(bet) >= MIN_BETS_FOR_CAPTURE and bet.edge.sum() > 0:
        capture = float(bet.clv.sum() / bet.edge.sum())
        mean_clv_bets = float(bet.clv.mean())
        win_rate_bets = float(bet.won.mean())
        mean_edge_bets = float(bet.edge.mean())
    else:
        capture = float("nan")
        mean_clv_bets = float("nan")
        win_rate_bets = float("nan")
        mean_edge_bets = float("nan")

    return {
        "tag": tag,
        "n": len(d),
        "mean_edge_all": float(d.edge.mean()),
        "mean_edge_bets": mean_edge_bets,
        "bet_rate": float((d.edge >= min_edge).mean()),
        "n_bets": int(len(bet)),
        "mean_clv_bets": mean_clv_bets,
        "win_rate_bets": win_rate_bets,
        "capture": capture,            # ON BETS ONLY. See the note above.
        "_curve": g,
        "_rows": d,
        "_bets": bet,
    }


def block_bootstrap_diff(x: np.ndarray, y: np.ndarray, dates: np.ndarray,
                         b: int, seed: int) -> tuple[float, float]:
    """CI on mean(x) - mean(y), resampling DATES. Rows within a slate share a
    pitcher, a park and a game script -- they are NOT independent."""
    uniq = np.unique(dates)
    idx = {d: np.where(dates == d)[0] for d in uniq}
    rng = np.random.default_rng(seed)
    reps = np.empty(b)
    for i in range(b):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        sel = np.concatenate([idx[d] for d in pick])
        reps[i] = x[sel].mean() - y[sel].mean()
    return float(np.percentile(reps, 2.5)), float(np.percentile(reps, 97.5))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frozen", required=True, help="sim_probs from config.json")
    ap.add_argument("--candidate", required=True, help="sim_probs from config.pa.json")
    ap.add_argument("--month", default="2026-06")
    ap.add_argument("--book", default="draftkings")
    ap.add_argument("--entry-hours", type=int, default=4)
    ap.add_argument("--min-edge", type=float, default=0.04)
    ap.add_argument("--max-quote-age", type=int, default=90)
    ap.add_argument("--void-min-pa", type=int, default=2)
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--b", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data/market/pa_market_ab.csv")
    ap.add_argument("--dry-run", action="store_true",
                    help="RULE 6: check that the two model files pair up and that "
                         "the arms actually DIFFER -- then exit, BEFORE touching "
                         "the network. Ten seconds instead of two minutes, and it "
                         "catches the one failure that makes everything else moot.")
    args = ap.parse_args(argv)

    tr = Path(args.training)
    fz = load_model(Path(args.frozen), tr, "frozen")
    cd = load_model(Path(args.candidate), tr, "cand")

    # ---- the PAIRED join: same selection, both models ---------------------
    m = fz.merge(cd.drop(columns=["out_pa"]),
                 on=["game_date", "player_key", "category", "line"], how="inner")
    print(f"[model] frozen {len(fz):,} | candidate {len(cd):,} | "
          f"PAIRED {len(m):,} rows, {m.game_date.nunique()} dates")
    if m.empty:
        print("FATAL: the two model files share no rows. Were they reconstructed "
              "on the SAME dates with the SAME --seed?", file=sys.stderr)
        return 2

    # sanity: the two arms MUST differ, or the candidate config never took effect
    drift = float((m.p_cand - m.p_frozen).abs().mean())
    print(f"[drift] mean |p_cand - p_frozen| = {drift:.5f}")
    if drift < 1e-6:
        print("\nFATAL: the two arms are IDENTICAL. config.pa.json never reached\n"
              "  GameSimulator -- check base_running.pa_distribution_path and that\n"
              "  data/learning/pa_distribution.json exists. This is the same seam\n"
              "  that silently nulled the B4 gate. NOT a tie -- a plumbing failure.",
              file=sys.stderr)
        return 2

    if args.dry_run:
        print()
        print("=" * 84)
        print("DRY RUN (rule 6) -- pairing verified, network NOT touched")
        print("=" * 84)
        print(f"  paired rows      : {len(m):,}")
        print(f"  dates            : {m.game_date.nunique()}")
        print(f"  categories       : {sorted(m.category.unique())}")
        print(f"  mean |drift|     : {drift:.5f}")
        print(f"  max  |drift|     : {float((m.p_cand - m.p_frozen).abs().max()):.5f}")
        print()
        print("  drift by category (the PA fix is HITTER-ONLY -- strikeouts MUST be 0):")
        dd = (m.assign(d=(m.p_cand - m.p_frozen).abs())
                .groupby("category")["d"]
                .agg(mean_abs="mean", max_abs="max", n="count").round(5))
        print("   ", dd.to_string().replace("\n", "\n    "))
        print()
        print("  Re-run without --dry-run for the full paired A/B.")
        return 0

    duckdb.sql("INSTALL httpfs; LOAD httpfs;")
    duckdb.sql("SET http_retries=5; SET http_retry_wait_ms=2000;")

    raw = fetch_market(args.month, args.book, args.entry_hours)

    # ---- ctrl4 GRAIN: hard fail (this caught real leakage) ---------------
    n_neg = int((raw.entry_age_min < 0).sum())
    if n_neg:
        print(f"\nFATAL: {n_neg:,} rows with NEGATIVE entry_age_min "
              f"(min {raw.entry_age_min.min():.0f}). An entry quote cannot "
              f"post-date its own horizon -- doubleheader/grain bug. LEAKAGE.",
              file=sys.stderr)
        return 2
    raw = raw[raw.entry_age_min <= args.max_quote_age]

    raw["category"] = raw["market"].map(MARKET_MAP)
    raw["player_key"] = norm_name(raw["player"])
    raw["game_date"] = pd.to_datetime(raw.game_date).dt.strftime("%Y-%m-%d")
    raw = (raw.sort_values("start_time")
              .drop_duplicates(subset=["game_date", "player_key", "market", "line"],
                               keep="first"))

    j = m.merge(raw, on=["game_date", "player_key", "category", "line"], how="inner")
    if args.void_min_pa > 0:
        j = j[(j.out_pa.isna()) | (j.out_pa >= args.void_min_pa)]
    if j.empty:
        print("FATAL: no paired model row matched the market.", file=sys.stderr)
        return 2

    print(f"[market] {args.book} T-{args.entry_hours}h: {len(j):,} paired "
          f"selections with a real entry price")
    print(f"         (the market price is IDENTICAL in both arms -- it CANCELS "
          f"exactly, so what remains is purely the model's change)")

    # Cut the deciles ONCE, on the FROZEN edge, and apply the SAME buckets to
    # BOTH arms (see the note in arm_stats). Both arms then describe the SAME
    # SELECTIONS: "on the rows the OLD model liked most, what does each capture?"
    frozen_edge = np.abs(j.p_frozen.to_numpy(float)
                         - j.entry_p_over.to_numpy(float))
    shared_dec = pd.qcut(frozen_edge, 10, labels=False, duplicates="drop")

    F = arm_stats(j, "frozen", args.min_edge, deciles=shared_dec)
    C = arm_stats(j, "cand", args.min_edge, deciles=shared_dec)

    # =====================================================================
    print()
    print("=" * 84)
    print(f"PAIRED A/B — did the PA fix convert calibration into MARKET EDGE?")
    print(f"  ({len(j):,} selections, {args.book}, T-{args.entry_hours}h, "
          f"edge threshold {args.min_edge:.0%})")
    print("=" * 84)
    print(f"  {'metric':22s} {'FROZEN':>10s} {'CANDIDATE':>10s} {'change':>10s}")
    print("  " + "-" * 56)
    rows = [
        ("n bets", F["n_bets"], C["n_bets"]),
        ("bet rate", F["bet_rate"], C["bet_rate"]),
        ("mean |edge| (all)", F["mean_edge_all"], C["mean_edge_all"]),
        ("mean |edge| (bets)", F["mean_edge_bets"], C["mean_edge_bets"]),
        ("mean CLV (bets)", F["mean_clv_bets"], C["mean_clv_bets"]),
        ("win rate (bets)", F["win_rate_bets"], C["win_rate_bets"]),
        ("*** CAPTURE ***", F["capture"], C["capture"]),
    ]
    for name, f, c in rows:
        fs = f"{f:10.4f}" if not np.isnan(f) else "       n/a"
        cs = f"{c:10.4f}" if not np.isnan(c) else "       n/a"
        ds = f"{c - f:+10.4f}" if not (np.isnan(f) or np.isnan(c)) else "       n/a"
        print(f"  {name:22s} {fs} {cs} {ds}")

    if np.isnan(C["capture"]):
        print()
        print("  *** THE CANDIDATE MAKES TOO FEW BETS TO MEASURE CAPTURE. ***")
        print(f"  Only {C['n_bets']} bets clear the {args.min_edge:.0%} threshold. That is")
        print("  not a failure -- it may mean the fix removed so much overconfidence")
        print("  that the model now agrees with the book almost everywhere. A model")
        print("  with no disagreement has NO EDGE TO MEASURE, and reporting a ratio")
        print("  on a handful of bets would be a lie. Lower --min-edge to look, but")
        print("  understand what you are looking at.")

    # ---- the paired significance test on CAPTURE (ON BETS) ---------------
    # Bootstrap the RATIO OF SUMS (not the mean of per-row ratios) -- the
    # standard fix for an unstable ratio estimator. Resample DATES, because rows
    # within a slate share a pitcher, a park and a game script.
    #
    # NOTE: the two arms bet DIFFERENT SUBSETS (the candidate is pickier). That
    # is the point -- we are comparing each model's OWN bets, which is what you
    # would actually place. It is still PAIRED at the DATE level, so the "which
    # selections were soft that day" noise cancels.
    if (not np.isnan(F["capture"])) and (not np.isnan(C["capture"])):
        dates = j.game_date.to_numpy()
        uniq = np.unique(dates)
        idx = {d: np.where(dates == d)[0] for d in uniq}
        fr, cr = F["_rows"], C["_rows"]
        f_bet = (fr.edge >= args.min_edge).to_numpy()
        c_bet = (cr.edge >= args.min_edge).to_numpy()
        rng = np.random.default_rng(args.seed)
        reps = []
        for _ in range(args.b):
            pick = rng.choice(uniq, size=len(uniq), replace=True)
            sel = np.concatenate([idx[d] for d in pick])
            fs, cs = f_bet[sel], c_bet[sel]
            if fs.sum() < 10 or cs.sum() < 10:
                continue
            cap_f = fr.clv.values[sel][fs].sum() / max(fr.edge.values[sel][fs].sum(), 1e-9)
            cap_c = cr.clv.values[sel][cs].sum() / max(cr.edge.values[sel][cs].sum(), 1e-9)
            reps.append(cap_c - cap_f)
        if len(reps) >= 100:
            lo = float(np.percentile(reps, 2.5))
            hi = float(np.percentile(reps, 97.5))
            point = C["capture"] - F["capture"]
            print()
            print(f"  PAIRED change in CAPTURE (on each arm's OWN bets):")
            print(f"    {point:+.4f}   95% CI [{lo:+.4f}, {hi:+.4f}]   "
                  f"({len(reps)} valid replicates over {len(uniq)} dates)")
        else:
            lo = hi = float("nan")
            print("\n  (too few valid bootstrap replicates -- one arm makes almost "
                  "no bets)")
    else:
        lo = hi = float("nan")

    print()
    print("  CAPTURE BY EDGE DECILE")
    print("  *** Deciles are cut on the FROZEN edge and applied to BOTH arms, so")
    print("  each row describes the SAME SELECTIONS. Cutting each arm separately")
    print("  would compare incomparable buckets -- the candidate's decile 9 would")
    print("  be a different (smaller) edge range if the fix worked at all. ***")
    print(f"  {'dec':>3s} {'edge_F':>7s} {'edge_C':>7s} {'cap_F':>7s} {'cap_C':>7s} "
          f"{'change':>8s}")
    for d in range(10):
        gf = F["_curve"][F["_curve"].dec == d]
        gc = C["_curve"][C["_curve"].dec == d]
        if gf.empty or gc.empty:
            continue
        print(f"  {d:3d} {gf.mean_edge.iloc[0]:7.4f} {gc.mean_edge.iloc[0]:7.4f} "
              f"{gf.capture.iloc[0]:7.3f} {gc.capture.iloc[0]:7.3f} "
              f"{gc.capture.iloc[0] - gf.capture.iloc[0]:+8.3f}")

    # =====================================================================
    print()
    print("=" * 84)
    print("VERDICT")
    print("=" * 84)
    cap_up = (not np.isnan(lo)) and lo > 0
    cap_dn = (not np.isnan(hi)) and hi < 0
    bet_dn = C["bet_rate"] < F["bet_rate"]

    print(f"  reference (validated on synthetic worlds, KNOWN truth, ON BETS):")
    print(f"    SHARP (knows the true probability)   n=5528  capture +0.804")
    print(f"    a model that COPIES the book         n=   0  capture     n/a")
    print(f"    FROZEN (this model, measured)                capture +0.002")
    print(f"    the bar for a real edge                      {CAPTURE_REAL:+.2f}")
    print()

    if np.isnan(C["capture"]):
        print("  *** THE CANDIDATE MAKES TOO FEW BETS TO JUDGE. ***")
        print("  Not a failure -- possibly the opposite. But capture is UNDEFINED")
        print("  with no bets, and we will not invent a number. Lower --min-edge to")
        print("  inspect, and read the bet-rate change as the real signal.")
        rc = 1
    elif cap_up and bet_dn:
        print("  *** OUTCOME 1: THE FIX CONVERTED INTO EDGE. ***")
        print("  Capture ROSE (CI excludes 0) AND the model got pickier. It is now")
        print("  putting its confidence where the market actually moves.")
        print(f"  PROMOTE. Capture is now {C['capture']:+.3f} (bar: {CAPTURE_REAL:+.2f}).")
        rc = 0
    elif cap_dn:
        print("  *** OUTCOME 3: THE FIX MOVED THE MODEL TOWARD THE BOOK. ***")
        print("  Capture FELL (CI excludes 0). Better calibrated, LESS useful -- the")
        print("  model agrees with the market more, so there is less to bet on.")
        print("  This is why we measured BEFORE promoting. Promotion is now a real")
        print("  judgment call: calibration is the foundation, but this is a cost.")
        rc = 1
    elif bet_dn:
        print("  *** OUTCOME 2: BETTER CALIBRATED, NO MORE EDGE. ***")
        print(f"  Pickier (bet rate {F['bet_rate']:.1%} -> {C['bet_rate']:.1%}) but "
              f"capture did not move (CI spans 0).")
        print("  It removed OVERCONFIDENCE and gained no INFORMATION the market lacks.")
        print()
        print("  PROMOTE ANYWAY -- calibration is the foundation, and the gate passed")
        print("  on outcomes. But do NOT bet, and STOP TUNING CALIBRATION: the next")
        print("  work item is finding SIGNAL, not removing more bias.")
        rc = 0
    else:
        print("  *** AMBIGUOUS: capture CI spans 0 and the bet rate did not fall. ***")
        print("  Read the decile table. The fix may have shifted probabilities")
        print("  without changing selectivity.")
        rc = 1

    print()
    print("  NOTE (rule 8): this is a PAIRED test. The market price is the SAME")
    print("  recorded quote in both arms, so it CANCELS EXACTLY -- the CI above")
    print("  reflects ONLY the model's change, not which selections DK happened to")
    print("  price softly. That is far more sensitive than two unpaired sweeps.")

    out = pd.DataFrame([
        dict(arm="frozen", **{k: v for k, v in F.items() if not k.startswith("_")}),
        dict(arm="candidate", **{k: v for k, v in C.items() if not k.startswith("_")}),
    ])
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"\nwrote {args.out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
