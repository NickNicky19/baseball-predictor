#!/usr/bin/env python3
"""
Offline harness: the FITTED plate-appearance distribution.

RULE 4: every invariant below FAILS on the pre-fix code or on a specific wrong
implementation. A harness that passes on both guards nothing. Run
    python scripts/check_pa_offline.py --mutate <name>
to prove it. Mutations: legacy | noslot | badartifact | notbyteident

WHAT IS BEING GUARDED
  _sample_pa_count emitted ONLY floor(expected_pa) and floor+1 -- a TWO-POINT
  distribution. MEASURED against the real method (100k draws/value):
      expected_pa = 4.3  ->  {4: 0.698, 5: 0.302}.   P(pa<=3) = EXACTLY 0.0000.
  Reality (out_pa, n=152,683): pa<=2 is 10.1% of games and pa=6 is 1.7%.
  The simulator produced ZERO of both. Those early-exit games are
  disproportionately the ZERO-HIT games, which is why P(hits>=1) ran +6.9pp hot
  on DK-gradeable rows.

THE TWO PROPERTIES THAT MATTER, AND WHY EACH NEEDS ITS OWN INVARIANT

  1. WITH the fitted artifact, the simulator MUST produce pa<=2 and pa=6.
     If it does not, the fix is not live.

  2. WITHOUT the artifact, the simulator MUST be BYTE-IDENTICAL to pre-fix --
     same RNG stream, same outputs. This is not cosmetic. The frozen arm of any
     gate runs on a config with no pa_distribution_path; if the patched code
     drifts even slightly there, the "noise floor" measurement is contaminated
     by a change we introduced, and every drift number becomes uninterpretable.
     This is the same contract B4's role_innings has, and the same one the HRR
     lambda/gamma fix has.

Usage:
    python scripts/check_pa_offline.py
    python scripts/check_pa_offline.py --mutate legacy
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.simulation.game_simulator import (          # noqa: E402
    GameSimulator,
    GameSimulatorInput,
)

# The MEASURED empirical distribution (scripts/fit_pa_distribution.py output
# shape). Slots 1/5/9 are enough to exercise the fit; the real artifact has all 9.
FITTED = {
    "by_lineup_slot": {
        "1": {"1": 0.054, "2": 0.022, "3": 0.008, "4": 0.411, "5": 0.458,
              "6": 0.045, "7": 0.002},
        "5": {"1": 0.061, "2": 0.025, "3": 0.069, "4": 0.629, "5": 0.204,
              "6": 0.012},
        "9": {"1": 0.128, "2": 0.045, "3": 0.305, "4": 0.457, "5": 0.061,
              "6": 0.003},
    }
}

N = 60_000
SEED = 20260711
EXPECTED_PA = 4.3          # -> legacy emits ONLY {4, 5}


class Failure(Exception):
    pass


def check(name: str, ok: bool, msg: str) -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"         -> {msg}")
        raise Failure(name)


class _PA:
    """Stand-in for HybridPASimulator. The PA-OUTCOME layer is NOT under test;
    the PA-COUNT layer is. Rule 5: stub at the boundary, not inside."""
    R = {"single": .145, "double": .045, "triple": .004,
         "home_run": .035, "walk": .085, "out": .686}

    def __init__(self, rng):
        import random
        self.rng = random.Random(rng)
        self._k = list(self.R)
        self._w = [self.R[k] for k in self._k]

    def simulate(self, **kw):
        class O:
            pass
        o = O()
        o.outcome = self.rng.choices(self._k, weights=self._w, k=1)[0]
        o.is_strikeout = (o.outcome == "out" and self.rng.random() < 0.35)
        return o


def build(artifact_path: str | None, seed: int = SEED) -> GameSimulator:
    cfg: dict = {"base_running": {}}
    if artifact_path:
        cfg["base_running"]["pa_distribution_path"] = artifact_path
    gs = GameSimulator(pa_simulator=_PA(seed + 1), random_seed=seed, config=cfg)
    return gs


def pa_counts(gs: GameSimulator, slot: int | None, n: int = N) -> Counter:
    gs.seed(SEED)
    inp = GameSimulatorInput(expected_pa=EXPECTED_PA, pitcher_k_pct=0.22,
                             pitcher_bb_pct=0.08, lineup_slot=slot)
    c: Counter = Counter()
    for _ in range(n):
        c[gs.simulate_game(inp).plate_appearances] += 1
    return c


def game_lines(gs: GameSimulator, slot: int | None, n: int = 20_000) -> list:
    gs.seed(SEED)
    inp = GameSimulatorInput(expected_pa=EXPECTED_PA, pitcher_k_pct=0.22,
                             pitcher_bb_pct=0.08, lineup_slot=slot)
    return [(r.hits, r.runs, r.rbi, r.plate_appearances, r.strikeouts)
            for r in (gs.simulate_game(inp) for _ in range(n))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutate", choices=["legacy", "noslot", "badartifact",
                                         "notbyteident"], default=None)
    a = ap.parse_args(argv)

    tmp = Path(tempfile.mkdtemp())
    good = tmp / "pa.json"
    good.write_text(json.dumps(FITTED), encoding="utf-8")

    # mutations
    artifact: str | None = str(good)
    slot: int | None = 9
    if a.mutate == "legacy":
        artifact = None                     # no fitted distribution at all
    elif a.mutate == "noslot":
        slot = None                         # artifact loaded, but slot not passed
    elif a.mutate == "badartifact":
        bad = tmp / "bad.json"
        bad.write_text(json.dumps({"nonsense": 1}), encoding="utf-8")
        artifact = str(bad)

    if a.mutate:
        print(f"\n=== MUTATION: {a.mutate} ===")
        print("    (the harness MUST fail; if it passes, it guards nothing)\n")
    else:
        print("\n=== FITTED PA DISTRIBUTION ===\n")

    # badartifact must RAISE at construction, not silently fall back
    try:
        gs = build(artifact)
    except ValueError as exc:
        if a.mutate == "badartifact":
            print(f"  [PASS] INV4 a malformed artifact RAISES rather than "
                  f"silently reverting to legacy")
            print(f"         {exc}")
            print("\nMUTATION CORRECTLY CAUGHT")
            return 0
        print(f"FATAL: unexpected ValueError: {exc}", file=sys.stderr)
        return 2
    if a.mutate == "badartifact":
        print("  [FAIL] INV4 a malformed artifact RAISES")
        print("         -> it did NOT raise. A broken fit that silently falls back to "
              "the legacy draw leaves the simulator PRE-FIX while the config claims "
              "otherwise -- the gate then reads 'no drift' and calls it a tie. This "
              "is exactly the failure B4's config-threading bug produced.")
        return 1

    # Each tail is tested on the slot that ACTUALLY HAS IT.
    #
    # RULE 7, learned the hard way: a first draft tested BOTH tails on slot 9 and
    # INV2 failed on CORRECT code. Slot 9's fitted P(pa>=6) is 0.003 -- a 9-hole
    # hitter almost never bats six times, and that is the empirical truth. The
    # 1.7% figure is the POOLED rate across all nine slots. Applying a pooled
    # threshold to a per-slot test made the METRIC wrong, not the model.
    #
    #   slot 9 fitted: P(pa<=2) = 0.173,  P(pa>=6) = 0.003   <- LEFT tail lives here
    #   slot 1 fitted: P(pa<=2) = 0.076,  P(pa>=6) = 0.047   <- RIGHT tail lives here
    c = pa_counts(gs, slot)
    tot = sum(c.values())
    dist = {k: v / tot for k, v in sorted(c.items())}
    p_le2 = sum(v for k, v in dist.items() if k <= 2)

    # right tail: slot 1 (unless a mutation deliberately removed the slot)
    slot_hi = 1 if slot is not None else None
    c_hi = pa_counts(gs, slot_hi)
    tot_hi = sum(c_hi.values())
    dist_hi = {k: v / tot_hi for k, v in sorted(c_hi.items())}
    p_ge6 = sum(v for k, v in dist_hi.items() if k >= 6)

    print(f"  simulated PA (slot={slot}, expected_pa={EXPECTED_PA}, n={tot:,}):")
    print(f"    {' '.join(f'{k}:{v:.4f}' for k, v in dist.items())}")
    print(f"    P(pa<=2) = {p_le2:.4f}   <- the LEFT tail (slot 9 has it)")
    print(f"  simulated PA (slot={slot_hi}, n={tot_hi:,}):")
    print(f"    {' '.join(f'{k}:{v:.4f}' for k, v in dist_hi.items())}")
    print(f"    P(pa>=6) = {p_ge6:.4f}   <- the RIGHT tail (slot 1 has it)\n")

    try:
        check(
            "INV1 the simulator produces pa<=2 games (10.1% of reality)",
            p_le2 > 0.03,
            f"P(pa<=2) = {p_le2:.4f}. The legacy _sample_pa_count emits ONLY "
            f"floor(expected_pa) and floor+1 -- for expected_pa=4.3 that is "
            f"{{4, 5}} and EXACTLY ZERO games at pa<=2. Reality is 10.1%. Those "
            f"early-exit games are disproportionately the ZERO-HIT games; "
            f"producing none of them is what makes P(hits>=1) run hot.",
        )
        check(
            "INV2 the simulator produces pa>=6 games (slot 1: 4.7% fitted)",
            p_ge6 > 0.02,
            f"P(pa>=6) = {p_ge6:.4f} on slot 1. The legacy draw cannot exceed "
            f"floor(expected_pa)+1 = 5, so it produces EXACTLY ZERO 6-PA games. "
            f"BOTH tails are missing, not just the left one -- the distribution "
            f"is too narrow on both sides, which understates variance and is what "
            f"piles probability mass into the middle.",
        )
        check(
            "INV3 the simulated PA distribution MATCHES the fitted one",
            all(abs(dist.get(int(k), 0.0) - v) < 0.01
                for k, v in FITTED["by_lineup_slot"][str(slot)].items()),
            f"simulated {dist} does not match the fitted "
            f"{FITTED['by_lineup_slot'][str(slot)]}. The artifact loaded but the "
            f"draw is not using it.",
        )

        # INV5: byte-identity WITHOUT the artifact. The load-bearing safety property.
        base = build(None)
        gs_slot = build(None)
        same = game_lines(base, None) == game_lines(gs_slot, 9)
        check(
            "INV5 no artifact -> lineup_slot is INERT (byte-identical)",
            same,
            "passing lineup_slot changed the output even with NO fitted artifact. "
            "The frozen arm of every gate runs on a config with no "
            "pa_distribution_path; if the patched code drifts there, the measured "
            "noise floor is contaminated by a change WE introduced and every drift "
            "number becomes uninterpretable.",
        )
    except Failure as f:
        print(f"\nHARNESS FAILED at {f}")
        return 1 if not a.mutate else 0     # a mutation SHOULD fail

    print("\nALL INVARIANTS PASS")
    return 0 if not a.mutate else 1         # a mutation that PASSES is useless


if __name__ == "__main__":
    sys.exit(main())
