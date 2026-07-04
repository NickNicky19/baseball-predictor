"""
BaseState — inning-level baserunning state machine.

Tracks outs, occupied bases, and counting stats (hits, HR, runs, RBI, walks).
Designed for plate-appearance simulators; logic follows standard forced-advance
rules on walks and realistic advancement on balls in play.

FIX (this revision): advance_single() previously left a phantom runner on
second base whenever a runner started there (bases [0,1,0] + single produced
[1,1,1] instead of [1,0,1]). All advancement methods now build the new base
state explicitly instead of mutating in place, which makes the transition
logic auditable at a glance.

Advancement model (conservative, single-advance):
- Single: batter to 1st; every runner advances exactly one base; runner on
  3rd scores. (Real MLB runners take an extra base ~50-60% of the time from
  2nd on a single; that refinement belongs in a calibrated follow-up.)
- Double: batter to 2nd; runners on 2nd/3rd score; runner on 1st to 3rd.
- Triple: batter to 3rd; all runners score.
- Home run: batter and all runners score.
- Walk: forced advancement only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class BaseState:
    """Mutable game state for a single half-inning (or multi-PA stint)."""

    hits: int = 0
    singles: int = 0
    doubles: int = 0
    triples: int = 0
    home_runs: int = 0
    runs: int = 0
    rbi: int = 0
    walks: int = 0
    strikeouts: int = 0
    outs: int = 0
    bases: List[int] = field(default_factory=lambda: [0, 0, 0])

    def reset_inning(self) -> None:
        self.outs = 0
        self.bases = [0, 0, 0]

    def record_out(self, is_strikeout: bool = False) -> None:
        self.outs += 1
        if is_strikeout:
            self.strikeouts += 1

    def advance_walk(self) -> None:
        """Walk with forced-runner advancement only."""
        self.walks += 1
        b1, b2, b3 = self.bases

        if b1 and b2 and b3:
            # Bases loaded: runner from 3rd forced home.
            self._score_runs(1, rbi_credit=1)
            self.bases = [1, 1, 1]
        elif b1 and b2:
            # 1st and 2nd: both forced up one.
            self.bases = [1, 1, 1]
        elif b1:
            # 1st only (3rd may or may not be occupied; runner on 3rd holds).
            self.bases = [1, 1, b3]
        else:
            # 1st empty: batter takes 1st, nobody else forced.
            self.bases = [1, b2, b3]

    def advance_single(self) -> None:
        """Single: batter to 1st, all runners advance one base, 3rd scores."""
        self.hits += 1
        self.singles += 1
        b1, b2, b3 = self.bases

        if b3:
            self._score_runs(1, rbi_credit=1)

        # FIX: build the new state explicitly. Old in-place mutation left the
        # runner-from-2nd's origin base occupied (phantom runner).
        self.bases = [1, b1, b2]

    def advance_double(self) -> None:
        """Double: batter to 2nd; runners from 2nd and 3rd score; 1st -> 3rd."""
        self.hits += 1
        self.doubles += 1
        b1, b2, b3 = self.bases

        runs_scored = int(b3) + int(b2)
        if runs_scored:
            self._score_runs(runs_scored, rbi_credit=runs_scored)

        self.bases = [0, 1, 1 if b1 else 0]

    def advance_triple(self) -> None:
        """Triple: batter to 3rd; all runners score."""
        self.hits += 1
        self.triples += 1
        runners = sum(self.bases)
        if runners:
            self._score_runs(runners, rbi_credit=runners)
        self.bases = [0, 0, 1]

    def advance_home_run(self) -> None:
        """Home run: batter and all runners score."""
        self.home_runs += 1
        self.hits += 1
        runners_on = sum(self.bases)
        total_runs = runners_on + 1
        self._score_runs(total_runs, rbi_credit=total_runs)
        self.bases = [0, 0, 0]

    def is_inning_over(self) -> bool:
        return self.outs >= 3

    def copy_stats(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "home_runs": self.home_runs,
            "runs": self.runs,
            "rbi": self.rbi,
            "walks": self.walks,
            "strikeouts": self.strikeouts,
            "outs": self.outs,
        }

    def total_runners(self) -> int:
        return sum(self.bases)

    def _score_run(self, rbi_credit: int = 0) -> None:
        self._score_runs(1, rbi_credit)

    def _score_runs(self, count: int, rbi_credit: int) -> None:
        self.runs += count
        self.rbi += rbi_credit
