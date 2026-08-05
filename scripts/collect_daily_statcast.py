#!/usr/bin/env python3
"""Capture or verify one create-only daily Statcast Search CSV response."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.statcast_daily_collector import (
    HTTPSStatcastTransport,
    capture_daily_statcast,
    create_expected_games_bundle_from_schedule,
    load_expected_games,
    verify_capture,
    verify_expected_games_bundle,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--official-date", required=True)
    capture.add_argument("--expected-games", required=True, type=Path)
    capture.add_argument("--output-root", required=True, type=Path)
    capture.add_argument("--retrieval-id", required=True)
    capture.add_argument("--previous-receipt", type=Path)
    capture.add_argument("--timeout-seconds", type=float, default=60.0)
    capture.add_argument("--max-attempts", type=int, default=3)
    verify = sub.add_parser("verify")
    verify.add_argument("--capture-dir", required=True, type=Path)
    expected = sub.add_parser("build-expected-games")
    expected.add_argument("--official-date", required=True)
    expected.add_argument("--schedule-response", required=True, type=Path)
    expected.add_argument("--output-dir", required=True, type=Path)
    expected_verify = sub.add_parser("verify-expected-games")
    expected_verify.add_argument("--bundle-dir", required=True, type=Path)
    expected_verify.add_argument("--schedule-response", required=True, type=Path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "build-expected-games":
        receipt = create_expected_games_bundle_from_schedule(
            schedule_response_path=args.schedule_response,
            official_date=args.official_date,
            output_dir=args.output_dir,
        )
        print(
            json.dumps(
                {
                    "official_date": receipt["selection"]["official_date"],
                    "played_game_count": receipt["selection"]["played_game_count"],
                    "excluded_unplayed_game_count": receipt["selection"]["excluded_unplayed_game_count"],
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "verify-expected-games":
        receipt = verify_expected_games_bundle(
            args.bundle_dir,
            schedule_response_path=args.schedule_response,
        )
        print(
            json.dumps(
                {
                    "official_date": receipt["selection"]["official_date"],
                    "played_game_count": receipt["selection"]["played_game_count"],
                    "verified": True,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "verify":
        receipt = verify_capture(args.capture_dir)
    else:
        expected_date, game_pks, expected_sha = load_expected_games(args.expected_games)
        if args.official_date != expected_date.isoformat():
            raise SystemExit("official date differs from expected-games file")
        previous = None
        if args.previous_receipt:
            if args.previous_receipt.name != "receipt.json":
                raise SystemExit("previous receipt path must end in receipt.json")
            previous = verify_capture(args.previous_receipt.parent)
        receipt = capture_daily_statcast(
            official_date=expected_date,
            expected_game_pks=game_pks,
            expected_games_sha256=expected_sha,
            output_root=args.output_root,
            retrieval_id=args.retrieval_id,
            transport=HTTPSStatcastTransport(),
            previous_receipt=previous,
            timeout_seconds=args.timeout_seconds,
            max_attempts=args.max_attempts,
        )
    print(
        json.dumps(
            {
                "terminal_status": receipt["terminal_status"],
                "official_date": receipt["official_date"],
                "retrieval_id": receipt["retrieval_id"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
