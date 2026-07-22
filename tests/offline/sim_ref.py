"""
Reference implementations of the BROKEN and FIXED GameSimulator inner loops.

BaseState is IMPORTED from src.simulation.base_state -- not copied -- so the
harness tracks the real state machine and fails if the transitions change.

The PA outcome draw is a stand-in for HybridPASimulator: the harness stubs the
PA layer (which is NOT under test) and exercises the base-state layer (which
IS under test). Per rule 5: we stub at the PA boundary, not inside the state
machine we are measuring.
"""
from __future__ import annotations
import random

from src.simulation.base_state import BaseState

# --------------------------------------------------------------------------
# Constants (config.json base_running + game_simulator.py module constants)
# --------------------------------------------------------------------------
BASE_STATE_DIST = {
    (0,0,0): 0.545, (1,0,0): 0.175, (0,1,0): 0.070, (0,0,1): 0.020,
    (1,1,0): 0.085, (1,0,1): 0.035, (0,1,1): 0.025, (1,1,1): 0.045,
}
P_SCORE_FROM_BASE = {"walk":0.27, "single":0.28, "double":0.40, "triple":0.56, "home_run":1.0}
_STATES = list(BASE_STATE_DIST)
_WEIGHTS = [BASE_STATE_DIST[s] for s in _STATES]

ON_BASE = ("single", "double", "triple", "home_run", "walk")
HIT = ("single", "double", "triple", "home_run")

# Structural placeholders (Step 2 -- NOT fitted, see PROJECT_CONTEXT)
LAMBDA_DEFAULT = 0.7   # base_state_mix_rate
GAMMA_DEFAULT  = 0.15  # run_traffic_boost


class PAStub:
    """Stand-in for HybridPASimulator. Fixed league-ish rates; the PA layer is
       not under test. Deterministic under its own seeded rng."""
    RATES = {"single":0.145, "double":0.045, "triple":0.004,
             "home_run":0.035, "walk":0.085, "out":0.686}
    def __init__(self, rng): 
        self.rng = rng
        self._kinds = list(self.RATES)
        self._w = [self.RATES[k] for k in self._kinds]
    def simulate(self):
        return self.rng.choices(self._kinds, weights=self._w, k=1)[0]


def _draw_state(rng):
    return list(rng.choices(_STATES, weights=_WEIGHTS, k=1)[0])


# --------------------------------------------------------------------------
# BROKEN: current src/simulation/game_simulator.py
# --------------------------------------------------------------------------
def simulate_game_broken(rng, pa, n_pa):
    hits = runs = rbi = 0
    for _ in range(n_pa):
        kind = pa.simulate()
        if kind in HIT:
            hits += 1
        if kind in ON_BASE:
            # _rbi_for_outcome: FRESH BaseState, fresh draw, discarded
            st = BaseState(bases=_draw_state(rng))
            getattr(st, "advance_" + kind)()
            rbi += st.rbi                      # st.runs never read
            # _run_for_batter: independent coin, ignores st
            if kind == "home_run":
                runs += 1
            elif rng.random() < P_SCORE_FROM_BASE[kind]:
                runs += 1
    return hits, runs, rbi


# --------------------------------------------------------------------------
# FIXED: Design B (Step 2)
# --------------------------------------------------------------------------
def simulate_game_fixed(rng, pa, n_pa, lam=LAMBDA_DEFAULT, gamma=GAMMA_DEFAULT,
                        _cumulative_rbi_bug=False):
    """One persistent BaseState per game; partial re-mix between the batter's
       PAs; runs via a run coin CONDITIONED on traffic behind the batter.
       state.runs is deliberately never read (wrong quantity: team runs, not
       batter runs)."""
    state = BaseState(bases=_draw_state(rng))
    hits = runs = rbi = 0
    for i in range(n_pa):
        if i > 0 and rng.random() < lam:
            state.bases = _draw_state(rng)     # ~8 intervening batters

        kind = pa.simulate()
        if kind in HIT:
            hits += 1
        if kind not in ON_BASE:
            continue

        rbi_before = state.rbi
        getattr(state, "advance_" + kind)()
        if _cumulative_rbi_bug:
            rbi += state.rbi                   # MUTATION: cumulative, not delta
        else:
            rbi += state.rbi - rbi_before      # per-PA delta

        if kind == "home_run":
            runs += 1
        else:
            p = P_SCORE_FROM_BASE[kind]
            behind = max(0, sum(state.bases) - 1)   # runners behind (excl. batter)
            p = min(1.0, p * (1.0 + gamma * behind))
            if rng.random() < p:
                runs += 1
    return hits, runs, rbi


# --------------------------------------------------------------------------
# MUTATION: the naive "read state.runs" design (Step 2 rejected this)
# --------------------------------------------------------------------------
def simulate_game_naive_staterun(rng, pa, n_pa, lam=LAMBDA_DEFAULT):
    state = BaseState(bases=_draw_state(rng))
    hits = 0
    for i in range(n_pa):
        if i > 0 and rng.random() < lam:
            state.bases = _draw_state(rng)
        kind = pa.simulate()
        if kind in HIT:
            hits += 1
        if kind in ON_BASE:
            getattr(state, "advance_" + kind)()
    return hits, state.runs, state.rbi         # runs == rbi identically
