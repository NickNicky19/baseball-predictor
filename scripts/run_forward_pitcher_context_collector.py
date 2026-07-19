#!/usr/bin/env python3
"""Run the isolated official-MLB T−4 opposing-pitcher context collector.

This collector has no odds, prediction, selection, payout, or wagering path.
It only records the official schedule's probable pitchers (or an immutable
source-error/missed state) for targets in an already-published capture plan.
Use ``--watch`` only on a machine that will remain awake through the declared
T−4 target windows; a missed target stays missed by design.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.forward_pitcher_context_collector import (  # noqa: E402
    RawScheduleResponse,
    run_tick,
)
from src.evaluation.forward_pitcher_context_ledger import ForwardPitcherContextLedger  # noqa: E402
from src.evaluation.shadow_capture_plan import ShadowCapturePlan  # noqa: E402


def load_runtime(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "forward-pitcher-context-runtime-v1":
        raise ValueError("runtime configuration schema is unknown")
    if payload.get("scope") != "future_only_research_input":
        raise ValueError("runtime scope is not future-only research input")
    source = payload.get("source")
    scheduler = payload.get("scheduler")
    invariants = payload.get("invariants")
    if not isinstance(source, dict) or not isinstance(scheduler, dict) or not isinstance(invariants, dict):
        raise ValueError("runtime configuration is incomplete")
    if source != {
        "name": "mlb_statsapi_schedule", "base_url": "https://statsapi.mlb.com/api/v1/schedule",
        "sport_id": 1, "hydrate": "probablePitcher,team,venue",
    }:
        raise ValueError("runtime source contract changed")
    if (
        not isinstance(scheduler.get("max_early_seconds"), int)
        or scheduler["max_early_seconds"] <= 0
        or not isinstance(scheduler.get("tick_seconds"), int)
        or scheduler["tick_seconds"] <= 0
        or scheduler.get("late_target_terminal_state") != "missed"
    ):
        raise ValueError("runtime scheduler contract changed")
    if invariants != {
        "retain_raw_response": True, "capture_before_or_at_t4_only": True,
        "backfill_forbidden": True, "research_only": True, "betting_authorized": False,
    }:
        raise ValueError("runtime safety invariants changed")
    return payload, hashlib.sha256(raw).hexdigest()


def fetcher(runtime: dict):
    source = runtime["source"]
    def fetch(official_date: str) -> RawScheduleResponse:
        query = urlencode({"sportId": str(source["sport_id"]), "date": official_date, "hydrate": source["hydrate"]})
        request = Request(f"{source['base_url']}?{query}", headers={"User-Agent": "baseball-predictor-pitcher-context/1.0"})
        with urlopen(request, timeout=30) as response:  # nosec B310: fixed official public host from locked runtime config
            if response.status != 200:
                raise RuntimeError(f"official schedule HTTP {response.status}")
            body = response.read()
        return RawScheduleResponse(body=body, received_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=ROOT / "config/forward_pitcher_context_runtime_v1.json")
    parser.add_argument("--watch", action="store_true", help="Tick until interrupted; otherwise perform exactly one tick.")
    args = parser.parse_args(argv)
    try:
        plan = ShadowCapturePlan.from_mapping(json.loads(args.plan.read_text(encoding="utf-8")))
        runtime, runtime_sha = load_runtime(args.runtime)
        ledger = ForwardPitcherContextLedger(args.root, plan, runtime_sha)
        fetch = fetcher(runtime)
        while True:
            result = run_tick(
                plan=plan, ledger=ledger, max_early_seconds=runtime["scheduler"]["max_early_seconds"],
                fetch_schedule=fetch, now=datetime.now(timezone.utc),
            )
            print(json.dumps({"tick_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **result, "research_only": True, "betting_authorized": False}, sort_keys=True))
            if not args.watch:
                break
            time.sleep(runtime["scheduler"]["tick_seconds"])
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
