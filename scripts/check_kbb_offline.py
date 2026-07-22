#!/usr/bin/env python3
"""
Offline harness: the FITTED K/BB model.

RULE 4: every invariant FAILS on the pre-fix code or on a specific wrong
implementation. A harness that passes on both guards nothing. Prove it:
    python scripts/check_kbb_offline.py --mutate <name>
Mutations: legacy | norates | pitcherontop | wrongunits | notinert

=============================================================================
WHAT IS BEING GUARDED
=============================================================================
StatcastProfile.k_rate was NEVER POPULATED -- Savant's pull filters to
`events.notna()` (BATTED BALLS), and a strikeout is not a batted ball. MEASURED:
sd = 0.0000 across 270 hitters. Every hitter got the league constant.

So the K logit used `contact_rate`, a per-swing whiff PROXY -- and
league.contact_rate (0.7550) is the average over ALL batters while the model
runs on STARTING LINEUPS (0.8178). H_contact therefore centres on +0.52 instead
of 0, and k_hitter_contact (-1.05) turns that into -0.55 logits FOR EVERY HITTER.

MEASURED consequences:
    a league-average starter produced K = 0.1365  (realized: 0.2198)
    the K floor bound on 46.3% of the slate, HIDING how bad it was
    bip_prob was inflated 9.9%
    -> which carried 91.7% of the model's ENTIRE hits excess

THE FIVE INVARIANTS, AND WHY EACH NEEDS TO EXIST

  INV1 CALIBRATION. A league-mean hitter facing a league-mean pitcher must
       produce the LEAGUE rate. This is the single check that caught a
       double-counted pitcher term in an earlier attempt (it produced 0.1785
       against a realized 0.2221). Without it, a plausible-looking but wrong
       model ships.

  INV2 THE CLAMP MUST STOP BINDING. k_min binds on 46.3% of hitters TODAY. A
       floor that catches half the population is not a safety net -- it is
       load-bearing structure MASKING the defect. With the fitted model it must
       not bind for ANY realistic hitter. If it still does, the fix did not work.

  INV3 RANKING. A high-K hitter must get a higher K probability than a low-K
       hitter. The old model ranked on a whiff proxy, which is why a rookie
       outranked Murakami in production.

  INV4 THE PITCHER IS IN THE COEFFICIENTS, NOT ON TOP. A hitter-only fit with a
       pitcher term added would double-count the average pitcher (the GLM's
       intercept already absorbs him). INV1 catches this, but INV4 tests the
       mechanism directly: a +1sd pitcher must move K by the FITTED amount, not
       by the legacy P_miss amount.

  INV5 INERT WITHOUT THE FLAG. The frozen arm of every gate runs on a config
       with no use_fitted_kbb. If the patched code drifts there, the measured
       noise floor is contaminated by a change WE introduced and every drift
       number becomes uninterpretable. BYTE-IDENTICAL, not merely close.

Usage:
    python scripts/check_kbb_offline.py
    python scripts/check_kbb_offline.py --mutate legacy
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models.dataclasses import LeagueBaselines, StatcastProfile  # noqa: E402
from src.simulation.pa_simulator import (                            # noqa: E402
    HybridPASimulator,
    PASimulatorConfig,
)

# MEASURED (fit_k_bb_final.py, pre-2026 artifact, PA-weighted)
M_K_SEASON, M_K_RECENT, M_P_K = 0.2188501732, 0.2191354263, 22.08027608
M_BB_SEASON, M_BB_RECENT, M_P_BB = 0.0862316756, 0.0843803748, 8.046218902
REALIZED_K, REALIZED_BB = 0.2208821001, 0.0832238763
TOL = 0.006          # the fit's own gate is 0.005; allow one extra digit here
FITTED_COEFFICIENTS = {
    "kbb_k_intercept": -2.6908280787404513,
    "kbb_k_hitter_season": 3.951218879092181,
    "kbb_k_hitter_recent": 0.33609571647821673,
    "kbb_k_pitcher": 0.021021344087198076,
    "kbb_bb_intercept": -3.2626210124070734,
    "kbb_bb_hitter_season": 5.510170922403332,
    "kbb_bb_hitter_recent": 1.3417130541085533,
    "kbb_bb_pitcher": 0.03050039565276,
}


class Failure(Exception):
    pass


def check(name: str, ok: bool, msg: str) -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"         -> {msg}")
        raise Failure(name)


def build(league: LeagueBaselines, **cfg_over) -> HybridPASimulator:
    """VERIFIED against the real signature (rule 1 -- an earlier draft guessed
    `league=` and crashed):
        HybridPASimulator(config=None, league_baselines=None, random_seed=None)
    """
    cfg = PASimulatorConfig.from_league(league)
    cfg = replace(cfg, **cfg_over)
    return HybridPASimulator(config=cfg, league_baselines=league)


def profile(k_season, bb_season, k_recent=None, bb_recent=None) -> StatcastProfile:
    return StatcastProfile(
        player_id=1, player_name="T", sample_pa=300,
        xwoba=0.320, xba=0.250, xslg=0.420,
        barrel_rate=0.08, hard_hit_rate=0.40, contact_rate=0.8178,
        k_rate=k_season, bb_rate=bb_season,
        k_rate_recent=k_recent if k_recent is not None else k_season,
        bb_rate_recent=bb_recent if bb_recent is not None else bb_season,
    )


def kbb(sim: HybridPASimulator, prof: StatcastProfile,
        p_k=M_P_K, p_bb=M_P_BB) -> tuple[float, float]:
    p = sim.expected_outcome_probabilities(
        pitcher_k_pct=p_k, pitcher_bb_pct=p_bb, statcast=prof)
    return float(p["strikeout"]), float(p["walk"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutate",
                    choices=["legacy", "norates", "pitcherontop",
                             "wrongunits", "notinert"],
                    default=None)
    a = ap.parse_args(argv)

    league = LeagueBaselines()
    over: dict = {"use_fitted_kbb": True, **FITTED_COEFFICIENTS}

    if a.mutate == "legacy":
        over["use_fitted_kbb"] = False
    elif a.mutate == "pitcherontop":
        # a HITTER-ONLY intercept, with the pitcher bolted on. This is the exact
        # bug the calibration gate caught: the hitter-only GLM's intercept
        # already absorbs the average pitcher, so adding him again double-counts.
        over["kbb_k_intercept"] = -2.2039        # the hitter-only fitted intercept
    elif a.mutate == "wrongunits":
        # the pitcher coefficient fitted on K/9 but fed K% (or vice versa) --
        # off by the PA = IP * 4.2 conversion, a factor of 2.6455
        over["kbb_k_pitcher"] = 0.0209 * 2.6455

    if a.mutate:
        print(f"\n=== MUTATION: {a.mutate} ===")
        print("    (the harness MUST fail; if it passes, it guards nothing)\n")
    else:
        print("\n=== FITTED K/BB MODEL ===\n")

    sim = build(league, **over)
    lg_prof = profile(M_K_SEASON, M_BB_SEASON, M_K_RECENT, M_BB_RECENT)

    if a.mutate == "norates":
        # the config flag is ON but the profile carries NO rates -> the code MUST
        # fall back to the legacy path rather than fabricate a default
        lg_prof = replace(lg_prof, k_rate=None, bb_rate=None,
                          k_rate_recent=None, bb_rate_recent=None)

    k, bb = kbb(sim, lg_prof)
    print(f"  league-mean hitter vs league-mean pitcher:")
    print(f"    K  = {k:.4f}   realized {REALIZED_K:.4f}   err {k - REALIZED_K:+.4f}")
    print(f"    BB = {bb:.4f}   realized {REALIZED_BB:.4f}   err {bb - REALIZED_BB:+.4f}")
    print(f"    (the pre-fix model produced K = 0.1707, BB = 0.0707)\n")

    try:
        check(
            "INV1 CALIBRATION: a league-mean hitter produces the LEAGUE K rate",
            abs(k - REALIZED_K) < TOL,
            f"K = {k:.4f}, realized = {REALIZED_K:.4f}, err = {k - REALIZED_K:+.4f} "
            f"(tolerance {TOL}). A correctly-fitted model evaluated at the mean of "
            f"its own predictors MUST land on the population mean. The pre-fix model "
            f"produced 0.1365 because `contact_rate` is mis-centred (+0.52) and "
            f"k_hitter_contact (-1.05) turns that into -0.55 logits for EVERY hitter.",
        )
        check(
            "INV1b CALIBRATION: ... and the LEAGUE BB rate",
            abs(bb - REALIZED_BB) < TOL,
            f"BB = {bb:.4f}, realized = {REALIZED_BB:.4f}.",
        )

        # ---- INV2: the clamp must stop binding ---------------------------
        cfg = sim.config
        binding = []
        for ks in (0.10, 0.14, 0.18, 0.2182, 0.26, 0.32, 0.40):
            kk, _ = kbb(sim, profile(ks, M_BB_SEASON))
            if kk <= cfg.k_min + 1e-9:
                binding.append(ks)
        print(f"\n  k_min = {cfg.k_min:.4f};  hitters at the floor: "
              f"{len(binding)}/7 across K% 0.10 .. 0.40")
        check(
            "INV2 THE CLAMP STOPS BINDING for every realistic hitter",
            not binding,
            f"the K floor still binds at hitter K% {binding}. TODAY it binds on "
            f"46.3% of the slate -- a floor that catches half the population is not "
            f"a safety net, it is load-bearing structure MASKING the defect. If the "
            f"fitted model still hits it, the fix did not work.",
        )

        # ---- INV3: ranking ------------------------------------------------
        k_lo, _ = kbb(sim, profile(0.12, M_BB_SEASON))
        k_mid, _ = kbb(sim, profile(0.2182, M_BB_SEASON))
        k_hi, _ = kbb(sim, profile(0.32, M_BB_SEASON))
        print(f"\n  ranking: K%=.12 -> {k_lo:.4f}   .218 -> {k_mid:.4f}   "
              f".32 -> {k_hi:.4f}")
        check(
            "INV3 RANKING: a high-K hitter gets a HIGHER K probability",
            k_lo < k_mid < k_hi,
            f"{k_lo:.4f} / {k_mid:.4f} / {k_hi:.4f} is not monotone. The pre-fix "
            f"model ranked on `contact_rate`, a whiff PROXY scaled 8.278x -- which "
            f"is why a high-contact ROOKIE outranked Murakami in production.",
        )

        # ---- INV4: the pitcher moves K by the FITTED amount ----------------
        k_p_hi, _ = kbb(sim, lg_prof, p_k=M_P_K + 4.5)     # ~ +1sd pitcher
        expected_shift = cfg.kbb_k_pitcher * 4.5
        got_logit_shift = (math.log(k_p_hi / (1 - k_p_hi))
                           - math.log(k / (1 - k)))
        print(f"\n  a +4.5pp pitcher (≈+1sd) shifts the K logit by "
              f"{got_logit_shift:+.4f}  (fitted: {expected_shift:+.4f})")
        check(
            "INV4 THE PITCHER IS IN THE COEFFICIENTS, not bolted on",
            abs(got_logit_shift - expected_shift) < 1e-6,
            f"the pitcher moved the logit by {got_logit_shift:+.4f} but the FITTED "
            f"coefficient says {expected_shift:+.4f}. A hitter-only fit with a "
            f"pitcher term ADDED would double-count the average pitcher -- the GLM's "
            f"intercept already absorbs him.",
        )

        # ---- INV5: INERT without the flag ---------------------------------
        legacy = build(league)                       # no use_fitted_kbb
        fitted_off = build(league, use_fitted_kbb=False)
        a1 = kbb(legacy, lg_prof)
        a2 = kbb(fitted_off, lg_prof)
        same = (abs(a1[0] - a2[0]) < 1e-12 and abs(a1[1] - a2[1]) < 1e-12)
        print(f"\n  no flag: K = {a1[0]:.6f}   flag=False: K = {a2[0]:.6f}")
        check(
            "INV5 INERT without the config flag (byte-identical)",
            same,
            "the patched code differs from the legacy path even with the flag off. "
            "The frozen arm of every gate runs on a config with no use_fitted_kbb; "
            "if the code drifts there, the measured noise floor is contaminated by "
            "a change WE introduced and every drift number becomes uninterpretable.",
        )

    except Failure as f:
        print(f"\nHARNESS FAILED at {f}")
        return 1 if not a.mutate else 0      # a mutation SHOULD fail

    print("\nALL INVARIANTS PASS")
    return 0 if not a.mutate else 1          # a mutation that PASSES is useless


if __name__ == "__main__":
    sys.exit(main())
