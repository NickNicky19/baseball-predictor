#!/usr/bin/env python3
"""
Re-score EVERY existing measurement under BOOK-CORRECT (void-aware) grading.

READ-ONLY. This changes nothing. It tells us how much of what we "know" is an
artifact of grading bets the books would VOID.

=============================================================================
THE PROBLEM
=============================================================================
data/learning/prediction_outcomes.csv (and the walk-forward pairs) grade EVERY
row, regardless of plate appearances. The books do not.

DraftKings MLB rules (verbatim, from the house rules):
  "bets on a batter in the starting lineup that are settled on the batting
   statistics recorded by such batter ... will be VOIDED if: (i) the batter
   records at least one plate appearance in that Game; and (ii) the batter bet
   on leaves the applicable Game for any reason or for no reason before the
   batter records their 2nd plate appearance."
  -> DK VOIDS pa == 1.  Gradeable at pa >= 2.

  "Pre-Game Player Props Markets - If the Selection bet on does not start the
   Game and Participate in the Game, bets on such Selection will be voided. If
   the Selection bet on does not start the Game, but Participates in the Game
   as a substitute, bets on such Selection will be voided."
  -> DK VOIDS substitutes entirely. Starters only.

PrizePicks:
  "If the MLB player ... has two or fewer plate appearances in the relevant
   official game for any reason (which may include, but is not limited to,
   injury, weather and/or performance) ... the entry will 'reboot'"
  -> PP VOIDS pa <= 2.  Gradeable at pa >= 3.

MEASURED IMPACT on the training set (2023-2026, n=152,683):
  pa == 1  ->  7.17% of all rows   (VOID on DK and PP)
  pa <= 2  -> 10.12% of all rows   (VOID on PP)
These are overwhelmingly ZERO-HIT games (a hitter with 1 PA gets a hit ~19% of
the time). Scoring them as losses on the OVER penalises the model for outcomes
the book throws away.

=============================================================================
THE HONEST CAVEAT -- THIS IS NOT A CLEAN FIX  (rule 8)
=============================================================================
DK's void rule is SIDE-DEPENDENT. Verbatim:
  "EXCEPT FOR BETS ON THE 'UNDER' SELECTION, X or Fewer markets, Live bets ..."
So an UNDER bet on a pa==1 batter STILL GRADES (and wins). The book voids the
over and keeps the under.

Our gate computes a TWO-SIDED Brier: p_over vs 1[actual >= ceil(line)]. There
is no "side" in the pairs file. So:
  - Excluding void rows is correct IF you only ever bet OVERS.
  - It is WRONG if you bet unders, because those rows do grade.
  - The truth is a MIXTURE that depends on which side you take, which the pairs
    file does not record.
This script reports BOTH and does not pretend the ambiguity away.

=============================================================================
WHAT I HAVE NOT VERIFIED  (rule 9)
=============================================================================
  - Underdog's void rule.  NOT CHECKED.
  - Onyx's void rule.      NOT CHECKED.
The user bets both. Do not assume they match DK or PP.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  rows voided at pa<2   : ~7%   of gate rows   (MEASURED on training set)
  rows voided at pa<3   : ~10%  of gate rows
  hits P(>=1) bias, ALL : +0.1146   (MEASURED)
  hits P(>=1) bias, pa>=2: +0.0687  (MEASURED)
  hits P(>=1) bias, pa>=3: +0.0609  (MEASURED)
  If any re-scored bias moves by MORE than the void share can explain, the
  JOIN is broken, not the model.

Usage:
    python scripts/regrade_gradeable.py
    python scripts/regrade_gradeable.py --frozen data/analysis/hrr/gate_frozen.csv \
                                        --candidate data/analysis/hrr/gate_cand.csv
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

KEYS = ["player_id", "game_date", "category", "line"]
OUTCOME_KEYS = ["player_id", "game_date", "category"]

# Book void thresholds. min_pa = the smallest PA count that still GRADES.
REGIMES = {
    "ALL (current, WRONG)": 0,   # what the project does today: grade everything
    "DK (pa>=2)": 2,             # DraftKings: voids pa==1 starters
    "PrizePicks (pa>=3)": 3,     # PrizePicks: voids pa<=2
}


def _load_pa(training: Path) -> pd.DataFrame:
    """out_pa = ACTUAL box-score plate appearances. This is the trial count the
    books settle on -- not expected_pa, not a projection."""
    tr = pd.read_csv(training, low_memory=False)
    if "out_pa" not in tr.columns:
        raise SystemExit(f"FATAL: {training} has no out_pa column.")
    pa = (tr[["player_id", "game_date", "out_pa"]]
          .dropna(subset=["out_pa"])
          .drop_duplicates(subset=["player_id", "game_date"]))
    return pa


def _block_bootstrap_ci(d: np.ndarray, dates: np.ndarray, b: int, seed: int,
                        alpha: float = 0.05) -> tuple[float, float]:
    uniq, inv = np.unique(dates, return_inverse=True)
    n = len(uniq)
    sums = np.zeros(n); cnts = np.zeros(n)
    np.add.at(sums, inv, d)
    np.add.at(cnts, inv, 1.0)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(b, n))
    rep = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    lo, hi = np.percentile(rep, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frozen", default="data/analysis/hrr/gate_frozen.csv")
    ap.add_argument("--candidate", default="data/analysis/hrr/gate_cand.csv")
    ap.add_argument("--pairs", default="data/models/gbm/wf_predictions_catboost.csv")
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--b", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    pa = _load_pa(Path(args.training))
    outcomes = pd.read_csv(args.pairs, low_memory=False)
    outcomes = (outcomes[OUTCOME_KEYS + ["actual_value"]]
                .dropna()
                .drop_duplicates(subset=OUTCOME_KEYS))

    frozen = pd.read_csv(args.frozen)
    cand = pd.read_csv(args.candidate) if Path(args.candidate).exists() else None

    # ---- join: sim probs + outcomes + ACTUAL pa -------------------------
    def prep(df: pd.DataFrame, col: str) -> pd.DataFrame:
        d = df[KEYS + ["sim_p_over"]].rename(columns={"sim_p_over": col})
        d = d.merge(outcomes, on=OUTCOME_KEYS, how="inner")
        n0 = len(d)
        d = d.merge(pa, on=["player_id", "game_date"], how="inner")
        rate = len(d) / n0 if n0 else 0.0
        if rate < 0.90:
            raise SystemExit(
                f"FATAL: only {rate:.1%} of rows matched an out_pa. A partial join is a\n"
                f"  BIASED SUBSAMPLE. Fix the join before trusting any number here.")
        d["over"] = (d["actual_value"] >= np.ceil(d["line"])).astype(float)
        return d

    f = prep(frozen, "p_frozen")
    print(f"[join] frozen rows with outcomes AND out_pa: {len(f):,}")

    # ---- 0. how many rows would each book VOID? -------------------------
    print("\n" + "=" * 74)
    print("0. VOID SHARE -- how many graded rows would the book throw away?")
    print("=" * 74)
    hit = f[f.category == "hits"]
    for name, min_pa in REGIMES.items():
        if min_pa == 0:
            continue
        voided = (hit.out_pa < min_pa).mean()
        print(f"  {name:22s} voids {voided:6.2%} of hits rows "
              f"({int((hit.out_pa < min_pa).sum())} of {len(hit)})")

    # ---- 1. calibration bias, per regime --------------------------------
    print("\n" + "=" * 74)
    print("1. CALIBRATION BIAS by category/line, under each grading regime")
    print("=" * 74)
    print(f"  {'category':11s} {'line':>5s} {'regime':22s} {'n':>6s} "
          f"{'model':>7s} {'actual':>7s} {'bias':>8s}")
    print("  " + "-" * 70)
    for cat in sorted(f.category.unique()):
        for line in sorted(f[f.category == cat].line.unique()):
            s0 = f[(f.category == cat) & (f.line == line)]
            for name, min_pa in REGIMES.items():
                s = s0 if min_pa == 0 else s0[s0.out_pa >= min_pa]
                if len(s) < 50:
                    continue
                m = s.p_frozen.mean(); a = s.over.mean()
                print(f"  {cat:11s} {line:5.1f} {name:22s} {len(s):6d} "
                      f"{m:7.4f} {a:7.4f} {m - a:+8.4f}")
            print()

    # ---- 2. re-score the HRR gate verdict -------------------------------
    if cand is None:
        print("(no candidate file -- skipping the gate re-score)")
        return 0

    c = prep(cand, "p_candidate")
    m = f.merge(c[KEYS + ["p_candidate"]], on=KEYS, how="inner")
    print("=" * 74)
    print("2. HRR GATE VERDICT, RE-SCORED under each grading regime")
    print("=" * 74)
    print("   The original verdict was FAIL (hrr 1.5: dbrier +0.00122, CI excludes 0).")
    print("   It was computed on a target that INCLUDES void rows. Does it hold?")
    print()
    print(f"  {'category':11s} {'line':>5s} {'regime':22s} {'n':>6s} "
          f"{'brier_f':>8s} {'brier_c':>8s} {'dbrier':>9s} {'ci_lo':>9s} "
          f"{'ci_hi':>9s}  verdict")
    print("  " + "-" * 96)
    for cat in ("hrr", "hits", "home_runs"):
        sub = m[m.category == cat]
        for line in sorted(sub.line.unique()):
            s0 = sub[sub.line == line]
            for name, min_pa in REGIMES.items():
                s = s0 if min_pa == 0 else s0[s0.out_pa >= min_pa]
                if len(s) < 50:
                    continue
                bf = float(((s.p_frozen - s.over) ** 2).mean())
                bc = float(((s.p_candidate - s.over) ** 2).mean())
                d = ((s.p_candidate - s.over) ** 2 - (s.p_frozen - s.over) ** 2).to_numpy()
                lo, hi = _block_bootstrap_ci(d, s.game_date.to_numpy(),
                                             b=args.b, seed=args.seed)
                v = "CANDIDATE" if hi < 0 else ("FROZEN" if lo > 0 else "TIE")
                print(f"  {cat:11s} {line:5.1f} {name:22s} {len(s):6d} "
                      f"{bf:8.5f} {bc:8.5f} {bc-bf:+9.5f} {lo:+9.5f} {hi:+9.5f}  {v}")
            print()

    print("=" * 74)
    print("READ THIS BEFORE ACTING ON THE ABOVE")
    print("=" * 74)
    print("  * DK's void rule EXEMPTS the 'Under' selection -- an under bet on a")
    print("    pa==1 batter STILL GRADES and wins. The Brier above is TWO-SIDED and")
    print("    has no side column, so excluding void rows is correct ONLY if you bet")
    print("    overs exclusively. The truth is a mixture. This is not a clean fix.")
    print("  * Underdog and Onyx void rules: NOT CHECKED. Do not assume they match.")
    print("  * This script is READ-ONLY. Nothing has been changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
