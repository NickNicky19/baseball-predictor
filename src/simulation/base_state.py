"""
BaseState — inning-level baserunning state machine.

Tracks outs, occupied bases, and counting stats (hits, HR, runs, RBI, walks).
Designed for plate-appearance simulators; logic follows standard forced-advance
rules on walks and realistic advancement on balls in play.
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
            self._score_run(rbi_credit=1)
            self.bases = [1, 1, 1]
        elif b1 and b2:
            self.bases = [1, 1, 1]
        elif b1:
            self.bases = [1, 1, b2]
        else:
            self.bases = [1, b2, b3]

    def advance_single(self) -> None:
        self.hits += 1
        self.singles += 1
        b1, b2, b3 = self.bases

        if b3:
            self._score_run(rbi_credit=1)
        if b2:
            self.bases[2] = 1
        else:
            self.bases[2] = 0

        if b1:
            self.bases[1] = 1
        else:
            self.bases[1] = b2

        self.bases[0] = 1

    def advance_double(self) -> None:
        self.hits += 1
        self.doubles += 1
        b1, b2, b3 = self.bases

        runs_scored = int(b3) + int(b2)
        if runs_scored:
            self._score_runs(runs_scored, rbi_credit=runs_scored)

        self.bases = [0, 0, 1 if b1 else 0]

    def advance_triple(self) -> None:
        self.hits += 1
        self.triples += 1
        runners = sum(self.bases)
        if runners:
            self._score_runs(runners, rbi_credit=runners)
        self.bases = [0, 0, 1]

    def advance_home_run(self) -> None:
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

    def _score_run(self, rbi_credit: int = 0) -> None:
        self._score_runs(1, rbi_credit)

    def _score_runs(self, count: int, rbi_credit: int) -> None:
        self.runs += count
        self.rbi += rbi_credit