#!/usr/bin/env python3
"""
THE MARKET VERDICT: does the simulator beat the closing line?

This is the only question that determines whether any of this makes money.
Everything else in the project measures whether the model is well-calibrated
AGAINST OUTCOMES. That says nothing about whether it beats the PRICE. A model
can be perfectly calibrated and still lose every bet, because the market is
ALSO calibrated -- and you pay the vig.

WHAT IS COMPARED
  model  : simulator P(over) from run_gate_reconstruct.py (p_ge_threshold)
  market : de-vigged closing probability from scripts/extract_closes.py
  truth  : the graded outcome (SmartStake `result`, cross-checked against the
           reconstruction's own actuals)

  Brier(model) vs Brier(market), on MATCHED rows, block-bootstrapped over dates.

  ALSO vs a DUMB BASELINE (rule 3): a constant predictor at the base rate. If
  the simulator beats the market by no more than a constant does, the simulator
  is not doing anything. You cannot recognise skill without knowing what NO
  skill looks like on this metric.

READ-ONLY, AND NEVER A TRAINING SIGNAL
  Fitting the model to the close and then evaluating against the close is
  training on the test set. This number is measured once and read once. If we
  find ourselves adjusting the model and re-running this, STOP -- that is a fit,
  not an evaluation.

=============================================================================
WHAT CAN AND CANNOT BE MEASURED TODAY  (verified, not assumed)
=============================================================================
  hits        model YES / market YES (DK, bet365 -- Pinnacle posts ZERO hits)
  home_runs   model YES / market YES (Pinnacle 2,256 rows)
  strikeouts  model YES / market YES (Pinnacle 879 rows)
  total_bases model NO  -- NOT in monte_carlo._category_value, NOT in
              reconstruct's HITTER_CATEGORIES. This is the BIGGEST market
              (75M rows, Pinnacle-covered) and we cannot score it until the
              model emits it. GameSimulationResult ALREADY carries singles/
              doubles/triples/home_runs -- it is ~5 lines. Not done here.
  rbi         model NO (same reason). 44M rows in the market.
  hrr         NO MARKET EXISTS. Not at any of ~75 books. Cannot be scored, ever.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  Brier at a ~50/50 line : ~0.24-0.25   (uncertainty = p(1-p) = 0.25 at p=0.5)
  Brier at hits>=1       : base rate ~0.61 -> uncertainty ~0.238
  Brier at home_runs>=1  : base rate ~0.11 -> uncertainty ~0.098
  A closing line's Brier should be AT OR BELOW the constant-predictor Brier.
  If the MARKET is worse than a constant, the de-vig or the join is broken --
  not the market. Books do not lose to a constant.
  Model Brier BELOW market Brier = the model has skill. Expect this to be
  RARE and SMALL if it happens at all.

=============================================================================
KNOWN CONFOUND, STATED UP FRONT  (rule 8)
=============================================================================
  The closing line is the LAST quote before first pitch. You CANNOT BET IT.
  You bet hours earlier, at worse prices. So even beating the close on Brier
  does not mean profit -- it means the model has information the market had at
  close. The obtainable-price question is separate and harder. This script
  answers "is there skill", not "is there profit".

Usage:
  # 1. reconstruct the model on graded dates (SmartStake grades Mar-Jun 2026)
  python run_gate_reconstruct.py --pairs data/models/gbm/wf_predictions_catboost.csv \
      --config config/config.json --dates 2026-06-20 2026-06-21 2026-06-22 \
      --out data/market/sim_probs_2026-06.csv

  # 2. extract the de-vigged closes for the same month
  python scripts/extract_closes.py --month 2026-06 \
      --out data/market/closes_2026-06.parquet

  # 3. the verdict
  python scripts/run_market_verdict.py \
      --sim data/market/sim_probs_2026-06.csv \
      --closes data/market/closes_2026-06.parquet
"""
from __future__ import annotations

