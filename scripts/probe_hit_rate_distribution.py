#!/usr/bin/env python3
"""
STAGE 1 — Is the model's PER-HITTER hit-rate distribution wrong, and HOW?

*** RULE 10: THIS DIAGNOSES. IT DOES NOT PRESCRIBE. ***
It does not test a mechanism. It measures a distribution and reports its shape.
Several mechanisms could produce the same shape; distinguishing them is STAGE 2,
and stage 2 does not get written until stage 1 has run.

=============================================================================
WHAT WE KNOW, AND WHAT WE DO NOT
=============================================================================
MEASURED (decompose_hits_bias.py, 2026-06-16, n=238 gradeable):
    baseline P(hits>=1)            0.6790   bias +0.0824
    + TRUE PA count                0.6641   closes 18.2%
    + TRUE per-PA hit rate         0.6336   closes 55.2%    <- the big one
    + BOTH                         0.6191   closes 72.7%
    ACTUAL                         0.5966

So forcing every hitter to the POOLED league hit rate closes 55% of the bias.

*** BUT MY FIRST READING OF THAT WAS WRONG, AND THE ALGEBRA SAYS SO. ***
I concluded "the per-hitter rates are miscalibrated". Check it:

    P(>=1) = 1 - (1-p)^n   is CONCAVE in p.
    By JENSEN, for a FIXED MEAN, spreading p across hitters LOWERS mean P(>=1);
    FLATTENING it RAISES mean P(>=1).

    So flattening should have made the bias WORSE. It made it BETTER.
    -> The improvement CANNOT be a pure spread effect. My model of what is
       happening is broken somewhere.

And the LEVEL only explains ~15% of it:
    model rate  = bip_rate * xba_on_contact = 0.69 * 0.326 = 0.2249 hits/PA
    REALIZED    = 0.21965 hits/PA  (pooled, 152,683 hitter-games)
    at 4 PA that level gap moves P(>=1) by only -0.0069,
    but the substitution moved it by -0.0454.

So ~85% of what the rate substitution did is UNEXPLAINED. That is the thing
this script is for.

=============================================================================
WHAT THIS MEASURES  (no patching, no substitution -- just READ the model)
=============================================================================
For every hitter on a slate:
    MODEL rate    = sum(single, double, triple, home_run) from
                    expected_outcome_probabilities()   -- the model's OWN number
    REALIZED rate = out_hits / out_pa, from that hitter's games BEFORE this date
                    (LEAKAGE-SAFE: strictly prior games only)

Then compare the DISTRIBUTIONS -- not just the means:
    * mean, sd, and the full decile ladder
    * the LOW TAIL specifically (how many hitters does the model think are bad?)
    * the rank correlation (does the model at least ORDER hitters correctly?)

AND -- instrumented, not assumed -- how often each CLAMP actually BINDS:
    hit_on_contact_min = 0.18   hit_on_contact_max = 0.45
    p_hit_non_hr clamp = [0.05, 0.45]
    hit_prob_cap (the safety net)
    context_hit_scale  = [0.88, 1.12]

A clamp that never fires is not the mechanism. A FLOOR that fires often, and a
CEILING that never does, is an ASYMMETRY -- and asymmetric clamping compresses
the low tail upward. That is ONE candidate mechanism. Others (over-shrinkage
toward the league mean; a context scale too narrow to move the rate) produce the
SAME shape. THIS SCRIPT CANNOT TELL THEM APART, and says so.

=============================================================================
LEAKAGE  (rule 1 -- the thing that would silently invalidate everything)
=============================================================================
The REALIZED rate MUST come only from games STRICTLY BEFORE the slate date. A
hitter's rate computed INCLUDING the game we are scoring would leak the outcome
into the "truth" we compare against, and a model that looked perfect would just
be reading the answer. Enforced with an assertion, not a comment.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  model mean hit rate    : 0.22 - 0.23   (bip * xba = 0.2249)
  realized mean          : 0.21 - 0.23   (pooled league = 0.2197)
  realized SD across hitters : 0.030 - 0.050
      (a .240 hitter and a .300 hitter differ by ~0.045 hits/PA; the spread of
       TRUE talent is real but not enormous, and small-sample season rates are
       NOISIER than talent, so the OBSERVED sd should EXCEED the talent sd)
  model SD               : UNKNOWN. This is the number we are here to find.

  If the model SD is much SMALLER than realized, the model is compressed.
  If it is much LARGER, the model is over-spread.
  If they match, the M1/M2/M3 family is DEAD and the answer is M4 -- something
  in a path I have not traced.

Usage:
    python scripts/probe_hit_rate_distribution.py --date 2026-06-16
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.learning.retrain_runner import RetrainRunner          # noqa: E402
from src.models.dataclasses import LeagueBaselines             # noqa: E402
from src.prediction.prop_engine import PropEngine              # noqa: E402
from src.features.feature_factory import FeatureFactory        # noqa: E402
from src.data.mlb_api import MLBStatsAPI                       # noqa: E402
from src.simulation.pa_simulator import HybridPASimulator      # noqa: E402

HIT_KEYS = ("single", "double", "triple", "home_run")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-06-16")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--min-prior-pa", type=int, default=100,
                    help="a hitter needs this many PRIOR plate appearances for "
                         "his realized rate to mean anything. Below it, the "
                         "'realized' rate is mostly noise and comparing the "
                         "model to it would measure sampling error, not bias.")
    ap.add_argument("--out", default="data/analysis/hit_rate_distribution.csv")
    args = ap.parse_args(argv)

    config = RetrainRunner.load_config(args.config)
    league = LeagueBaselines.from_config(config)
    api = MLBStatsAPI(season=int(args.date[:4]), config=config)
    factory = FeatureFactory(config=config, league_baselines=league, mlb_api=api)
    engine = PropEngine(config=config, league_baselines=league)

    bundles = factory.build_bundles(args.date)
    if not bundles:
        print(f"FATAL: no bundles for {args.date}", file=sys.stderr)
        return 2

    # ---- realized rates, STRICTLY PRIOR games (leakage guard) -------------
    tr = pd.read_csv(args.training, low_memory=False)
    tr = tr.dropna(subset=["out_pa", "out_hits"])
    tr = tr[tr.out_pa > 0]

    prior = tr[tr.game_date < args.date]
    if prior.empty:
        print(f"FATAL: no games before {args.date}.", file=sys.stderr)
        return 2

    # ASSERTION, not a comment: no row used for the "truth" may be on or after
    # the slate date. A rate computed including the game we score would leak the
    # outcome into the ground truth.
    assert (prior.game_date < args.date).all(), "LEAKAGE: prior set contains the slate date"
    print(f"[leakage] realized rates use {len(prior):,} games strictly BEFORE "
          f"{args.date} (max date: {prior.game_date.max()})")

    real = (prior.groupby("player_id")
            .agg(prior_pa=("out_pa", "sum"), prior_hits=("out_hits", "sum"))
            .reset_index())
    real["realized_rate"] = real.prior_hits / real.prior_pa

    # ---- the model's OWN hit rate, per hitter. NO PATCHING. --------------
    # We call the same function the simulator calls, with the same inputs
    # prop_engine builds. Reading the model, not modifying it.
    pa_sim: HybridPASimulator = engine.monte_carlo.game_simulator.pa_simulator
    cfg = pa_sim.config

    # Instrument the clamps. We WRAP _clamp to count how often each bound BINDS,
    # keyed by the (lo, hi) pair -- this is measurement, not a change in
    # behaviour: the wrapper returns exactly what the original returned.
    clamp_hits: dict[tuple[float, float], dict[str, int]] = {}
    orig_clamp = HybridPASimulator._clamp

    def counting_clamp(x, lo, hi):
        rec = clamp_hits.setdefault((round(lo, 4), round(hi, 4)),
                                    {"lo": 0, "hi": 0, "n": 0})
        rec["n"] += 1
        if x < lo:
            rec["lo"] += 1
        elif x > hi:
            rec["hi"] += 1
        return orig_clamp(x, lo, hi)

    HybridPASimulator._clamp = staticmethod(counting_clamp)

    rows = []
    try:
        for b in bundles:
            pid = int(b.hitter.player.mlb_id)
            si = engine._bundle_to_sim_input(
                b, rich_features=b.metadata.get("rich_features", {}))
            p = pa_sim.expected_outcome_probabilities(
                pitcher_k_pct=si.pitcher_k_pct + si.umpire_k_bias,
                pitcher_bb_pct=si.pitcher_bb_pct,
                pitcher_hr_per_9=si.pitcher_hr_per_9,
                park_hr_factor=si.park_hr_factor * si.weather_hr_factor,
                park_hits_factor=si.park_hits_factor,
                handedness_advantage=si.handedness_advantage,
                recent_form_mult=si.recent_form_mult,
                bvp_ops_factor=si.bvp_ops_factor,
                bvp_hr_factor=si.bvp_hr_factor,
                statcast=si.statcast,
                rich_features=si.rich_features,
            )
            rows.append(dict(
                player_id=pid,
                player_name=b.hitter.player.name,
                model_rate=float(sum(p[k] for k in HIT_KEYS)),
                model_k=float(p["strikeout"]),
                model_bb=float(p["walk"]),
                sample_pa=float(b.statcast.sample_pa or 0.0),
                xba=float(b.statcast.xba) if b.statcast.xba is not None else np.nan,
            ))
    finally:
        HybridPASimulator._clamp = staticmethod(orig_clamp)   # ALWAYS restore

    mdl = pd.DataFrame(rows)
    m = mdl.merge(real, on="player_id", how="inner")
    n_all = len(m)
    m = m[m.prior_pa >= args.min_prior_pa]
    print(f"[join] {len(m)}/{n_all} hitters with >= {args.min_prior_pa} prior PA")
    if len(m) < 40:
        print("FATAL: too few hitters with enough prior PA.", file=sys.stderr)
        return 2

    # =====================================================================
    print()
    print("=" * 78)
    print(f"HIT-RATE DISTRIBUTION — {args.date}, n = {len(m)} hitters")
    print("=" * 78)
    print(f"  {'':16s} {'MODEL':>10s} {'REALIZED':>10s} {'diff':>9s}")
    print("  " + "-" * 48)
    for label, f in (("mean", np.mean), ("sd", np.std),
                     ("min", np.min), ("max", np.max)):
        a, b_ = float(f(m.model_rate)), float(f(m.realized_rate))
        print(f"  {label:16s} {a:10.4f} {b_:10.4f} {a - b_:+9.4f}")

    print()
    print("  DECILE LADDER (the shape, not just the moments)")
    print(f"  {'pct':>5s} {'MODEL':>10s} {'REALIZED':>10s} {'diff':>9s}")
    for q in (5, 10, 25, 50, 75, 90, 95):
        a = float(np.percentile(m.model_rate, q))
        b_ = float(np.percentile(m.realized_rate, q))
        flag = ""
        if q <= 10 and a - b_ > 0.010:
            flag = "  <- model too HIGH in the low tail"
        print(f"  {q:4d}% {a:10.4f} {b_:10.4f} {a - b_:+9.4f}{flag}")

    # rank correlation: does the model at least ORDER hitters correctly?
    rho = float(pd.Series(m.model_rate).corr(pd.Series(m.realized_rate),
                                             method="spearman"))
    pear = float(pd.Series(m.model_rate).corr(pd.Series(m.realized_rate)))
    print()
    print(f"  Spearman rank corr (model vs realized) : {rho:+.4f}")
    print(f"  Pearson  corr                          : {pear:+.4f}")
    print("    A model with NO per-hitter information would score ~0. A model")
    print("    that ranks hitters well but mis-scales them shows HIGH rho and a")
    print("    compressed SD -- those are DIFFERENT problems with different fixes.")

    # =====================================================================
    print()
    print("=" * 78)
    print("CLAMP INSTRUMENTATION — which bounds actually BIND?  (measured, not assumed)")
    print("=" * 78)
    known = {
        (round(cfg.hit_on_contact_min, 4), round(cfg.hit_on_contact_max, 4)):
            "hit_on_contact target [min, max]",
        (0.05, 0.45): "p_hit_non_hr [0.05, 0.45]",
        (round(cfg.k_min, 4), round(cfg.k_max, 4)): "K prob [k_min, k_max]",
        (round(cfg.bb_min, 4), round(cfg.bb_max, 4)): "BB prob [bb_min, bb_max]",
        (round(cfg.hr_min, 4), round(cfg.hr_max, 4)): "HR|BIP [hr_min, hr_max]",
    }
    print(f"  {'bound':38s} {'n':>7s} {'hit LO':>8s} {'hit HI':>8s} {'lo%':>7s} {'hi%':>7s}")
    print("  " + "-" * 78)
    for k, rec in sorted(clamp_hits.items(), key=lambda kv: -kv[1]["n"]):
        name = known.get(k, f"other {k}")
        n = rec["n"]
        lo_pct = rec["lo"] / n if n else 0.0
        hi_pct = rec["hi"] / n if n else 0.0
        flag = ""
        if lo_pct > 0.05 and hi_pct < 0.01:
            flag = "  <- FLOOR binds, ceiling does not: ASYMMETRIC"
        print(f"  {name:38s} {n:7d} {rec['lo']:8d} {rec['hi']:8d} "
              f"{lo_pct:7.1%} {hi_pct:7.1%}{flag}")

    print()
    print("  A clamp that NEVER fires is not a mechanism. A FLOOR that fires often")
    print("  while the ceiling does not is an ASYMMETRY, and asymmetric clamping")
    print("  compresses the low tail UPWARD.")

    # ---- shrinkage: how much is the model pulling toward the league mean? --
    print()
    print("=" * 78)
    print("SHRINKAGE — how much weight does each hitter get on his OWN xBA?")
    print("=" * 78)
    m["own_weight"] = m.sample_pa / (m.sample_pa + max(cfg.xba_shrinkage_pa, 1.0))
    print(f"  xba_shrinkage_pa = {cfg.xba_shrinkage_pa}")
    print(f"  mean sample_pa   = {m.sample_pa.mean():.1f}")
    print(f"  weight on own xBA: mean {m.own_weight.mean():.3f}  "
          f"min {m.own_weight.min():.3f}  max {m.own_weight.max():.3f}")
    print(f"  -> the average hitter's rate is "
          f"{1 - m.own_weight.mean():.1%} LEAGUE MEAN by construction.")
    print("  That COMPRESSES the spread toward the middle. Whether that is the")
    print("  problem, or merely A problem, this script cannot say.")

    # =====================================================================
    print()
    print("=" * 78)
    print("VERDICT — STAGE 1 ONLY")
    print("=" * 78)
    sd_m = float(np.std(m.model_rate))
    sd_r_obs = float(np.std(m.realized_rate))

    # ====================================================================
    # NOISE-CORRECT THE REALIZED SD.  *** THIS IS NOT OPTIONAL. ***
    # ====================================================================
    # RULE 8 -- what the quantity MEANS. `realized_rate` is a SEASON-TO-DATE
    # rate, and it is a NOISY estimate of true talent. For a hitter with N prior
    # PA, Var(observed) = Var(TALENT) + p(1-p)/N  -- the binomial sampling term.
    #
    # A hitter with 150 PA has a standard error of sqrt(.22*.78/150) = 0.034 --
    # AS LARGE AS THE ENTIRE TALENT SPREAD. So the OBSERVED sd is INFLATED, and
    # a naive "model_sd / observed_sd" is BIASED DOWNWARD: it would make the
    # model look compressed even if it were perfect.
    #
    # Subtract the mean sampling variance to recover the TALENT sd. This is the
    # standard reliability correction and it is the number that means something.
    p_bar = float(m.realized_rate.mean())
    samp_var = float((p_bar * (1 - p_bar) / m.prior_pa).mean())
    talent_var = max(1e-9, sd_r_obs ** 2 - samp_var)
    sd_r_talent = float(np.sqrt(talent_var))

    ratio_obs = sd_m / sd_r_obs if sd_r_obs > 0 else float("nan")
    ratio = sd_m / sd_r_talent if sd_r_talent > 0 else float("nan")
    p10_m = float(np.percentile(m.model_rate, 10))
    p10_r = float(np.percentile(m.realized_rate, 10))

    print(f"  model SD                        : {sd_m:.4f}")
    print(f"  realized SD (OBSERVED)          : {sd_r_obs:.4f}")
    print(f"    minus sampling noise           -{np.sqrt(samp_var):.4f}  "
          f"(mean prior PA = {m.prior_pa.mean():.0f})")
    print(f"  realized SD (TRUE TALENT)       : {sd_r_talent:.4f}")
    print()
    print(f"  model SD / OBSERVED  SD = {ratio_obs:.3f}   <- BIASED DOWN, do not use")
    print(f"  model SD / TALENT    SD = {ratio:.3f}   <- *** THE REAL NUMBER ***")
    print()
    print("    The observed realized SD is INFLATED by sampling noise: a hitter")
    print("    with 150 PA has a standard error of ~0.034, as large as the entire")
    print("    talent spread. Comparing the model to the OBSERVED sd would make it")
    print("    look compressed even if it were perfect. The talent SD is the")
    print("    observed variance MINUS the mean binomial sampling variance.")
    print()
    print(f"  10th pct: model {p10_m:.4f} vs realized {p10_r:.4f} "
          f"({p10_m - p10_r:+.4f})")
    print("    (the 10th percentile of the OBSERVED realized rates is itself")
    print("     dragged DOWN by noise, so this understates the model's low-tail")
    print("     problem if anything -- it is the conservative direction.)")
    print()
    if ratio < 0.75 and (p10_m - p10_r) > 0.008:
        print("  *** THE MODEL'S HIT-RATE DISTRIBUTION IS COMPRESSED, AND THE")
        print("      COMPRESSION IS IN THE LOW TAIL. ***")
        print("  The model does not believe in bad hitters. Too few low-rate")
        print("  hitters means too few zero-hit games, which inflates P(>=1).")
        print()
        print("  *** RULE 10: THIS DOES NOT TELL US WHICH MECHANISM. ***")
        print("  At least three produce this SAME shape:")
        print("    M1 asymmetric clamping   (see the clamp table above)")
        print("    M2 over-shrinkage        (see the shrinkage block above)")
        print("    M3 context scale too narrow to move the rate")
        print("  The tables above are EVIDENCE, not a verdict. Stage 2 must")
        print("  distinguish them -- and stage 2 does not get written until we")
        print("  have looked at these numbers together.")
        rc = 0
    elif abs(ratio - 1.0) < 0.15:
        print("  *** THE DISTRIBUTIONS MATCH. THE M1/M2/M3 FAMILY IS DEAD. ***")
        print("  The model's per-hitter hit-rate spread is about right. Whatever")
        print("  the rate substitution was doing, it was NOT fixing a compressed")
        print("  distribution. The answer is M4 -- something in a path not yet")
        print("  traced. Do NOT touch the clamps or the shrinkage.")
        rc = 1
    else:
        print(f"  *** AMBIGUOUS. SD ratio {ratio:.3f}, low-tail gap "
              f"{p10_m - p10_r:+.4f}. ***")
        print("  Read the decile ladder. The signal is not clean enough to call.")
        rc = 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    m.to_csv(out, index=False)
    print(f"\nwrote {out}  (the raw per-hitter rows, so this is auditable)")
    print("  READ-ONLY. Nothing in the model was modified; the clamp counter was")
    print("  a pass-through wrapper and has been restored.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
