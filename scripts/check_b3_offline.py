#!/usr/bin/env python3
"""
B3 offline harness — distributional pitcher K (additive wrap).

Verifies, with NO network and NO data files, that project_pitcher_strikeouts
now ships a valid analytic strikeout distribution WITHOUT changing the point
estimate the live model ships. Run from repo root:

    python scripts/check_b3_offline.py        (PowerShell: $env:PYTHONPATH=".")

Checks:
  1-5   pure distribution math (mean identity, PMF validity, monotone
        survival, quantile ordering, NB mean preservation)
  6-8   gate-key convention: keys are float(ceil(line)) and the EXACT probe
        sequence run_gate_reconstruct.py uses finds them
  9-12  end-to-end through PropEngine.project_pitcher_strikeouts with real
        PitcherGameContext: simulation attached, additive (projected_value ==
        round(sim.mean, 2)), skill separation, dispersion knob preserves mean
  13    serialization: _projection_to_dict round-trips the simulation block
"""
from __future__ import annotations

import math
import sys
from types import SimpleNamespace

# --- import shim: repo root or scripts/ co-location (matches other runners) --
try:
    from src.prediction.prop_engine import (
        PropEngine,
        K_GATE_LINES,
        k_count_distribution,
        _poisson_pmf,
        _nbinom_pmf,
    )
    from src.models.dataclasses import (
        GameContext,
        PitcherGameContext,
        PlayerIdentity,
        _projection_to_dict,
    )
except ImportError as exc:  # pragma: no cover
    print(f"IMPORT FAILED: {exc}")
    print('Run from repo root with PYTHONPATH="." (PowerShell: $env:PYTHONPATH=".")')
    sys.exit(2)

PASS = 0
FAIL = 0