import argparse
import math
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

# our category -> SmartStake market
CATEGORY_MAP = {
    "hits": "hits",
    "home_runs": "home_runs",
    "strikeouts": "strikeouts",
}

# Benchmark preference per category. Pinnacle is the sharpest book, but it
# posts NOTHING for hits (measured: 0 rows). Fall back in this order.
BENCHMARK_ORDER = ["pinnacle", "ps3838", "bet365", "draftkings", "novig", "fanatics"]


def norm_name(s: pd.Series) -> pd.Series:
    def strip_accents(x):
        return "".join(c for c in unicodedata.normalize("NFKD", str(x))
                       if not unicodedata.combining(c))
    return (s.map(strip_accents).str.lower().str.strip()
             .str.replace(r"[.'`\-]", "", regex=True)
             .str.replace(r"\s+", " ", regex=True)
             .str.replace(r"\s+(jr|sr|ii|iii|iv|v)$", "", regex=True)
             .str.strip())


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def block_bootstrap_diff(d: np.ndarray, dates: np.ndarray, b: int, seed: int):
    """CI on mean(d), resampling DATES (rows within a slate are correlated)."""
    uniq, inv = np.unique(dates, return_inverse=True)
    n = len(uniq)
    sums = np.zeros(n); cnts = np.zeros(n)
    np.add.at(sums, inv, d); np.add.at(cnts, inv, 1.0)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(b, n))
    rep = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    return float(np.percentile(rep, 2.5)), float(np.percentile(rep, 97.5)), n


