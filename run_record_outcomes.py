#!/usr/bin/env python3
"""
Record actual outcomes and append prediction-outcome pairs for retraining.

Run after a slate's games are final:
    python run_record_outcomes.py --date 2026-07-01
    python run_record_outcomes.py --date 2026-07-01 --verbose

BACKFILL (the automation's self-healing path):
    python run_record_outcomes.py --backfill-days 7

WHY BACKFILL EXISTS
-------------------
The scheduled workflow graded YESTERDAY and only yesterday. Three independent
failure modes each lost a day PERMANENTLY, and none of them were noisy:

  1. The predict job failed (a transient MLB API blip -- this is what killed the
     2026-07-10 run), so no archive was written. The next day's record job looked
     for it, didn't find it, said "nothing to grade", and exited CLEAN. Nobody
     ever went back for it.

  2. The predict job succeeded but its git commit/push step failed, so the
     archive existed on disk and never reached the repo.

  3. Games were not final at 14:00 UTC (10:00 ET) -- a West Coast extra-innings
     game, or a lagging boxscore feed. record_for_date returned final_games=0,
     this script exited 1, the workflow went red, and the pairs were never
     recorded. Nothing retried them.

Every one of those is a hole in the FORWARD-PAIRS dataset -- the thing that gates
B5 (bulk_innings is still a placeholder waiting on forward K rows) and feeds
calibration. A collector that silently drops days is not a collector.

Backfill closes all three: each run re-checks the last N days and grades anything
still ungraded. A failure today heals tomorrow, with no human action.

SAFE BY CONSTRUCTION (verified against outcome_recorder.py, not assumed):
  * IDEMPOTENT. _build_pair_rows loads the existing (player_id, game_date,
    category) keys and skips any it already has, so re-running a graded date
    appends NOTHING and reports pairs_skipped_duplicate. The loop needs no
    "have I already done this?" bookkeeping -- the recorder already knows.
  * "NOT FINAL YET" IS NOT A FAILURE. record_for_date returns a REPORT (not an
    exception) with final_games=0 when the slate is incomplete. The loop skips
    that date and retries on the next run. That is the West-Coast case, not an
    error, and failing on it would turn the workflow red for the most ordinary
    reason there is.
  * A MISSING ARCHIVE IS NOT A FAILURE EITHER. It raises RetrainError, caught
    per-date. One un-predicted day must not abort the grading of the other six.

NOT BACKFILLED: PREDICTIONS. This tool only grades archives that ALREADY EXIST;
it never regenerates a missed slate. A prediction rebuilt days later is not a
forward prediction -- it would see lineups, weather and probable pitchers that
were not knowable at 4 PM ET on the day, and (per PredictionArchive._dict_to_
projection, which sets simulation=None) would not even round-trip its simulation
block. Injecting reconstructions into the forward-pairs set would contaminate the
one dataset whose entire value is that it is FORWARD. A lost slate stays lost; a
lost GRADE gets healed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from typing import Any, Optional

from src.learning.outcome_recorder import OutcomeRecorder
from src.learning.retrain_runner import RetrainRunner
from src.utils.errors import ConfigError, RetrainError
from src.utils.logging import setup_logging

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pair archived predictions with MLB actuals for retraining.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", metavar="YYYY-MM-DD", help="Single game date to record")
    group.add_argument(
        "--backfill-days",
        type=int,
        metavar="N",
        help="Grade every ungraded date in the last N days (self-healing; "
             "idempotent -- already-graded dates are no-ops)",
    )
    parser.add_argument("--config", metavar="PATH", help="Path to config.json")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")
    return parser.parse_args(argv)


def backfill_dates(days: int, today: Optional[date] = None) -> list[str]:
    """The last `days` dates, OLDEST FIRST, excluding today.

    Today is excluded because its games are not final -- grading it would always
    land in the "no final games yet" branch and add nothing.

    Oldest first so a partial/interrupted run still makes progress on the dates
    most at risk of being forgotten (the oldest ones are the ones about to fall
    out of the window forever).
    """
    today = today or date.today()
    return [
        (today - timedelta(days=offset)).isoformat()
        for offset in range(days, 0, -1)
    ]


def run_backfill(
    recorder: OutcomeRecorder,
    dates: list[str],
    log: logging.Logger,
) -> tuple[int, list[dict[str, Any]]]:
    """Grade every date in `dates`. Returns (exit_code, per-date reports).

    A date that cannot be graded YET (games not final) or that was never
    predicted (no archive) is SKIPPED, not failed. Both are expected states in a
    rolling window; failing on them would make the scheduled workflow red for
    entirely ordinary reasons, and a workflow that cries wolf is one nobody
    reads. Only an UNEXPECTED error fails the run.
    """
    reports: list[dict[str, Any]] = []
    n_graded = n_pairs = n_pending = n_missing = n_failed = n_already = 0

    for game_date in dates:
        try:
            report = recorder.record_for_date(game_date)
        except RetrainError as exc:
            if str(exc).startswith("No archived predictions"):
                # No archive: the slate was never predicted, or the predict
                # job's commit never landed. Expected in a rolling window.
                n_missing += 1
                reports.append({"game_date": game_date, "status": "no_archive",
                                "detail": str(exc)})
                log.info("%s: no archive -- nothing to grade (skipped)", game_date)
                continue
            # A present archive that violates provenance/identity is a material
            # failure, not an ordinary missing slate. Continue other dates but
            # fail the workflow so the quarantine cannot pass unnoticed.
            n_failed += 1
            reports.append({"game_date": game_date, "status": "ineligible_archive",
                            "detail": str(exc)})
            log.error("%s: INELIGIBLE ARCHIVE -- %s", game_date, exc)
            continue
        except Exception as exc:  # noqa: BLE001 — one bad date must not kill the rest
            n_failed += 1
            reports.append({"game_date": game_date, "status": "error",
                            "detail": f"{type(exc).__name__}: {exc}"})
            log.error("%s: FAILED -- %s: %s", game_date, type(exc).__name__, exc)
            continue

        entry = report.to_dict()
        if report.final_games == 0:
            # Games not final yet (West Coast, extra innings, lagging feed).
            # NOT an error -- the next scheduled run picks it up.
            n_pending += 1
            entry["status"] = "pending_final"
            log.info("%s: games not final yet -- will retry on the next run", game_date)
        elif report.pairs_appended > 0:
            n_graded += 1
            n_pairs += report.pairs_appended
            entry["status"] = "graded"
            log.info("%s: graded %d new pair(s)", game_date, report.pairs_appended)
        else:
            # Every key was a duplicate: already graded. Idempotence working.
            n_already += 1
            entry["status"] = "already_graded"
            log.info("%s: already graded (%d duplicate key(s) skipped)",
                     game_date, report.pairs_skipped_duplicate)
        reports.append(entry)

    print("\nBACKFILL SUMMARY")
    print("-" * 62)
    print(f"  window          : {len(dates)} date(s), {dates[0]} .. {dates[-1]}")
    print(f"  newly graded    : {n_graded} date(s), {n_pairs} new pair(s)")
    print(f"  already graded  : {n_already}")
    print(f"  pending final   : {n_pending}   <- NOT an error; retried next run")
    print(f"  no archive      : {n_missing}   <- slate never predicted / not committed")
    print(f"  errors          : {n_failed}")

    if n_failed:
        print(f"\n{n_failed} date(s) failed unexpectedly -- see the log above.",
              file=sys.stderr)
        return EXIT_ERROR, reports
    return EXIT_OK, reports


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)
    log = logging.getLogger("run_record_outcomes")

    try:
        config = RetrainRunner.load_config(args.config)
        recorder = OutcomeRecorder.from_config(config)

        # ---- BACKFILL MODE -------------------------------------------------
        if args.backfill_days is not None:
            if args.backfill_days < 1:
                print("--backfill-days must be >= 1", file=sys.stderr)
                return EXIT_ERROR
            dates = backfill_dates(args.backfill_days)
            log.info("Backfilling %d date(s): %s .. %s", len(dates), dates[0], dates[-1])
            code, _reports = run_backfill(recorder, dates, log)
            print(f"\nPairs file: {recorder._pairs_path()}")
            return code

        # ---- SINGLE-DATE MODE (behaviour unchanged) ------------------------
        report = recorder.record_for_date(args.date)

        print(json.dumps(report.to_dict(), indent=2))
        if report.pairs_appended == 0 and report.final_games == 0:
            print(
                f"\nNo final games for {args.date} yet. Re-run after the slate completes.",
                file=sys.stderr,
            )
            return EXIT_ERROR

        print(
            f"\nRecorded {report.pairs_appended} pairs "
            f"({report.pairs_skipped_duplicate} duplicates skipped)"
        )
        print(f"Pairs file: {recorder._pairs_path()}")
        print("Next: python run_retrain.py")
        return EXIT_OK

    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except RetrainError as exc:
        print(f"Recording error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
