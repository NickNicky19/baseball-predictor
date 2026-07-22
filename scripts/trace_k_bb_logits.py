#!/usr/bin/env python3
"""
TRACE the K and BB logits — TERM BY TERM, PRE-CLAMP.

*** RULE 10: THIS DISTINGUISHES TWO MECHANISMS. IT DOES NOT PRESCRIBE. ***

=============================================================================
WHERE WE ARE  (all measured, none assumed)
=============================================================================
trace_hit_rate.py RECONCILED (err -0.00036) and attributed the model's excess:

    term            ratio    log-share of the +10.7% excess
    bip_prob        1.0994   91.7%   <- THIS ONE
    xba_contact     1.0092    8.9%
    hit_scale       0.9994   -0.6%
    hit_rate_scale  1.0000    0.0%

And bip_prob = 1 - k_prob - bb_prob, so the excess IS a K/BB problem:

                     MODEL    REALIZED (starters)   config league
    K rate          0.1707        0.2234               0.2250
    BB rate         0.0707        0.0846               0.0850
    BIP             0.7586        0.6920               0.6900

*** THE POPULATION-MISMATCH HYPOTHESIS IS DEAD. *** Starting-lineup hitters
strike out 22.34% of the time (n=152,683 games). The config's league value
(0.2250) is almost exactly right FOR STARTERS. The intercept is correct and the
model STILL misses by 5.3 percentage points. Something downstream is driving K
down for everyone.

=============================================================================
THE TWO SURVIVING MECHANISMS  (enumerated, NOT chosen between)
=============================================================================
    k_logit = k_intercept
            + k_pitcher_miss  * P_miss        (=  1.10 * P_miss)
            + k_hitter_contact* H_contact     (= -1.05 * H_contact)
            + k_form          * form          (=  0.30 * form)
            + k_quality       * H_quality     (=  0.08 * H_quality)

    k_intercept = logit(league k_rate) = logit(0.225) = -1.2368
      -> a hitter with EVERY other term at ZERO produces EXACTLY 0.225.
      So any deviation from 0.225 is the SUM OF THE OTHER FOUR TERMS.

  M2 -- THE COEFFICIENTS ARE MIS-SCALED.
       H_contact = (contact_rate - league_contact) * contact_scale,
       and contact_scale = 1/(league_contact * 0.16) ~= 8.0.
       If starters' contact rates run ABOVE the league mean, then
       k_hitter_contact (-1.05) * H_contact (positive) DRIVES K DOWN for
       EVERYONE. A large scale on a small deviation becomes a large logit shift.

  M3 -- THE CLAMP IS DOING THE WORK.
       MEASURED (probe_hit_rate_distribution.py): the K FLOOR binds on 46.3% of
       hitters, the ceiling on 2.2%. k_min = max(0.05, k_rate*0.55) = 0.1238.
       If the RAW sigmoid is going BELOW 0.1238 for half the slate, the model's
       raw K predictions are absurd and the floor is the only thing preventing
       worse. The reported mean (0.1707) would then be a FLOOR ARTEFACT, not the
       model's actual belief.

*** THESE ARE DISTINGUISHABLE, AND THE DISTINCTION MATTERS. ***
  If M2: the raw (pre-clamp) K rate is ~0.17 and the clamp rarely saves it. The
         COEFFICIENTS are wrong.
  If M3: the raw K rate is far BELOW 0.17 -- maybe 0.10 -- and the clamp is
         hauling it up. The MODEL is producing nonsense and the clamp is hiding
         how bad it is.
  They need DIFFERENT fixes and this script tells them apart.

=============================================================================
HOW  (rule 8 -- a logit is a SUM, so it partitions EXACTLY)
=============================================================================
We capture each ADDEND of k_logit and bb_logit per hitter, PRE-SIGMOID and
PRE-CLAMP. Because a logit is additive, the contribution of each term is exact
-- no approximation, no attribution heuristic.

Then:
    sigmoid(k_intercept)                  = 0.225 by construction
    sigmoid(k_intercept + sum(others))    = the RAW model K
    clamp(that, k_min, k_max)             = the REPORTED K

The gap between RAW and REPORTED is exactly what the clamp is doing.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  sigmoid(k_intercept)  : 0.2250  (EXACT -- it is logit(0.225) by construction.
                                   If this is not 0.225, from_league is broken.)
  reported mean K       : 0.1707  (MEASURED)
  raw mean K            : UNKNOWN -- this is the number that decides M2 vs M3
  mean H_contact        : UNKNOWN -- if it is a large POSITIVE number, M2 is live

  If sigmoid(k_intercept) != 0.225, STOP. The intercept is not what the code
  claims and every attribution below it is void.

Usage:
    python scripts/trace_k_bb_logits.py --date 2026-06-16
"""
from __future__ import annotations

