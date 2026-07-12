#!/usr/bin/env python3
"""
DECOMPOSE the +5.5pp `P(hits >= 1)` bias. Do not GUESS at it.

=============================================================================
WHY THIS SCRIPT EXISTS  (an honest accounting)
=============================================================================
Three hypotheses have been proposed for this bias and all three were wrong or
partial:

  1. OVERDISPERSION      -> the diagnostic was BROKEN. phi = (y-mu)^2/Var(mu)
                            assumed mu was the expectation GIVEN the trial
                            count. It was not (predicted_value is built from
                            PROJECTED pa). phi came back 1668. Junk.
  2. PA DISTRIBUTION      -> REAL, but only 38% of it. Measured: the fitted
                            per-slot PA distribution removes +0.0339 of the
                            +0.0890 bias on DK-gradeable rows.
  3. xBA vs BA (hit rate) -> DEAD. hit_rate_scale fitted to 0.9919, not the
                            0.92 predicted. Realized BA-on-contact is 0.3174
                            vs xBA 0.3200 -- a 0.8% gap, not 6-8%. It moves
                            P(hits>=1) by -0.34pp against a -3.5pp gap. It
                            closes ~10%.

That is a pattern: GUESSING AT MECHANISMS instead of LOCALIZING THE ERROR.

This script does not test a hypothesis. It PARTITIONS the error into its three
possible sources and reports how much each accounts for. It cannot be fooled by
a wrong guess, because it does not make one.

=============================================================================
THE PARTITION  (rule 8 -- state exactly what each quantity means)
=============================================================================
For a hitter-game, P(hits >= 1) is determined by exactly three things:

    A. THE PA COUNT          how many trials he actually got
    B. THE PER-PA HIT RATE   P(hit) on each trial
    C. THE SHAPE             how the trials combine -- INDEPENDENT (binomial)
                             or CORRELATED (overdispersed)

Nothing else. So the bias must live in A, B, C, or some combination.

We measure each by SUBSTITUTION, using the model's OWN simulator:

    baseline    : the simulator as it runs today            -> P_model
    +true PA    : force the simulator to use the ACTUAL out_pa for that game
    +true rate  : force the per-PA hit rate to the REALIZED BA-on-contact
    +both       : both substitutions
    reality     : the actual outcome                        -> P_actual

  gap closed by A alone   = P(+true PA)   - P_model
  gap closed by B alone   = P(+true rate) - P_model
  gap closed by A and B   = P(+both)      - P_model
  RESIDUAL (must be C)    = P_actual      - P(+both)

If the residual is ~0 after forcing the true PA and the true rate, then A and B
explain everything and there is NO shape problem. If a large residual REMAINS
after both are correct, then the SHAPE is wrong BY ELIMINATION -- not by my
say-so, and not because I like the hypothesis.

=============================================================================
WHY SUBSTITUTION AND NOT ALGEBRA
=============================================================================
An algebraic decomposition would need a closed form for what the simulator does.
It does not have one -- it is a Monte Carlo over a logit cascade with clamps, a
hit cap, and a base-state machine. Any closed form I wrote would be a MODEL OF
THE MODEL, and its error would be indistinguishable from the model's error.
So we run the REAL simulator and substitute REAL inputs. Rule 5: stub at the
boundary, never inside.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  P_model      : ~0.63    (MEASURED: 0.6314 at 4 PA, and 0.6856 on gate rows)
  P_actual     : ~0.597   (MEASURED on DK-gradeable rows)
  total gap    : ~-0.035 to -0.09 depending on the row set

  A (PA count) : should close ~35-45%   (the PA counterfactual measured 38%)
  B (hit rate) : should close ~5-15%    (the hit_rate_scale fit implies ~10%)
  A+B together : should close ~45-60%
  C (residual) : ~40-55%   -- IF the shape is the remaining problem

  If A+B close ~100%, the shape is FINE and there is nothing left to fix.
  If A+B close <20%, the partition is BROKEN -- because A and B were each
  MEASURED to close more than that individually, and they cannot both be wrong.

Usage:
    python scripts/decompose_hits_bias.py --date 2026-06-16
    python scripts/decompose_hits_bias.py --date 2026-06-16 --n-sims 4000
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


def resolve_slot(bundle) -> int | None:
    """Lineup slot, or None. NEVER a silent default -- a fabricated slot would
    corrupt the PA substitution."""
    for val in (
        getattr(getattr(bundle.hitter, "game", None), "batting_order", None),
        getattr(getattr(bundle.hitter, "game", None), "lineup_slot", None),
        (bundle.metadata or {}).get("lineup_slot"),
        (bundle.metadata or {}).get("batting_order"),
    ):
        if val is None:
            continue
        try:
            s = int(val)
        except (TypeError, ValueError):
            continue
        if 1 <= s <= 9:
            return s
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-06-16")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--n-sims", type=int, default=4000)
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--seed", type=int, default=20260712)
    ap.add_argument("--void-min-pa", type=int, default=2,
                    help="DK voids a starter's prop at pa==1. Score gradeable "
                         "rows only -- those are the bets that settle.")
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

    # ---- actuals -------------------------------------------------------
    tr = pd.read_csv(args.training, low_memory=False)
    act = tr[tr.game_date == args.date][
        ["player_id", "out_pa", "out_hits", "out_k", "out_bb"]].dropna()
    if act.empty:
        print(f"FATAL: no graded outcomes for {args.date}.", file=sys.stderr)
        return 2
    act = act[act.out_pa > 0]
    act["bip"] = act.out_pa - act.out_k - act.out_bb
    actuals = {int(r.player_id): r for r in act.itertuples()}

    # ---- the REALIZED league per-PA hit rate (rule 2: measured, not chosen) --
    # POOLED over the whole training set, not this one date -- a single slate is
    # far too small to estimate a rate, and using the same date we score on
    # would be fitting to the test set.
    full = tr.dropna(subset=["out_pa", "out_hits", "out_k", "out_bb"])
    full = full[full.out_pa > 0]
    league_hit_per_pa = float(full.out_hits.sum() / full.out_pa.sum())
    print(f"[fit] REALIZED league hit rate = {league_hit_per_pa:.5f} hits/PA "
          f"(pooled over {len(full):,} hitter-games -- NOT this date)")

    gs = engine.monte_carlo.game_simulator
    pa_sim = gs.pa_simulator
    orig_sample_pa = gs._sample_pa_count
    orig_probs = pa_sim.expected_outcome_probabilities

    rng = np.random.default_rng(args.seed)

    # =====================================================================
    # THE FOUR ARMS
    # =====================================================================
    def run(force_pa: bool, force_rate: bool) -> pd.DataFrame:
        """Simulate every hitter. force_pa -> use his ACTUAL out_pa.
        force_rate -> rescale his hit probabilities to the REALIZED league rate.

        RULE 5: we patch at the BOUNDARY (the two functions the game simulator
        calls), never inside the logit cascade. Substituting a real input is a
        measurement; rewriting the internals would be a different model.
        """
        rows = []
        for b in bundles:
            pid = int(b.hitter.player.mlb_id)
            a = actuals.get(pid)
            if a is None:
                continue
            slot = resolve_slot(b)

            # --- patch A: force the TRUE plate-appearance count ------------
            if force_pa:
                true_pa = int(a.out_pa)
                gs._sample_pa_count = lambda _e, _s=None, _p=true_pa: _p
            else:
                gs._sample_pa_count = orig_sample_pa

            # --- patch B: force the TRUE per-PA hit rate -------------------
            if force_rate:
                def patched_probs(*pa_args, _orig=orig_probs, **pa_kw):
                    p = dict(_orig(*pa_args, **pa_kw))
                    hit_keys = ("single", "double", "triple", "home_run")
                    model_hit = sum(p[k] for k in hit_keys)
                    if model_hit <= 1e-9:
                        return p
                    # Rescale the HIT mass to the realized league rate, keeping
                    # the MIX among hit types intact, and dump the difference
                    # into out_on_bip so the distribution still sums to 1.
                    scale = league_hit_per_pa / model_hit
                    for k in hit_keys:
                        p[k] *= scale
                    p["out_on_bip"] = max(
                        0.0, 1.0 - p["strikeout"] - p["walk"]
                        - sum(p[k] for k in hit_keys))
                    return p
                pa_sim.expected_outcome_probabilities = patched_probs
            else:
                pa_sim.expected_outcome_probabilities = orig_probs

            gs.seed(args.seed)
            sim_in = engine._bundle_to_sim_input(
                b, rich_features=b.metadata.get("rich_features", {}))

            hits = np.empty(args.n_sims, dtype=int)
            pas = np.empty(args.n_sims, dtype=int)
            for i in range(args.n_sims):
                r = gs.simulate_game(sim_in)
                hits[i] = r.hits
                pas[i] = r.plate_appearances

            rows.append(dict(
                player_id=pid, slot=slot,
                sim_mean_pa=float(pas.mean()),
                sim_mean_hits=float(hits.mean()),
                sim_p_ge1=float((hits >= 1).mean()),
                out_pa=int(a.out_pa),
                out_hits=int(a.out_hits),
            ))

        gs._sample_pa_count = orig_sample_pa
        pa_sim.expected_outcome_probabilities = orig_probs
        return pd.DataFrame(rows)

    print()
    print("=" * 78)
    print(f"SIMULATING FOUR ARMS  ({len(bundles)} hitters x {args.n_sims:,} sims)")
    print("=" * 78)
    print("  [1/4] baseline     (the simulator as it runs today)")
    base = run(False, False)
    print("  [2/4] +true PA     (forced to the ACTUAL out_pa)")
    a_pa = run(True, False)
    print("  [3/4] +true rate   (hit mass rescaled to the REALIZED league rate)")
    a_rt = run(False, True)
    print("  [4/4] +both")
    a_bo = run(True, True)

    # ---- align on the gradeable rows ------------------------------------
    key = ["player_id"]
    m = (base.rename(columns={"sim_p_ge1": "p_base", "sim_mean_pa": "pa_base",
                              "sim_mean_hits": "h_base"})
         .merge(a_pa[key + ["sim_p_ge1"]].rename(columns={"sim_p_ge1": "p_pa"}), on=key)
         .merge(a_rt[key + ["sim_p_ge1"]].rename(columns={"sim_p_ge1": "p_rt"}), on=key)
         .merge(a_bo[key + ["sim_p_ge1"]].rename(columns={"sim_p_ge1": "p_bo"}), on=key))

    if args.void_min_pa > 0:
        n0 = len(m)
        m = m[m.out_pa >= args.void_min_pa]
        print(f"\n[void] dropped {n0 - len(m)} rows the book would VOID "
              f"(pa < {args.void_min_pa}); {len(m)} gradeable rows remain")

    if len(m) < 30:
        print("FATAL: too few gradeable rows to decompose.", file=sys.stderr)
        return 2

    p_actual = float((m.out_hits >= 1).mean())

    # =====================================================================
    print()
    print("=" * 78)
    print(f"DECOMPOSITION — P(hits >= 1),  {args.date},  n = {len(m)} gradeable")
    print("=" * 78)
    print(f"  {'arm':28s} {'P(hits>=1)':>11s} {'bias':>9s} {'gap closed':>12s}")
    print("  " + "-" * 64)

    p_base = float(m.p_base.mean())
    bias0 = p_base - p_actual

    arms = [
        ("baseline (as it runs)", p_base),
        ("+ TRUE PA count", float(m.p_pa.mean())),
        ("+ TRUE per-PA hit rate", float(m.p_rt.mean())),
        ("+ BOTH", float(m.p_bo.mean())),
    ]
    for name, p in arms:
        bias = p - p_actual
        closed = (bias0 - bias) / bias0 if abs(bias0) > 1e-9 else 0.0
        cs = f"{closed:11.1%}" if name != "baseline (as it runs)" else "          —"
        print(f"  {name:28s} {p:11.4f} {bias:+9.4f} {cs}")
    print(f"  {'ACTUAL':28s} {p_actual:11.4f} {0.0:+9.4f}")

    p_both = float(m.p_bo.mean())
    residual = p_both - p_actual
    closed_both = (bias0 - residual) / bias0 if abs(bias0) > 1e-9 else 0.0

    # ---- did the substitutions actually TAKE? (rule 4) -------------------
    print()
    print("  SUBSTITUTION CHECK -- the patches must have TAKEN EFFECT:")
    pa_err = float((a_pa.sim_mean_pa - a_pa.out_pa).abs().mean())
    print(f"    mean |simulated PA - actual PA| in the +true-PA arm: {pa_err:.4f}")
    print(f"      (MUST be ~0. If not, the PA patch did NOT take, and the")
    print(f"       'gap closed by A' number below is meaningless.)")
    base_pa_err = float((base.sim_mean_pa - base.out_pa).abs().mean())
    print(f"    same in the BASELINE arm (for contrast):            {base_pa_err:.4f}")
    if pa_err > 0.01:
        print("\n    *** FATAL: the PA substitution did not take effect. ***",
              file=sys.stderr)
        return 2

    # =====================================================================
    print()
    print("=" * 78)
    print("VERDICT — where does the bias actually live?")
    print("=" * 78)
    a_only = (bias0 - (float(m.p_pa.mean()) - p_actual)) / bias0 if bias0 else 0
    b_only = (bias0 - (float(m.p_rt.mean()) - p_actual)) / bias0 if bias0 else 0

    print(f"  total bias to explain          : {bias0:+.4f}")
    print()
    print(f"  A. PA COUNT alone closes       : {a_only:6.1%}")
    print(f"  B. PER-PA HIT RATE alone closes: {b_only:6.1%}   <- UPPER BOUND, see below")
    print()
    print("  *** B IS AN UPPER BOUND (rule 8). *** The rate substitution forces the")
    print("  POOLED league rate on EVERY hitter, so it also erases any error in the")
    print("  CROSS-HITTER SPREAD -- not just the level. The true hit-rate")
    print("  contribution is AT MOST this, and probably less. A is exact (it")
    print("  substitutes each hitter's own actual PA count).")
    print()
    print(f"  A + B together close           : {closed_both:6.1%}")
    print(f"  C. RESIDUAL (the SHAPE)        : {residual:+.4f}  "
          f"({1 - closed_both:.1%} of the bias)")
    print()
    print("  reference (stated BEFORE the run):")
    print("    A should close ~35-45%  (the PA counterfactual measured 38%)")
    print("    B should close ~5-15%   (the hit_rate_scale fit implies ~10%)")
    print("    A+B should close ~45-60%")
    print()

    if abs(residual) < 0.005:
        print("  *** A AND B EXPLAIN EVERYTHING. THE SHAPE IS FINE. ***")
        print("  With the true PA count and the true per-PA hit rate, the simulator")
        print("  reproduces reality. There is NO independence/overdispersion problem.")
        print("  Fix A and B and the bias is GONE. Nothing else to find.")
        rc = 0
    elif closed_both < 0.20:
        print("  *** THE PARTITION IS BROKEN. ***")
        print("  A and B were each MEASURED to close more than this individually")
        print("  (38% and ~10%). They cannot both be wrong. Something in the")
        print("  substitution is not doing what it claims. Read the SUBSTITUTION")
        print("  CHECK above before trusting any number here.")
        rc = 2
    else:
        print("  *** A SUBSTANTIAL RESIDUAL SURVIVES BOTH FIXES. ***")
        print(f"  Even with the TRUE PA count AND the TRUE per-PA hit rate, the")
        print(f"  simulator still says {p_both:.4f} where reality says {p_actual:.4f}.")
        print()
        print("  BY ELIMINATION, the SHAPE is wrong. P(hits>=1) is determined by")
        print("  exactly three things -- the trial count, the per-trial rate, and how")
        print("  the trials COMBINE. Two are now correct and the error survives.")
        print()
        print("  The simulator draws each PA INDEPENDENTLY. Real hitters have")
        print("  CORRELATED PAs (the same pitcher, the same stuff, the same weather,")
        print("  the same park, that day). Correlated trials produce MORE zero-hit")
        print("  games than a binomial with the same mean. Under-producing zeros is")
        print("  exactly what inflates P(>=1).")
        print()
        print("  This is NOT a hypothesis I picked -- it is what remains after the")
        print("  other two were measured and substituted out.")
        rc = 1

    print()
    print("  READ-ONLY. The simulator was patched IN MEMORY and restored.")
    print("  config/config.json is untouched.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