def main(argv=None) -> int:
    # Retained as a historical diagnostic only. Its target is SmartStake's
    # numeric result and its identity bridge is name/date based; both are
    # disqualified for the canonical market contract. Never emit new verdicts
    # from a path whose target was measured wrong on evaluated rows.
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from src.evaluation.retired_market_evaluators import retired_market_evaluator_exit
    return retired_market_evaluator_exit(Path(__file__).name)

    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True, help="run_gate_reconstruct.py output")
    ap.add_argument("--closes", required=True, help="extract_closes.py parquet")
    ap.add_argument("--book", default=None,
                    help="force a benchmark book (default: best available per category)")
    ap.add_argument("--b", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz",
                    help="bridge for HITTER rows: player_id -> player_name")
    ap.add_argument("--training-pitchers",
                    default="data/training/training_pitchers_2023_2026.csv.gz",
                    help="bridge for PITCHER rows (strikeouts). The hitters file "
                         "contains NO pitchers -- measured: 435/438 strikeout rows "
                         "failed to resolve without this.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    sim = pd.read_csv(args.sim)
    cl = pd.read_parquet(args.closes)

    # ---- BRIDGE: sim has player_id, market has NAMES -----------------------
    # VERIFIED (read the source): run_gate_reconstruct.py writes exactly
    #     player_id, game_date, category, line, sim_p_over
    # -- no player_name. SmartStake keys on a lowercase name string and has NO
    # MLB player_id. The two cannot be joined directly; we must bridge.
    #
    # MEASURED, and this was NOT obvious: bridging through the HITTER training
    # set alone resolves hitters 6415/6415 (100%) but pitchers 3/438 -- because
    # a HITTERS file contains no pitchers. An earlier 95% guard fired on this and
    # called it a "biased subsample". It was not. It was a MISSING TABLE. Both
    # training files are required.
    #
    # We deliberately do NOT patch run_gate_reconstruct.py to emit player_name:
    # it is the closed B4 gate's infrastructure, and changing its output schema
    # would fork a tool whose reproducibility other results depend on.
    if "player_name" not in sim.columns:
        frames = []
        for label, p in (("hitters", args.training),
                         ("pitchers", args.training_pitchers)):
            path = Path(p)
            if not path.exists():
                print(f"[bridge] WARNING: {label} file {path} not found -- skipping",
                      file=sys.stderr)
                continue
            t = pd.read_csv(path, low_memory=False)
            need = {"player_id", "game_date", "player_name"}
            if not need <= set(t.columns):
                print(f"[bridge] WARNING: {path.name} lacks {sorted(need - set(t.columns))} "
                      f"-- skipping", file=sys.stderr)
                continue
            frames.append(t[["player_id", "game_date", "player_name"]])
            print(f"[bridge] loaded {label}: {len(t):,} rows from {path.name}")

        if not frames:
            print("FATAL: no bridge file carries player_id/game_date/player_name.",
                  file=sys.stderr)
            return 2

        bridge = (pd.concat(frames, ignore_index=True)
                  .dropna()
                  .drop_duplicates(subset=["player_id", "game_date"]))

        n0 = len(sim)
        sim = sim.merge(bridge, on=["player_id", "game_date"], how="left")
        miss = sim[sim["player_name"].isna()]
        rate = 1.0 - len(miss) / max(1, n0)
        print(f"[bridge] player_id -> player_name: {n0 - len(miss)}/{n0} ({rate:.1%})")
        if len(miss):
            print("  unresolved by category:")
            print("   ", miss.groupby("category").size().to_string().replace("\n", "\n    "))
        if rate < 0.95:
            print(f"\nFATAL: only {rate:.1%} of sim rows resolved to a name.\n"
                  f"  Check that BOTH training files exist AND cover these dates.\n"
                  f"  If the unresolved rows are all ONE category, the bridge is\n"
                  f"  missing that category's table -- not a biased sample.",
                  file=sys.stderr)
            return 2
        sim = sim.dropna(subset=["player_name"])

    sim["player_key"] = norm_name(sim["player_name"])

    # ---- pick the benchmark book per category ---------------------------
    print("=" * 78)
    print("BENCHMARK SELECTION  (Pinnacle posts ZERO hits -- measured)")
    print("=" * 78)
    chosen: dict[str, str] = {}
    for cat in CATEGORY_MAP:
        sub = cl[cl.category == cat]
        counts = sub.book.value_counts()
        if args.book:
            if args.book not in counts.index:
                print(f"  {cat:12s} FORCED book '{args.book}' has NO rows -- skipping")
                continue
            chosen[cat] = args.book
        else:
            pick = next((b for b in BENCHMARK_ORDER if counts.get(b, 0) > 200), None)
            if pick is None:
                print(f"  {cat:12s} no book with >200 rows -- skipping")
                continue
            chosen[cat] = pick
        print(f"  {cat:12s} -> {chosen[cat]:12s} ({int(counts[chosen[cat]]):,} closes, "
              f"overround {sub[sub.book==chosen[cat]].overround.mean():.4f})")

    if not chosen:
        print("\nFATAL: no usable benchmark book for any category.", file=sys.stderr)
        return 2

    # ---- join: model P(over) + market P(over) + outcome, same selection --
    rows = []
    print()
    print("=" * 78)
    print("JOIN  (model p_ge_threshold  <->  de-vigged close, same player/line)")
    print("=" * 78)
    for cat, book in chosen.items():
        m = cl[(cl.category == cat) & (cl.book == book)].copy()
        s = sim[sim.category == cat].copy()
        if s.empty or m.empty:
            continue
        j = s.merge(m, on=["game_date", "player_key", "category", "line"],
                    how="inner", suffixes=("_sim", "_mkt"))
        n_sim, n_mkt = len(s), len(m)
        print(f"  {cat:12s} sim={n_sim:5d}  market={n_mkt:5d}  MATCHED={len(j):5d}")
        if j.empty:
            continue
        # outcome: over means the graded stat cleared the line
        j["y"] = (j["result"] > j["line"]).astype(float)
        j["p_model"] = j["sim_p_over"].clip(1e-6, 1 - 1e-6)
        j["p_market"] = j["p_over_devig"].clip(1e-6, 1 - 1e-6)
        rows.append(j)

    if not rows:
        print("\nFATAL: nothing matched. Check date formats and name normalization.",
              file=sys.stderr)
        return 2
    J = pd.concat(rows, ignore_index=True)

    # ---- THE VERDICT ----------------------------------------------------
    print()
    print("=" * 78)
    print("THE VERDICT -- Brier: model vs de-vigged CLOSE vs a CONSTANT")
    print("=" * 78)
    print("  (lower is better. dBrier = model - market; NEGATIVE means the model wins.)")
    print()
    print(f"  {'category':11s} {'line':>5s} {'book':11s} {'n':>5s} {'base':>6s} "
          f"{'B_model':>8s} {'B_mkt':>8s} {'B_const':>8s} {'dBrier':>9s} "
          f"{'ci_lo':>9s} {'ci_hi':>9s}  verdict")
    print("  " + "-" * 108)

    out_rows = []
    for (cat, line), g in J.groupby(["category", "line"]):
        if len(g) < 30:
            continue
        y = g["y"].to_numpy(float)
        base = float(y.mean())
        b_model = brier(g["p_model"], y)
        b_mkt = brier(g["p_market"], y)
        b_const = brier(np.full(len(y), base), y)   # the no-skill floor
        d = ((g["p_model"] - y) ** 2 - (g["p_market"] - y) ** 2).to_numpy()
        lo, hi, n_dates = block_bootstrap_diff(d, g["game_date"].to_numpy(),
                                               args.b, args.seed)
        v = "MODEL" if hi < 0 else ("MARKET" if lo > 0 else "TIE")
        book = g["book"].iloc[0]
        print(f"  {cat:11s} {line:5.1f} {book:11s} {len(g):5d} {base:6.3f} "
              f"{b_model:8.5f} {b_mkt:8.5f} {b_const:8.5f} {b_model-b_mkt:+9.5f} "
              f"{lo:+9.5f} {hi:+9.5f}  {v}")
        out_rows.append(dict(category=cat, line=line, book=book, n=len(g),
                             n_dates=n_dates, base_rate=base,
                             brier_model=b_model, brier_market=b_mkt,
                             brier_constant=b_const, dbrier=b_model - b_mkt,
                             ci_lo=lo, ci_hi=hi, verdict=v))

    # ---- sanity: the market must beat a constant -------------------------
    print()
    print("=" * 78)
    print("SANITY (rule 7): the MARKET must beat a CONSTANT. If it does not, the")
    print("de-vig or the join is broken -- books do not lose to a constant.")
    print("=" * 78)
    bad = [r for r in out_rows if r["brier_market"] >= r["brier_constant"]]
    if bad:
        print("  *** THE MARKET LOSES TO A CONSTANT ON THESE LINES ***")
        for r in bad:
            print(f"    {r['category']} {r['line']}: market {r['brier_market']:.5f} "
                  f">= constant {r['brier_constant']:.5f}")
        print("  DO NOT TRUST THE VERDICT ABOVE. Fix the join/de-vig first.")
    else:
        print("  OK -- the market beats a constant on every line. The benchmark is sane.")

    print()
    print("=" * 78)
    print("READ THIS")
    print("=" * 78)
    print("  * The CLOSE is the last price before first pitch. YOU CANNOT BET IT.")
    print("    Beating it on Brier means the model has skill -- NOT that it makes")
    print("    money. Profit needs the model to beat the price you can ACTUALLY GET,")
    print("    after vig, hours earlier. That is a harder, separate question.")
    print("  * This number is READ-ONLY. Do not tune the model against it.")
    print("  * total_bases (the BIGGEST market, 75M rows) is NOT measured here --")
    print("    the model does not emit it. GameSimulationResult already carries")
    print("    singles/doubles/triples/home_runs; adding it is ~5 lines.")

    if args.out and out_rows:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(out_rows).to_csv(args.out, index=False)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
