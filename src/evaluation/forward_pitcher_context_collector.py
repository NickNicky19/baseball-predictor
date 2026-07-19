"""One-tick, future-only collector for official MLB probable-pitcher context.

Network transport is deliberately injected.  The collector can only observe a
target in the predeclared interval ending at T-4; all other states are named
in the immutable context ledger.  It has no sportsbook, prediction, or wager
code.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from src.evaluation.forward_pitcher_context import (
    ForwardPitcherContextError,
    context_from_schedule,
    games_from_raw_schedule_response,
)
from src.evaluation.forward_pitcher_context_ledger import ForwardPitcherContextLedger
from src.evaluation.shadow_capture_plan import ShadowCapturePlan


class ForwardPitcherContextCollectorError(ValueError):
    """A collector setting or transport result cannot prove a source fact."""


@dataclass(frozen=True)
class RawScheduleResponse:
    body: bytes
    received_at_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.body, bytes) or not self.body:
            raise ForwardPitcherContextCollectorError("schedule response body must be nonempty bytes")
        try:
            parsed = datetime.fromisoformat(str(self.received_at_utc).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ForwardPitcherContextCollectorError("schedule response receipt must be ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ForwardPitcherContextCollectorError("schedule response receipt needs timezone")
        object.__setattr__(self, "received_at_utc", parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ForwardPitcherContextCollectorError("collector clock must include timezone")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_tick(
    *,
    plan: ShadowCapturePlan,
    ledger: ForwardPitcherContextLedger,
    max_early_seconds: int,
    fetch_schedule: Callable[[str], RawScheduleResponse],
    now: datetime,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, int]:
    """Process only target windows due on this tick and return exact counts.

    ``max_early_seconds`` is an operational scheduler tolerance, not a model
    parameter.  It is retained by the caller's immutable runtime configuration
    and every observation keeps the actual receipt time for later audit.
    """
    if isinstance(max_early_seconds, bool) or int(max_early_seconds) <= 0:
        raise ForwardPitcherContextCollectorError("max_early_seconds must be positive")
    if ledger.plan.plan_sha256 != plan.plan_sha256:
        raise ForwardPitcherContextCollectorError("ledger is bound to a different capture plan")
    current = _dt(_utc(now))
    completed_clock = clock or (lambda: datetime.now(timezone.utc))
    terminal = ledger.terminal_target_ids()
    due = []
    missed = []
    for target in plan.targets:
        if target.target_id in terminal:
            continue
        horizon = _dt(target.entry_target_at_utc)
        if current > horizon:
            missed.append(target)
        elif current >= horizon - timedelta(seconds=int(max_early_seconds)):
            due.append(target)
    for target in missed:
        ledger.append_exclusion(
            target=target, state="missed", observed_at_utc=_utc(now),
            detail="collector tick occurred after the declared T-horizon; no backfill attempted",
        )
    if not due:
        return {"future": len(plan.targets) - len(terminal) - len(missed), "captured": 0, "source_error": 0, "missed": len(missed)}

    # One raw official response is a source fact shared by targets in this tick.
    date = plan.official_game_date
    try:
        response = fetch_schedule(date)
        raw = json.loads(response.body.decode("utf-8"))
        games = games_from_raw_schedule_response(raw)
    except Exception as exc:
        detail = f"official schedule fetch/parse failed ({type(exc).__name__})"
        completed_at = _utc(completed_clock())
        completed = _dt(completed_at)
        source_errors = 0
        missed_after_fetch = 0
        for target in due:
            # A failed transport may finish after the target. In that case it
            # is a missed window, not a falsely timely source error.
            state = "source_error" if completed <= _dt(target.entry_target_at_utc) else "missed"
            ledger.append_exclusion(target=target, state=state, observed_at_utc=completed_at, detail=detail)
            source_errors += int(state == "source_error")
            missed_after_fetch += int(state == "missed")
        return {"future": len(plan.targets) - len(terminal) - len(missed) - len(due), "captured": 0, "source_error": source_errors, "missed": len(missed) + missed_after_fetch}

    captured = 0
    source_errors = 0
    missed_after_resolution = 0
    for target in due:
        try:
            context = context_from_schedule(
                target=target, plan=plan, captured_at_utc=response.received_at_utc,
                source_payload_sha256=hashlib.sha256(response.body).hexdigest(), schedule_games=games,
            )
            ledger.append_captured(target=target, context=context, raw_payload=response.body)
            captured += 1
        except Exception as exc:
            observed = _dt(response.received_at_utc)
            state = "source_error" if observed <= _dt(target.entry_target_at_utc) else "missed"
            ledger.append_exclusion(
                target=target, state=state, observed_at_utc=response.received_at_utc,
                detail=f"official schedule target resolution failed ({type(exc).__name__})",
            )
            source_errors += int(state == "source_error")
            missed_after_resolution += int(state == "missed")
    return {"future": len(plan.targets) - len(terminal) - len(missed) - len(due), "captured": captured, "source_error": source_errors, "missed": len(missed) + missed_after_resolution}
