"""
Offline harness — the outcomes BACKFILL window.

    python scripts/check_backfill_offline.py

WHAT THIS GUARDS
----------------
The scheduled record-outcomes workflow graded YESTERDAY and only yesterday, and
three separate failure modes each lost a day PERMANENTLY and SILENTLY:

  1. predict job failed -> no archive -> next day's record job says "nothing to
     grade" and exits CLEAN. Nobody goes back. (This is what happened on
     2026-07-10.)
  2. predict succeeded but its git push failed -> archive never reached the repo.
  3. games not final at 10:00 ET -> run_record_outcomes exited 1 -> workflow red
     -> pairs never recorded, never retried.

Backfill heals all three by re-checking the last N days each run. The three
properties that make that SAFE are what this harness pins:

  IDEMPOTENT      re-running a graded date must append NOTHING (Group 2).
  PENDING != FAIL games-not-final must SKIP, not fail the run -- otherwise the
                  workflow goes red every time a West Coast game runs long, and
                  a workflow that cries wolf is one nobody reads (Group 3).
  MISSING != FAIL a date that was never predicted must not abort the other six
                  (Group 3).

The recorder is stubbed at the record_for_date seam, so no network and no CSV
writes. The REAL run_backfill / backfill_dates are exercised -- not
reimplemented, because a harness that restates the logic cannot catch it
regressing.
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((PASS if cond else FAIL, name, detail))


# ---------------------------------------------------------------------------
# GROUP 0 — import the REAL module through the package graph.
# ---------------------------------------------------------------------------
try:
    import importlib
    _mod = importlib.import_module("run_record_outcomes")
    check("0a import run_record_outcomes through the package graph", True)
except Exception as exc:  # noqa: BLE001
    _mod = None
    check("0a import run_record_outcomes through the package graph", False,
          f"{type(exc).__name__}: {exc}")

if _mod is None:
    for s, n, d in results:
        print(f"[{s}] {n}   -- {d}")
    sys.exit(1)

backfill_dates = _mod.backfill_dates
run_backfill = _mod.run_backfill
EXIT_OK = _mod.EXIT_OK
EXIT_ERROR = _mod.EXIT_ERROR

from src.utils.errors import RetrainError  # noqa: E402

log = logging.getLogger("harness")
logging.basicConfig(level=logging.CRITICAL)  # silence the loop's own logging


class Report:
    """Duck-typed OutcomeRecordingReport."""

    def __init__(self, game_date: str, final_games: int, pairs_appended: int,
                 pairs_skipped_duplicate: int = 0, notes: str = ""):
        self.game_date = game_date
        self.final_games = final_games
        self.pairs_appended = pairs_appended
        self.pairs_skipped_duplicate = pairs_skipped_duplicate
        self.notes = notes

    def to_dict(self) -> dict[str, Any]:
        return {"game_date": self.game_date, "final_games": self.final_games,
                "pairs_appended": self.pairs_appended,
                "pairs_skipped_duplicate": self.pairs_skipped_duplicate,
                "notes": self.notes}


class StubRecorder:
    """Stands in for OutcomeRecorder, scripted per date.

    behaviours[date] is one of:
      ("graded", n)        -> n new pairs
      ("already", n)       -> 0 new, n duplicates (the idempotence path)
      ("pending",)         -> final_games=0 (games not final yet)
      ("no_archive",)      -> raises RetrainError (slate never predicted)
      ("boom",)            -> raises an unexpected error
    """

    def __init__(self, behaviours: dict[str, tuple]):
        self.behaviours = behaviours
        self.calls: list[str] = []

    def record_for_date(self, game_date: str):
        self.calls.append(game_date)
        kind, *rest = self.behaviours.get(game_date, ("no_archive",))
        if kind == "no_archive":
            raise RetrainError(f"No archived predictions for {game_date}")
        if kind == "boom":
            raise ValueError("unexpected boom")
        if kind == "pending":
            return Report(game_date, final_games=0, pairs_appended=0,
                          notes="No final games yet; try again after the slate completes")
        if kind == "already":
            return Report(game_date, final_games=15, pairs_appended=0,
                          pairs_skipped_duplicate=rest[0])
        return Report(game_date, final_games=15, pairs_appended=rest[0])

    def _pairs_path(self):
        return Path("data/learning/prediction_outcomes.csv")


# ---------------------------------------------------------------------------
# GROUP 1 — THE WINDOW: which dates get checked.
# ---------------------------------------------------------------------------
TODAY = date(2026, 7, 11)
d7 = backfill_dates(7, today=TODAY)

check("1a window has exactly N dates", len(d7) == 7, str(d7))
check("1b TODAY is EXCLUDED (its games are not final -- grading it adds nothing)",
      TODAY.isoformat() not in d7, str(d7))
check("1c most recent date is YESTERDAY", d7[-1] == "2026-07-10", d7[-1])
check("1d oldest first (a partial run progresses on the dates about to be lost)",
      d7 == sorted(d7), str(d7))
check("1e oldest date is exactly N days back", d7[0] == "2026-07-04", d7[0])
check("1f a 1-day window == yesterday only (matches the OLD behaviour)",
      backfill_dates(1, today=TODAY) == ["2026-07-10"],
      str(backfill_dates(1, today=TODAY)))
check("1g window spans a month boundary correctly",
      backfill_dates(3, today=date(2026, 7, 2)) ==
      ["2026-06-29", "2026-06-30", "2026-07-01"],
      str(backfill_dates(3, today=date(2026, 7, 2))))


# ---------------------------------------------------------------------------
# GROUP 2 — IDEMPOTENCE: re-running a graded date must append NOTHING.
#
# This is what makes the loop safe to run every single day. Verified against
# outcome_recorder._build_pair_rows, which loads the existing
# (player_id, game_date, category) keys and skips any it already has.
# ---------------------------------------------------------------------------
rec = StubRecorder({d: ("already", 30) for d in d7})
code, reports = run_backfill(rec, d7, log)

check("2a a fully-graded window exits OK", code == EXIT_OK, f"exit={code}")
check("2b every date reports 'already_graded'",
      all(r["status"] == "already_graded" for r in reports),
      str([r["status"] for r in reports]))
check("2c ZERO new pairs appended on a re-run  [IDEMPOTENCE]",
      sum(r["pairs_appended"] for r in reports) == 0,
      "a re-run that appends rows would duplicate the pairs file")
check("2d every date was still CHECKED (the loop does not skip on its own)",
      rec.calls == d7, str(rec.calls))


# ---------------------------------------------------------------------------
# GROUP 3 — THE THREE FAILURE MODES ARE HEALED, AND DO NOT FAIL THE RUN.
# ---------------------------------------------------------------------------
mixed = StubRecorder({
    d7[0]: ("already", 30),     # long-graded
    d7[1]: ("graded", 28),      # <- HEALED: predict-commit failed, now graded
    d7[2]: ("no_archive",),     # <- slate never predicted (the 07-10 case)
    d7[3]: ("graded", 30),      # <- HEALED: was 'pending' yesterday, final now
    d7[4]: ("already", 26),
    d7[5]: ("pending",),        # <- West Coast game still running
    d7[6]: ("already", 30),
})
code, reports = run_backfill(mixed, d7, log)
by_date = {r["game_date"]: r for r in reports}

check("3a a mixed window exits OK (pending/missing are NOT failures)",
      code == EXIT_OK,
      f"exit={code} -- failing here turns the workflow red every time a game "
      f"runs long, and a workflow that cries wolf is one nobody reads")
check("3b ungraded dates got GRADED  [THE WHOLE POINT]",
      by_date[d7[1]]["status"] == "graded" and by_date[d7[3]]["status"] == "graded",
      f"{by_date[d7[1]]['status']} / {by_date[d7[3]]['status']}")
check("3c 58 new pairs recovered that the old single-date job would have LOST",
      sum(r.get("pairs_appended", 0) for r in reports) == 58,
      str(sum(r.get("pairs_appended", 0) for r in reports)))
check("3d 'games not final' -> pending_final, not an error",
      by_date[d7[5]]["status"] == "pending_final", by_date[d7[5]]["status"])
check("3e 'no archive' -> no_archive, not an error",
      by_date[d7[2]]["status"] == "no_archive", by_date[d7[2]]["status"])
check("3f a missing date does NOT abort the dates after it",
      len(mixed.calls) == 7 and by_date[d7[3]]["status"] == "graded",
      f"only {len(mixed.calls)} of 7 dates were attempted")


# ---------------------------------------------------------------------------
# GROUP 4 — A REAL ERROR STILL FAILS (the guard must not swallow everything).
# ---------------------------------------------------------------------------
boom = StubRecorder({d: ("already", 30) for d in d7} | {d7[3]: ("boom",)})
code, reports = run_backfill(boom, d7, log)
by_date = {r["game_date"]: r for r in reports}

check("4a an UNEXPECTED error fails the run (exit 1)", code == EXIT_ERROR,
      f"exit={code} -- a real bug must not be silently swallowed")
check("4b the failing date is reported as an error",
      by_date[d7[3]]["status"] == "error", by_date[d7[3]]["status"])
check("4c the OTHER dates were still processed (one bad date != abort)",
      len(boom.calls) == 7 and by_date[d7[6]]["status"] == "already_graded",
      f"{len(boom.calls)} dates attempted")

# An entirely empty window (nothing predicted at all) must not be a failure --
# it is the honest state of a repo mid-offseason.
empty = StubRecorder({})
code, reports = run_backfill(empty, d7, log)
check("4d a window with NO archives at all exits OK (offseason is not an error)",
      code == EXIT_OK and all(r["status"] == "no_archive" for r in reports),
      f"exit={code}")


# ---------------------------------------------------------------------------
# GROUP 5 — SINGLE-DATE MODE IS UNCHANGED (do not break what works).
# ---------------------------------------------------------------------------
import argparse  # noqa: E402

parse_args = _mod.parse_args

ns = parse_args(["--date", "2026-07-01"])
check("5a --date still parses", ns.date == "2026-07-01" and ns.backfill_days is None)

ns = parse_args(["--backfill-days", "7"])
check("5b --backfill-days parses", ns.backfill_days == 7 and ns.date is None)

# The two modes are mutually exclusive: passing both is a user error, not a
# silent precedence rule nobody can remember.
try:
    parse_args(["--date", "2026-07-01", "--backfill-days", "7"])
    check("5c --date and --backfill-days are mutually exclusive", False,
          "both were accepted -- which one wins? ambiguity is a bug")
except SystemExit:
    check("5c --date and --backfill-days are mutually exclusive", True)

# And one of them is REQUIRED -- a bare invocation must not silently do nothing.
try:
    parse_args([])
    check("5d one of --date / --backfill-days is required", False,
          "a bare call was accepted and would silently no-op")
except SystemExit:
    check("5d one of --date / --backfill-days is required", True)


# ---------------------------------------------------------------------------
# REPORT
# ---------------------------------------------------------------------------
n_fail = sum(1 for r in results if r[0] == FAIL)
width = max(len(n) for _, n, _ in results)
print()
for status, name, detail in results:
    line = f"[{status}] {name.ljust(width)}"
    if detail and status == FAIL:
        line += f"   -- {detail}"
    print(line)

total = len(results)
print(f"\n{total - n_fail}/{total} checks passed")
sys.exit(1 if n_fail else 0)
