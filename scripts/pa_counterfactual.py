#!/usr/bin/env python3
"""
PA COUNTERFACTUAL: how much of the +6.9pp `P(hits>=1)` bias does the plate-
appearance distribution actually cause?

=============================================================================
THE CLAIM UNDER TEST  (stated so it can FAIL)
=============================================================================
    Substituting the EMPIRICAL PA distribution into the simulator closes the
    +6.9pp gap on P(hits >= 1).

Three outcomes, all decisive:
    bias -> ~0            PA is the cause. Fix _sample_pa_count.
    bias barely moves     PA is NOT the cause. The per-PA HIT RATE is. Different bug.
    bias overshoots < 0   The empirical PA fit is too aggressive; truth is between.

=============================================================================
WHAT IS ALREADY MEASURED  (facts, this session -- not assumptions)
=============================================================================
  * _sample_pa_count emits ONLY floor(expected_pa) and floor+1.
    Verified against the real method, 100k draws per value:
        expected_pa=4.3 -> {4: 0.698, 5: 0.302}.  P(pa<=3) = EXACTLY 0.0000.
  * Simulator PA distribution : {3: 0.070, 4: 0.766, 5: 0.164}
    Reality (out_pa, n=152,683): {1: .072, 2: .030, 3: .107, 4: .543,
                                  5: .231, 6: .017, 7: .001}
    The simulator produces ZERO games at pa<=2 (10.1% of reality) and ZERO at
    pa=6 (1.7% of reality). BOTH tails are missing, not just the left one.
  * Simulator mean_pa 4.095 vs empirical 3.886 -- the model gives every hitter
    ~0.21 EXTRA plate appearances. The mean is too high, not just the shape.
  * The low-PA games are LINEUP REGULARS who got pulled, NOT pinch-hitters:
    every pa<=2 player on 2026-06-27 had a real lineup_slot (1-9).
  * Bias on `hits>=0.5`: +0.1146 all rows, +0.0687 at pa>=2 (DK-gradeable).

=============================================================================
MECHANISM PRE-CHECK  (rule 4, applied to a MEASUREMENT)
=============================================================================
Before trusting this counterfactual on real data, the mechanism was validated on
SYNTHETIC data where the per-PA hit rate is CORRECT BY CONSTRUCTION (0.205 in
both arms, so ANY gap is caused by the PA distribution alone):

    simulator PA dist   mean_pa=4.095   P(hits>=1)=0.6062
    EMPIRICAL PA dist   mean_pa=3.886   P(hits>=1)=0.5763
    gap from PA alone = +0.0299

  ** THE PA DISTRIBUTION EXPLAINS ~44% OF THE MEASURED +0.0687 BIAS. **
  ** THE OTHER ~56% (+0.0388) COMES FROM SOMEWHERE ELSE. **

So this counterfactual is EXPECTED to close roughly HALF the gap, not all of it.
If it closes ALL of it, something is wrong -- probably the empirical PA fit is
absorbing hit-rate error. If it closes NONE of it, the synthetic mechanism check
was wrong and this script is broken. Rule 7: the sanity range is
    expected bias reduction: 0.025 - 0.035   (i.e. +0.0687 -> ~+0.035-0.044)

=============================================================================
THE FIT  (rule 2 -- FITTED, not hand-picked)
=============================================================================
The replacement PA distribution is fitted PER LINEUP SLOT from the training set:
~17,000 rows per slot across 2023-2026 (measured: slot 1 = 17,194 ... slot 9 =
16,818). Mean out_pa decreases monotonically 4.341 (leadoff) -> 3.288 (9-hole).
This is a real empirical distribution, NOT a structural placeholder.

NOT FITTED and NOT USED: any parameter tuned to make the bias go away. We measure
what the empirical distribution does. We do not search for a distribution that
produces a target number -- that is training on the test set.

=============================================================================
WHAT THIS SCRIPT DOES NOT DO
=============================================================================
It does NOT modify the simulator. It monkey-patches _sample_pa_count IN MEMORY
for the counterfactual arm only, runs both arms on the SAME bundles with the SAME
seed, and reports. config/config.json is never touched. Nothing is promoted.

Usage:
    python scripts/pa_counterfactual.py --date 2026-06-16
    python scripts/pa_counterfactual.py --date 2026-06-16 --n-sims 4000
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.learning.retrain_runner import RetrainRunner          # noqa: E402
from src.models.dataclasses import LeagueBaselines             # noqa: E402
from src.prediction.prop_engine import PropEngine              # noqa: E402
from src.features.feature_factory import FeatureFactory        # noqa: E402
from src.data.mlb_api import MLBStatsAPI                       # noqa: E402

# Sanity range, stated BEFORE the run (rule 7). Derived from the synthetic
# mechanism check, NOT from the answer we want.
EXPECTED_BIAS_REDUCTION = (0.020, 0.040)


def fit_pa_distribution(training: Path) -> dict[int, dict[int, float]]:
    """Empirical P(out_pa = k | lineup_slot), fitted from the training set.

    ~17k rows per slot. This is a FIT (rule 2), not a placeholder. Returns
    {slot: {pa: prob}}. Slots with <500 rows are pooled into the league-wide
    distribution rather than fitted on noise.
    """
    tr = pd.read_csv(training, low_memory=False)
    d = tr[(tr.out_pa.notna()) & (tr.out_pa > 0)
           & (tr.lineup_slot.between(1, 9))].copy()
    d["out_pa"] = d["out_pa"].astype(int)
    d["lineup_slot"] = d["lineup_slot"].astype(int)

    pooled = d["out_pa"].value_counts(normalize=True).sort_index().to_dict()

    out: dict[int, dict[int, float]] = {}
    for slot in range(1, 10):
        s = d[d.lineup_slot == slot]
        if len(s) < 500:
            print(f"  [fit] slot {slot}: only {len(s)} rows -- POOLING "
                  f"(too thin to fit; rule 2: do not fit on noise)")
            out[slot] = dict(pooled)
            continue
        out[slot] = s["out_pa"].value_counts(normalize=True).sort_index().to_dict()
    return out


def resolve_slot(bundle) -> int:
    """Get the hitter's lineup slot, or raise.

    RULE 1/9: an earlier draft did `getattr(...) or metadata.get(...) or 5`.
    If BOTH were absent, EVERY hitter would silently get slot 5, the per-slot
    fit would collapse to a single distribution, and the script would report a
    result that LOOKS fine and means nothing. A silent fallback on the join key
    of a fit is not a default -- it is a fabrication. Fail loudly instead.
    """
    for src_name, val in (
        ("hitter.game.batting_order", getattr(getattr(bundle.hitter, "game", None),
                                              "batting_order", None)),
        ("hitter.game.lineup_slot", getattr(getattr(bundle.hitter, "game", None),
                                            "lineup_slot", None)),
        ("metadata['lineup_slot']", (bundle.metadata or {}).get("lineup_slot")),
        ("metadata['batting_order']", (bundle.metadata or {}).get("batting_order")),
    ):
        if val is None:
            continue
        try:
            slot = int(val)
        except (TypeError, ValueError):
            continue
        if 1 <= slot <= 9:
            return slot
    raise LookupError(
        "no lineup slot on this bundle. Tried hitter.game.batting_order, "
        "hitter.game.lineup_slot, metadata['lineup_slot'], "
        "metadata['batting_order']. The PA fit is PER SLOT -- without the slot "
        "the fit cannot be applied, and defaulting to slot 5 would silently "
        "fabricate the join key."
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-06-16",
                    help="a date with confirmed lineups and graded outcomes")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--n-sims", type=int, default=4000)
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--seed", type=int, default=20260711)
    args = ap.parse_args(argv)

    # ---- 1. fit the empirical PA distribution (per lineup slot) ----------
    print("=" * 76)
    print("1. FIT the empirical PA distribution from the training set")
    print("=" * 76)
    pa_dist = fit_pa_distribution(Path(args.training))
    print(f"  {'slot':>4s} {'mean_pa':>8s}  distribution")
    for slot in range(1, 10):
        d = pa_dist[slot]
        mean = sum(k * v for k, v in d.items())
        shown = " ".join(f"{k}:{d[k]:.3f}" for k in sorted(d) if d[k] >= 0.005)
        print(f"  {slot:4d} {mean:8.3f}  {shown}")

    # ---- 2. build the day's bundles -------------------------------------
    config = RetrainRunner.load_config(args.config)
    league = LeagueBaselines.from_config(config)
    mlb_api = MLBStatsAPI(season=int(args.date[:4]), config=config)
    factory = FeatureFactory(config=config, league_baselines=league, mlb_api=mlb_api)
    engine = PropEngine(config=config, league_baselines=league)

    bundles = factory.build_bundles(args.date)
    if not bundles:
        print(f"FATAL: no bundles for {args.date}", file=sys.stderr)
        return 2

    gs = engine.monte_carlo.game_simulator
    orig_sample_pa = gs._sample_pa_count          # keep the real one

    # RULE 5: the patched sampler MUST NOT draw from gs.rng. The shipped
    # _sample_pa_count consumes ONE random() from gs.rng; a choices() call
    # consumes a different number. Sharing the stream would RESEAT every
    # downstream draw and make the two arms differ for reasons that have
    # nothing to do with the PA distribution -- exactly the RNG-reseating bug
    # that bit game_simulator.py earlier today. Give the patch its own Random.
    pa_rng = random.Random(args.seed ^ 0x5A5A)

    # Resolve every slot UP FRONT so a missing lineup_slot fails before we
    # spend minutes simulating (rule 6: cheap check before expensive run).
    try:
        slots = {b.hitter.player.mlb_id: resolve_slot(b) for b in bundles}
    except LookupError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2
    slot_counts = Counter(slots.values())
    print(f"\n  lineup slots resolved: {len(slots)} hitters, "
          f"{len(slot_counts)} distinct slots {dict(sorted(slot_counts.items()))}")
    if len(slot_counts) < 5:
        print("FATAL: fewer than 5 distinct lineup slots. The slot lookup is "
              "probably returning a constant -- the per-slot fit would be "
              "meaningless.", file=sys.stderr)
        return 2

    # ---- 3. run BOTH arms on the SAME bundles, SAME seed -----------------
    def run_arm(label: str, patched: bool) -> dict:
        """Simulate every hitter n_sims times. The ONLY difference between arms
        is _sample_pa_count. Same bundles, same seed, same PA-outcome RNG."""
        rows = []
        pa_seen: Counter[int] = Counter()
        for b in bundles:
            slot = slots[b.hitter.player.mlb_id]

            if patched:
                dist = pa_dist[slot]
                ks = list(dist)
                ws = [dist[k] for k in ks]

                # own RNG -- see the pa_rng note above (rule 5)
                def _patched(_expected_pa, _ks=ks, _ws=ws):
                    return pa_rng.choices(_ks, weights=_ws, k=1)[0]

                gs._sample_pa_count = _patched
            else:
                gs._sample_pa_count = orig_sample_pa

            gs.seed(args.seed)
            sim_in = engine._bundle_to_sim_input(
                b, rich_features=b.metadata.get("rich_features", {}))

            hits = []
            for _ in range(args.n_sims):
                r = gs.simulate_game(sim_in)
                hits.append(r.hits)
                pa_seen[r.plate_appearances] += 1

            h = np.asarray(hits, float)
            rows.append(dict(
                player_id=b.hitter.player.mlb_id,
                slot=slot,
                mean_hits=float(h.mean()),
                p_ge1=float((h >= 1).mean()),
                p_ge2=float((h >= 2).mean()),
            ))
        gs._sample_pa_count = orig_sample_pa      # ALWAYS restore
        tot = sum(pa_seen.values())
        return {"rows": pd.DataFrame(rows),
                "pa_dist": {k: v / tot for k, v in sorted(pa_seen.items())}}

    print()
    print("=" * 76)
    print(f"2. SIMULATE both arms  ({len(bundles)} hitters x {args.n_sims:,} sims)")
    print("=" * 76)
    print("  arm A: FROZEN      (_sample_pa_count as shipped)")
    a = run_arm("frozen", patched=False)
    print("  arm B: EMPIRICAL   (PA drawn from the fitted per-slot distribution)")
    b_ = run_arm("empirical", patched=True)

    print("\n  realised PA distribution:")
    print(f"    {'pa':>3s} {'frozen':>9s} {'empirical':>10s}")
    for k in sorted(set(a["pa_dist"]) | set(b_["pa_dist"])):
        print(f"    {k:3d} {a['pa_dist'].get(k, 0):9.4f} {b_['pa_dist'].get(k, 0):10.4f}")

    # RULE 4: ASSERT the patch took effect. If the monkey-patch silently failed,
    # both arms would be identical and this script would report "reduction ~0 ->
    # hypothesis FALSIFIED" -- a false negative that looks exactly like a real
    # result. The shipped sampler emits ZERO games at pa<=2 (measured); the
    # empirical one MUST emit some.
    frozen_le2 = sum(v for k, v in a["pa_dist"].items() if k <= 2)
    emp_le2 = sum(v for k, v in b_["pa_dist"].items() if k <= 2)
    print(f"\n  P(pa<=2): frozen {frozen_le2:.4f}   empirical {emp_le2:.4f}")
    if emp_le2 < 0.03:
        print("\nFATAL: the EMPIRICAL arm produced almost no pa<=2 games "
              f"({emp_le2:.4f}). The monkey-patch did NOT take effect. Any "
              "'no reduction' verdict below would be a FALSE NEGATIVE.",
              file=sys.stderr)
        return 2
    if frozen_le2 > 0.001:
        print(f"\nWARNING: the FROZEN arm produced pa<=2 games ({frozen_le2:.4f}). "
              "The shipped _sample_pa_count should emit ZERO of these (measured). "
              "Something is off -- read the table above before trusting the verdict.")

    # ---- 4. join to the ACTUAL outcomes ---------------------------------
    tr = pd.read_csv(args.training, low_memory=False)
    act = tr[(tr.game_date == args.date)][["player_id", "out_pa", "out_hits"]].dropna()
    if act.empty:
        print(f"FATAL: no graded outcomes for {args.date} in the training set.",
              file=sys.stderr)
        return 2

    A = a["rows"].merge(act, on="player_id", how="inner")
    B = b_["rows"].merge(act, on="player_id", how="inner")
    if A.empty:
        print("FATAL: no simulated hitter matched an outcome row.", file=sys.stderr)
        return 2

    # ---- 5. THE VERDICT --------------------------------------------------
    print()
    print("=" * 76)
    print("3. VERDICT -- does the empirical PA distribution close the bias?")
    print("=" * 76)
    print(f"  {'arm':22s} {'n':>5s} {'model P(>=1)':>13s} {'actual':>8s} {'BIAS':>9s}")
    print("  " + "-" * 62)

    results = {}
    for tag, D in (("ALL rows", "all"), ("pa>=2 (DK-gradeable)", "grade")):
        for name, F in (("FROZEN", A), ("EMPIRICAL", B)):
            d = F if D == "all" else F[F.out_pa >= 2]
            if len(d) < 20:
                continue
            m = float(d.p_ge1.mean())
            y = float((d.out_hits >= 1).mean())
            results[(tag, name)] = m - y
            print(f"  {name + ' / ' + tag:22s} {len(d):5d} {m:13.4f} {y:8.4f} "
                  f"{m - y:+9.4f}")
        print()

    key = ("pa>=2 (DK-gradeable)", "FROZEN")
    key2 = ("pa>=2 (DK-gradeable)", "EMPIRICAL")
    if key in results and key2 in results:
        before, after = results[key], results[key2]
        reduction = before - after
        print("=" * 76)
        print("  BIAS (gradeable rows):")
        print(f"    FROZEN     {before:+.4f}")
        print(f"    EMPIRICAL  {after:+.4f}")
        print(f"    REDUCTION  {reduction:+.4f}")
        print()
        lo, hi = EXPECTED_BIAS_REDUCTION
        print(f"  SANITY (rule 7, stated BEFORE the run): expected reduction "
              f"{lo:.3f}-{hi:.3f}")
        print(f"  -- from a synthetic mechanism check where the hit rate was CORRECT")
        print(f"     by construction, the PA distribution alone accounted for +0.0299")
        print(f"     of a measured +0.0687 bias, i.e. ~44%.")
        print()
        if reduction < 0.005:
            print("  *** THE PA DISTRIBUTION IS NOT THE CAUSE. ***")
            print("  Swapping it barely moved the bias. The missing-tails hypothesis")
            print("  is FALSIFIED. The per-PA HIT RATE is the bug -- look at")
            print("  hit_rate_scale / xba_on_contact / the Statcast contact model.")
        elif lo <= reduction <= hi:
            print("  *** CONFIRMED, AND AS PREDICTED. ***")
            print(f"  The PA distribution causes ~{100*reduction/max(before,1e-9):.0f}% "
                  f"of the bias -- matching the synthetic mechanism check.")
            print(f"  Fixing _sample_pa_count would leave ~{after:+.4f} residual bias")
            print("  from the per-PA hit rate. BOTH need fixing; this is one of two.")
        elif reduction > hi:
            print("  *** REDUCTION EXCEEDS THE PREDICTED RANGE. ***")
            print("  The empirical PA fit is absorbing MORE than PA error -- it may be")
            print("  soaking up hit-rate bias too. Do NOT ship this as a pure PA fix")
            print("  without understanding why. Rule 7: an out-of-range result means")
            print("  the MEASUREMENT is suspect, not that we got lucky.")
        else:
            print("  *** REDUCTION BELOW THE PREDICTED RANGE. ***")
            print("  Less than the synthetic check implied. Read the realised PA")
            print("  distribution above -- the patch may not be taking effect.")

    print()
    print("=" * 76)
    print("  READ-ONLY. The simulator was monkey-patched IN MEMORY and restored.")
    print("  config/config.json is untouched. Nothing was promoted.")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(main())
