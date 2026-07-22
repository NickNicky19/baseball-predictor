#!/usr/bin/env python3
"""
FIT the K and BB models — WHICH PREDICTOR, and WHAT COEFFICIENT?

*** RULE 2: the coefficient comes from a FIT, not from judgment. ***
*** RULE 10: this MEASURES competing predictors. It does not choose the fix. ***

=============================================================================
WHAT THE TRACE FOUND  (measured, reconciled to 8e-17)
=============================================================================
trace_k_bb_logits.py partitioned the K logit EXACTLY (a logit is a sum):

    term                mean logit    share of the shift off the intercept
    k_hitter_contact      -0.5463     89.9%   <- THIS
    k_quality             -0.0358      5.9%
    k_pitcher_miss        -0.0254      4.2%
    k_form                -0.0001      0.0%

    intercept alone        -> K = 0.2250   (correct: sigmoid(logit(0.225)))
    + the mean shift       -> K = 0.1365
    REALIZED (starters)          0.2234    (n = 152,683 games)

  And the mechanism:
    league.contact_rate            = 0.7550   (the CENTERING constant)
    actual mean contact, starters  = 0.8178   (MEASURED on the slate)
    contact_scale                  = 8.278    (= 1/(0.7550 * 0.16))
    -> H_contact = (0.8178 - 0.7550) * 8.278 = +0.5203 for the AVERAGE hitter
    -> k_hitter_contact (-1.05) * 0.5203     = -0.5463 logits, for EVERYONE

  H_contact should center on ZERO. It centers on +0.52. The centering constant
  describes ALL batters; the model runs on STARTING LINEUPS.

  AND the clamp is load-bearing: the RAW (pre-clamp) K goes as low as 0.027,
  the floor binds on 46.3% of hitters, and it hauls the mean up by +0.015. A
  safety net that catches half the population is not a safety net.

=============================================================================
THE QUESTION NOBODY EVER ASKED  (and it is bigger than the coefficient)
=============================================================================
    *** WHY IS THE MODEL PREDICTING K FROM contact_rate AT ALL? ***

  contact_rate is a PER-SWING WHIFF PROXY for strikeout propensity.
  The hitter's OWN K RATE is the thing itself -- and it is RIGHT THERE:
      StatcastProfile.k_rate        (built from the same Savant pull)
      roll15_k_rate / roll30_k_rate (in the training set)

  Using a noisy proxy, scaled 8.278x, with a hand-picked coefficient of -1.05,
  when the direct measurement is available, is a modelling choice that has never
  been justified. And it has a measurable cost: sd(H_contact) = 0.62 means
  contact rate DOMINATES the K logit, so the model ranks hitters almost entirely
  on contact -- which is why a high-contact rookie outranks Murakami in
  production.

  *** THIS SCRIPT DOES NOT ASSUME THE PROXY IS WORSE. IT MEASURES IT. ***

=============================================================================
WHAT IS FITTED, AND HOW  (rule 8 -- state what each quantity means)
=============================================================================
  TARGET:  the hitter's K rate in the NEXT game = out_k / out_pa
           (a rate, not a count -- weighted by out_pa, because a 1-PA game
            carries almost no information and must not count as much as a 5-PA
            game. An unweighted regression on rates would let pinch-hit
            appearances dominate.)

  PREDICTORS, head to head:
    P1  roll30_k_rate     the hitter's OWN K rate over a trailing 30-day window
    P2  roll15_k_rate     shorter window (noisier, more recent)
    P3  a CONSTANT        the league rate -- the no-information baseline
    (contact_rate is NOT in the training set -- it comes from a Savant pull at
     reconstruct time. Its predictive power is measured SEPARATELY, below, on a
     reconstructed slate.)

  METRIC:  out-of-sample R^2 and RMSE, on a TEMPORAL split (train on early
           games, test on later ones). NEVER a random split -- that would leak
           point-in-time structure and flatter every predictor equally.

  AND THE COEFFICIENT: an OLS fit of
      logit(K_next) ~ a + b * (predictor - mean(predictor))
  gives EXACTLY the intercept and slope the logit model should use. b IS
  k_hitter_contact * contact_scale, in the model's own units.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  league K rate (starters)     : 0.2234   (MEASURED, n=152,683)
  R^2 of a CONSTANT            : 0.000    (by definition -- the baseline)
  R^2 of roll30_k_rate         : 0.25 - 0.55
      (a hitter's own recent K rate is a STRONG predictor of his next K rate --
       K rate is one of the most stable hitter skills. If it comes back below
       0.15, the rolling feature is broken or mis-joined.)
  fitted slope on logit scale  : POSITIVE and O(1)
      (a hitter who strikes out more, strikes out more. If the slope is
       NEGATIVE, the measurement is broken, not baseball.)

  If roll30_k_rate does NOT beat the constant, STOP -- the feature is broken and
  nothing below is trustworthy.

Usage:
    python scripts/fit_k_bb_model.py
    python scripts/fit_k_bb_model.py --split-date 2025-06-01
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

EPS = 1e-6


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 0.01, 0.99)
    return np.log(p / (1.0 - p))


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def binomial_glm(x: np.ndarray, counts: np.ndarray,
                 trials: np.ndarray) -> tuple[float, float]:
    """Fit logit(p) = a + b*x by MAXIMUM LIKELIHOOD on the COUNTS.

    *** THIS IS NOT A STYLISTIC CHOICE. OLS ON CLIPPED LOGITS IS BROKEN, AND I
        PROVED IT ON SYNTHETIC DATA WHERE I SET THE TRUTH. ***

    An earlier draft fit  logit(clip(k_count/pa, 0.01, 0.99)) ~ a + b*x  by
    weighted least squares. On a synthetic world with a KNOWN true relationship
    (logit(K) = -2.30 + 5.00*prior_k):

        estimator                        intercept error   predicted K at the mean
        OLS on clipped per-game logits        -1.383            0.1220  (true 0.2335)
        BINOMIAL GLM                          +0.018            0.2345  CORRECT

    WHY IT BREAKS (rule 8 -- I never asked what the target MEANS at the boundary):
      36% of starter-games have ZERO strikeouts. k_rate = 0 -> clipped to 0.01
      -> logit(0.01) = -4.60. A THIRD OF THE TARGETS ARE AN ARTEFACT OF THE CLIP,
      not the data. The regression line is dragged through a mass of fake -4.6s
      and the intercept collapses.

      That is EXACTLY the pathology we saw on the real data: the fit predicted
      an 11.1% K rate for a hitter whose own recent K rate was 22.3%.

    WHY THE GLM IS CORRECT BY CONSTRUCTION:
      A zero-strikeout game contributes log(1-p)*pa to the likelihood. It needs
      NO logit. There is nothing to clip. The boundary is handled exactly.

    Newton-Raphson (IRLS). Converges in a handful of iterations for a 2-parameter
    logistic; no dependency beyond numpy.
    """
    a, b = 0.0, 0.0
    n = len(x)
    X = np.column_stack([np.ones(n), x])
    for _ in range(100):
        eta = X @ np.array([a, b])
        p = 1.0 / (1.0 + np.exp(-eta))
        p = np.clip(p, 1e-9, 1.0 - 1e-9)
        # gradient and Hessian of the binomial log-likelihood
        grad = X.T @ (counts - trials * p)
        W = trials * p * (1.0 - p)
        H = X.T @ (X * W[:, None])
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            break
        a_new, b_new = a + step[0], b + step[1]
        if abs(a_new - a) < 1e-10 and abs(b_new - b) < 1e-10:
            a, b = a_new, b_new
            break
        a, b = a_new, b_new
    return float(a), float(b)


def r2(y: np.ndarray, yhat: np.ndarray, w: np.ndarray) -> float:
    W = w.sum()
    my = (w * y).sum() / W
    ss_res = (w * (y - yhat) ** 2).sum()
    ss_tot = (w * (y - my) ** 2).sum()
    return float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def binomial_glm_multi(X: np.ndarray, counts: np.ndarray,
                       trials: np.ndarray) -> np.ndarray:
    """Multi-predictor binomial GLM. Same Newton-Raphson, k predictors.

    Used to test whether a SEASON + RECENT blend beats season alone -- an
    EMPIRICAL question, decided by out-of-sample log-likelihood, not by
    preference (rule 10).
    """
    n, k = X.shape
    Xd = np.column_stack([np.ones(n), X])
    beta = np.zeros(k + 1)
    for _ in range(100):
        p = np.clip(1.0 / (1.0 + np.exp(-(Xd @ beta))), 1e-9, 1 - 1e-9)
        grad = Xd.T @ (counts - trials * p)
        W = trials * p * (1.0 - p)
        H = Xd.T @ (Xd * W[:, None])
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            break
        if np.max(np.abs(step)) < 1e-10:
            beta = beta + step
            break
        beta = beta + step
    return beta


def binom_loglik(counts: np.ndarray, trials: np.ndarray,
                 p: np.ndarray) -> float:
    """Log-likelihood of the OBSERVED COUNTS under Binomial(trials, p).

    *** THIS IS THE PRIMARY METRIC, AND R^2 ON PER-GAME RATES IS NOT. ***

    RULE 8 -- what the quantity means. An earlier draft scored predictors with
    R^2 on the per-game K RATE (out_k / out_pa). That metric is STRUCTURALLY
    INCAPABLE of seeing skill here, and I proved it on synthetic data where the
    predictor was PERFECT (it WAS the hitter's true K rate):

        R^2 of a PERFECT predictor, per-game rate : +0.0455
        R^2 of a CONSTANT,          per-game rate : +0.0000

    A perfect predictor scores barely above nothing. Why: a 4-PA game's K rate
    is one of {0, .25, .5, .75, 1}. The GAME-LEVEL sd is ~0.21; the TALENT sd is
    ~0.045. The noise is FIVE TIMES the signal, so R^2 is measuring the coin
    flip, not the coin.

    On the SAME synthetic data, binomial log-likelihood separated them cleanly:
        perfect predictor : -57189
        constant          : -58386
        improvement       :  +1197

    And it is the RIGHT metric on principle, not merely a working one: the
    simulator needs the PER-PA PROBABILITY to be right, and the likelihood of
    the realized counts under that probability IS the thing being asked.
    """
    p = np.clip(p, 1e-6, 1.0 - 1e-6)
    # log C(n,k) is CONSTANT across predictors (it does not involve p), so it
    # cancels in every comparison. We keep it for an interpretable absolute
    # number, using lgamma for numerical stability at large n.
    from scipy.special import gammaln
    log_c = (gammaln(trials + 1) - gammaln(counts + 1)
             - gammaln(trials - counts + 1))
    return float((log_c + counts * np.log(p)
                  + (trials - counts) * np.log(1.0 - p)).sum())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--split-date", default="2025-06-01",
                    help="TEMPORAL split. Train strictly before, test on/after. "
                         "NEVER random -- a random split leaks point-in-time "
                         "structure and flatters every predictor equally.")
    ap.add_argument("--min-pa", type=int, default=2,
                    help="drop games with fewer PA than this -- a 1-PA game's K "
                         "'rate' is 0.000 or 1.000 and is nearly pure noise")
    ap.add_argument("--min-recent-pa", type=int, default=40,
                    help="a rolling feature computed on very few PA is noise")
    ap.add_argument("--out", default="data/learning/k_bb_fit.json")
    args = ap.parse_args(argv)

    tr = pd.read_csv(args.training, low_memory=False)
    need = {"out_pa", "out_k", "out_bb", "game_date", "lineup_slot"}
    missing = need - set(tr.columns)
    if missing:
        print(f"FATAL: {args.training} missing {sorted(missing)}", file=sys.stderr)
        return 2

    d = tr[(tr.out_pa >= args.min_pa) & (tr.lineup_slot.between(1, 9))].copy()
    d["k_rate"] = d.out_k / d.out_pa
    d["bb_rate"] = d.out_bb / d.out_pa

    # ================================================================
    # THE PREDICTOR THE SIMULATOR WILL ACTUALLY RECEIVE.
    # ================================================================
    # RULE 1 -- fit what the model will be FED, not what is convenient.
    #
    # The plan is to populate StatcastProfile.k_rate from
    # HittingStatsSnapshot.season, i.e. the hitter's SEASON-TO-DATE K rate. In
    # the training set that is pit_k / pit_pa (the 'pit_' prefix = prior-to-game
    # season totals).
    #
    # *** THIS IS NOT THE SAME PREDICTOR AS roll30_k_rate, AND THE SLOPE DIFFERS.
    #     REGRESSION DILUTION: a predictor measured with error has its slope
    #     ATTENUATED toward zero --
    #         b_observed = b_true * var_signal / (var_signal + var_noise)
    #     MEASURED sample sizes and sds:
    #         season (pit_pa)  208 PA   sd 0.0811   <- LEAST noisy
    #         roll30           ~120 PA  sd 0.0850
    #         recent (recent_pa) 51 PA  sd 0.0920   <- MOST noisy
    #     A slope fitted on the NOISIER roll30 would UNDER-correct when applied
    #     to the CLEANER season rate. Using it would be a hand-picked number
    #     wearing a fit's clothes. ***
    if {"pit_k", "pit_pa"} <= set(d.columns):
        d["season_k_rate"] = np.where(d.pit_pa > 0, d.pit_k / d.pit_pa, np.nan)
    if {"pit_bb", "pit_pa"} <= set(d.columns):
        d["season_bb_rate"] = np.where(d.pit_pa > 0, d.pit_bb / d.pit_pa, np.nan)
    if {"recent_k", "recent_pa"} <= set(d.columns):
        d["recent_k_rate"] = np.where(d.recent_pa > 0,
                                      d.recent_k / d.recent_pa, np.nan)
    if {"recent_bb", "recent_pa"} <= set(d.columns):
        d["recent_bb_rate"] = np.where(d.recent_pa > 0,
                                       d.recent_bb / d.recent_pa, np.nan)

    print("=" * 84)
    print(f"FIT the K/BB model — {len(d):,} starter-games, "
          f"{d.game_date.min()} .. {d.game_date.max()}")
    print("=" * 84)
    lg_k = float(d.out_k.sum() / d.out_pa.sum())
    lg_bb = float(d.out_bb.sum() / d.out_pa.sum())
    print(f"  REALIZED league K rate  (starters): {lg_k:.4f}")
    print(f"  REALIZED league BB rate (starters): {lg_bb:.4f}")
    print(f"  the model produces                : K 0.1707   BB 0.0707")
    print(f"  the model's RAW (pre-clamp)       : K 0.1556   BB 0.0702")

    # ---- which rolling features exist? ----------------------------------
    cands = [c for c in ("roll30_k_rate", "roll15_k_rate") if c in d.columns]
    if not cands:
        print("\nFATAL: no roll*_k_rate column in the training set. The head-to-"
              "head cannot be run.", file=sys.stderr)
        return 2
    print(f"\n  available predictors: {cands}")

    results = {}
    for target, tgt_col, lg_rate, pred_cols in (
        # season_* FIRST -- it is the predictor the SIMULATOR will receive.
        ("K", "k_rate", lg_k,
         [c for c in ("season_k_rate", "roll30_k_rate", "recent_k_rate")
          if c in d.columns]),
        ("BB", "bb_rate", lg_bb,
         [c for c in ("season_bb_rate", "roll30_bb_rate", "recent_bb_rate")
          if c in d.columns]),
    ):
        print()
        print("=" * 84)
        print(f"{target} — HEAD TO HEAD  (out-of-sample, TEMPORAL split at "
              f"{args.split_date})")
        print("=" * 84)
        if not pred_cols:
            print(f"  no roll*_{target.lower()}_rate columns. SKIPPED.")
            continue

        print(f"  PRIMARY METRIC: binomial log-likelihood of the OBSERVED COUNTS.")
        print(f"  R^2 on per-game rates is NOT used -- a PERFECT predictor scores")
        print(f"  only +0.046 on it (proved on synthetic data), because the")
        print(f"  game-level binomial sd (~0.21) is 5x the talent sd (~0.045).")
        print()
        print(f"  {'predictor':20s} {'n_test':>7s} {'logL':>12s} {'vs const':>10s} "
              f"{'season R2':>10s} {'slope':>8s} {'intercept':>10s}")
        print("  (the CONSTANT is scored on the SAME rows, immediately beneath each")
        print("   predictor -- a baseline on different rows is not a baseline)")
        print("  " + "-" * 84)

        # *** THE BASELINE MUST BE COMPUTED ON THE SAME ROWS AS THE PREDICTOR. ***
        # RULE 8 -- a previous version computed the constant's log-likelihood on
        # ALL test rows, but each predictor's on rows filtered by
        # recent_pa_30 >= 40. DIFFERENT DENOMINATORS. The "vs const" column then
        # compared two numbers from two different populations and was meaningless
        # (it reported -41.9 when the predictor had in fact BEATEN the constant
        # by +41.9). A comparison on mismatched rows is not a weak comparison --
        # it is not a comparison at all.
        #
        # So the constant is now scored INSIDE the per-predictor loop, on exactly
        # the rows that predictor is scored on.
        cnt_col = "out_k" if target == "K" else "out_bb"

        best = None
        for pc in pred_cols:
            sub = d.dropna(subset=[pc, tgt_col]).copy()
            recent_col = ("recent_pa_30" if "30" in pc else "recent_pa_15")
            if recent_col in sub.columns:
                sub = sub[sub[recent_col] >= args.min_recent_pa]

            tr_ = sub[sub.game_date < args.split_date]
            te_ = sub[sub.game_date >= args.split_date]
            if len(tr_) < 500 or len(te_) < 200:
                print(f"  {pc:20s} TOO THIN ({len(tr_)}/{len(te_)})")
                continue

            # BINOMIAL GLM on the COUNTS -- see the note in binomial_glm().
            xtr = tr_[pc].to_numpy(float)
            ctr = tr_[cnt_col].to_numpy(float)
            wtr = tr_.out_pa.to_numpy(float)
            a, b = binomial_glm(xtr, ctr, wtr)

            xte = te_[pc].to_numpy(float)
            pred = sigmoid(a + b * xte)
            cnt = te_[cnt_col].to_numpy(float)
            pa_ = te_.out_pa.to_numpy(float)

            # *** BOTH scored on THE SAME ROWS. ***
            ll = binom_loglik(cnt, pa_, pred)
            ll_const = binom_loglik(cnt, pa_, np.full(len(te_), lg_rate))
            gain = ll - ll_const

            # SEASON-LEVEL R^2 -- also on the same rows, against the same baseline
            gg = (te_.assign(_c=cnt, _p=pa_, _pred=pred).groupby("player_id")
                  .agg(c=("_c", "sum"), p=("_p", "sum"), pred=("_pred", "mean")))
            gg = gg[gg.p >= 100]
            if len(gg) > 20:
                obs = (gg.c / gg.p).to_numpy(float)
                wts = gg.p.to_numpy(float)
                R2s = r2(obs, gg.pred.to_numpy(float), wts)
                R2c = r2(obs, np.full(len(gg), lg_rate), wts)
            else:
                R2s = R2c = float("nan")

            print(f"  {pc:20s} {len(te_):7d} {ll:12.1f} {gain:+10.1f} "
                  f"{R2s:10.4f} {b:8.4f} {a:10.4f}")
            print(f"  {'  (constant, same rows)':20s} {len(te_):7d} "
                  f"{ll_const:12.1f} {0.0:+10.1f} {R2c:10.4f}")

            if best is None or gain > best["ll_gain"]:
                best = dict(predictor=pc, ll=ll, ll_const=ll_const, ll_gain=gain,
                            season_r2=R2s, season_r2_const=R2c,
                            slope=b, intercept=a,
                            n_train=len(tr_), n_test=len(te_),
                            mean_x=float((wtr * xtr).sum() / wtr.sum()))

        # ================================================================
        # DOES A SEASON + RECENT BLEND BEAT SEASON ALONE?
        # ================================================================
        # A hitter's SEASON rate is stable but STALE (it includes April, when he
        # may have been a different player). His RECENT rate is current but
        # NOISY (51 PA). A two-term logit can use BOTH -- and whether that is
        # actually better is an EMPIRICAL question, not a modelling preference.
        # We MEASURE it (rule 10: the data picks, not me).
        s_col = f"season_{target.lower()}_rate"
        r_col = f"recent_{target.lower()}_rate"
        if s_col in d.columns and r_col in d.columns:
            sub = d.dropna(subset=[s_col, r_col, tgt_col]).copy()
            sub = sub[(sub.pit_pa >= 50) & (sub.recent_pa >= 20)]
            tr_ = sub[sub.game_date < args.split_date]
            te_ = sub[sub.game_date >= args.split_date]
            if len(tr_) > 500 and len(te_) > 200:
                Xtr = np.column_stack([tr_[s_col].to_numpy(float),
                                       tr_[r_col].to_numpy(float)])
                ctr = tr_[cnt_col].to_numpy(float)
                wtr = tr_.out_pa.to_numpy(float)
                coefs = binomial_glm_multi(Xtr, ctr, wtr)

                Xte = np.column_stack([te_[s_col].to_numpy(float),
                                       te_[r_col].to_numpy(float)])
                cnt = te_[cnt_col].to_numpy(float)
                pa_ = te_.out_pa.to_numpy(float)
                pred = sigmoid(coefs[0] + Xte @ coefs[1:])
                ll_b = binom_loglik(cnt, pa_, pred)
                ll_c = binom_loglik(cnt, pa_, np.full(len(te_), lg_rate))

                # season ALONE, on the SAME rows -- a fair head-to-head
                a_s, b_s = binomial_glm(tr_[s_col].to_numpy(float), ctr, wtr)
                pred_s = sigmoid(a_s + b_s * te_[s_col].to_numpy(float))
                ll_s = binom_loglik(cnt, pa_, pred_s)

                print()
                print(f"  BLEND vs SEASON ALONE  (same rows, n_test={len(te_)})")
                print(f"    season alone      logL {ll_s:12.1f}  "
                      f"vs const {ll_s - ll_c:+9.1f}")
                print(f"    season + recent   logL {ll_b:12.1f}  "
                      f"vs const {ll_b - ll_c:+9.1f}   "
                      f"vs season {ll_b - ll_s:+9.1f}")
                print(f"    blend coefs: a={coefs[0]:+.4f}  "
                      f"b_season={coefs[1]:+.4f}  b_recent={coefs[2]:+.4f}")
                if ll_b - ll_s > 10:
                    print(f"    -> the BLEND is better by {ll_b - ll_s:+.1f} logL.")
                    print(f"       Recent form carries signal the season average")
                    print(f"       washes out. Worth the extra term.")
                    best["blend"] = dict(intercept=float(coefs[0]),
                                         b_season=float(coefs[1]),
                                         b_recent=float(coefs[2]),
                                         ll_gain_vs_season=float(ll_b - ll_s))
                else:
                    print(f"    -> the blend adds {ll_b - ll_s:+.1f} logL. NOT worth")
                    print(f"       an extra term and an extra data dependency.")

        if best is None:
            print(f"  no usable predictor for {target}.")
            continue
        results[target] = best

        # ---- SANITY (rule 7) --------------------------------------------
        print()
        if best["ll_gain"] <= 0:
            print(f"  *** {best['predictor']} DOES NOT BEAT A CONSTANT ***")
            print(f"  (log-likelihood gain {best['ll_gain']:+.1f} -- a predictor that")
            print(f"  carries information CANNOT score worse than knowing nothing.)")
            print("  The rolling feature is broken or mis-joined. STOP.")
            return 1
        if best["slope"] <= 0:
            print(f"  *** THE SLOPE IS NEGATIVE ({best['slope']:.4f}). ***")
            print("  A hitter who strikes out more should strike out more. The")
            print("  MEASUREMENT is broken, not baseball. STOP.")
            return 1
        print(f"  OK -- {best['predictor']} beats the constant "
              f"(logL gain {best['ll_gain']:+.1f}, season R^2 "
              f"{best['season_r2']:.4f}) and the slope is positive.")

        # ---- HARD CHECK (rule 7): the fitted line MUST reproduce the league --
        at_mean_chk = sigmoid(best["intercept"] + best["slope"] * best["mean_x"])
        print()
        print(f"  CALIBRATION CHECK: at the MEAN predictor value, the fitted line")
        print(f"  must reproduce the LEAGUE rate. If it does not, the estimator is")
        print(f"  broken (this is exactly what caught the clipped-logit OLS bug).")
        print(f"    fitted at mean : {at_mean_chk:.4f}")
        print(f"    realized league: {lg_rate:.4f}")
        print(f"    error          : {at_mean_chk - lg_rate:+.4f}")
        if abs(at_mean_chk - lg_rate) > 0.015:
            print(f"\n  *** THE FITTED LINE DOES NOT REPRODUCE THE LEAGUE RATE. ***",
                  file=sys.stderr)
            print(f"  A correctly-fitted model, evaluated at the mean of its own", file=sys.stderr)
            print(f"  predictor, MUST land on the population mean. It does not.", file=sys.stderr)
            print(f"  The ESTIMATOR is broken, not the feature. STOP.", file=sys.stderr)
            return 1
        print(f"    OK -- the fitted line lands on the league rate.")

        # ---- what does this mean IN THE MODEL'S UNITS? -------------------
        print()
        print(f"  *** THE FITTED {target} MODEL, IN THE MODEL'S OWN UNITS ***")
        print(f"    logit({target}_next) = {best['intercept']:+.4f} "
              f"+ {best['slope']:+.4f} * {best['predictor']}")
        print()
        print(f"    at the league mean ({best['predictor']} = "
              f"{best['mean_x']:.4f}):")
        at_mean = sigmoid(best["intercept"] + best["slope"] * best["mean_x"])
        print(f"      -> {target} = {at_mean:.4f}   "
              f"(realized league: {lg_rate:.4f})")
        print()
        sd_x = float(d[best["predictor"]].std())
        print(f"    a hitter 1 SD above the mean ({best['predictor']} "
              f"+{sd_x:.4f}):")
        one_sd = sigmoid(best["intercept"] + best["slope"] * (best["mean_x"] + sd_x))
        print(f"      -> {target} = {one_sd:.4f}   "
              f"(a shift of {best['slope']*sd_x:+.4f} logits)")

    # =====================================================================
    print()
    print("=" * 84)
    print("COMPARISON TO WHAT THE MODEL CURRENTLY DOES")
    print("=" * 84)
    print("  The model's K logit uses:")
    print("      k_hitter_contact * H_contact")
    print("      = -1.05 * (contact_rate - 0.7550) * 8.278")
    print("      = -8.69 * (contact_rate - 0.7550)     <- logits per unit of contact")
    print()
    print("  MEASURED (trace_k_bb_logits.py, 2026-06-16):")
    print("      sd(H_contact)        = 0.6212")
    print("      -> a 1-SD hitter shifts the K logit by -1.05 * 0.62 = -0.65")
    if "K" in results:
        b = results["K"]
        sd_x = float(d[b["predictor"]].std())
        print()
        print(f"  FITTED ({b['predictor']}):")
        print(f"      -> a 1-SD hitter shifts the K logit by "
              f"{b['slope'] * sd_x:+.4f}")
        print()
        ratio = abs(-1.05 * 0.6212) / abs(b["slope"] * sd_x) if b["slope"] else float("nan")
        print(f"  *** THE MODEL'S CONTACT TERM IS {ratio:.2f}x THE MAGNITUDE OF THE")
        print(f"      FITTED RELATIONSHIP ON THE DIRECT PREDICTOR. ***")
        print()
        print("  *** WHAT THIS CANNOT SAY (rule 8/9 -- stated, not hidden): ***")
        print("  contact_rate is NOT in the training set (it comes from a Savant")
        print("  pull at reconstruct time), so this CANNOT report contact_rate's")
        print("  own R^2. It measures how well the DIRECT predictor works. A true")
        print("  head-to-head against contact_rate would need a reconstruction over")
        print("  many dates. But if the direct predictor's R^2 is HIGH, that is")
        print("  sufficient evidence to prefer it WITHOUT that run: a proxy cannot")
        print("  beat the thing it is a proxy FOR.")
        print()
        print("  RULE 10: this does NOT tell you which fix to apply. Three remain:")
        print("    F1  recenter league.contact_rate to the starter population")
        print("    F2  refit k_hitter_contact against the data")
        print("    F3  REPLACE contact_rate with the hitter's OWN k_rate as the")
        print("        predictor -- the thing itself rather than a proxy")
        print("  The R^2 table above is the evidence for choosing. It is not the")
        print("  choice.")

    if results:
        art = {
            "_comment": (
                "FITTED K/BB relationships from the training set. The model "
                "currently predicts K from contact_rate (a per-swing whiff PROXY) "
                "scaled 8.278x with a hand-picked coefficient of -1.05. This "
                "measures how well the DIRECT predictor (the hitter's own rolling "
                "K rate) does instead, out-of-sample, on a TEMPORAL split. "
                "CANDIDATE ONLY -- promotion goes through the gate."
            ),
            "provenance": {
                "source": args.training,
                "fitted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "split_date": args.split_date,
                "n_starter_games": int(len(d)),
                "realized_league_k": lg_k,
                "realized_league_bb": lg_bb,
            },
            "fits": results,
        }
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(art, f, indent=2)
            f.write("\n")
        print(f"\nwrote {out}")

    print()
    print("  *** RULE 10: EVIDENCE, NOT A VERDICT. ***")
    print("  This measures WHICH PREDICTOR WORKS and WHAT THE COEFFICIENT IS.")
    print("  It does not decide the fix. Read the R^2 table first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
