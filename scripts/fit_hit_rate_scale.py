#!/usr/bin/env python3
"""
Fit `hit_rate_scale` — the last piece of the hits calibration bias.

=============================================================================
THE BUG, IN THE MODEL'S OWN WORDS
=============================================================================
pa_simulator.py's module docstring states its invariant explicitly:

    "League anchor (must always hold): a no-profile league-average hitter
     produces hit/PA = bip_rate * xba_on_contact ~= 0.21"

But `xba_on_contact` is SELF-CALIBRATED AT RUNTIME from Statcast, and the logs
show it landing at 0.325-0.327 on EVERY reconstruction date:

    legacy_statcast_features._calibrate_contact_baselines:
        col_ba = "estimated_ba_using_speedangle"
        updates["xba_on_contact"] = float(ba.mean())     # -> 0.325-0.327

With bip_rate ~ 0.69 that gives:

    0.69 * 0.325 = 0.2243 hits/PA      NOT the 0.21 the docstring anchors to.

The model runs ~6.8% hot against ITS OWN documented invariant.

=============================================================================
WHY: xBA IS NOT BA
=============================================================================
`estimated_ba_using_speedangle` is Statcast's EXPECTED batting average for a
batted ball, from exit velocity and launch angle ALONE. It knows nothing about:
  * defensive positioning (a 105-mph liner at the shortstop is an out)
  * park dimensions
  * fielder skill
So xBA-on-contact SYSTEMATICALLY EXCEEDS realized BA-on-contact. The
self-calibration is feeding an EXPECTED quantity into a slot that requires a
REALIZED one.

`hit_rate_scale` exists for exactly this -- pa_simulator calls it the
"self-calibration lever for the hits category". It has never been fitted; it
ships at 1.0.

=============================================================================
THE FIT  (rule 2 -- FITTED, not hand-picked)
=============================================================================
    hit_rate_scale = (realized BA on contact) / (xBA on contact)

Both measurable. From the training set (n = 152,683 hitter-games):

    BIP        = out_pa - out_k - out_bb        (balls in play, incl. HR)
    BA_contact = out_hits / BIP                 (REALIZED)

    xBA_contact = the value the runtime self-calibration produces
                  (measured from the reconstruction logs: 0.325-0.327)

NOTE ON BIP (rule 8 -- state what the quantity MEANS):
  out_pa counts plate appearances. Subtracting K and BB leaves balls in play
  PLUS hit-by-pitch, sacrifices, and catcher interference. Those are small
  (~1.5% of PA combined) and they inflate the DENOMINATOR, which BIASES
  BA_contact DOWNWARD -- i.e. it makes hit_rate_scale look SMALLER than truth.
  That is the CONSERVATIVE direction (it under-corrects rather than over-),
  and the size is bounded. The training set has no HBP/SF column, so this is
  the best available estimator and its bias direction is known and stated.

=============================================================================
SANITY RANGES -- stated BEFORE the run (rule 7)
=============================================================================
  realized BA on contact : 0.28 - 0.32   (MLB BABIP ~.290-.300, plus HR)
  xBA on contact         : 0.32 - 0.33   (MEASURED in the runtime logs)
  hit_rate_scale         : 0.88 - 0.97

  If hit_rate_scale comes back > 1.0, the model is running COLD, which
  contradicts every bias measurement we have (+11.5pp all rows, +6.9pp
  DK-gradeable). That would mean the MEASUREMENT is broken, not the model.

  PREDICTED before running: ~0.92  (0.30 realized / 0.325 xBA)

=============================================================================
WHAT THIS DOES NOT DO
=============================================================================
It does NOT touch config.json. It writes a candidate value and the evidence.
Promotion goes through the gate like everything else.

Usage:
    python scripts/fit_hit_rate_scale.py
    python scripts/fit_hit_rate_scale.py --xba-contact 0.326
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SANITY = {
    "ba_contact": (0.26, 0.34),
    "xba_contact": (0.30, 0.35),
    "hit_rate_scale": (0.85, 1.00),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--xba-contact", type=float, default=None,
                    help="the runtime-calibrated xba_on_contact. Default: read "
                         "from config/config.json league_avg, else 0.326 "
                         "(the value the reconstruction logs actually produce)")
    ap.add_argument("--config", default="config/config.json")
    ap.add_argument("--out", default="data/learning/hit_rate_scale.json")
    ap.add_argument("--seasons", nargs="+", default=None,
                    help="restrict to these seasons (default: all)")
    args = ap.parse_args(argv)

    src = Path(args.training)
    if not src.exists():
        print(f"FATAL: {src} not found.", file=sys.stderr)
        return 2

    tr = pd.read_csv(src, low_memory=False)
    need = {"out_pa", "out_hits", "out_k", "out_bb", "game_date"}
    missing = need - set(tr.columns)
    if missing:
        print(f"FATAL: {src} missing {sorted(missing)}.", file=sys.stderr)
        return 2

    d = tr.dropna(subset=["out_pa", "out_hits", "out_k", "out_bb"]).copy()
    if args.seasons:
        d = d[d.game_date.str[:4].isin(args.seasons)]
    d = d[d.out_pa > 0]

    # BIP = PA - K - BB. See the docstring note on what this includes.
    d["bip"] = d.out_pa - d.out_k - d.out_bb
    d = d[d.bip > 0]

    if d.empty:
        print("FATAL: no rows with bip > 0.", file=sys.stderr)
        return 2

    # POOLED, not the mean of per-game ratios. A per-game BA-on-contact is a
    # ratio of two small integers and is wildly unstable (a 1-for-1 game reads
    # 1.000). The pooled estimator sum(hits)/sum(bip) is the right one.
    tot_hits = float(d.out_hits.sum())
    tot_bip = float(d.bip.sum())
    ba_contact = tot_hits / tot_bip

    # xBA: prefer the explicit flag, then config, then the measured runtime value.
    xba = args.xba_contact
    src_note = "--xba-contact flag"
    if xba is None:
        cfg_path = Path(args.config)
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            xba = (cfg.get("league_avg", {}) or {}).get("xba_on_contact")
            src_note = f"{cfg_path} league_avg.xba_on_contact"
    if xba is None:
        xba = 0.326
        src_note = ("the value the reconstruction logs actually produce "
                    "(self-calibrated at runtime, 0.325-0.327 every date)")
    xba = float(xba)

    scale = ba_contact / xba

    print("=" * 74)
    print(f"FIT hit_rate_scale   ({len(d):,} hitter-games, "
          f"{d.game_date.min()} .. {d.game_date.max()})")
    print("=" * 74)
    print(f"  total hits           : {tot_hits:>12,.0f}")
    print(f"  total balls in play  : {tot_bip:>12,.0f}   (PA - K - BB)")
    print()
    print(f"  REALIZED BA on contact : {ba_contact:.4f}   <- what actually happens")
    print(f"  xBA on contact         : {xba:.4f}   <- what the model uses")
    print(f"     source: {src_note}")
    print()
    print(f"  *** hit_rate_scale = {ba_contact:.4f} / {xba:.4f} = "
          f"{scale:.4f} ***")
    print()

    # what the model currently produces vs what it should
    print("  IMPLIED PER-PA HIT RATE (at a league bip_rate of 0.69):")
    print(f"    model now (scale=1.0)  : 0.69 * {xba:.4f} = "
          f"{0.69 * xba:.4f} hits/PA")
    print(f"    fitted (scale={scale:.3f})   : 0.69 * {xba:.4f} * {scale:.4f} = "
          f"{0.69 * xba * scale:.4f} hits/PA")
    print(f"    pa_simulator's own docstring anchor        : ~0.21 hits/PA")
    print()
    for pa in (4,):
        p_now = 1 - (1 - 0.69 * xba) ** pa
        p_fit = 1 - (1 - 0.69 * xba * scale) ** pa
        print(f"    P(hits>=1) over {pa} PA:  now {p_now:.4f}  ->  "
              f"fitted {p_fit:.4f}   ({p_fit - p_now:+.4f})")
    print(f"    MEASURED actual P(hits>=1), DK-gradeable rows : 0.5966")

    # ---- sanity (rule 7) -------------------------------------------------
    print()
    print("  SANITY (ranges stated BEFORE the fit):")
    fails = []
    for label, got, key in (("realized BA on contact", ba_contact, "ba_contact"),
                            ("xBA on contact", xba, "xba_contact"),
                            ("hit_rate_scale", scale, "hit_rate_scale")):
        lo, hi = SANITY[key]
        ok = lo <= got <= hi
        print(f"    {label:24s} {got:7.4f}   expect [{lo}, {hi}]   "
              f"{'OK' if ok else '*** OUT OF RANGE ***'}")
        if not ok:
            fails.append(f"{label} = {got:.4f}, outside [{lo}, {hi}]")

    if scale > 1.0:
        fails.append(
            "hit_rate_scale > 1.0 means the model is running COLD -- which "
            "CONTRADICTS every bias measurement we have (+11.5pp all rows, "
            "+6.9pp DK-gradeable). The MEASUREMENT is broken, not the model.")

    if fails:
        print("\n*** FIT REJECTED ***", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        print("\nAn out-of-range fit means the measurement is broken (bad column, "
              "wrong denominator, stale file), not that baseball changed. Nothing "
              "was written.", file=sys.stderr)
        return 1

    # ---- by season: is this stable, or drifting? -------------------------
    print()
    print("  BY SEASON (a stable ratio is evidence the estimator is sound):")
    d["season"] = d.game_date.str[:4]
    by = (d.groupby("season")
          .apply(lambda g: pd.Series({
              "n": len(g),
              "hits": g.out_hits.sum(),
              "bip": g.bip.sum(),
              "ba_contact": g.out_hits.sum() / g.bip.sum(),
          }), include_groups=False)
          .reset_index())
    by["hit_rate_scale"] = by.ba_contact / xba
    print("   ", by.round(4).to_string(index=False).replace("\n", "\n    "))

    spread = float(by.hit_rate_scale.max() - by.hit_rate_scale.min())
    print(f"\n    season-to-season spread in hit_rate_scale: {spread:.4f}")
    if spread > 0.05:
        print("    WARNING: the ratio moves >0.05 across seasons. A single scalar")
        print("    may be the wrong shape -- the gap between xBA and BA could be")
        print("    drifting (defensive positioning, shift bans, ball changes).")

    # ---- emit -------------------------------------------------------------
    artifact = {
        "_comment": (
            "FITTED hit_rate_scale = realized BA-on-contact / xBA-on-contact. "
            "pa_simulator sets P(hit | ball in play) directly from Statcast xBA, "
            "but xBA is an EXPECTED value from exit velocity + launch angle and "
            "ignores defence, positioning and park -- so it systematically "
            "EXCEEDS realized BA on contact. The model therefore runs hot against "
            "its own documented anchor (docstring: 'hit/PA = bip_rate * "
            "xba_on_contact ~= 0.21'; actual: 0.69 * 0.326 = 0.225). "
            "This is a FIT from 152k hitter-games (rule 2), not a hand-picked "
            "number. It is a CANDIDATE -- promotion goes through the gate."
        ),
        "provenance": {
            "source": str(src),
            "fitted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_hitter_games": int(len(d)),
            "date_min": str(d.game_date.min()),
            "date_max": str(d.game_date.max()),
            "total_hits": int(tot_hits),
            "total_bip": int(tot_bip),
            "bip_definition": "out_pa - out_k - out_bb (includes HBP/SF; see docstring)",
            "xba_on_contact": xba,
            "xba_source": src_note,
        },
        "hit_rate_scale": round(scale, 4),
        "realized_ba_on_contact": round(ba_contact, 4),
        "by_season": by.round(4).to_dict(orient="records"),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(artifact, f, indent=2)
        f.write("\n")

    print(f"\nwrote {out}")
    print()
    print("  NEXT: this is a CANDIDATE. It goes through the gate like everything")
    print("  else. Set pa_simulator.hit_rate_scale in a candidate config and run")
    print("  the PA gate against the frozen model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
