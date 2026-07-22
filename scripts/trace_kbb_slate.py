#!/usr/bin/env python3
"""
WHY DID THE MEAN HITS PROJECTION *RISE* AFTER THE K/BB FIX?

*** RULE 10: THIS TRACES. IT DOES NOT PRESCRIBE. ***

=============================================================================
THE ANOMALY
=============================================================================
The fitted K/BB model RAISES strikeouts (a league-average starter: 0.1707 ->
0.2157) and RAISES walks (0.0707 -> 0.0830). Both of those REDUCE balls in play:

    bip = 1 - K - BB      0.7586 -> 0.7013

Fewer balls in play must mean FEWER HITS. But on the live 2026-07-12 slate the
mean hits projection went the OTHER WAY:

    mean hits projection   0.8930  ->  0.9429   (+0.0499)

*** THAT IS ARITHMETICALLY BACKWARDS, AND I DO NOT UNDERSTAND IT. ***

=============================================================================
THE HYPOTHESIS I HAVE -- AND WHY I WILL NOT ACT ON IT
=============================================================================
The "0.1707 -> 0.2157" comparison is at the LEAGUE MEAN. The slate is not the
league mean, and more importantly:

  MEASURED (probe_hit_rate_distribution.py): the OLD model's K floor (k_min =
  0.1238) BOUND ON 46.3% OF HITTERS. So the old model's K was not 0.171
  uniformly -- it was PINNED AT THE FLOOR for nearly half the slate, and higher
  for the rest.

  The new model gives every hitter his OWN K rate: ~0.15 for a low-K hitter,
  ~0.30 for a high-K one.

  So for the hitters the old model OVER-struck-out, K goes DOWN -> MORE balls in
  play -> MORE hits. If those hitters outnumber (or outweigh) the ones whose K
  goes up, the SLATE MEAN can rise even though the LEAGUE-MEAN hitter's K rises.

THAT IS A PLAUSIBLE MECHANISM. It is not evidence. I have been wrong four times
today reasoning from mechanism, so this script MEASURES it instead:

  For every hitter, in BOTH arms:
      k_prob, bb_prob, bip_prob, hits/PA, expected hits
  and the DELTA. Then we see EXACTLY which hitters drive the mean, and whether
  the K/BB arithmetic reconciles with the hits change.

=============================================================================
THE RECONCILIATION -- this is what makes the trace conclusive
=============================================================================
hits/PA = bip_prob * xba_contact * hit_scale * hit_rate_scale   (VERIFIED: the
(1 - hr_on_bip) terms cancel EXACTLY -- trace_hit_rate.py reconciled to -0.00036)

So if ONLY K and BB changed, then:
      hits_new / hits_old  ==  bip_new / bip_old       EXACTLY.

*** IF THAT IDENTITY HOLDS, the hits change is FULLY EXPLAINED by K/BB and there
    is no bug -- the mean rose because the old model over-struck-out the
    majority of the slate.
    IF IT DOES NOT HOLD, something ELSE changed, and THAT is the bug. ***

This cannot be fooled by a wrong hypothesis. It is an identity, not a story.

Usage:
    python scripts/trace_kbb_slate.py --date 2026-07-12
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

HIT_KEYS = ("single", "double", "triple", "home_run")


def probe(engine: PropEngine, bundles) -> pd.DataFrame:
    """Read the model's OWN K/BB/hit probabilities for every hitter. No patching."""
    pa_sim = engine.monte_carlo.game_simulator.pa_simulator
    rows = []
    for b in bundles:
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
        k = float(p["strikeout"])
        bb = float(p["walk"])
        rows.append(dict(
            player_id=int(b.hitter.player.mlb_id),
            player_name=b.hitter.player.name,
            k_prob=k, bb_prob=bb,
            bip_prob=1.0 - k - bb,
            hit_rate=float(sum(p[key] for key in HIT_KEYS)),
            expected_pa=float(b.expected_pa),
            own_k_rate=float(b.statcast.k_rate) if b.statcast and b.statcast.k_rate is not None else np.nan,
        ))
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-07-12")
    ap.add_argument("--frozen-config", default="config/config.json")
    ap.add_argument("--cand-config", default="config/config.kbb.json")
    ap.add_argument("--out", default="data/analysis/kbb/slate_trace.csv")
    args = ap.parse_args(argv)

    # Bundles are built ONCE and shared, so the ONLY difference between the arms
    # is the config. If we rebuilt them per arm, a Savant re-pull could shift the
    # runtime-calibrated league baselines and contaminate the comparison.
    cfg_f = RetrainRunner.load_config(args.frozen_config)
    league = LeagueBaselines.from_config(cfg_f)
    api = MLBStatsAPI(season=int(args.date[:4]), config=cfg_f)
    factory = FeatureFactory(config=cfg_f, league_baselines=league, mlb_api=api)
    bundles = factory.build_bundles(args.date)
    if not bundles:
        print(f"FATAL: no bundles for {args.date}", file=sys.stderr)
        return 2
    print(f"[bundles] {len(bundles)} hitters, built ONCE and shared by both arms")

    eng_f = PropEngine(config=cfg_f, league_baselines=league)
    cfg_c = RetrainRunner.load_config(args.cand_config)
    eng_c = PropEngine(config=cfg_c, league_baselines=league)

    fitted = getattr(eng_c.monte_carlo.game_simulator.pa_simulator.config,
                     "use_fitted_kbb", False)
    print(f"[config] candidate use_fitted_kbb = {fitted}")
    if not fitted:
        print("FATAL: the candidate config did not enable use_fitted_kbb. The two\n"
              "  arms would be identical and this trace would measure nothing.",
              file=sys.stderr)
        return 2

    F = probe(eng_f, bundles)
    C = probe(eng_c, bundles)
    m = F.merge(C, on=["player_id", "player_name"], suffixes=("_f", "_c"))

    # =====================================================================
    print()
    print("=" * 84)
    print(f"SLATE MEANS — {args.date}, n = {len(m)} hitters")
    print("=" * 84)
    print(f"  {'':14s} {'FROZEN':>9s} {'FITTED':>9s} {'change':>9s}")
    for lbl, cf, cc in (("K prob", "k_prob_f", "k_prob_c"),
                        ("BB prob", "bb_prob_f", "bb_prob_c"),
                        ("bip prob", "bip_prob_f", "bip_prob_c"),
                        ("hits/PA", "hit_rate_f", "hit_rate_c")):
        a, b = float(m[cf].mean()), float(m[cc].mean())
        print(f"  {lbl:14s} {a:9.4f} {b:9.4f} {b - a:+9.4f}")

    # =====================================================================
    print()
    print("=" * 84)
    print("*** THE IDENTITY *** — if ONLY K/BB changed, then")
    print("      hits_new / hits_old  ==  bip_new / bip_old,  EXACTLY.")
    print("  (hits/PA = bip * xba_contact * hit_scale * hit_rate_scale, and the")
    print("   (1-hr_on_bip) terms cancel -- verified, reconciled to -0.00036)")
    print("=" * 84)
    m["ratio_hits"] = m.hit_rate_c / m.hit_rate_f
    m["ratio_bip"] = m.bip_prob_c / m.bip_prob_f
    m["identity_err"] = m.ratio_hits - m.ratio_bip
    err = float(m.identity_err.abs().max())
    print(f"  max |hits_ratio - bip_ratio| across {len(m)} hitters: {err:.2e}")
    print()
    if err < 1e-6:
        print("  *** THE IDENTITY HOLDS. ***")
        print("  The hits change is FULLY EXPLAINED by the K/BB change. Nothing")
        print("  else moved. There is NO BUG in the patch -- the mean rose because")
        print("  of WHICH hitters changed, not because something is broken.")
    else:
        print("  *** THE IDENTITY IS VIOLATED. SOMETHING ELSE CHANGED. ***")
        print(f"  max error {err:.2e}. The K/BB fix was supposed to touch ONLY the")
        print("  K and BB logits. If hits moved by more (or less) than bip did, then")
        print("  the patch is altering something it should not. THAT is the bug.")
        worst = m.reindex(m.identity_err.abs().sort_values(ascending=False).index)
        print()
        print(worst[["player_name", "ratio_hits", "ratio_bip", "identity_err"]]
              .head(10).to_string(index=False))
        return 1

    # =====================================================================
    print()
    print("=" * 84)
    print("SO WHY DID THE MEAN RISE? — which hitters drove it")
    print("=" * 84)
    m["d_k"] = m.k_prob_c - m.k_prob_f
    m["d_hits"] = m.hit_rate_c - m.hit_rate_f
    up = m[m.d_k < 0]          # K went DOWN -> more BIP -> more hits
    dn = m[m.d_k > 0]          # K went UP   -> fewer BIP -> fewer hits
    print(f"  hitters whose K went DOWN: {len(up):3d}  "
          f"(mean dK {up.d_k.mean():+.4f}, mean d_hits {up.d_hits.mean():+.4f})")
    print(f"  hitters whose K went UP  : {len(dn):3d}  "
          f"(mean dK {dn.d_k.mean():+.4f}, mean d_hits {dn.d_hits.mean():+.4f})")
    print()
    print("  MEASURED (probe_hit_rate_distribution.py): the OLD model's K FLOOR")
    print(f"  (k_min) bound on 46.3% of hitters. So the old model was not")
    print("  producing K = 0.171 uniformly -- it was PINNING nearly half the slate")
    print("  at the floor and OVER-striking-out the rest. Giving every hitter his")
    print("  OWN K rate LOWERS K for the over-struck majority.")

    print()
    print("  BIGGEST K DROPS (their hits RISE):")
    w = m.reindex(m.d_k.sort_values().index)
    print("   ", w[["player_name", "own_k_rate_c", "k_prob_f", "k_prob_c",
                    "hit_rate_f", "hit_rate_c"]].head(8)
          .round(4).to_string(index=False).replace("\n", "\n    "))
    print()
    print("  BIGGEST K RISES (their hits FALL):")
    print("   ", w[["player_name", "own_k_rate_c", "k_prob_f", "k_prob_c",
                    "hit_rate_f", "hit_rate_c"]].tail(8)
          .round(4).to_string(index=False).replace("\n", "\n    "))

    # =====================================================================
    print()
    print("=" * 84)
    print("IS THE NEW SLATE MEAN *RIGHT*?  (the only question that matters)")
    print("=" * 84)
    print(f"  the model's mean hits/PA:  frozen {m.hit_rate_f.mean():.4f}  ->  "
          f"fitted {m.hit_rate_c.mean():.4f}")
    print(f"  REALIZED league hits/PA (starters, 152,683 games): 0.2197")
    print()
    ef = abs(m.hit_rate_f.mean() - 0.2197)
    ec = abs(m.hit_rate_c.mean() - 0.2197)
    print(f"  |error| frozen {ef:.4f}   fitted {ec:.4f}")
    if ec < ef:
        print(f"  -> the fitted model is CLOSER to the realized league rate.")
        print(f"     The mean RISING is not a bug. The old model was too LOW on")
        print(f"     hits/PA for the majority of the slate because it was")
        print(f"     over-striking-them-out.")
    else:
        print(f"  -> the fitted model is FURTHER from the realized rate. That is a")
        print(f"     problem, and the identity holding means it is NOT a patch bug --")
        print(f"     it is the COEFFICIENTS. Read the K distribution above.")
    print()
    print("  NOTE: this slate is not the league. A single day's lineups can differ")
    print("  from the season average. The GATE is what settles it, against real")
    print("  outcomes on 16 dates.")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    m.to_csv(out, index=False)
    print(f"\nwrote {out}  ({len(m)} rows, per-hitter, both arms)")
    print("\n  READ-ONLY. Nothing was modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
