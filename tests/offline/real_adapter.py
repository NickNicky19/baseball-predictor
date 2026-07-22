"""
--real mode adapter: drive the SHIPPED src.simulation.GameSimulator through the
same invariants as sim_ref.

RULE 5: the offline harness proves the DESIGN; this proves the SHIPPED CODE.
A harness that only ever tests sim_ref guards the reference implementation, not
the thing that runs in production. Mutations here are injected by CONFIG (the
only lever the gate has), not by editing the module.
"""
from __future__ import annotations
import random
from src.simulation.game_simulator import GameSimulator, GameSimulatorInput

# Same PA rates as sim_ref.PAStub -- the PA layer is not under test.
_RATES = {"single":0.145,"double":0.045,"triple":0.004,"home_run":0.035,"walk":0.085,"out":0.686}


class _PA:
    def __init__(self, rng):
        self.rng = rng
        self._k = list(_RATES); self._w = [_RATES[x] for x in self._k]
    def simulate(self, **kw):
        class O:  # minimal PAOutcome
            pass
        o = O()
        o.outcome = self.rng.choices(self._k, weights=self._w, k=1)[0]
        o.is_strikeout = (o.outcome == "out" and self.rng.random() < 0.35)
        return o


def make(lam=None, gamma=None, seed=0):
    """Build a real GameSimulator with the PA layer stubbed."""
    cfg = {"base_running": {}}
    if lam is not None:
        cfg["base_running"]["base_state_mix_rate"] = lam
    if gamma is not None:
        cfg["base_running"]["run_traffic_boost"] = gamma
    gs = GameSimulator(pa_simulator=_PA(random.Random(seed + 1)),
                       random_seed=seed, config=cfg)
    gs.rng = random.Random(seed)
    return gs


def simulate(gs, n_pa):
    """One game at a FIXED pa count (harness controls the PA mix)."""
    inp = GameSimulatorInput(expected_pa=float(n_pa), pitcher_k_pct=0.22,
                             pitcher_bb_pct=0.08)
    r = gs.simulate_game(inp)
    return r.hits, r.runs, r.rbi
