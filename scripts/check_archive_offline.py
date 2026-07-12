"""Mutation-test the archive fix. Rule 4: must FAIL on the broken loader."""
import json, sys
from dataclasses import dataclass, field
from typing import Any, Optional

# ---- minimal stand-ins matching the REAL dataclasses (read from source) ----
@dataclass
class MonteCarloResult:
    n_sims: int
    category: str
    mean: float
    median: float
    p10: float
    p90: float
    p_ge_threshold: dict = field(default_factory=dict)
    per_game_samples: Optional[list] = None

@dataclass
class PropProjection:
    player_id: int
    player_name: str
    category: str
    game_date: str
    projected_value: float
    confidence: float
    simulation: Optional[MonteCarloResult] = None
    outcome_probs: Optional[Any] = None
    team: str = ""
    opponent: str = ""
    opposing_pitcher: str = ""
    lineup_status: str = "unknown"

# ---- the WRITE side, verbatim from dataclasses._projection_to_dict ----
def projection_to_dict(p):
    sim = p.simulation
    return {
        "player_id": p.player_id, "player_name": p.player_name,
        "category": p.category, "game_date": p.game_date,
        "projected_value": p.projected_value, "confidence": p.confidence,
        "team": p.team, "opponent": p.opponent,
        "opposing_pitcher": p.opposing_pitcher, "lineup_status": p.lineup_status,
        "simulation": {
            "n_sims": sim.n_sims, "mean": sim.mean, "median": sim.median,
            "p10": sim.p10, "p90": sim.p90,
            "p_ge_threshold": dict(sim.p_ge_threshold),
        } if sim else None,
    }

# ---- BROKEN loader (what ships today) ----
def load_broken(d):
    return PropProjection(
        player_id=int(d["player_id"]), player_name=str(d["player_name"]),
        category=d["category"], game_date=str(d["game_date"]),
        projected_value=float(d["projected_value"]),
        confidence=float(d.get("confidence", 0.5)),
        simulation=None,                                   # THE BUG
    )

# ---- NAIVE fix: restores the block but keeps STRING keys ----
def load_naive(d):
    raw = d.get("simulation")
    sim = MonteCarloResult(
        n_sims=raw["n_sims"], category=d["category"], mean=raw["mean"],
        median=raw["median"], p10=raw["p10"], p90=raw["p90"],
        p_ge_threshold=raw["p_ge_threshold"],              # keys stay STRINGS
    ) if raw else None
    return PropProjection(
        player_id=int(d["player_id"]), player_name=str(d["player_name"]),
        category=d["category"], game_date=str(d["game_date"]),
        projected_value=float(d["projected_value"]),
        confidence=float(d.get("confidence", 0.5)), simulation=sim,
    )

# ---- FIXED loader ----
def _to_float_keys(raw):
    if not isinstance(raw, dict): return {}
    out = {}
    for k, v in raw.items():
        try: out[float(k)] = float(v)
        except (TypeError, ValueError): pass
    return out

def load_fixed(d):
    raw = d.get("simulation")
    sim = MonteCarloResult(
        n_sims=int(raw.get("n_sims", 0)), category=d["category"],
        mean=float(raw.get("mean", 0.0)), median=float(raw.get("median", 0.0)),
        p10=float(raw.get("p10", 0.0)), p90=float(raw.get("p90", 0.0)),
        p_ge_threshold=_to_float_keys(raw.get("p_ge_threshold")),
        per_game_samples=None,
    ) if raw else None
    return PropProjection(
        player_id=int(d["player_id"]), player_name=str(d["player_name"]),
        category=d["category"], game_date=str(d["game_date"]),
        projected_value=float(d["projected_value"]),
        confidence=float(d.get("confidence", 0.5)), simulation=sim,
    )

# ---- the round trip, THROUGH REAL JSON (this is where float keys die) ----
orig = PropProjection(
    player_id=592450, player_name="Aaron Judge", category="hits",
    game_date="2026-07-11", projected_value=1.12, confidence=0.71,
    simulation=MonteCarloResult(n_sims=8000, category="hits", mean=1.12,
                                median=1.0, p10=0.0, p90=3.0,
                                p_ge_threshold={1.0: 0.681, 2.0: 0.254}),
)
on_disk = json.loads(json.dumps(projection_to_dict(orig)))   # REAL json round trip

print("what the JSON actually contains:")
print(f"  p_ge_threshold = {on_disk['simulation']['p_ge_threshold']}   <- KEYS ARE STRINGS\n")

FAIL = 0
def check(name, cond, detail=""):
    global FAIL
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        print(f"         -> {detail}")
        FAIL += 1

print("=" * 66)
print("BROKEN loader (ships today) -- the harness MUST catch this")
print("=" * 66)
p = load_broken(on_disk)
check("INV1 simulation block is restored", p.simulation is not None,
      "simulation=None. EdgeCalculator falls back to a normal approximation "
      "with a hard-coded spread -- the MC tail probability, which IS the "
      "product, is silently replaced by a Gaussian guess.")

print()
print("=" * 66)
print("NAIVE fix (restores the block, keeps STRING keys) -- MUST ALSO FAIL")
print("=" * 66)
p = load_naive(on_disk)
check("INV1 simulation block is restored", p.simulation is not None)
got = p.simulation.p_ge_threshold.get(1.0)
check("INV2 p_ge_threshold[1.0] is retrievable BY FLOAT", got is not None,
      f"get(1.0) -> {got}. Keys are {list(p.simulation.p_ge_threshold)}. "
      "The block is PRESENT BUT DEAD -- every consumer looks up by float, so "
      "every lookup misses. This is WORSE than the original bug because it "
      "LOOKS restored.")

print()
print("=" * 66)
print("FIXED loader")
print("=" * 66)
p = load_fixed(on_disk)
check("INV1 simulation block is restored", p.simulation is not None)
check("INV2 p_ge_threshold[1.0] retrievable by float",
      p.simulation.p_ge_threshold.get(1.0) == 0.681,
      f"got {p.simulation.p_ge_threshold.get(1.0)}")
check("INV3 p_ge_threshold[2.0] retrievable by float",
      p.simulation.p_ge_threshold.get(2.0) == 0.254)
check("INV4 category taken from the PARENT (not in the block)",
      p.simulation.category == "hits")
check("INV5 n_sims / mean / p10 / p90 round-trip",
      (p.simulation.n_sims, p.simulation.mean, p.simulation.p10, p.simulation.p90)
      == (8000, 1.12, 0.0, 3.0))
check("INV6 per_game_samples is None (not serialized, by design)",
      p.simulation.per_game_samples is None)

# a projection with NO simulation must still load
no_sim = json.loads(json.dumps(projection_to_dict(
    PropProjection(1, "X", "hits", "2026-07-11", 1.0, 0.5, simulation=None))))
q = load_fixed(no_sim)
check("INV7 a projection with no simulation still loads (None, no crash)",
      q.simulation is None)

print(f"\n{'ALL PASS' if FAIL == 0 else f'{FAIL} FAILURE(S)'}")
print("\nMUTATION SUMMARY: the harness fails on BOTH the broken loader AND the")
print("naive string-key 'fix'. It guards the real defect, not a cosmetic one.")
