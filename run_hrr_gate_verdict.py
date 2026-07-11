#!/usr/bin/env python3
"""
HRR gate verdict — FROZEN sim vs HRR-CANDIDATE sim, block bootstrap over dates.

Same machinery as run_b4_gate_verdict.py, with the ROLES SWAPPED. B4 was a
PITCHER change: strikeouts were the verdict and hitters were the negative
controls. The HRR structural fix is a HITTER change, so:

  * hrr lines (1.5/2.5) are THE VERDICT. PASS iff FROZEN is significantly
    better on ZERO hrr lines (a tie is equal -- "equal or better").
  * hits, home_runs, strikeouts are NEGATIVE CONTROLS. The fix touches only
    runs/RBI attribution inside GameSimulator; it must NOT move the hits or
    home_runs marginals (offline INV5 asserts the hits marginal is unchanged),
    and it cannot touch pitcher K at all. Any significant control line means the
    change leaked -- investigate before trusting the hrr verdict.

Running run_b4_gate_verdict.py on this change would invert the logic: it would
exit 2 ("K probabilities are IDENTICAL") because a hitter change cannot move K,
and it would flag hrr movement -- the entire point of the change -- as a leak.

*** THE PLUMBING CHECK IS THE POINT ***
If hrr drift ~ 0, that is NOT "the fix is neutral". It means the fix DIDN'T
HAPPEN. Two ways that occurs, both silent:
  (a) the candidate config never reached GameSimulator.__init__ through
      reconstruct_objects -> PropEngine -> _build_monte_carlo (the exact seam
      that silently nulled the B4 gate -- see the scar comment in
      run_reconstruct_date.py);
  (b) base_state_mix_rate == 1.0, which is mathematically identical to the
      pre-fix per-PA resample (a persistent BaseState that fully re-mixes every
      PA is a no-op).
Either way the candidate arm IS the frozen arm and the verdict below is
meaningless. We exit 2 rather than report a TIE.

NOISE FLOOR IS NOT OPTIONAL. Run frozen-vs-frozen FIRST (reconstruct the same
dates twice with the SAME frozen config) to measure the Monte Carlo
reconstruction noise floor. A single-date smoke has n_dates=1, which makes the
block bootstrap degenerate (ci_lo == ci_hi on every row) and "significance"
meaningless. Candidate drift is only interpretable against that floor.

Usage:
  # 0) noise floor: SAME frozen config both sides, same dates
  python run_hrr_gate_verdict.py \
      --frozen data/analysis/hrr/sim_probs_frozen.csv \
      --candidate data/analysis/hrr/sim_probs_frozen2.csv \
      --pairs data/models/gbm/wf_predictions_catboost.csv \
      --noise-floor

  # 1) the real verdict
  python run_hrr_gate_verdict.py \
      --frozen data/analysis/hrr/sim_probs_frozen.csv \
      --candidate data/analysis/hrr/sim_probs_hrr.csv \
      --pairs data/models/gbm/wf_predictions_catboost.csv \
      --out data/analysis/hrr/hrr_gate_metrics.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

KEYS = ["player_id", "game_date", "category", "line"]
OUTCOME_KEYS = ["player_id", "game_date", "category"]
ACTUAL_COL_CANDIDATES = ("actual_value", "actual", "y_true", "actual_outcome")

# ROLE SWAP vs B4: hrr is the verdict; everything else is a negative control.
VERDICT_CATEGORY = "hrr"
CONTROL_CATEGORIES = ("hits", "home_runs", "strikeouts")


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
              f"(kept first -- check for the local+CI same-day collision gotcha)")
    print(f"[load] outcomes from {path} column '{actual_col}' ({len(out)} rows)")
    return out.rename(columns={actual_col: "actual"})


def block_bootstrap_ci(d: np.ndarray, dates: np.ndarray, b: int, seed: int,
                       alpha: float = 0.05) -> tuple[float, float]:
    """Percentile CI of mean(d) resampling DATES with replacement (the block).

    With n_dates == 1 this is DEGENERATE: every replicate is the same date, so
    ci_lo == ci_hi == the point estimate and every row reads 'significant'.
    Callers must not believe significance from a single-date run.
    """
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
        raise ValueError("no matched rows with outcomes -- check key/date formats")

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
    ap = argparse.ArgumentParser(
        description="HRR gate: frozen-sim vs candidate-sim block-bootstrap verdict.")
    ap.add_argument("--frozen", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--pairs", default="data/models/gbm/wf_predictions_catboost.csv")
    ap.add_argument("--actual-col", default=None)
    ap.add_argument("--prob-col", default="sim_p_over")
    ap.add_argument("--b", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None)
    ap.add_argument("--noise-floor", action="store_true",
                    help="frozen-vs-frozen control: BOTH inputs are the same frozen "
                         "config. Inverts the plumbing check (drift MUST be ~0) and "
                         "reports the MC reconstruction noise floor instead of a verdict.")
    args = ap.parse_args(argv)

    frozen = _load_probs(Path(args.frozen), args.prob_col)
    candidate = _load_probs(Path(args.candidate), args.prob_col)
    outcomes = _load_outcomes(Path(args.pairs), args.actual_col)
    metrics, drift = compare(frozen, candidate, outcomes, args.prob_col, args.b, args.seed)

    n_dates = int(metrics["n_dates"].max()) if not metrics.empty else 0

    print("\nPER-LINE VERDICTS (dbrier = candidate - frozen; NEGATIVE favors the "
          "candidate; read the CI, not the point winner)")
    print(metrics.to_string(index=False))
    print("\nPROBABILITY DRIFT candidate vs frozen")
    print(drift.to_string(index=False))

    if n_dates <= 1:
        print("\n*** WARNING: n_dates = %d. The block bootstrap resamples DATES, so a "
              "single-date run is DEGENERATE: ci_lo == ci_hi and every line reads "
              "'significant'. Do NOT read significance from this run -- it is a "
              "plumbing smoke test only. ***" % n_dates)

    hrr_drift = drift[drift["category"] == VERDICT_CATEGORY]
    hrr_moved = (not hrr_drift.empty) and float(hrr_drift["mean_abs_dp"].iloc[0]) > 1e-6

    # ---------------- noise-floor mode ----------------
    if args.noise_floor:
        print("\nNOISE-FLOOR CONTROL (frozen vs frozen)")
        print("-" * 70)
        if hrr_moved:
            print("Monte Carlo reconstruction noise floor, per category:")
            print(drift.to_string(index=False))
            print("\nCandidate hrr drift must EXCEED this floor to mean anything. "
                  "Any 'significant' line here is a FALSE POSITIVE by construction "
                  "(both arms are the same model) -- that is the point of the control.")
            sig = metrics[metrics["verdict"] != "TIE"]
            if not sig.empty:
                print(f"\n{len(sig)} line(s) read 'significant' on IDENTICAL models -- "
                      f"this is the false-positive rate of the gate, not a result:\n"
                      f"{sig.to_string(index=False)}")
        else:
            print("hrr drift is EXACTLY 0 -- reconstructions are deterministic/cached, "
                  "so the MC noise floor is 0 and any candidate drift is real signal. "
                  "(Confirm the two frozen runs used different RNG paths; if they were "
                  "byte-identical files this control proves nothing.)")
        return 0

    # ---------------- verdict mode ----------------
    hrr_lines = metrics[metrics["category"] == VERDICT_CATEGORY]
    ctrl_lines = metrics[metrics["category"].isin(CONTROL_CATEGORIES)]
    hrr_frozen_wins = hrr_lines[hrr_lines["verdict"] == "FROZEN"]
    ctrl_sig = ctrl_lines[ctrl_lines["verdict"] != "TIE"]

    print("\nGATE SUMMARY")
    print("-" * 70)
    if hrr_lines.empty:
        print("FAIL-TO-RUN: no hrr lines matched -- nothing to gate on.")
        return 2
    if not hrr_moved:
        print("FAIL-TO-RUN: hrr probabilities are IDENTICAL between frozen and "
              "candidate. THE FIX DID NOT HAPPEN. This is not a TIE.\n"
              "  (a) the candidate config never reached GameSimulator.__init__ -- check "
              "reconstruct_objects passes config -> PropEngine -> _build_monte_carlo -> "
              "GameSimulator(config=...), and that config/config.hrr.json actually "
              "carries base_running.base_state_mix_rate; or\n"
              "  (b) base_state_mix_rate == 1.0, which is mathematically identical to "
              "the pre-fix per-PA resample (a persistent BaseState that fully re-mixes "
              "every PA is a no-op).\n"
              "Do not read the verdict above as evidence.")
        return 2
    if not ctrl_sig.empty:
        print(f"WARNING: {len(ctrl_sig)} control line(s) moved SIGNIFICANTLY. The HRR "
              f"fix must not touch hits/home_runs (offline INV5) and cannot touch "
              f"strikeouts at all. Investigate (leak, or MC noise above the floor) "
              f"before trusting the hrr verdict:\n{ctrl_sig.to_string(index=False)}")

    if hrr_frozen_wins.empty:
        print(f"PASS: frozen sim significantly better on ZERO of {len(hrr_lines)} hrr "
              f"line(s) (ties count as equal). The HRR structural fix calibrates "
              f"equal-or-better on hrr.")
        rc = 0 if ctrl_sig.empty else 1
    else:
        print(f"FAIL: frozen sim significantly better on {len(hrr_frozen_wins)} hrr "
              f"line(s):\n{hrr_frozen_wins.to_string(index=False)}")
        rc = 1

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        metrics.to_csv(args.out, index=False)
        print(f"\nwrote {args.out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
