"""
Game simulator — one hitter's game line from repeated PA simulation.

FIXES (this revision — the HRR structural fix):
1. ONE PERSISTENT BaseState PER SIMULATED GAME. The previous revision built a
   FRESH BaseState on every plate appearance, read one field off it, and threw
   it away:

       def _rbi_for_outcome(self, kind):
           b1, b2, b3 = self._sample_base_state()   # fresh draw, every PA
           state = BaseState(bases=[b1, b2, b3])    # new object, every PA
           state.advance_single()
           return state.rbi                         # read once, DISCARDED

   Nothing persisted across PAs, so within a simulated game hits/runs/RBI were
   modelled as near-independent when they are strongly correlated. hrr =
   hits + runs + rbi, so the MEAN survived but the DISTRIBUTION was wrong —
   and for a Brier-scored over/under the distribution IS the product.

2. BASE-STATE MIXING (`base_state_mix_rate`, lambda). Between this hitter's own
   PAs, ~8 other batters bat. That is what DEFAULT_BASE_STATE_DIST is for: a
   TRANSITION TARGET, not a per-PA resample. With probability lambda the state
   re-mixes to the league stationary distribution; with probability 1 - lambda
   it carries forward. THIS IS THE FIX. At lambda = 1.0 the persistent state is
   mathematically identical to the old per-PA resample — a "persistent
   BaseState" that fully re-mixes every PA is a no-op wearing a state machine
   costume (verified: tests/offline/test_hrr_joint.py, mutation `lambda1`).

3. THE RUN COIN IS CONDITIONED, NOT DELETED (`run_traffic_boost`, gamma).
   state.runs is STILL never read, and that is deliberate: BaseState.runs counts
   runs scored by the batter's TEAM during his PA, not runs scored BY THE BATTER.
   Every advance_*() calls _score_runs(count, rbi_credit) with rbi_credit ==
   count, so state.runs == state.rbi identically. Reading it would force a
   player's runs to always equal his RBI (Corr = 1.000 — verified, mutation
   `staterun`). The batter's own run still needs a conditional-probability
   shortcut (p_score_from_base), but it is now CONDITIONED on the traffic
   behind him rather than flipped independently of the game state.

4. RBI IS READ AS A PER-PA DELTA. With one BaseState carried across PAs,
   `rbi += state.rbi` would sum RUNNING TOTALS (1 + 3 + 6 + ...) instead of the
   increment. Read `state.rbi - rbi_before` (mutation `cumrbi`).

DEGENERATE WHEN ABSENT — IMPORTANT. If `base_state_mix_rate` / `run_traffic_boost`
are missing from config, this simulator constructs at lambda = 1.0 / gamma = 0.0,
which reproduces the PRE-FIX behaviour exactly (same distribution, same RNG draw
sequence). This is deliberate, and it mirrors the B4 `role_innings` precedent:
threading config through must be INERT for the frozen/live path, and only take
effect once a candidate config supplies the keys. It also means that if a gate's
candidate config fails to reach this constructor, hrr probabilities will be
IDENTICAL between frozen and candidate — which the gate reports as FAIL-TO-RUN
rather than silently mistaking a plumbing failure for a weak result.

PLATE-APPEARANCE DISTRIBUTION (this revision):
_sample_pa_count emitted ONLY floor(expected_pa) and floor(expected_pa)+1 --
a TWO-POINT distribution. Measured against the real method (100k draws per
value): expected_pa=4.3 -> {4: 0.698, 5: 0.302}, P(pa<=3) = EXACTLY 0.0000.

    simulator : {3: 0.070, 4: 0.766, 5: 0.164}
    reality   : {1: .072, 2: .030, 3: .107, 4: .543, 5: .231, 6: .017, 7: .001}
                (out_pa, n = 152,683 training rows)

The simulator produced ZERO games at pa<=2 (10.1% of reality) and ZERO at pa=6
(1.7%). BOTH tails missing. It also over-projects the MEAN: 4.095 vs 3.886.
Those early-exit games (pulled, blowout, injury) are disproportionately the
ZERO-HIT games -- which is why P(hits>=1) ran +6.9pp hot on DK-gradeable rows.

The fix draws PA from an EMPIRICAL PER-LINEUP-SLOT distribution FITTED from the
training set (>=16,818 rows per slot; scripts/fit_pa_distribution.py). Measured
to remove +0.0339 of the +0.0890 bias -- inside the 0.020-0.040 range predicted
by an independent synthetic mechanism check.

UNLIKE lambda and gamma, THIS IS A FIT, NOT A PLACEHOLDER (rule 2).

Why REPLACE expected_pa rather than condition on it: lineup_intelligence.py
computes expected_pa = league.pa_per_game * slot_factor[slot] * status_scale.
It is a PURE FUNCTION of lineup slot and lineup status -- there is no
hitter-specific input at all. Within a slot (and reconstructions run
include_projected=False, so status is always 'confirmed'), expected_pa is a
CONSTANT. Replacing it therefore discards NOTHING. Read the source before
measuring: the partial correlation of expected_pa with out_pa controlling for
slot is zero BY CONSTRUCTION.

STRUCTURAL, NOT FITTED: lambda and gamma are deliberate modelling choices, not
values fitted from data. They cannot be identified from box-score pairs (which
carry no runners-on information); identifying them needs Retrosheet/Statcast
play-by-play. See PROJECT_CONTEXT. Do not "tune" them against the gate metric —
that is training on the test set.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Any

from src.models.dataclasses import StatcastProfile, GameSimulationResult
from src.simulation.pa_simulator import HybridPASimulator
from src.simulation.base_state import BaseState


# Long-run MLB frequency of each base state at the start of a PA.
# Order: (runner on 1st, runner on 2nd, runner on 3rd) -> probability.
# Empirically stable across seasons to within ~1-2 points.
#
# Under this revision this is a TRANSITION TARGET (the state the ~8 intervening
# batters leave behind), not a per-PA resample. It is a STATIONARY distribution
# and does NOT identify a transition kernel — infinitely many kernels share it,
# differing in exactly the autocorrelation this fix restores. That degree of
# freedom is `base_state_mix_rate` below.
DEFAULT_BASE_STATE_DIST: dict[tuple[int, int, int], float] = {
    (0, 0, 0): 0.545,
    (1, 0, 0): 0.175,
    (0, 1, 0): 0.070,
    (0, 0, 1): 0.020,
    (1, 1, 0): 0.085,
    (1, 0, 1): 0.035,
    (0, 1, 1): 0.025,
    (1, 1, 1): 0.045,
}

# P(batter eventually scores | base reached on this PA). Long-run MLB values;
# HR is definitionally 1.0. Calibratable via constructor / config.
DEFAULT_P_SCORE_FROM_BASE: dict[str, float] = {
    "walk": 0.27,
    "single": 0.28,
    "double": 0.40,
    "triple": 0.56,
    "home_run": 1.0,
}

# --- Structural placeholders (NOT fitted — see module docstring) ------------
# lambda: P(base state re-mixes to the league stationary distribution between
# this hitter's own PAs). 1.0 == full re-mix == PRE-FIX behaviour (a no-op).
# Defaulting to 1.0 keeps this module INERT unless a config supplies the key.
DEFAULT_BASE_STATE_MIX_RATE: float = 1.0

# gamma: multiplicative boost to P(batter scores) per runner on base BEHIND him
# after his PA. 0.0 == unconditioned coin == PRE-FIX behaviour.
DEFAULT_RUN_TRAFFIC_BOOST: float = 0.0

# The values the HRR candidate config ships (config/config.hrr.json). Recorded
# here for provenance only — they are NOT the defaults, deliberately.
#   base_state_mix_rate = 0.7   (structural: ~8 intervening batters ~ one inning
#                                turnover, so mostly but not fully re-mixed)
#   run_traffic_boost   = 0.15  (structural: conservative; a batter reaching with
#                                two runners behind gets p * 1.30)

_ON_BASE = ("single", "double", "triple", "home_run", "walk")


@dataclass(frozen=True)
class GameSimulatorInput:
    """Inputs required to simulate one hitter's game."""

    expected_pa: float
    pitcher_k_pct: float
    pitcher_bb_pct: float
    park_hr_factor: float = 1.0
    park_hits_factor: float = 1.0
    weather_hr_factor: float = 1.0
    umpire_k_bias: float = 0.0
    handedness_advantage: float = 0.0
    recent_form_mult: float = 1.0
    bvp_ops_factor: float = 1.0
    bvp_hr_factor: float = 1.0
    statcast: Optional[StatcastProfile] = None
    pitcher_hr_per_9: Optional[float] = None
    rich_features: Optional[dict[str, Any]] = None
    # ADDITIVE. Needed to draw from the FITTED per-slot PA distribution.
    # Defaults to None -> _sample_pa_count takes the legacy floor/floor+1 path,
    # byte-identically. An existing caller that does not set it is unaffected.
    lineup_slot: Optional[int] = None


