#!/usr/bin/env python3
"""
B4 gate verdict — FROZEN sim vs B4-CANDIDATE sim, block bootstrap over dates.

Compares two run_gate_reconstruct.py outputs (same --seed => same dates):
  --frozen     sim_probs from the frozen config (dab23f4fbac8 baseline; the
               closed gate-#4(b) sim_probs.csv is reusable here — the frozen
               sim hasn't changed)
  --candidate  sim_probs from a B4-ENABLED config (passed to
               run_gate_reconstruct.py via --config; config.json itself is
               NOT edited until the verdict passes)

VERDICT METHOD (identical to the closed gate's, per PROJECT_CONTEXT):
per-row Brier difference d_i = brier_candidate - brier_frozen on MATCHED keys
(player_id, game_date, category, line), significance from a BLOCK BOOTSTRAP
OVER DATES (default B=4000, 95% CI) — never a fixed epsilon; rows within a
slate are correlated. Murphy skill (UNC - Brier) printed per line so
noise-floor lines read honestly. Outcomes joined from --pairs; over means
actual >= ceil(line), the same integer-threshold convention as everywhere.

WHAT PASSES: B4 only changes pitcher expected_innings, so
  * strikeouts lines are the verdict: PASS iff FROZEN is significantly
    better on ZERO K lines (a tie is equal — "equal or better").
  * hitter lines are NEGATIVE CONTROLS: B4 must not move them beyond
    Monte Carlo reconstruction noise; any significant hitter line means the
    change leaked somewhere it shouldn't — investigate before trusting the
    K verdict.
The drift table (mean/max |candidate_p - frozen_p| per category) doubles as
the plumbing check: K probs MUST move for short-outing arms (if K drift ~ 0
the B4 config never reached the estimator in the reconstruction path — fix
the plumbing, don't read the verdict); hitter drift should look like MC
noise (or 0.0 if reconstructions are cached).

Usage:
  python run_b4_gate_verdict.py \
      --frozen data/models/gbm/calibration/sim_probs.csv \
      --candidate data/analysis/b4/sim_probs_b4.csv \
      --pairs data/models/gbm/wf_predictions_catboost.csv \
      --out data/analysis/b4/b4_gate_metrics.csv
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

KEYS = ["player_id", "game_date", "category", "line"]
OUTCOME_KEYS = ["player_id", "game_date", "category"]
ACTUAL_COL_CANDIDATES = ("actual_value", "actual", "y_true", "actual_outcome")
K_CATEGORY = "strikeouts"


def _load_probs(path: Path, prob_col: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in KEYS + [prob_col] if c not in df.columns]
    if missing:
        raise ValueError(f"{path} missing columns {missing}; has {list(df.columns)}")
    df = df[KEYS + [prob_col]].copy()
    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").astype("Int64")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    df[prob_col] = pd.to_numeric(df[prob_col], errors="coerce")
    df = df.dropna(subset=KEYS + [prob_col])
    n_before = len(df)
    df = df.drop_duplicates(subset=KEYS)
    if len(df) != n_before:
        print(f"[load] {path}: dropped {n_before - len(df)} duplicate key rows")
    return df


def _load_outcomes(path: Path, actual_col: Optional[str]) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    if actual_col is None:
        for cand in ACTUAL_COL_CANDIDATES:
            if cand in df.columns:
                actual_col = cand
                break
    if actual_col is None or actual_col not in df.columns:
        raise ValueError(
            f"Could not find an actuals column in {path}. Tried "
            f"{ACTUAL_COL_CANDIDATES}; available: {list(df.columns)}. "
            f"Pass --actual-col explicitly.")
    missing = [c for c in OUTCOME_KEYS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} missing outcome key columns {missing}")
    out = df[OUTCOME_KEYS + [actual_col]].copy()
    out["player_id"] = pd.to_numeric(out["player_id"], errors="coerce").astype("Int64")
    out[actual_col] = pd.to_numeric(out[actual_col], errors="coerce")
    out = out.dropna(subset=OUTCOME_KEYS + [actual_col])
    n_before = len(out)
    out = out.drop_duplicates(subset=OUTCOME_KEYS)
    if len(out) != n_before:
        print(f"[load] {path}: dropped {n_before - len(out)} duplicate outcome rows "
              f"(kept first — check for the local+CI same-day collision gotcha)")
    print(f"[load] outcomes from {path} column '{actual_col}' ({len(out)} rows)")
    return out.rename(columns={actual_col: "actual"})


def block_bootstrap_ci(d: np.ndarray, dates: np.ndarray, b: int, seed: int,
                       alpha: float = 0.05) -> tuple[float, float]:
    """Percentile CI of mean(d) resampling DATES with replacement (the block)."""
    uniq, inv = np.unique(dates, return_inverse=True)
    n_dates = len(uniq)
    sums = np.zeros(n_dates)
    counts = np.zeros(n_dates)
    np.add.at(sums, inv, d)
    np.add.at(counts, inv, 1.0)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_dates, size=(b, n_dates))
    rep = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    lo, hi = np.percentile(rep, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def compare(frozen: pd.DataFrame, candidate: pd.DataFrame, outcomes: pd.DataFrame,
            prob_col: str, b: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    m = frozen.rename(columns={prob_col: "p_frozen"}).merge(
        candidate.rename(columns={prob_col: "p_candidate"}), on=KEYS, how="inner")
    print(f"[match] frozen={len(frozen)} candidate={len(candidate)} matched={len(m)} "
          f"(inner-join on {KEYS}; unmatched rows are dropped, never scored)")
    m = m.merge(outcomes, on=OUTCOME_KEYS, how="inner")
    print(f"[match] with outcomes: {len(m)} rows")
    if m.empty:
        raise ValueError("no matched rows with outcomes — check key/date formats")

    m["over"] = (m["actual"] >= np.ceil(m["line"])).astype(float)
    m["d"] = (m["p_candidate"] - m["over"]) ** 2 - (m["p_frozen"] - m["over"]) ** 2

    rows = []
    for (cat, line), g in m.groupby(["category", "line"], observed=True):
        base = float(g["over"].mean())
        unc = base * (1.0 - base)
        brier_f = float(((g["p_frozen"] - g["over"]) ** 2).mean())
        brier_c = float(((g["p_candidate"] - g["over"]) ** 2).mean())
        d_point = brier_c - brier_f
        lo, hi = block_bootstrap_ci(g["d"].to_numpy(float),
                                    g["game_date"].to_numpy(), b=b, seed=seed)
        if hi < 0:
            verdict = "CANDIDATE"
        elif lo > 0:
            verdict = "FROZEN"
        else:
            verdict = "TIE"
        rows.append(dict(
            category=cat, line=float(line), n=int(len(g)),
            n_dates=int(g["game_date"].nunique()), base_rate=round(base, 4),
            brier_frozen=round(brier_f, 5), brier_candidate=round(brier_c, 5),
            dbrier=round(d_point, 5), ci_lo=round(lo, 5), ci_hi=round(hi, 5),
            murphy_frozen=round(unc - brier_f, 5),
            murphy_candidate=round(unc - brier_c, 5),
            verdict=verdict,
        ))
    metrics = pd.DataFrame(rows).sort_values(["category", "line"]).reset_index(drop=True)

    drift = (m.assign(abs_dp=(m["p_candidate"] - m["p_frozen"]).abs())
              .groupby("category", observed=True)["abs_dp"]
              .agg(mean_abs_dp="mean", max_abs_dp="max", n="count")
              .round(5).reset_index())
    return metrics, drift


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B4 gate: frozen-sim vs candidate-sim block-bootstrap verdict.")
    ap.add_argument("--frozen", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--pairs", default="data/models/gbm/wf_predictions_catboost.csv",
                    help="outcomes source (must carry player_id/game_date/category + actuals)")
    ap.add_argument("--actual-col", default=None,
                    help=f"actuals column in --pairs (default: auto from {ACTUAL_COL_CANDIDATES})")
    ap.add_argument("--prob-col", default="sim_p_over")
    ap.add_argument("--b", type=int, default=4000, help="bootstrap replicates (default 4000)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None, help="optional metrics CSV path")
    args = ap.parse_args(argv)

    frozen = _load_probs(Path(args.frozen), args.prob_col)
    candidate = _load_probs(Path(args.candidate), args.prob_col)
    outcomes = _load_outcomes(Path(args.pairs), args.actual_col)
    metrics, drift = compare(frozen, candidate, outcomes, args.prob_col, args.b, args.seed)

    print("\nPER-LINE VERDICTS (dbrier = candidate - frozen; NEGATIVE favors B4; read the CI, "
          "not the point winner)")
    print(metrics.to_string(index=False))
    print("\nPROBABILITY DRIFT candidate vs frozen (plumbing check: K must move, "
          "hitters ~ MC noise)")
    print(drift.to_string(index=False))

    k_lines = metrics[metrics["category"] == K_CATEGORY]
    hitter_lines = metrics[metrics["category"] != K_CATEGORY]
    k_frozen_wins = k_lines[k_lines["verdict"] == "FROZEN"]
    hitter_sig = hitter_lines[hitter_lines["verdict"] != "TIE"]
    k_drift = drift[drift["category"] == K_CATEGORY]
    k_moved = (not k_drift.empty) and float(k_drift["mean_abs_dp"].iloc[0]) > 1e-6

    print("\nGATE SUMMARY")
    print("-" * 60)
    if k_lines.empty:
        print("FAIL-TO-RUN: no strikeouts lines matched — nothing to gate on.")
        return 2
    if not k_moved:
        print("FAIL-TO-RUN: K probabilities are IDENTICAL between frozen and candidate. "
              "The B4 config never reached the estimator in the reconstruction path "
              "(check that reconstruct_objects passes config through to MLBStatsAPI and "
              "that point-in-time PitchingStatsSnapshot populates `games`). "
              "Do not read the verdict above as evidence.")
        return 2
    if not hitter_sig.empty:
        print(f"WARNING: {len(hitter_sig)} hitter line(s) moved SIGNIFICANTLY — B4 must not "
              f"touch hitters. Investigate (leak or excessive MC noise) before trusting "
              f"the K verdict:\n{hitter_sig.to_string(index=False)}")
    if k_frozen_wins.empty:
        print(f"PASS: frozen sim significantly better on ZERO of {len(k_lines)} K lines "
              f"(ties count as equal). B4 calibrates equal-or-better on K.")
        rc = 0 if hitter_sig.empty else 1
    else:
        print(f"FAIL: frozen sim significantly better on {len(k_frozen_wins)} K line(s):\n"
              f"{k_frozen_wins.to_string(index=False)}")
        rc = 1

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        metrics.to_csv(args.out, index=False)
        print(f"\nwrote {args.out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
