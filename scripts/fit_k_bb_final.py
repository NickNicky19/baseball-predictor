#!/usr/bin/env python3
"""
FINAL K/BB FIT — hitter season + hitter recent + PITCHER, in ONE GLM.

*** RULE 2: every coefficient is FITTED. ***
*** RULE 1: fit the EXACT quantities the simulator will be fed. ***

=============================================================================
WHY THIS SUPERSEDES fit_k_bb_model.py
=============================================================================
The earlier fit produced a hitter-only model:

    K : logit = -2.6299 + 3.4288*season_k + 1.5163*recent_k

and I was about to write it into pa_simulator with a pitcher term ADDED ON TOP:

    k_logit = kbb_k_intercept + kbb_k_season*k_s + kbb_k_recent*k_r
              + kbb_k_pitcher_miss * P_miss          <-- *** WRONG ***

*** THAT DOUBLE-COUNTS THE PITCHER. ***
The GLM was fitted on REAL GAMES AGAINST REAL PITCHERS, so the intercept
(-2.6299) ALREADY ABSORBS THE AVERAGE PITCHER EFFECT. Adding P_miss on top
counts the average pitcher twice. It would have passed every syntax check,
produced plausible numbers, and been WRONG.

Caught by a sanity check that the fitted model must reproduce the LEAGUE rate at
the LEAGUE mean. It produced 0.1785 against a realized 0.2221. Rule 7 earned its
keep.

=============================================================================
THE FIX: PUT THE PITCHER *IN* THE FIT, NOT ON TOP OF IT
=============================================================================
    logit(K) = a + b1*hitter_season_k + b2*hitter_recent_k + b3*pitcher_k_pct

All three coefficients fitted jointly. The intercept then means what it should:
the log-odds for a league-average hitter facing a league-average pitcher.

=============================================================================
UNITS -- THE PLACE A FIT LIKE THIS DIES  (rule 8)
=============================================================================
The training set has `opp_sp_k9`: strikeouts per NINE INNINGS.
The simulator receives `pitcher_k_pct`: strikeouts per BATTER FACED (0-100).

*** THESE ARE DIFFERENT QUANTITIES. *** A coefficient fitted on K/9 and applied
to K% would be wrong by a factor of ~2.6 -- silently.

We convert using THE EXACT FORMULA THE MODEL ITSELF USES
(PitchingStatsSnapshot.k_pct):
      estimated_pa = innings_pitched * 4.2
      k_pct = strikeouts / estimated_pa * 100
so, from K/9:
      k_pct = (k9 / 9) / 4.2 * 100 = k9 * 2.6455

*** AND WE FIT ON THE RAW k_pct, NOT ON THE SIMULATOR'S P_miss LATENT. ***
P_miss = (pitcher_k_pct/100 - league_k) * pitcher_k_scale -- another
transformation, another place for a scale error to hide. The FITTED path
REPLACES the legacy logit, so it does not need to reuse P_miss. Fitting and
serving the SAME quantity means the coefficient transfers EXACTLY, with zero
transformations in between.

=============================================================================
SANITY -- stated BEFORE the run (rule 7), and it is a HARD GATE
=============================================================================
  *** THE FITTED MODEL, EVALUATED AT THE LEAGUE-MEAN HITTER FACING THE
      LEAGUE-MEAN PITCHER, MUST REPRODUCE THE LEAGUE RATE. ***
      realized starter K  = 0.2221
      realized starter BB = 0.0846
  Tolerance 0.005. If it misses, the fit is WRONG and nothing is written.
  This is the check that caught the double-counted pitcher.

  fitted hitter-season slope : POSITIVE (a hitter who Ks more, Ks more)
  fitted pitcher slope       : POSITIVE for K (a pitcher who Ks more, gets more)
  If either is negative, the MEASUREMENT is broken, not baseball.

Usage:
    python scripts/fit_k_bb_final.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.provenance import sha256_file  # noqa: E402

# The EXACT conversion PitchingStatsSnapshot.k_pct uses: PA = IP * 4.2
K9_TO_PCT = 100.0 / 9.0 / 4.2          # = 2.6455

# *** THE GATE BASELINE IS COMPUTED FROM THE FITTED ROWS, NOT HARDCODED. ***
#
# RULE 8 -- and this rejected a CORRECT fit before it was caught.
# An earlier version hardcoded REALIZED = {"K": 0.2221} -- a rate MEASURED on
# 141,731 starter-games. But this script filters to rows with pit_pa >= 50,
# recent_pa >= 20, and a sane pitcher K% -- which drops ~29,000 rows and shifts
# the realized rate to 0.2198.
#
# The gate then compared the fit against a population IT WAS NOT FITTED ON and
# rejected it for a -0.0064 error that was really -0.0041 (inside tolerance).
#
# A baseline measured on different rows is not a baseline. It is a different
# number that happens to look like one.
#
# The realized rate is now computed FROM `d` -- the exact rows the GLM sees.
TOL = 0.005

# For reference only. NOT used as the gate. Measured on the UNFILTERED starter
# population (141,731 games), which is a different row set.
REALIZED_UNFILTERED = {"K": 0.2221, "BB": 0.0846}


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def binomial_glm(X: np.ndarray, counts: np.ndarray,
                 trials: np.ndarray) -> np.ndarray:
    """Multi-predictor binomial GLM by Newton-Raphson (IRLS).

    Maximum likelihood on the COUNTS. NOT OLS on clipped logits -- that
    estimator is BROKEN here and I proved it on synthetic data with a known
    truth: 36% of starter-games have ZERO strikeouts, k_rate=0 clips to 0.01,
    logit(0.01) = -4.60, and a THIRD of the targets become an artefact of the
    clip. It recovered an intercept 1.38 too low.

    A zero-strikeout game contributes log(1-p)*pa to the likelihood. No logit,
    nothing to clip. The boundary is exact by construction.
    """
    n, k = X.shape
    Xd = np.column_stack([np.ones(n), X])
    beta = np.zeros(k + 1)
    for _ in range(200):
        p = np.clip(sigmoid(Xd @ beta), 1e-9, 1 - 1e-9)
        grad = Xd.T @ (counts - trials * p)
        W = trials * p * (1.0 - p)
        H = Xd.T @ (Xd * W[:, None])
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            break
        beta = beta + step
        if np.max(np.abs(step)) < 1e-11:
            break
    return beta


def binom_loglik(counts, trials, p) -> float:
    from scipy.special import gammaln
    p = np.clip(p, 1e-6, 1 - 1e-6)
    lc = gammaln(trials + 1) - gammaln(counts + 1) - gammaln(trials - counts + 1)
    return float((lc + counts * np.log(p)
                  + (trials - counts) * np.log(1 - p)).sum())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--split-date", default="2025-06-01")
    ap.add_argument(
        "--data-before",
        default=None,
        help=(
            "exclusive YYYY-MM-DD boundary for every row used by the fit or "
            "its validation. Use this to make a historical artifact time-safe"
        ),
    )
    ap.add_argument("--min-pa", type=int, default=2)
    ap.add_argument("--min-season-pa", type=int, default=50)
    ap.add_argument("--min-recent-pa", type=int, default=20)
    ap.add_argument("--out", default="data/learning/k_bb_final.json")
    args = ap.parse_args(argv)

    tr = pd.read_csv(args.training, low_memory=False)
    need = {"out_pa", "out_k", "out_bb", "pit_pa", "pit_k", "pit_bb",
            "recent_pa", "recent_k", "recent_bb", "opp_sp_k9", "opp_sp_bb9",
            "game_date", "lineup_slot"}
    missing = need - set(tr.columns)
    if missing:
        print(f"FATAL: missing columns {sorted(missing)}", file=sys.stderr)
        return 2

    source_dates = pd.to_datetime(tr["game_date"], errors="coerce")
    if source_dates.isna().any():
        print("FATAL: game_date contains missing or unparsable values.", file=sys.stderr)
        return 2
    tr = tr.copy()
    tr["game_date"] = source_dates.dt.strftime("%Y-%m-%d")
    if args.data_before and pd.Timestamp(args.split_date) >= pd.Timestamp(args.data_before):
        print("FATAL: --split-date must be earlier than --data-before.", file=sys.stderr)
        return 2

    d = tr[(tr.out_pa >= args.min_pa) & (tr.lineup_slot.between(1, 9))].copy()
    if args.data_before:
        d = d[pd.to_datetime(d["game_date"]) < pd.Timestamp(args.data_before)].copy()

    # ---- the EXACT quantities the simulator will receive -------------------
    d["h_k_season"] = np.where(d.pit_pa > 0, d.pit_k / d.pit_pa, np.nan)
    d["h_bb_season"] = np.where(d.pit_pa > 0, d.pit_bb / d.pit_pa, np.nan)
    d["h_k_recent"] = np.where(d.recent_pa > 0, d.recent_k / d.recent_pa, np.nan)
    d["h_bb_recent"] = np.where(d.recent_pa > 0, d.recent_bb / d.recent_pa, np.nan)
    # K/9 -> K%, via the SAME formula PitchingStatsSnapshot.k_pct uses
    d["p_k_pct"] = d.opp_sp_k9 * K9_TO_PCT
    d["p_bb_pct"] = d.opp_sp_bb9 * K9_TO_PCT

    d = d[(d.pit_pa >= args.min_season_pa) & (d.recent_pa >= args.min_recent_pa)]
    d = d.dropna(subset=["h_k_season", "h_bb_season", "h_k_recent",
                         "h_bb_recent", "p_k_pct", "p_bb_pct"])
    # a pitcher K% outside a sane band is a data error, not a pitcher
    d = d[(d.p_k_pct > 5) & (d.p_k_pct < 45)
          & (d.p_bb_pct > 0) & (d.p_bb_pct < 25)]

    # *** THE GATE BASELINE: the realized rate ON THE ROWS THE FIT USES. ***
    REALIZED = {
        "K": float(d.out_k.sum() / d.out_pa.sum()),
        "BB": float(d.out_bb.sum() / d.out_pa.sum()),
    }

    print("=" * 84)
    print(f"FINAL K/BB FIT — {len(d):,} starter-games, "
          f"{d.game_date.min()} .. {d.game_date.max()}")
    print("=" * 84)
    print(f"  realized K  ON THE FITTED ROWS : {REALIZED['K']:.4f}   <- THE GATE")
    print(f"  realized BB ON THE FITTED ROWS : {REALIZED['BB']:.4f}   <- THE GATE")
    print(f"    (unfiltered starter population, for reference: "
          f"K {REALIZED_UNFILTERED['K']:.4f}, BB {REALIZED_UNFILTERED['BB']:.4f})")
    print(f"    The filters (pit_pa>={args.min_season_pa}, "
          f"recent_pa>={args.min_recent_pa}, sane pitcher K%) drop rows and SHIFT")
    print(f"    the realized rate. The gate MUST use the rate of the rows the GLM")
    print(f"    actually sees -- a baseline from different rows is not a baseline.")
    print()
    print(f"  PREDICTOR MEANS (PA-weighted -- the fit is weighted by out_pa):")
    W = d.out_pa.to_numpy(float)
    means = {}
    for c in ("h_k_season", "h_k_recent", "h_bb_season", "h_bb_recent",
              "p_k_pct", "p_bb_pct"):
        means[c] = float((W * d[c].to_numpy(float)).sum() / W.sum())
        print(f"    {c:14s} {means[c]:8.4f}")
    print()
    print(f"  UNITS: opp_sp_k9 is strikeouts per NINE INNINGS. The simulator gets")
    print(f"  pitcher_k_pct = strikeouts per BATTER FACED (0-100). Converted with")
    print(f"  the SAME formula the model uses (PA = IP * 4.2):  k_pct = k9 * "
          f"{K9_TO_PCT:.4f}")

    results = {}
    for target, cnt_col, hs, hr, pp in (
        ("K", "out_k", "h_k_season", "h_k_recent", "p_k_pct"),
        ("BB", "out_bb", "h_bb_season", "h_bb_recent", "p_bb_pct"),
    ):
        print()
        print("=" * 84)
        print(f"{target} — hitter_season + hitter_recent + PITCHER, ONE GLM")
        print("=" * 84)

        trn = d[d.game_date < args.split_date]
        tst = d[d.game_date >= args.split_date]
        if trn.empty or tst.empty:
            print(
                f"FATAL: chronology produced empty train/test rows "
                f"(split={args.split_date}, data_before={args.data_before}).",
                file=sys.stderr,
            )
            return 2
        Xtr = np.column_stack([trn[hs], trn[hr], trn[pp]]).astype(float)
        Xte = np.column_stack([tst[hs], tst[hr], tst[pp]]).astype(float)
        ctr = trn[cnt_col].to_numpy(float)
        wtr = trn.out_pa.to_numpy(float)
        cte = tst[cnt_col].to_numpy(float)
        wte = tst.out_pa.to_numpy(float)

        beta = binomial_glm(Xtr, ctr, wtr)
        a, b_s, b_r, b_p = (float(v) for v in beta)

        pred = sigmoid(a + Xte @ beta[1:])
        lg_rate = REALIZED[target]
        ll = binom_loglik(cte, wte, pred)
        ll_c = binom_loglik(cte, wte, np.full(len(tst), lg_rate))

        print(f"  logit({target}) = {a:+.4f}")
        print(f"                + {b_s:+.4f} * hitter_{target.lower()}_season")
        print(f"                + {b_r:+.4f} * hitter_{target.lower()}_recent")
        print(f"                + {b_p:+.4f} * pitcher_{target.lower()}_pct")
        print()
        print(f"  out-of-sample logL      : {ll:12.1f}  "
              f"(n_test = {len(tst):,})")
        print(f"  constant, same rows     : {ll_c:12.1f}")
        print(f"  *** GAIN over a constant: {ll - ll_c:+12.1f} ***")

        # ---- THE HARD GATE (rule 7) --------------------------------------
        at_mean = float(sigmoid(a + b_s * means[hs] + b_r * means[hr]
                                + b_p * means[pp]))
        print()
        print(f"  *** CALIBRATION GATE ***")
        print(f"  a league-mean hitter facing a league-mean pitcher must produce")
        print(f"  the LEAGUE rate. This is the check that caught the double-counted")
        print(f"  pitcher in the previous attempt (it produced 0.1785 vs 0.2221).")
        print(f"    fitted at the means : {at_mean:.4f}")
        print(f"    realized league     : {REALIZED[target]:.4f}")
        print(f"    error               : {at_mean - REALIZED[target]:+.4f}   "
              f"(tolerance {TOL})")
        if abs(at_mean - REALIZED[target]) > TOL:
            print(f"\n  *** FIT REJECTED: the model does not reproduce the league "
                  f"rate. ***", file=sys.stderr)
            print(f"  A correctly-fitted model evaluated at the mean of its own "
                  f"predictors\n  MUST land on the population mean. It does not. "
                  f"Nothing was written.", file=sys.stderr)
            return 1
        print(f"    OK.")

        if b_s <= 0:
            print(f"\n  *** the hitter slope is NEGATIVE ({b_s:.4f}). A hitter who "
                  f"{target}s more\n  should {target} more. The MEASUREMENT is "
                  f"broken. ***", file=sys.stderr)
            return 1
        if target == "K" and b_p <= 0:
            print(f"\n  *** the pitcher slope is NEGATIVE ({b_p:.4f}). A pitcher "
                  f"who strikes out\n  more batters should strike out more "
                  f"batters. BROKEN. ***", file=sys.stderr)
            return 1

        # ---- what it means, in plain numbers ------------------------------
        sd_hs = float(d[hs].std())
        sd_pp = float(d[pp].std())
        print()
        print(f"  WHAT THE FITTED MODEL SAYS:")
        for lbl, v in ((f"low-{target} hitter (-1sd)", means[hs] - sd_hs),
                       ("league-average hitter", means[hs]),
                       (f"high-{target} hitter (+1sd)", means[hs] + sd_hs)):
            r = sigmoid(a + b_s * v + b_r * v + b_p * means[pp])
            print(f"    {lbl:26s} -> {target} = {r:.4f}")
        print()
        print(f"    (vs a league-average pitcher; +/-1sd on BOTH season and recent)")
        print()
        print(f"    a +1sd PITCHER shifts the logit by "
              f"{b_p * sd_pp:+.4f}  ({target} "
              f"{sigmoid(a + b_s*means[hs] + b_r*means[hr] + b_p*(means[pp]+sd_pp)):.4f})")

        # RULE 10 -- report what the coefficients SAY, do not spin it.
        # In the hitter-only fit, `recent` carried a coefficient of 1.52 and the
        # blend beat season-alone by +174 logL. With the PITCHER in the model,
        # `recent` collapses. That is not a bug -- it means the earlier "blend
        # wins" result was an ARTEFACT of the missing pitcher term: `recent` was
        # standing in for variance the pitcher explains. season and recent are
        # ~0.98 correlated, so once the pitcher is present, recent adds little.
        share_r = abs(b_r) / (abs(b_s) + abs(b_r)) if (abs(b_s)+abs(b_r)) > 0 else 0
        print()
        print(f"  RECENT-FORM WEIGHT: {share_r:.1%} of the hitter signal")
        if share_r < 0.15:
            print(f"    -> `recent` is carrying almost nothing now that the PITCHER")
            print(f"       is in the model. In the hitter-ONLY fit it carried 1.52")
            print(f"       and 'the blend wins' looked like a real finding. It was")
            print(f"       an ARTEFACT: recent was standing in for pitcher variance.")
            print(f"       season and recent are ~0.98 correlated. Keep the term (it")
            print(f"       is fitted and costs nothing) but do NOT claim recent form")
            print(f"       is a meaningful signal here -- the data says it is not.")

        results[target] = dict(
            intercept=a, hitter_season=b_s, hitter_recent=b_r, pitcher=b_p,
            recent_share_of_hitter_signal=float(share_r),
            ll=ll, ll_const=ll_c, ll_gain=ll - ll_c,
            n_train=int(len(trn)), n_test=int(len(tst)),
            mean_hitter_season=means[hs], mean_hitter_recent=means[hr],
            mean_pitcher=means[pp],
            fitted_at_means=at_mean, realized_league=REALIZED[target],
        )

    # =====================================================================
    print()
    print("=" * 84)
    print("COMPARISON TO THE MODEL'S CURRENT K/BB")
    print("=" * 84)
    print(f"  {'':22s} {'CURRENT':>9s} {'FITTED':>9s} {'REALIZED':>9s}")
    print(f"  {'K,  league-avg starter':22s} {0.1707:9.4f} "
          f"{results['K']['fitted_at_means']:9.4f} {REALIZED['K']:9.4f}")
    print(f"  {'BB, league-avg starter':22s} {0.0707:9.4f} "
          f"{results['BB']['fitted_at_means']:9.4f} {REALIZED['BB']:9.4f}")
    bip_cur = 1 - 0.1707 - 0.0707
    bip_fit = 1 - results["K"]["fitted_at_means"] - results["BB"]["fitted_at_means"]
    print(f"  {'-> bip_prob':22s} {bip_cur:9.4f} {bip_fit:9.4f} {1-REALIZED["K"]-REALIZED["BB"]:9.4f}")
    print()
    xba = 0.326
    print(f"  hits/PA = bip * xba_on_contact ({xba}):")
    print(f"    {'CURRENT':10s} {bip_cur*xba:.4f}")
    print(f"    {'FITTED':10s} {bip_fit*xba:.4f}")
    print(f"    {'REALIZED':10s} 0.2197")
    print()
    print(f"  P(hits>=1) over 4 PA:")
    for lbl, r in (("CURRENT", bip_cur*xba), ("FITTED", bip_fit*xba),
                   ("REALIZED", 0.2197)):
        print(f"    {lbl:10s} {1-(1-r)**4:.4f}")

    art = {
        "_comment": (
            "FINAL K/BB model. Hitter season + hitter recent + PITCHER, fitted "
            "JOINTLY by binomial GLM on starter-games, temporal split, scored "
            "out-of-sample. The pitcher is IN the fit, not added on top -- an "
            "earlier hitter-only fit had its intercept absorb the average pitcher "
            "effect, and bolting a pitcher term onto it DOUBLE-COUNTED him "
            "(it produced K=0.1785 against a realized 0.2221). "
            "UNITS: pitcher_*_pct is per BATTER FACED (0-100), converted from the "
            "training set's per-9 rates with the SAME formula the model uses "
            "(PA = IP * 4.2). The simulator must feed the SAME quantity. "
            "CANDIDATE -- promotion goes through the gate."
        ),
        "provenance": {
            "source": args.training,
            "source_sha256": sha256_file(args.training),
            "fitted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "split_date": args.split_date,
            "data_before_exclusive": args.data_before,
            "n_rows": int(len(d)),
            "fit_universe_date_min": str(d.game_date.min()),
            "fit_universe_date_max": str(d.game_date.max()),
            "train_date_min": str(d[d.game_date < args.split_date].game_date.min()),
            "train_date_max": str(d[d.game_date < args.split_date].game_date.max()),
            "validation_date_min": str(d[d.game_date >= args.split_date].game_date.min()),
            "validation_date_max": str(d[d.game_date >= args.split_date].game_date.max()),
            "k9_to_pct": K9_TO_PCT,
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
    print("  CANDIDATE ONLY. Promotion goes through the gate, like everything else.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
