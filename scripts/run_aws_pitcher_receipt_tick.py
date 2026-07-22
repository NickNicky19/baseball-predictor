#!/usr/bin/env python3
"""Run one AWS-primary tick for pre-published pitcher-context receipt plans.

This is intentionally narrower than ``run_slate.py`` and the older shadow
collector.  It neither loads a model nor accesses odds, lineups, prices,
outcomes, selections, settlement, or credentials.  It only turns an already
published, future-only official MLB schedule plan into one immutable terminal
probable-starter receipt per T-4 target.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_forward_pitcher_context_collector import fetcher, load_runtime
from src.evaluation.forward_pitcher_context_collector import run_tick
from src.evaluation.forward_pitcher_context_ledger import ForwardPitcherContextLedger
from src.evaluation.shadow_capture_plan import ShadowCapturePlan, ShadowCapturePlanError

try:  # Linux-only service lock; keeping imports portable permits offline tests.
    import fcntl
except ImportError:  # pragma: no cover - exercised by Windows test harness import
    fcntl = None  # type: ignore[assignment]


class AWSReceiptTickError(ValueError):
    """The isolated AWS receipt runner cannot prove its operating contract."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_plan(path: Path) -> ShadowCapturePlan:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AWSReceiptTickError(f"cannot load receipt plan {path.name}") from exc
    try:
        return ShadowCapturePlan.from_mapping(raw)
    except ShadowCapturePlanError as exc:
        raise AWSReceiptTickError(f"invalid receipt plan {path.name}: {exc}") from exc


def _eligible_plan_paths(plan_dir: Path) -> list[Path]:
    if not plan_dir.is_dir():
        raise AWSReceiptTickError("receipt plan directory does not exist")
    # Only a fixed suffix is accepted; schedules, contexts, and arbitrary JSON
    # cannot be mistaken for plans.
    paths = sorted(plan_dir.glob("*.plan.json"))
    return paths


def run_all(*, plan_dir: Path, ledger_root: Path, runtime_path: Path, now: datetime | None = None) -> dict:
    runtime, runtime_sha = load_runtime(runtime_path)
    current = now or _utc_now()
    results: list[dict] = []
    for plan_path in _eligible_plan_paths(plan_dir):
        plan = _load_plan(plan_path)
        # The plan parser has already rejected the sealed period.  Retain this
        # explicit check as a second boundary against a future parser change.
        if plan.official_game_date.startswith("2026-05-"):
            raise AWSReceiptTickError("May 2026 plan reached AWS receipt runner")
        ledger = ForwardPitcherContextLedger(
            ledger_root / plan.official_game_date / plan.plan_sha256,
            plan,
            runtime_sha,
        )
        tick = run_tick(
            plan=plan,
            ledger=ledger,
            max_early_seconds=runtime["scheduler"]["max_early_seconds"],
            fetch_schedule=fetcher(runtime),
            now=current,
        )
        results.append({"plan": plan_path.name, "plan_sha256": plan.plan_sha256, **tick})
    payload = {
        "schema_version": "aws-pitcher-receipt-tick-v1",
        "tick_at_utc": current.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_sha256": runtime_sha,
        "plans": results,
        "collector_state": "awaiting_published_plan" if not results else "processed",
        "research_only": True,
        "betting_authorized": False,
        "model_or_market_accessed": False,
    }
    payload["payload_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--ledger-root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=ROOT / "config/forward_pitcher_context_runtime_v1.json")
    parser.add_argument("--lock-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if fcntl is None:
            raise AWSReceiptTickError("AWS receipt service requires Linux advisory-lock support")
        args.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with args.lock_file.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AWSReceiptTickError("another receipt tick is still running") from exc
            print(json.dumps(run_all(
                plan_dir=args.plan_dir,
                ledger_root=args.ledger_root,
                runtime_path=args.runtime,
            ), sort_keys=True))
    except (OSError, ValueError, RuntimeError, AWSReceiptTickError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
