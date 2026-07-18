#!/usr/bin/env python3
"""
TRACE: the model produces MORE hits than its own formula says it should.

*** RULE 10: THIS TRACES. IT DOES NOT PRESCRIBE. ***
It reconciles the model against its OWN documented identity, term by term, and
reports which term breaks it. It names no culprit and proposes no fix. Naming a
mechanism from a single number is exactly the error that has cost this
investigation three wrong hypotheses.

=============================================================================
THE CONTRADICTION -- and it is INTERNAL, not model-vs-reality
=============================================================================
pa_simulator.py's module docstring states an invariant:

    "League anchor (must always hold): a no-profile league-average hitter
     produces hit/PA = bip_rate * xba_on_contact ~= 0.21"

MEASURED (probe_hit_rate_distribution.py, 2026-06-16, 246 hitters):
    the model's actual mean hit rate    : 0.2459 hits/PA
    its own formula (0.69 * 0.326)      : 0.2249 hits/PA
    REALIZED league rate                : 0.2197 hits/PA

  The model exceeds ITS OWN FORMULA by +0.021 hits/PA. That is a discrepancy
  INSIDE the model, not between the model and the world. It has a definite
  answer sitting in the code.

  (The formula ALSO exceeds reality by +0.005 -- a separate, smaller question.
   This trace is about the +0.021.)

AND A SECOND SYMPTOM, PROBABLY THE SAME CAUSE:
    Spearman rank corr (model vs realized hit rate) : +0.33
    model SD / noise-corrected TALENT SD            : 1.92
  The model spreads hitters out AGGRESSIVELY while ordering them POORLY. In the
  live 2026-07-12 slate its top hits projections are rookies and fringe players
  (A.J. Ewing, Luis Lara, Anthony Seigler) while Ohtani and Freeman -- both in
  confirmed lineups -- are nowhere near the top.

  Wide spread + weak ordering is the signature of a model amplifying NOISE
  rather than SIGNAL. Whether that is the same defect as the +0.021 is exactly
  what this trace is for.

=============================================================================
THE ALGEBRA IS NOT THE BUG  (already verified -- rule 9, do not re-litigate)
=============================================================================
    target       = xba_contact * hit_scale * hit_rate_scale
    p_hit_non_hr = (target - hr_on_bip) / (1 - hr_on_bip)
    hits/PA      = non_hr_bip * p_hit_non_hr + hr_prob
                 = bip*(1-hr_on_bip) * (target - hr_on_bip)/(1-hr_on_bip)
                   + bip*hr_on_bip
                 = bip*(target - hr_on_bip) + bip*hr_on_bip
                 = bip * target

  The (1 - hr_on_bip) terms CANCEL EXACTLY. Traced numerically: +0.0000
  difference. So GIVEN ITS INPUTS the code produces exactly bip * target.

  *** THEREFORE THE DISCREPANCY IS IN THE INPUTS, NOT THE ARITHMETIC. ***
  hits/PA = bip_prob * xba_contact * hit_scale * hit_rate_scale
  and the identity only holds if:
        bip_prob    == 0.69   (the league value)
        xba_contact == 0.326  (the league value)
        hit_scale   == 1.0
        hit_rate_scale == 1.0
  This script measures EACH of those four, per hitter, and reports which one
  breaks it.

=============================================================================
THE FOUR CANDIDATE TERMS  (rule 10 -- enumerated, NOT chosen between)
=============================================================================
  T1 bip_prob      = 1 - k_prob - bb_prob.
       MEASURED: the K clamp FLOOR binds on 46.3% of hitters and the BB floor
       on 20%. Both RAISE their probability, which LOWERS bip, which LOWERS
       hits. That pushes the WRONG WAY -- so T1 alone cannot explain a model
       that is too HIGH. But it must be measured, not assumed away.

  T2 xba_contact   = weight*player_xba + (1-weight)*league_xba,
                     weight = sample_pa/(sample_pa + 120)
       league_xba (0.326) is the average over ALL batted balls -- including
       pitchers hitting, bench players, September call-ups. But the model runs
       on STARTING LINEUPS, which are SELECTED for hitting ability. If the mean
       starter's xBA exceeds the league mean, the shrunk value does too.

  T3 hit_scale     = _context_hit_scale(park_hits_factor, bvp_ops_factor),
                     clamped to [0.88, 1.12]
       If park/BvP factors skew above 1.0 systematically, this multiplies up.

  T4 hit_rate_scale = 1.0 (never fitted; ships at the default)

  ALL FOUR ARE MULTIPLICATIVE. Their product IS the model's hit rate relative to
  the anchor. That is why this reconciles EXACTLY rather than approximately --
  and why the trace cannot be fooled by a wrong guess.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  bip_prob    : 0.66 - 0.72   (league 0.69)
  xba_contact : 0.320 - 0.345 (league anchor 0.326; starters should be HIGHER)
  hit_scale   : 0.98 - 1.03   (park/BvP roughly cancel across a slate)
  product     : the model's mean hit rate / 0.2249 = 0.2459/0.2249 = 1.093

  *** The four terms MUST multiply to the observed 1.093. If they do not, the
  TRACE is broken -- not the model. That is a hard reconciliation check and it
  is the whole point of doing it this way. ***

Usage:
    python scripts/trace_hit_rate.py --date 2026-06-16
    python scripts/trace_hit_rate.py --date 2026-07-12   (the live slate)
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
    ap.add_argument("--out", default="data/analysis/hit_rate_trace.csv")
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
    # The LEAGUE the simulator actually holds -- NOT the config's static value.
    # legacy_statcast_features._calibrate_contact_baselines REPLACES it at
    # runtime from the Statcast pull. Reading config.json here would be reading
    # a number the model does not use.
    lg = pa_sim.league
    print(f"[league] the simulator's LIVE league baselines (runtime-calibrated):")
    print(f"           xba_on_contact  = {lg.xba_on_contact:.4f}")
    print(f"           xwoba_on_contact= {lg.xwoba_on_contact:.4f}")
    print(f"           k_pct           = {lg.k_pct:.2f}")
    print(f"           bb_pct          = {lg.bb_pct:.2f}")
    print(f"         (config.json says xba_on_contact = "
          f"{config.get('league_avg', {}).get('xba_on_contact')} -- if these "
          f"DIFFER, the runtime value is the one that matters)")

    # ---- capture EVERY intermediate, per hitter. No patching of behaviour. --
    orig_latent = HybridPASimulator._build_latent_profile
    orig_bipdist = HybridPASimulator._bip_outcome_distribution
    orig_ctxscale = HybridPASimulator._context_hit_scale

    captured: dict = {}

    def spy_latent(self, *a, **kw):
        out = orig_latent(self, *a, **kw)
        captured["latent"] = dict(out)
        return out

    def spy_ctxscale(self, park_hits_factor, bvp_ops_factor):
        out = orig_ctxscale(self, park_hits_factor, bvp_ops_factor)
        captured["hit_scale"] = float(out)
        captured["park_hits_factor"] = float(park_hits_factor)
        captured["bvp_ops_factor"] = float(bvp_ops_factor)
        return out

    def spy_bipdist(self, latent, hr_on_bip, park_hits_factor=1.0,
                    bvp_ops_factor=1.0):
        out = orig_bipdist(self, latent, hr_on_bip,
                           park_hits_factor, bvp_ops_factor)
        captured["hr_on_bip"] = float(hr_on_bip)
        return out

    HybridPASimulator._build_latent_profile = spy_latent
    HybridPASimulator._bip_outcome_distribution = spy_bipdist
    HybridPASimulator._context_hit_scale = spy_ctxscale

    rows = []
    try:
        for b in bundles:
            captured.clear()
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
            lat = captured.get("latent", {})
            k_prob, bb_prob = float(p["strikeout"]), float(p["walk"])
            bip = 1.0 - k_prob - bb_prob
            model_hit = float(sum(p[k] for k in HIT_KEYS))

            sample_pa = float(b.statcast.sample_pa or 0.0)
            own_w = sample_pa / (sample_pa + max(cfg.xba_shrinkage_pa, 1.0))

            rows.append(dict(
                player_id=int(b.hitter.player.mlb_id),
                player_name=b.hitter.player.name,
                slot=int(b.hitter.lineup_slot),
                # THE FOUR TERMS
                bip_prob=bip,
                xba_contact=float(lat.get("xba_contact", np.nan)),
                hit_scale=float(captured.get("hit_scale", np.nan)),
                hit_rate_scale=float(cfg.hit_rate_scale),
                # the pieces that build them
                k_prob=k_prob, bb_prob=bb_prob,
                hr_on_bip=float(captured.get("hr_on_bip", np.nan)),
                raw_xba=float(b.statcast.xba) if b.statcast.xba is not None else np.nan,
                sample_pa=sample_pa,
                own_weight=own_w,
                park_hits_factor=float(captured.get("park_hits_factor", np.nan)),
                bvp_ops_factor=float(captured.get("bvp_ops_factor", np.nan)),
                # the answer
                model_hit_rate=model_hit,
            ))
    finally:
        HybridPASimulator._build_latent_profile = orig_latent
        HybridPASimulator._bip_outcome_distribution = orig_bipdist
        HybridPASimulator._context_hit_scale = orig_ctxscale

    m = pd.DataFrame(rows)
    anchor = lg.k_pct, lg.bb_pct
    lg_bip = 1.0 - lg.k_pct / 100.0 - lg.bb_pct / 100.0
    lg_anchor_rate = lg_bip * lg.xba_on_contact

    # =====================================================================
    print()
    print("=" * 82)
    print(f"TERM-BY-TERM RECONCILIATION — {args.date}, n = {len(m)} hitters")
    print("=" * 82)
    print("  hits/PA = bip_prob * xba_contact * hit_scale * hit_rate_scale")
    print("  (the (1 - hr_on_bip) terms CANCEL EXACTLY -- verified numerically)")
    print()
    print(f"  {'term':18s} {'LEAGUE':>10s} {'MODEL mean':>11s} {'ratio':>8s}")
    print("  " + "-" * 52)
    terms = [
        ("bip_prob", lg_bip, float(m.bip_prob.mean())),
        ("xba_contact", lg.xba_on_contact, float(m.xba_contact.mean())),
        ("hit_scale", 1.0, float(m.hit_scale.mean())),
        ("hit_rate_scale", 1.0, float(m.hit_rate_scale.mean())),
    ]
    product = 1.0
    for name, lgv, mv in terms:
        r = mv / lgv if lgv else float("nan")
        product *= r
        print(f"  {name:18s} {lgv:10.4f} {mv:11.4f} {r:8.4f}")
    print("  " + "-" * 52)
    print(f"  {'PRODUCT of ratios':18s} {'':10s} {'':11s} {product:8.4f}")

    implied = lg_anchor_rate * product
    actual = float(m.model_hit_rate.mean())
    print()
    print(f"  league anchor rate  = {lg_bip:.4f} * {lg.xba_on_contact:.4f} "
          f"= {lg_anchor_rate:.5f} hits/PA")
    print(f"  anchor * product    = {implied:.5f} hits/PA   <- what the terms predict")
    print(f"  ACTUAL model mean   = {actual:.5f} hits/PA   <- what it produces")
    print(f"  reconciliation err  = {actual - implied:+.5f}")

    # ---- HARD CHECK (rule 7): the terms MUST reconcile ------------------
    if abs(actual - implied) > 0.004:
        print()
        print("  *** THE TRACE DOES NOT RECONCILE. ***", file=sys.stderr)
        print(f"  The four terms predict {implied:.5f} but the model produces "
              f"{actual:.5f}.", file=sys.stderr)
        print("  Either a term is missing, or a CLAMP is binding and truncating the", file=sys.stderr)
        print("  product (the clamps are NOT multiplicative). Check the clamp table", file=sys.stderr)
        print("  below before trusting ANY attribution.", file=sys.stderr)
    else:
        print()
        print("  RECONCILED. The four terms fully account for the model's hit rate.")

    # ---- WHICH TERM CARRIES THE ERROR? ---------------------------------
    print()
    print("=" * 82)
    print("ATTRIBUTION — how much of the excess does each term carry?")
    print("=" * 82)
    excess = actual / lg_anchor_rate
    print(f"  the model runs {excess:.4f}x its own league anchor "
          f"({100*(excess-1):+.1f}%)")
    print()
    print(f"  {'term':18s} {'ratio':>8s} {'log-share of the excess':>26s}")
    print("  " + "-" * 56)
    logs = {name: np.log(mv / lgv) for name, lgv, mv in terms if lgv}
    tot_log = sum(logs.values())
    for name, lgv, mv in terms:
        r = mv / lgv
        share = (logs[name] / tot_log) if abs(tot_log) > 1e-12 else 0.0
        bar = "#" * int(abs(share) * 40)
        print(f"  {name:18s} {r:8.4f} {share:>13.1%}  {bar}")
    print()
    print("  (log-shares are ADDITIVE for a product, so they partition the excess")
    print("   exactly. A term with a ratio of 1.000 contributes nothing.)")

    # ---- the pieces behind the biggest term -----------------------------
    print()
    print("=" * 82)
    print("THE PIECES BEHIND EACH TERM  (evidence, NOT a verdict -- rule 10)")
    print("=" * 82)

    print(f"\n  T1 bip_prob = 1 - k_prob - bb_prob")
    print(f"     k_prob : model {m.k_prob.mean():.4f}  vs league "
          f"{lg.k_pct/100:.4f}   ({m.k_prob.mean()/(lg.k_pct/100):.3f}x)")
    print(f"     bb_prob: model {m.bb_prob.mean():.4f}  vs league "
          f"{lg.bb_pct/100:.4f}   ({m.bb_prob.mean()/(lg.bb_pct/100):.3f}x)")
    print(f"     -> bip : model {m.bip_prob.mean():.4f}  vs league {lg_bip:.4f}")

    print(f"\n  T2 xba_contact = w*player_xba + (1-w)*league_xba,  "
          f"w = sample_pa/(sample_pa+{cfg.xba_shrinkage_pa:.0f})")
    print(f"     league_xba (the shrink TARGET) : {lg.xba_on_contact:.4f}")
    print(f"     mean RAW player xba            : {m.raw_xba.mean():.4f}")
    print(f"     mean SHRUNK xba_contact        : {m.xba_contact.mean():.4f}")
    print(f"     mean weight on own xba         : {m.own_weight.mean():.3f}")
    print(f"     mean sample_pa                 : {m.sample_pa.mean():.0f}")
    print()
    print(f"     *** The shrink TARGET is the league average over ALL batted")
    print(f"     balls. But this slate is STARTING LINEUPS -- selected for")
    print(f"     hitting ability. If mean RAW player xba ({m.raw_xba.mean():.4f})")
    print(f"     exceeds the league target ({lg.xba_on_contact:.4f}), the shrunk")
    print(f"     value does too, and every hitter is pulled toward a number that")
    print(f"     is WRONG FOR THIS POPULATION. ***")
    print(f"     gap: {m.raw_xba.mean() - lg.xba_on_contact:+.4f}")

    print(f"\n  T3 hit_scale = f(park_hits_factor, bvp_ops_factor), clamped "
          f"[{cfg.context_hit_scale_min}, {cfg.context_hit_scale_max}]")
    print(f"     mean park_hits_factor : {m.park_hits_factor.mean():.4f}")
    print(f"     mean bvp_ops_factor   : {m.bvp_ops_factor.mean():.4f}")
    print(f"     mean hit_scale        : {m.hit_scale.mean():.4f}")

    print(f"\n  T4 hit_rate_scale = {cfg.hit_rate_scale} (config; never fitted)")

    # ---- the SPREAD question -------------------------------------------
    print()
    print("=" * 82)
    print("THE SPREAD — the model orders hitters POORLY (rho=+0.33) but spreads")
    print("them WIDELY (1.9x talent sd). Which term carries the SPREAD?")
    print("=" * 82)
    print(f"  {'term':18s} {'sd':>9s} {'cv (sd/mean)':>14s}")
    for name in ("bip_prob", "xba_contact", "hit_scale", "model_hit_rate"):
        s, mu = float(m[name].std()), float(m[name].mean())
        print(f"  {name:18s} {s:9.5f} {s/mu if mu else 0:14.4f}")
    print()
    print("  A term with a LARGE coefficient of variation is doing most of the")
    print("  spreading. If that term is NOT the one that correlates with real")
    print("  hitting ability, the model is spreading on NOISE.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    m.to_csv(out, index=False)
    print(f"\nwrote {out}  ({len(m)} rows -- per-hitter, auditable)")
    print()
    print("  *** RULE 10: THIS IS EVIDENCE, NOT A VERDICT. ***")
    print("  The attribution table says WHICH TERM carries the excess. It does")
    print("  NOT say why that term is wrong, and it does NOT prescribe a fix.")
    print("  Read it, then decide what to measure NEXT.")
    print()
    print("  READ-ONLY. The three spies were pass-through wrappers and have been")
    print("  restored. Nothing in the model was modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