import argparse
import math
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


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-06-16")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--out", default="data/analysis/k_bb_logit_trace.csv")
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

    pa_sim: HybridPASimulator = engine.monte_carlo.game_simulator.pa_simulator
    cfg = pa_sim.config
    lg = pa_sim.league

    # ---- HARD CHECK (rule 7): the intercept MUST reproduce the league rate --
    k_from_intercept = sigmoid(cfg.k_intercept)
    bb_from_intercept = sigmoid(cfg.bb_intercept)
    print("=" * 82)
    print("INTERCEPT CHECK — a hitter with every other term at ZERO")
    print("=" * 82)
    print(f"  k_intercept  = {cfg.k_intercept:+.5f}  -> sigmoid = "
          f"{k_from_intercept:.5f}   (league k_pct/100 = {lg.k_pct/100:.5f})")
    print(f"  bb_intercept = {cfg.bb_intercept:+.5f}  -> sigmoid = "
          f"{bb_from_intercept:.5f}   (league bb_pct/100 = {lg.bb_pct/100:.5f})")
    ok_k = abs(k_from_intercept - lg.k_pct / 100.0) < 0.002
    ok_bb = abs(bb_from_intercept - lg.bb_pct / 100.0) < 0.002
    if not (ok_k and ok_bb):
        print("\n  *** THE INTERCEPT DOES NOT REPRODUCE THE LEAGUE RATE. ***",
              file=sys.stderr)
        print("  PASimulatorConfig.from_league sets k_intercept = logit(k_rate),\n"
              "  so sigmoid(k_intercept) MUST equal the league rate. It does not.\n"
              "  Either from_league did not run, or an override replaced the\n"
              "  intercept. STOP -- every attribution below this is void.",
              file=sys.stderr)
        return 2
    print("\n  OK -- the intercepts reproduce the league rates exactly. So any")
    print("  deviation from them is the SUM OF THE OTHER TERMS, and a logit is")
    print("  ADDITIVE, so those terms partition the deviation EXACTLY.")

    # ---- capture every ADDEND, per hitter --------------------------------
    orig_latent = HybridPASimulator._build_latent_profile
    cap: dict = {}

    def spy(self, *a, **kw):
        out = orig_latent(self, *a, **kw)
        cap["latent"] = dict(out)
        return out

    HybridPASimulator._build_latent_profile = spy

    rows = []
    try:
        for b in bundles:
            cap.clear()
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
            lat = cap["latent"]

            # rebuild the logits from the SAME formula the model uses
            k_terms = {
                "k_intercept": cfg.k_intercept,
                "k_pitcher_miss": cfg.k_pitcher_miss * lat["P_miss"],
                "k_hitter_contact": cfg.k_hitter_contact * lat["H_contact"],
                "k_form": cfg.k_form * lat["form"],
                "k_quality": cfg.k_quality * lat["H_quality"],
            }
            bb_terms = {
                "bb_intercept": cfg.bb_intercept,
                "bb_pitcher_control": cfg.bb_pitcher_control * lat["P_control"],
                "bb_hitter_contact": cfg.bb_hitter_contact * lat["H_contact"],
                "bb_handedness": cfg.bb_handedness * lat["handedness"],
            }
            k_logit = sum(k_terms.values())
            bb_logit = sum(bb_terms.values())
            k_raw = sigmoid(k_logit)
            bb_raw = sigmoid(bb_logit)

            rows.append(dict(
                player_id=int(b.hitter.player.mlb_id),
                player_name=b.hitter.player.name,
                **{f"kt_{k}": v for k, v in k_terms.items()},
                **{f"bt_{k}": v for k, v in bb_terms.items()},
                k_logit=k_logit, bb_logit=bb_logit,
                k_raw=k_raw, bb_raw=bb_raw,
                k_reported=float(p["strikeout"]),
                bb_reported=float(p["walk"]),
                H_contact=float(lat["H_contact"]),
                H_quality=float(lat["H_quality"]),
                P_miss=float(lat["P_miss"]),
                P_control=float(lat["P_control"]),
                form=float(lat["form"]),
                contact_rate=float(b.statcast.contact_rate)
                    if b.statcast.contact_rate is not None else np.nan,
            ))
    finally:
        HybridPASimulator._build_latent_profile = orig_latent

    m = pd.DataFrame(rows)

    # ---- HARD CHECK: our rebuilt logit must reproduce the model's output ---
    err = float((m.k_raw.clip(cfg.k_min, cfg.k_max) - m.k_reported).abs().max())
    print()
    print(f"  [verify] max |clamp(our k_raw) - model's k| = {err:.2e}")
    if err > 1e-9:
        print("  *** OUR REBUILT LOGIT DOES NOT REPRODUCE THE MODEL. ***\n"
              "  A term is missing from the reconstruction. STOP.", file=sys.stderr)
        return 2
    print("  OK -- our term-by-term rebuild reproduces the model EXACTLY, so the")
    print("  attribution below is arithmetic, not an estimate.")

    # =====================================================================
    print()
    print("=" * 82)
    print("*** M2 vs M3 — IS THE CLAMP DOING THE WORK? ***")
    print("=" * 82)
    k_raw_mean = float(m.k_raw.mean())
    k_rep_mean = float(m.k_reported.mean())
    n_floored = int((m.k_raw < cfg.k_min).sum())
    n_ceiled = int((m.k_raw > cfg.k_max).sum())

    print(f"  k_min = {cfg.k_min:.4f}   k_max = {cfg.k_max:.4f}")
    print()
    print(f"  {'':22s} {'K':>9s} {'BB':>9s}")
    print(f"  {'league (= intercept)':22s} {lg.k_pct/100:9.4f} {lg.bb_pct/100:9.4f}")
    print(f"  {'RAW model (pre-clamp)':22s} {k_raw_mean:9.4f} "
          f"{float(m.bb_raw.mean()):9.4f}")
    print(f"  {'REPORTED (post-clamp)':22s} {k_rep_mean:9.4f} "
          f"{float(m.bb_reported.mean()):9.4f}")
    print(f"  {'REALIZED (starters)':22s} {0.2234:9.4f} {0.0846:9.4f}")
    print()
    print(f"  hitters at the K FLOOR   : {n_floored}/{len(m)} "
          f"({n_floored/len(m):.1%})")
    print(f"  hitters at the K CEILING : {n_ceiled}/{len(m)} "
          f"({n_ceiled/len(m):.1%})")
    print(f"  the clamp MOVED the mean by: {k_rep_mean - k_raw_mean:+.4f}")
    print()
    print(f"  RAW K distribution: min {m.k_raw.min():.4f}  "
          f"p10 {m.k_raw.quantile(.10):.4f}  median {m.k_raw.median():.4f}  "
          f"p90 {m.k_raw.quantile(.90):.4f}  max {m.k_raw.max():.4f}")

    # =====================================================================
    print()
    print("=" * 82)
    print("K LOGIT — TERM BY TERM  (a logit is a SUM, so this is EXACT)")
    print("=" * 82)
    print(f"  {'term':22s} {'mean':>9s} {'sd':>9s} {'min':>9s} {'max':>9s}")
    print("  " + "-" * 62)
    kt_cols = [c for c in m.columns if c.startswith("kt_")]
    for c in kt_cols:
        print(f"  {c[3:]:22s} {m[c].mean():9.4f} {m[c].std():9.4f} "
              f"{m[c].min():9.4f} {m[c].max():9.4f}")
    print("  " + "-" * 62)
    print(f"  {'k_logit (sum)':22s} {m.k_logit.mean():9.4f} {m.k_logit.std():9.4f} "
          f"{m.k_logit.min():9.4f} {m.k_logit.max():9.4f}")
    print()
    non_int = [c for c in kt_cols if c != "kt_k_intercept"]
    shift = float(m[non_int].sum(axis=1).mean())
    print(f"  mean shift OFF the intercept: {shift:+.4f} logits")
    print(f"    intercept alone  -> K = {sigmoid(cfg.k_intercept):.4f}")
    print(f"    + the mean shift -> K = {sigmoid(cfg.k_intercept + shift):.4f}")
    print()
    print(f"  WHICH TERM CARRIES THAT SHIFT?")
    print(f"  {'term':22s} {'mean logit':>11s} {'share of shift':>16s}")
    for c in non_int:
        mv = float(m[c].mean())
        sh = mv / shift if abs(shift) > 1e-9 else 0.0
        bar = "#" * min(40, int(abs(sh) * 40))
        print(f"  {c[3:]:22s} {mv:+11.4f} {sh:>15.1%}  {bar}")

    # =====================================================================
    print()
    print("=" * 82)
    print("BB LOGIT — TERM BY TERM")
    print("=" * 82)
    bt_cols = [c for c in m.columns if c.startswith("bt_")]
    print(f"  {'term':22s} {'mean':>9s} {'sd':>9s}")
    for c in bt_cols:
        print(f"  {c[3:]:22s} {m[c].mean():9.4f} {m[c].std():9.4f}")
    non_int_bb = [c for c in bt_cols if c != "bt_bb_intercept"]
    shift_bb = float(m[non_int_bb].sum(axis=1).mean())
    print()
    print(f"  mean shift OFF the intercept: {shift_bb:+.4f} logits")
    print(f"    intercept alone  -> BB = {sigmoid(cfg.bb_intercept):.4f}")
    print(f"    + the mean shift -> BB = {sigmoid(cfg.bb_intercept + shift_bb):.4f}")

    # =====================================================================
    print()
    print("=" * 82)
    print("H_contact — the latent BOTH logits share")
    print("=" * 82)
    print(f"  contact_scale = {cfg.contact_scale:.3f}  "
          f"(= 1 / (league_contact * 0.16))")
    print(f"  league contact_rate = {lg.contact_rate:.4f}")
    print()
    print(f"  H_contact = (contact_rate - {lg.contact_rate:.4f}) * "
          f"{cfg.contact_scale:.3f}")
    print(f"    mean   {m.H_contact.mean():+.4f}")
    print(f"    sd     {m.H_contact.std():.4f}")
    print(f"    min    {m.H_contact.min():+.4f}")
    print(f"    max    {m.H_contact.max():+.4f}")
    print()
    print(f"  raw contact_rate on this slate: mean {m.contact_rate.mean():.4f}, "
          f"sd {m.contact_rate.std():.4f}")
    print(f"  -> a hitter 1 SD above the mean in contact gets a K-logit shift of")
    print(f"     {cfg.k_hitter_contact:.2f} * {m.H_contact.std():.3f} = "
          f"{cfg.k_hitter_contact * m.H_contact.std():+.4f}")
    print()
    print("  H_contact appears in BOTH the K logit (coef "
          f"{cfg.k_hitter_contact:+.2f}) and")
    print(f"  the BB logit (coef {cfg.bb_hitter_contact:+.2f}). BOTH are NEGATIVE,")
    print("  so a positive H_contact pushes K DOWN *and* BB DOWN -- and BOTH of")
    print("  those RAISE bip_prob, which RAISES hits. The two errors COMPOUND.")

    # =====================================================================
    print()
    print("=" * 82)
    print("VERDICT — M2 or M3?  (rule 10: this NAMES the mechanism, not the fix)")
    print("=" * 82)
    clamp_share = abs(k_rep_mean - k_raw_mean) / abs(k_rep_mean - lg.k_pct / 100.0) \
        if abs(k_rep_mean - lg.k_pct / 100.0) > 1e-9 else 0.0
    print(f"  the model's K is {lg.k_pct/100 - k_rep_mean:+.4f} BELOW league.")
    print(f"  of that gap, the CLAMP accounts for {clamp_share:.1%}")
    print()
    if n_floored / len(m) > 0.30 and (k_rep_mean - k_raw_mean) > 0.01:
        print("  *** M3: THE CLAMP IS DOING THE WORK. ***")
        print(f"  {n_floored/len(m):.0%} of hitters have a RAW K below the floor, and the")
        print(f"  clamp is hauling the mean UP by {k_rep_mean - k_raw_mean:+.4f}.")
        print("  The model's RAW K predictions are worse than the reported ones --")
        print("  the floor is HIDING how bad they are. The reported 0.1707 is a")
        print("  FLOOR ARTEFACT, not the model's belief.")
        print()
        print("  A clamp that binds on a third of the population is not a safety")
        print("  net. It is load-bearing, and it is masking the real defect.")
        rc = 0
    elif abs(k_rep_mean - k_raw_mean) < 0.005:
        print("  *** M2: THE COEFFICIENTS ARE WRONG. THE CLAMP IS INCIDENTAL. ***")
        print(f"  The clamp moves the mean by only {k_rep_mean - k_raw_mean:+.4f}.")
        print("  The RAW logit itself produces a K rate far below league. The K")
        print("  term table above shows WHICH addend carries the shift.")
        rc = 0
    else:
        print("  *** BOTH ARE IN PLAY. ***")
        print(f"  The clamp moves the mean by {k_rep_mean - k_raw_mean:+.4f} AND the raw")
        print(f"  logit is already off by {k_raw_mean - lg.k_pct/100:+.4f}. Read the term")
        print("  table: the coefficients are wrong AND the clamp is masking part of it.")
        rc = 0

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    m.to_csv(out, index=False)
    print(f"\nwrote {out}  ({len(m)} rows, per-hitter, every addend)")
    print()
    print("  *** RULE 10: EVIDENCE, NOT A VERDICT ON WHAT TO DO. ***")
    print("  This says WHICH mechanism and WHICH term. It does not say what the")
    print("  coefficient SHOULD be -- that is a FIT, and a fit needs its own")
    print("  measurement against data, not a number I choose.")
    print()
    print("  READ-ONLY. The spy was a pass-through and has been restored.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
