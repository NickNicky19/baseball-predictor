#!/usr/bin/env python3
"""
IS THE xba_on_contact GAP REAL, NOW THAT THE K/BB CONFOUND IS GONE?

*** RULE 10: THIS MEASURES. IT DOES NOT PRESCRIBE. ***

=============================================================================
WHY THIS QUESTION IS DIFFERENT NOW
=============================================================================
scripts/fit_hit_rate_scale.py already asked this and got:

    realized BA on contact : 0.3174
    xBA on contact         : 0.3200   (config)  /  0.326 (RUNTIME-calibrated)
    -> hit_rate_scale      : 0.9919

and I called it a dead end: a 0.8% correction that closes ~10% of the bias.

*** BUT THAT FIT WAS CONFOUNDED, AND I DID NOT SEE IT. ***

    hits/PA = bip_prob * xba_contact * hit_scale * hit_rate_scale

At the time, bip_prob was ALSO wrong -- inflated 9.9% by the K/BB defect
(K = 0.1707 against a realized 0.2198). So the model had TWO errors:

    bip too HIGH   (+9.9%)     <- the K/BB bug
    xba maybe HIGH (+2.7%?)    <- this question

and the hits/PA error was their PRODUCT. Fixing one WITHOUT measuring the other
is how a "small" correction gets dismissed.

MEASURED, right now, after the K/BB fix (trace_kbb_slate.py, 2026-07-12):

    model mean hits/PA   frozen 0.2260  ->  FITTED 0.2384
    REALIZED (starters, 152,683 games)              0.2197

*** THE FITTED MODEL IS NOW +1.9pp HIGH ON hits/PA -- WORSE THAN FROZEN. ***
And the identity  hits_new/hits_old == bip_new/bip_old  held to 5.55e-17, so
that is NOT a patch bug. The K/BB fix removed one error and LEFT THE OTHER ONE
NAKED. Two errors that partially cancelled are now one error, visible.

=============================================================================
WHAT THIS MEASURES  (rule 8 -- state what each quantity IS)
=============================================================================
  xba_on_contact  = Statcast's estimated_ba_using_speedangle, averaged over
                    BATTED BALLS. An EXPECTED value from exit velocity and
                    launch angle ALONE -- it knows nothing about defensive
                    positioning, park dimensions, or fielder skill. A 105-mph
                    liner at the shortstop has a high xBA and is an out.

  REALIZED BA on contact = out_hits / (out_pa - out_k - out_bb)
                    What ACTUALLY HAPPENED, from the box scores.

  The model uses xBA where it needs BA. If xBA systematically EXCEEDS realized
  BA, every hitter's hit rate is too high -- and with the K/BB error gone, that
  is now the whole remaining bias.

  NOTE (rule 8, the same caveat as before): PA - K - BB also contains HBP and
  sacrifices (~1.5% of PA). Those inflate the DENOMINATOR, so realized BA reads
  LOW and the correction reads SMALL -- the CONSERVATIVE direction. The training
  set has no HBP/SF column; this is the best available estimator and its bias
  direction is known and stated.

=============================================================================
THE THING THAT MAKES THIS FIT DIFFERENT FROM THE LAST ONE
=============================================================================
The previous fit compared a POOLED realized BA against a POOLED xBA. This one
ALSO checks the SLATE-LEVEL identity:

    predicted hits/PA = bip_prob * xba_contact
    realized  hits/PA = out_hits / out_pa

If the model's bip_prob is now CORRECT (it should be, after the K/BB fix) then
ANY remaining hits/PA error MUST be in xba_contact. That is arithmetic, not
inference -- and it is the check the previous fit did not do.

=============================================================================
SANITY -- stated BEFORE the run (rule 7)
=============================================================================
  realized BA on contact : 0.30 - 0.33   (MLB BABIP ~.290-.300, plus HR)
  xBA on contact         : 0.32 - 0.33   (MEASURED in the runtime logs: 0.326)
  implied hit_rate_scale : 0.94 - 1.00

  If the scale comes back > 1.0, the model runs COLD on contact -- which would
  CONTRADICT the +1.9pp hits/PA excess we just measured. That would mean the
  MEASUREMENT is broken, not the model.

Usage:
    python scripts/probe_xba_gap.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# MEASURED in every reconstruction log this session. The RUNTIME-calibrated
# value, NOT config.json's static 0.3200 -- legacy_statcast_features
# ._calibrate_contact_baselines REPLACES self.league at runtime, so this is what
# the simulator ACTUALLY uses. Reading config would be reading a number the model
# does not use.
XBA_RUNTIME = 0.326

# MEASURED (fit_k_bb_final.py, on the rows the GLM saw)
REALIZED_K, REALIZED_BB = 0.2198, 0.0841


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--xba", type=float, default=XBA_RUNTIME,
                    help="the RUNTIME-calibrated xba_on_contact the simulator "
                         "actually uses (config.json's static value is NOT it)")
    args = ap.parse_args(argv)

    tr = pd.read_csv(args.training, low_memory=False)
    d = tr.dropna(subset=["out_pa", "out_hits", "out_k", "out_bb"])
    d = d[(d.out_pa > 0) & (d.lineup_slot.between(1, 9))].copy()
    d["bip"] = d.out_pa - d.out_k - d.out_bb
    d = d[d.bip > 0]

    tot_pa = float(d.out_pa.sum())
    tot_hits = float(d.out_hits.sum())
    tot_bip = float(d.bip.sum())
    tot_k = float(d.out_k.sum())
    tot_bb = float(d.out_bb.sum())

    ba_contact = tot_hits / tot_bip
    hits_per_pa = tot_hits / tot_pa
    bip_per_pa = tot_bip / tot_pa

    print("=" * 84)
    print(f"THE xba GAP — {len(d):,} starter-games, "
          f"{d.game_date.min()} .. {d.game_date.max()}")
    print("=" * 84)
    print(f"  REALIZED, from the box scores:")
    print(f"    K per PA          : {tot_k/tot_pa:.4f}")
    print(f"    BB per PA         : {tot_bb/tot_pa:.4f}")
    print(f"    bip per PA        : {bip_per_pa:.4f}")
    print(f"    BA on contact     : {ba_contact:.4f}   <- what ACTUALLY happens")
    print(f"    hits per PA       : {hits_per_pa:.4f}")
    print()
    print(f"  the model uses xba_on_contact = {args.xba:.4f}  "
          f"(RUNTIME-calibrated, not config's 0.3200)")
    print(f"    gap: {args.xba - ba_contact:+.4f}   "
          f"({100*(args.xba/ba_contact - 1):+.2f}% relative)")

    # =====================================================================
    print()
    print("=" * 84)
    print("*** THE DECOMPOSITION THE PREVIOUS FIT DID NOT DO ***")
    print("=" * 84)
    print("  hits/PA = bip_prob * xba_contact")
    print()
    print("  With the K/BB fix, bip_prob should now be RIGHT. So any remaining")
    print("  hits/PA error MUST be in xba_contact. That is ARITHMETIC.")
    print()

    # what the model produces, with the FITTED K/BB
    bip_fitted = 1.0 - REALIZED_K - REALIZED_BB      # the fitted model reproduces these
    hits_fitted = bip_fitted * args.xba

    # what it SHOULD produce
    hits_should = bip_per_pa * ba_contact

    print(f"  {'':26s} {'bip':>9s} {'xba':>9s} {'hits/PA':>9s}")
    print(f"  {'FITTED model':26s} {bip_fitted:9.4f} {args.xba:9.4f} "
          f"{hits_fitted:9.4f}")
    print(f"  {'REALIZED':26s} {bip_per_pa:9.4f} {ba_contact:9.4f} "
          f"{hits_should:9.4f}")
    print(f"  {'error':26s} {bip_fitted-bip_per_pa:+9.4f} "
          f"{args.xba-ba_contact:+9.4f} {hits_fitted-hits_should:+9.4f}")
    print()
    print(f"  bip error   : {100*(bip_fitted/bip_per_pa - 1):+.2f}%   "
          f"<- the K/BB fix closed this")
    print(f"  xba error   : {100*(args.xba/ba_contact - 1):+.2f}%   "
          f"<- THIS IS WHAT REMAINS")
    print(f"  hits error  : {100*(hits_fitted/hits_should - 1):+.2f}%")

    # =====================================================================
    print()
    print("=" * 84)
    print("WHY THE PREVIOUS FIT DISMISSED THIS")
    print("=" * 84)
    bip_old = 1.0 - 0.1707 - 0.0707        # the PRE-FIX model's K and BB
    hits_old = bip_old * args.xba
    print(f"  BEFORE the K/BB fix the model had TWO errors:")
    print(f"    bip  {bip_old:.4f} vs realized {bip_per_pa:.4f}   "
          f"({100*(bip_old/bip_per_pa-1):+.2f}%)")
    print(f"    xba  {args.xba:.4f} vs realized {ba_contact:.4f}   "
          f"({100*(args.xba/ba_contact-1):+.2f}%)")
    print(f"    -> hits/PA {hits_old:.4f} vs realized {hits_should:.4f}   "
          f"({100*(hits_old/hits_should-1):+.2f}%)")
    print()
    print(f"  BOTH errors pushed hits UP, so they COMPOUNDED -- and the xba error")
    print(f"  looked small next to the bip error. `hit_rate_scale` came back 0.9919")
    print(f"  and I called it a dead end. It was not a dead end. It was MASKED.")
    print()
    print(f"  With bip FIXED, the xba error is now the WHOLE remaining bias.")

    # =====================================================================
    print()
    print("=" * 84)
    print("THE IMPLIED CORRECTION  (rule 2 -- FITTED, not chosen)")
    print("=" * 84)
    scale = ba_contact / args.xba
    print(f"  hit_rate_scale = realized_BA / xBA = {ba_contact:.4f} / "
          f"{args.xba:.4f} = {scale:.4f}")
    print()
    print(f"  with that scale, the FITTED model would produce:")
    hits_corrected = bip_fitted * args.xba * scale
    print(f"    hits/PA = {bip_fitted:.4f} * {args.xba:.4f} * {scale:.4f} = "
          f"{hits_corrected:.4f}")
    print(f"    realized                                        = "
          f"{hits_should:.4f}")
    print(f"    error   = {hits_corrected - hits_should:+.4f}  "
          f"({100*(hits_corrected/hits_should-1):+.2f}%)")
    print()
    for pa in (4,):
        p_now = 1 - (1 - hits_fitted) ** pa
        p_fix = 1 - (1 - hits_corrected) ** pa
        p_real = 1 - (1 - hits_should) ** pa
        print(f"  P(hits>=1) over {pa} PA:")
        print(f"    fitted K/BB only  {p_now:.4f}")
        print(f"    + xba correction  {p_fix:.4f}")
        print(f"    REALIZED          {p_real:.4f}")

    # ---- sanity (rule 7) -------------------------------------------------
    print()
    print("  SANITY (stated BEFORE the run):")
    fails = []
    for lbl, got, lo, hi in (("realized BA on contact", ba_contact, 0.30, 0.33),
                             ("xBA on contact", args.xba, 0.32, 0.33),
                             ("implied hit_rate_scale", scale, 0.94, 1.00)):
        ok = lo <= got <= hi
        print(f"    {lbl:24s} {got:7.4f}   expect [{lo}, {hi}]   "
              f"{'OK' if ok else '*** OUT OF RANGE ***'}")
        if not ok:
            fails.append(lbl)
    if scale > 1.0:
        fails.append("scale > 1.0 would mean the model runs COLD on contact, "
                     "which CONTRADICTS the +1.9pp hits excess we just measured")

    print()
    print("=" * 84)
    print("VERDICT")
    print("=" * 84)
    if fails:
        print(f"  *** OUT OF RANGE: {fails} ***")
        print("  An out-of-range value means the MEASUREMENT is broken, not that")
        print("  baseball changed. Do not act on this.")
        return 1

    print(f"  The xba gap is {100*(args.xba/ba_contact - 1):+.2f}% and it is now")
    print(f"  the WHOLE remaining hits bias -- the K/BB fix removed the error that")
    print(f"  was masking it.")
    print()
    print(f"  *** RULE 10: THIS IS NOT A PRESCRIPTION. ***")
    print(f"  The correction is a SINGLE SCALAR ({scale:.4f}) applied to every")
    print(f"  hitter. Whether that is the right SHAPE is a separate question this")
    print(f"  script cannot answer: the xBA-vs-BA gap may VARY by hitter (a")
    print(f"  fast runner beats out more infield hits than his xBA implies; a")
    print(f"  slow slugger fewer). A scalar assumes it does not.")
    print()
    print(f"  fit_hit_rate_scale.py's BY-SEASON table already hinted at this: the")
    print(f"  ratio moved 0.0281 across seasons (2023: 1.0090, 2026: 0.9809).")
    print(f"  A single scalar may be the wrong shape. MEASURE the per-hitter gap")
    print(f"  before shipping one.")
    print()
    print(f"  AND: the K/BB fix has not passed its gate yet. Do not stack a second")
    print(f"  change on an unverified first one (rule 6, and the PA fix's lesson --")
    print(f"  one change, one gate, one verdict).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