def check(n: int, name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    status = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    line = f"[{status}] {n:>2}. {name}"
    if detail:
        line += f"  ({detail})"
    print(line)


def _fake_snapshot(k_per_9, bb_per_9=3.0, innings_pitched=40.0, games_started=10):
    """Duck-typed PitchingStatsSnapshot: project_pitcher_strikeouts and
    _pitcher_confidence only read these four attributes."""
    return SimpleNamespace(
        k_per_9=k_per_9,
        bb_per_9=bb_per_9,
        innings_pitched=innings_pitched,
        games_started=games_started,
    )


def _fake_pitcher(expected_innings=5.5):
    return PitcherGameContext(
        player=PlayerIdentity(mlb_id=543037, name="Test Starter", team="HOU"),
        game=GameContext(
            game_pk=1,
            game_date="2025-06-15",
            venue="Test Park",
            is_home=True,
            opponent="TEX",
        ),
        expected_innings=expected_innings,
    )


def main() -> int:
    # ---------------- pure distribution math ----------------
    p_ge, med, p10, p90, mean = k_count_distribution(5.5)

    check(1, "mean identity (Poisson)", abs(mean - 5.5) < 1e-12,
          f"mean={mean}")

    pmf_total = sum(_poisson_pmf(i, 5.5) for i in range(0, 200))
    check(2, "Poisson PMF sums to 1", abs(pmf_total - 1.0) < 1e-9,
          f"sum={pmf_total:.9f}")

    keys = sorted(p_ge)
    vals = [p_ge[k] for k in keys]
    check(3, "P(K>=t) monotone decreasing in t",
          vals == sorted(vals, reverse=True) and all(0.0 <= v <= 1.0 for v in vals),
          f"{ {k: round(v,4) for k,v in p_ge.items()} }")

    check(4, "quantiles ordered p10<=median<=p90", p10 <= med <= p90,
          f"p10={p10} med={med} p90={p90}")

    a, mk = 0.15, 5.5
    r = 1.0 / a
    pp = r / (r + mk)
    nb_mean = sum(i * _nbinom_pmf(i, r, pp) for i in range(0, 400))
    check(5, "NB dispersion preserves mean", abs(nb_mean - mk) < 1e-3,
          f"NB mean={nb_mean:.4f} target={mk}")

    # ---------------- gate-key convention ----------------
    check(6, "keys are float(ceil(line)) = {5.0, 6.0, 7.0}",
          keys == [5.0, 6.0, 7.0] and all(isinstance(k, float) for k in keys),
          f"keys={keys}")

    # EXACT probe sequence from run_gate_reconstruct._p_over_from_projection
    all_hit = True
    for L in K_GATE_LINES:
        k = math.ceil(L)
        hit = None
        for cand in (float(k), int(k), k, f"{float(k)}", f"{k}"):
            if cand in p_ge:
                hit = p_ge[cand]
                break
        if hit is None:
            all_hit = False
    check(7, "gate harness key probe hits for every line", all_hit)

    manual = 1.0 - sum(_poisson_pmf(i, 5.5) for i in range(0, 5))
    check(8, "P(K>=5) matches manual 1-CDF(4)", abs(p_ge[5.0] - manual) < 1e-12,
          f"{p_ge[5.0]:.6f} vs {manual:.6f}")

    # ---------------- end-to-end through PropEngine ----------------
    engine = PropEngine(config={})
    proj = engine.project_pitcher_strikeouts(
        _fake_pitcher(), _fake_snapshot(k_per_9=9.5), _fake_snapshot(k_per_9=10.0)
    )

    check(9, "simulation attached (not None), category/analytic sentinel ok",
          proj.simulation is not None
          and proj.simulation.category == "strikeouts"
          and proj.simulation.n_sims == 0,
          f"n_sims={getattr(proj.simulation, 'n_sims', '?')}")

    check(10, "ADDITIVE: projected_value == round(sim.mean, 2)",
          proj.simulation is not None
          and abs(proj.projected_value - round(proj.simulation.mean, 2)) < 1e-9,
          f"point={proj.projected_value} sim.mean={proj.simulation.mean:.4f}")

    weak = engine.project_pitcher_strikeouts(
        _fake_pitcher(), _fake_snapshot(k_per_9=5.0), _fake_snapshot(k_per_9=5.0)
    )
    ace = engine.project_pitcher_strikeouts(
        _fake_pitcher(), _fake_snapshot(k_per_9=12.0), _fake_snapshot(k_per_9=12.5)
    )
    sep = all(
        ace.simulation.p_ge_threshold[k] > weak.simulation.p_ge_threshold[k]
        for k in (5.0, 6.0, 7.0)
    )
    check(11, "skill separation: ace P(over) > weak P(over) at every line", sep,
          f"ace P(>=5)={ace.simulation.p_ge_threshold[5.0]:.3f} "
          f"weak={weak.simulation.p_ge_threshold[5.0]:.3f}")

    engine_disp = PropEngine(config={"pitcher_k_dispersion": 0.15})
    proj_d = engine_disp.project_pitcher_strikeouts(
        _fake_pitcher(), _fake_snapshot(k_per_9=9.5), _fake_snapshot(k_per_9=10.0)
    )
    check(12, "dispersion knob: same point value, wider tail",
          abs(proj_d.projected_value - proj.projected_value) < 1e-9
          and proj_d.simulation.p_ge_threshold[7.0] > proj.simulation.p_ge_threshold[7.0],
          f"point {proj_d.projected_value} == {proj.projected_value}; "
          f"P(>=7): {proj_d.simulation.p_ge_threshold[7.0]:.4f} > "
          f"{proj.simulation.p_ge_threshold[7.0]:.4f}")

    # ---------------- serialization ----------------
    d = _projection_to_dict(proj)
    sim_block = d.get("simulation")
    check(13, "_projection_to_dict serializes the K simulation block",
          isinstance(sim_block, dict)
          and sim_block.get("p_ge_threshold")
          and 5.0 in sim_block["p_ge_threshold"],
          f"keys={sorted(sim_block['p_ge_threshold']) if sim_block else None}")

    total = PASS + FAIL
    print(f"\n{PASS}/{total} checks passed" + ("" if FAIL == 0 else f" — {FAIL} FAILED"))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