class GameSimulator:
    """Simulates a hitter's performance in a game using PA-level simulation."""

    def __init__(
        self,
        pa_simulator: Optional[HybridPASimulator] = None,
        league_baselines=None,
        random_seed: Optional[int] = None,
        base_state_dist: Optional[dict[tuple[int, int, int], float]] = None,
        p_score_from_base: Optional[dict[str, float]] = None,
        base_state_mix_rate: Optional[float] = None,
        run_traffic_boost: Optional[float] = None,
        pa_distribution: Optional[dict[int, dict[int, float]]] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        # Config-driven base-running parameters (config/base_running block)
        # take effect unless explicitly overridden by the kwargs above.
        br = (config or {}).get("base_running", {}) or {}
        if config and p_score_from_base is None:
            p_score_from_base = br.get("p_score_from_base") or None
        if base_state_mix_rate is None:
            base_state_mix_rate = br.get("base_state_mix_rate")
        if run_traffic_boost is None:
            run_traffic_boost = br.get("run_traffic_boost")

        self.league = league_baselines
        self.pa_simulator = pa_simulator or HybridPASimulator(
            league_baselines=league_baselines,
            random_seed=random_seed,
        )
        self.rng = random.Random(random_seed)
        self._base_states, self._base_state_weights = self._normalize_dist(
            base_state_dist or DEFAULT_BASE_STATE_DIST
        )
        self.p_score_from_base = dict(DEFAULT_P_SCORE_FROM_BASE)
        if p_score_from_base:
            self.p_score_from_base.update(p_score_from_base)

        # Structural placeholders. Absent -> degenerate to PRE-FIX behaviour;
        # see the DEGENERATE WHEN ABSENT note in the module docstring.
        self.base_state_mix_rate = float(
            DEFAULT_BASE_STATE_MIX_RATE if base_state_mix_rate is None
            else base_state_mix_rate
        )
        self.run_traffic_boost = float(
            DEFAULT_RUN_TRAFFIC_BOOST if run_traffic_boost is None
            else run_traffic_boost
        )

        # BYTE-IDENTICAL LEGACY PATH. lambda == 1.0 is *distributionally*
        # identical to the pre-fix per-PA resample, but not byte-identical: the
        # fixed path draws an initial state before the loop and a lambda coin
        # each PA, which shifts every downstream draw off the shared self.rng
        # stream. That would contaminate the frozen arm of the gate (the
        # frozen-vs-frozen noise-floor control reconstructs with a config that
        # has NO base_running.base_state_mix_rate key, so it MUST reproduce the
        # live model exactly). When lambda is fully degenerate we therefore
        # replay the ORIGINAL draw sequence verbatim.
        self._legacy_base_state = (
            self.base_state_mix_rate >= 1.0 and self.run_traffic_boost == 0.0
        )

        # ---- EMPIRICAL PA DISTRIBUTION (fitted; see module docstring) -------
        # {lineup_slot: {pa: prob}}. Absent -> None -> _sample_pa_count falls
        # back to the legacy floor/floor+1 draw, BYTE-IDENTICALLY (same single
        # rng.random() call, same position in the stream). Threading this
        # through is therefore INERT on the frozen/live path until a config
        # supplies base_running.pa_distribution_path -- the same
        # degenerate-when-absent contract as lambda/gamma and B4's role_innings.
        if pa_distribution is None and config:
            pa_distribution = self._load_pa_distribution(
                (config.get("base_running", {}) or {}).get("pa_distribution_path")
            )
        self._pa_dist_states, self._pa_dist_weights = self._prepare_pa_dist(
            pa_distribution
        )

    def seed(self, seed: int) -> None:
        """Reseed both the game-level and PA-level RNGs (reproducibility)."""
        self.rng.seed(seed)
        self.pa_simulator.rng.seed(seed + 1)

    def simulate_game(self, sim_input: GameSimulatorInput) -> GameSimulationResult:
        """Simulate one full game for a hitter and return aggregated results."""
        hits = singles = doubles = triples = home_runs = 0
        walks = strikeouts = runs = rbi = 0

        effective_park_hr = sim_input.park_hr_factor * sim_input.weather_hr_factor
        effective_pitcher_k = sim_input.pitcher_k_pct + sim_input.umpire_k_bias

        n_pa = self._sample_pa_count(
            sim_input.expected_pa, getattr(sim_input, "lineup_slot", None)
        )

        # ONE persistent BaseState for the whole simulated game. This is what
        # couples hits -> RBI causally across PAs; a fresh state per PA (the old
        # behaviour) throws that coupling away.
        #
        # In legacy mode we do NOT pre-draw (the original didn't), so the RNG
        # stream stays aligned with the pre-fix code. The state object is still
        # allocated once, but it is fully resampled inside the on-base branch
        # below, exactly as _rbi_for_outcome used to do.
        state = BaseState()
        if not self._legacy_base_state:
            state.bases = list(self._sample_base_state())

        for i in range(n_pa):
            # Between the hitter's own PAs, ~8 other batters bat. Re-mix the
            # state toward the league stationary distribution with probability
            # lambda; otherwise carry it forward. At lambda = 1.0 this is the
            # old per-PA resample exactly (and the fix is inert).
            if not self._legacy_base_state:
                if i > 0 and self.rng.random() < self.base_state_mix_rate:
                    state.bases = list(self._sample_base_state())

            outcome = self.pa_simulator.simulate(
                pitcher_k_pct=effective_pitcher_k,
                pitcher_bb_pct=sim_input.pitcher_bb_pct,
                park_hr_factor=effective_park_hr,
                park_hits_factor=sim_input.park_hits_factor,
                handedness_advantage=sim_input.handedness_advantage,
                recent_form_mult=sim_input.recent_form_mult,
                bvp_ops_factor=sim_input.bvp_ops_factor,
                bvp_hr_factor=sim_input.bvp_hr_factor,
                statcast=sim_input.statcast,
                pitcher_hr_per_9=sim_input.pitcher_hr_per_9,
                rich_features=sim_input.rich_features,
            )

            kind = outcome.outcome
            if kind == "home_run":
                home_runs += 1
                hits += 1
            elif kind == "triple":
                triples += 1
                hits += 1
            elif kind == "double":
                doubles += 1
                hits += 1
            elif kind == "single":
                singles += 1
                hits += 1
            elif kind == "walk":
                walks += 1
            elif kind == "out" and outcome.is_strikeout:
                strikeouts += 1

            if kind not in _ON_BASE:
                continue

            # LEGACY: the pre-fix code drew a FRESH base state here, on each
            # on-base PA, and discarded it. Reproduce that draw (and its position
            # in the RNG stream) exactly.
            if self._legacy_base_state:
                state.bases = list(self._sample_base_state())

            # RBI: advance the PERSISTENT state and take the PER-PA DELTA.
            # `rbi += state.rbi` would sum running totals and double-count every
            # multi-RBI game.
            rbi_before = state.rbi
            self._advance(state, kind)
            rbi += state.rbi - rbi_before

            # The batter's OWN run. state.runs is deliberately not read here:
            # it counts TEAM runs during the PA, not the batter's run, and it is
            # identically equal to state.rbi (see module docstring).
            runs += self._run_for_batter(kind, state)

        return GameSimulationResult(
            plate_appearances=n_pa,
            hits=hits,
            singles=singles,
            doubles=doubles,
            triples=triples,
            home_runs=home_runs,
            runs=runs,
            rbi=rbi,
            walks=walks,
            strikeouts=strikeouts,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sample_pa_count(self, expected_pa: float, lineup_slot: Optional[int] = None) -> int:
        """Plate appearances for one simulated game.

        FITTED PATH (lineup_slot known AND a fitted distribution is loaded):
        draw from the empirical P(pa | lineup_slot). This is the ONLY path that
        can produce pa<=2 (10.1% of real games) or pa=6 (1.7%) -- the legacy
        draw produces EXACTLY ZERO of both, which is what made P(hits>=1) run
        +6.9pp hot.

        LEGACY PATH (no fitted distribution, or no slot): floor(expected_pa)
        plus one with p = fractional part. Kept BYTE-IDENTICAL -- one
        rng.random() call, same stream position -- so that a config without
        base_running.pa_distribution_path reproduces the pre-fix model exactly.
        """
        if self._pa_dist_states and lineup_slot in self._pa_dist_states:
            return self.rng.choices(
                self._pa_dist_states[lineup_slot],
                weights=self._pa_dist_weights[lineup_slot],
                k=1,
            )[0]

        base = int(expected_pa)
        frac = max(0.0, expected_pa - base)
        if frac > 0 and self.rng.random() < frac:
            return base + 1
        return max(0, base)

    @staticmethod
    def _load_pa_distribution(path: Optional[str]) -> Optional[dict[int, dict[int, float]]]:
        """Load the fitted PA distribution artifact, or None.

        A MISSING file returns None (legacy path -- inert). A file that EXISTS
        but is malformed RAISES: a silently-ignored broken fit would leave the
        simulator on the legacy path while the config says otherwise, and the
        gate would read 'no drift' and call it a tie. That is the exact
        failure mode B4's config-threading bug produced. Fail loudly.
        """
        if not path:
            return None
        p = Path(path)
        if not p.exists():
            return None
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        by_slot = raw.get("by_lineup_slot")
        if not by_slot:
            raise ValueError(
                f"{p} exists but has no 'by_lineup_slot' block. A malformed PA "
                f"distribution must not silently fall back to the legacy draw -- "
                f"the simulator would run pre-fix while the config claims "
                f"otherwise."
            )
        out: dict[int, dict[int, float]] = {}
        for slot, dist in by_slot.items():
            out[int(slot)] = {int(k): float(v) for k, v in dist.items()}
        return out

    @staticmethod
    def _prepare_pa_dist(
        dist: Optional[dict[int, dict[int, float]]]
    ) -> tuple[dict[int, list[int]], dict[int, list[float]]]:
        """Normalise the fitted distribution into rng.choices-ready lists."""
        if not dist:
            return {}, {}
        states: dict[int, list[int]] = {}
        weights: dict[int, list[float]] = {}
        for slot, d in dist.items():
            ks = sorted(d)
            ws = [max(0.0, float(d[k])) for k in ks]
            total = sum(ws)
            if total <= 0:
                raise ValueError(
                    f"PA distribution for lineup_slot {slot} has zero total "
                    f"weight -- refusing to sample from it."
                )
            states[int(slot)] = [int(k) for k in ks]
            weights[int(slot)] = [w / total for w in ws]
        return states, weights

    def _sample_base_state(self) -> tuple[int, int, int]:
        return self.rng.choices(self._base_states, weights=self._base_state_weights, k=1)[0]

    @staticmethod
    def _advance(state: BaseState, kind: str) -> None:
        """Apply the PA outcome to the persistent base state."""
        if kind == "single":
            state.advance_single()
        elif kind == "double":
            state.advance_double()
        elif kind == "triple":
            state.advance_triple()
        elif kind == "home_run":
            state.advance_home_run()
        elif kind == "walk":
            state.advance_walk()

    def _run_for_batter(self, kind: str, state: BaseState) -> int:
        """Whether the batter himself scores after reaching base.

        CONDITIONED on the post-PA state: a batter who reaches with runners
        behind him is likelier to be driven in. gamma = 0.0 reproduces the old
        unconditioned coin exactly.

        Note this is NOT state.runs — that is the team's runs during the PA (and
        equals state.rbi identically). The batter's own run is a downstream event
        that BaseState does not track, because BaseState does not know WHICH
        runner is ours.
        """
        if kind == "home_run":
            return 1
        p = self.p_score_from_base.get(kind, 0.0)
        if self.run_traffic_boost:
            behind = max(0, sum(state.bases) - 1)  # runners on, excluding batter
            p = min(1.0, p * (1.0 + self.run_traffic_boost * behind))
        return 1 if self.rng.random() < p else 0

    @staticmethod
    def _normalize_dist(
        dist: dict[tuple[int, int, int], float]
    ) -> tuple[list[tuple[int, int, int]], list[float]]:
        states = list(dist.keys())
        weights = [max(0.0, dist[s]) for s in states]
        total = sum(weights) or 1.0
        return states, [w / total for w in weights]
