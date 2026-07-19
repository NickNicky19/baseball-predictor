#!/usr/bin/env python3
"""Synthetic and mutation checks for future-only opposing-pitcher context."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.forward_pitcher_context import (  # noqa: E402
    ForwardPitcherContextError,
    context_from_schedule,
    load_context,
    publish_context,
)
from src.evaluation.shadow_capture_plan import plan_from_schedule  # noqa: E402
from scripts.publish_forward_pitcher_context import main as publish_main  # noqa: E402


H = "a" * 64


def fails(fn) -> bool:
    try:
        fn()
    except (ValueError, ForwardPitcherContextError):
        return True
    return False


def game(**overrides):
    row = {
        "gamePk": 901,
        "officialDate": "2026-07-20",
        "gameDate": "2026-07-20T23:10:00Z",
        "gameType": "R",
        "teams": {
            "home": {"team": {"name": "Home"}, "probablePitcher": {"id": 111}},
            "away": {"team": {"name": "Away"}, "probablePitcher": {"id": 222}},
        },
    }
    row.update(overrides)
    return row


def main() -> int:
    plan = plan_from_schedule(
        official_game_date="2026-07-20", entry_hours=4, policy_sha256=H,
        schedule_snapshot=[game()],
    )
    target = plan.targets[0]
    valid = context_from_schedule(
        target=target, plan=plan,
        captured_at_utc="2026-07-20T19:05:00Z", source_payload_sha256="b" * 64,
        schedule_games=[game()],
    )
    assert valid.candidate_input_eligible
    print("[OK] exact regular-season target with both probable pitchers is eligible")

    with tempfile.TemporaryDirectory(prefix="pitcher_context_") as temp:
        path = Path(temp) / "context.json"
        assert publish_context(valid, plan, target, path) and not publish_context(valid, plan, target, path)
        assert load_context(path, plan, target).context_sha256 == valid.context_sha256
        print("[OK] atomic context publication is idempotent and plan-bound")

        altered = json.loads(path.read_text(encoding="utf-8"))
        altered["home_probable_pitcher"]["player_id"] = 333
        path.write_text(json.dumps(altered), encoding="utf-8")
        assert fails(lambda: load_context(path, plan, target))
        print("[OK] MUTATION altered pitcher identity fails context hash validation")

        plan_path = Path(temp) / "plan.json"
        raw_path = Path(temp) / "schedule.json"
        published = Path(temp) / "published.json"
        plan.write(plan_path)
        raw_path.write_text(json.dumps({"dates": [{"games": [game()]}]}), encoding="utf-8")
        arguments = [
            "--plan", str(plan_path), "--target-id", target.target_id,
            "--schedule-raw", str(raw_path), "--received-at-utc", "2026-07-20T19:05:00Z",
            "--out", str(published),
        ]
        assert publish_main(arguments) == 0 and publish_main(arguments) == 0
        assert load_context(published, plan, target).candidate_input_eligible
        print("[OK] retained raw MLB response publishes one idempotent target-bound record")

    assert fails(lambda: context_from_schedule(
        target=target, plan=plan,
        captured_at_utc="2026-07-20T19:11:00Z", source_payload_sha256="b" * 64,
        schedule_games=[game()],
    ))
    print("[OK] MUTATION post-target observation fails")

    assert fails(lambda: context_from_schedule(
        target=target, plan=plan,
        captured_at_utc="2026-07-20T19:05:00Z", source_payload_sha256="not-a-hash",
        schedule_games=[game()],
    ))
    print("[OK] MUTATION raw schedule hash absence fails")

    assert fails(lambda: context_from_schedule(
        target=target, plan=plan,
        captured_at_utc="2026-07-20T19:05:00Z", source_payload_sha256="b" * 64,
        schedule_games=[game(), game()],
    ))
    print("[OK] MUTATION ambiguous schedule target fails")

    other_plan = plan_from_schedule(
        official_game_date="2026-07-20", entry_hours=4, policy_sha256=H,
        schedule_snapshot=[game(gamePk=902)],
    )
    assert fails(lambda: context_from_schedule(
        target=target, plan=other_plan,
        captured_at_utc="2026-07-20T19:05:00Z", source_payload_sha256="b" * 64,
        schedule_games=[game()],
    ))
    print("[OK] MUTATION target from a different capture plan fails")

    unavailable = copy.deepcopy(game())
    unavailable["teams"]["away"].pop("probablePitcher")
    missing = context_from_schedule(
        target=target, plan=plan,
        captured_at_utc="2026-07-20T19:05:00Z", source_payload_sha256="b" * 64,
        schedule_games=[unavailable],
    )
    assert missing.away_probable_pitcher.status == "unavailable" and not missing.candidate_input_eligible
    print("[OK] unavailable pitcher is retained and cannot become an eligible input")

    malformed = copy.deepcopy(game())
    malformed["teams"]["home"]["probablePitcher"] = {"id": "unknown"}
    bad = context_from_schedule(
        target=target, plan=plan,
        captured_at_utc="2026-07-20T19:05:00Z", source_payload_sha256="b" * 64,
        schedule_games=[malformed],
    )
    assert bad.home_probable_pitcher.status == "malformed" and not bad.candidate_input_eligible
    print("[OK] malformed pitcher source is retained and excluded")

    spring = game(gameType="S")
    nonregular = context_from_schedule(
        target=target, plan=plan,
        captured_at_utc="2026-07-20T19:05:00Z", source_payload_sha256="b" * 64,
        schedule_games=[spring],
    )
    assert not nonregular.candidate_input_eligible
    print("[OK] non-regular game cannot silently qualify for a regular-season challenger")

    print("10/10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
