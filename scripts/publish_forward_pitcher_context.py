#!/usr/bin/env python3
"""Publish one future-only T-horizon pitcher-context record from retained MLB JSON.

This script does not fetch data.  A separately permitted collector must retain
the complete MLB schedule response first, provide its actual receipt time, and
then invoke this publisher for exactly one target in an existing capture plan.
It refuses guessed timestamps, post-target data, partial game identity, and
any overwrite of an already-published context fact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.forward_pitcher_context import (  # noqa: E402
    ForwardPitcherContextError,
    context_from_schedule,
    publish_context,
)
from src.evaluation.shadow_capture_plan import ShadowCapturePlan  # noqa: E402


def _games(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ForwardPitcherContextError("raw MLB schedule response must be an object")
    dates = raw.get("dates")
    if not isinstance(dates, list) or len(dates) != 1 or not isinstance(dates[0], dict):
        raise ForwardPitcherContextError("raw MLB schedule response must contain exactly one date record")
    games = dates[0].get("games")
    if not isinstance(games, list):
        raise ForwardPitcherContextError("raw MLB schedule response lacks games list")
    if any(not isinstance(game, dict) for game in games):
        raise ForwardPitcherContextError("raw MLB schedule contains a non-object game")
    return games


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--schedule-raw", required=True, type=Path)
    parser.add_argument("--received-at-utc", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        plan = ShadowCapturePlan.from_mapping(json.loads(args.plan.read_text(encoding="utf-8")))
        targets = [target for target in plan.targets if target.target_id == args.target_id]
        if len(targets) != 1:
            raise ForwardPitcherContextError("target_id is not uniquely present in plan")
        raw_bytes = args.schedule_raw.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
        context = context_from_schedule(
            target=targets[0], plan=plan, captured_at_utc=args.received_at_utc,
            source_payload_sha256=hashlib.sha256(raw_bytes).hexdigest(),
            schedule_games=_games(raw),
        )
        changed = publish_context(context, plan, targets[0], args.out)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, ForwardPitcherContextError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    print(f"PITCHER CONTEXT {'PUBLISHED' if changed else 'ALREADY VERIFIED'}")
    print(f"  target_id: {context.target_id}")
    print(f"  context_sha256: {context.context_sha256}")
    print(f"  candidate_input_eligible: {context.candidate_input_eligible}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
