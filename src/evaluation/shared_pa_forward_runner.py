"""One-tick orchestration for the separate future shared-PA evidence release."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

from src.evaluation.shadow_capture_plan import ShadowCapturePlan
from src.evaluation.shared_pa_forward_collector import (
    RawPregameResponse,
    SharedPAForwardUnsafePayloadError,
    build_projected_player_snapshot,
    projected_lineups_from_schedule,
)
from src.evaluation.shared_pa_forward_ledger import (
    SharedPAForwardLedger,
    side_target_id,
)


class SharedPAForwardRunnerError(ValueError):
    """The tick cannot prove that it observed every due target safely."""


@dataclass(frozen=True)
class StatsBatch:
    responses: Mapping[int, RawPregameResponse]
    errors: Mapping[int, str]

    def __post_init__(self) -> None:
        if set(self.responses) & set(self.errors):
            raise SharedPAForwardRunnerError("stats batch has both a response and error for one player")
        if any(not isinstance(player_id, int) or player_id <= 0 for player_id in {*self.responses, *self.errors}):
            raise SharedPAForwardRunnerError("stats batch player identity is invalid")
        if any(not isinstance(value, RawPregameResponse) for value in self.responses.values()):
            raise SharedPAForwardRunnerError("stats batch contains an invalid raw response")
        if any(not isinstance(value, str) or not value.strip() for value in self.errors.values()):
            raise SharedPAForwardRunnerError("stats batch error must be a non-empty redacted label")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SharedPAForwardRunnerError("runner clock must include a timezone")
    return value.astimezone(timezone.utc)


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def run_tick(
    *,
    plan: ShadowCapturePlan,
    ledger: SharedPAForwardLedger,
    loaded_contract: Mapping[str, object],
    max_early_seconds: int,
    fetch_lineup_schedule: Callable[[str], RawPregameResponse],
    fetch_stats_batch: Callable[[list[int], int], StatsBatch],
    collector_instance_id: str,
    collector_code_sha256: str,
    runtime_manifest_sha256: str,
    now: datetime,
    completed_clock: Callable[[], datetime] | None = None,
) -> dict[str, int]:
    """Process due game-side targets once; a late retry becomes permanently missed."""
    if isinstance(max_early_seconds, bool) or not isinstance(max_early_seconds, int) or max_early_seconds <= 0:
        raise SharedPAForwardRunnerError("max_early_seconds must be a positive integer")
    if ledger.plan.plan_sha256 != plan.plan_sha256:
        raise SharedPAForwardRunnerError("ledger is bound to another plan")
    current = _utc(now)
    clock = completed_clock or (lambda: datetime.now(timezone.utc))
    terminal = ledger.terminal_side_ids()
    due = []
    missed = []
    for target in plan.targets:
        horizon = _parse_utc(target.entry_target_at_utc)
        remaining = [
            side for side in ("away", "home")
            if side_target_id(plan=plan, target=target, side=side) not in terminal
        ]
        if not remaining:
            continue
        if current > horizon:
            missed.append((target, remaining))
        elif current >= horizon - timedelta(seconds=max_early_seconds):
            due.append((target, remaining))
    for target, sides in missed:
        for side in sides:
            ledger.append_exclusion(
                target=target,
                side=side,
                state="missed_before_horizon",
                observed_at_utc=_stamp(current),
                detail="collector tick occurred after T-minus-4; no backfill attempted",
            )
    if not due:
        return {
            "captured_complete": 0,
            "lineup_unavailable": 0,
            "source_error": 0,
            "missed_before_horizon": sum(len(sides) for _, sides in missed),
            "future": 2 * len(plan.targets) - len(terminal) - sum(len(sides) for _, sides in missed),
        }

    try:
        lineup_response = fetch_lineup_schedule(plan.official_game_date)
    except Exception as exc:
        completed = _utc(clock())
        source_error = 0
        late = 0
        for target, sides in due:
            state = "source_error" if completed <= _parse_utc(target.entry_target_at_utc) else "missed_before_horizon"
            for side in sides:
                ledger.append_exclusion(
                    target=target,
                    side=side,
                    state=state,
                    observed_at_utc=_stamp(completed),
                    detail=f"official lineup schedule fetch failed ({type(exc).__name__})",
                )
                source_error += int(state == "source_error")
                late += int(state == "missed_before_horizon")
        return {
            "captured_complete": 0,
            "lineup_unavailable": 0,
            "source_error": source_error,
            "missed_before_horizon": sum(len(sides) for _, sides in missed) + late,
            "future": 2 * len(plan.targets) - len(terminal) - sum(len(sides) for _, sides in missed) - sum(len(sides) for _, sides in due),
        }

    result = {
        "captured_complete": 0,
        "lineup_unavailable": 0,
        "source_error": 0,
        "missed_before_horizon": sum(len(sides) for _, sides in missed),
        "future": 2 * len(plan.targets) - len(terminal) - sum(len(sides) for _, sides in missed) - sum(len(sides) for _, sides in due),
    }
    for target, sides in due:
        try:
            parsed = projected_lineups_from_schedule(
                response=lineup_response, plan=plan, target=target
            )
        except Exception as exc:
            observed = _parse_utc(lineup_response.received_at_utc)
            state = "lineup_malformed" if observed <= _parse_utc(target.entry_target_at_utc) else "missed_before_horizon"
            safe_raw = None if isinstance(exc, SharedPAForwardUnsafePayloadError) else lineup_response.body
            for side in sides:
                ledger.append_exclusion(
                    target=target,
                    side=side,
                    state=state,
                    observed_at_utc=lineup_response.received_at_utc,
                    detail=f"official lineup target parsing failed ({type(exc).__name__})",
                    raw_payload=safe_raw,
                )
                result[state] = result.get(state, 0) + 1
            continue
        for side in sides:
            player_ids = parsed.get(side)
            if player_ids is None:
                ledger.append_exclusion(
                    target=target,
                    side=side,
                    state="lineup_unavailable",
                    observed_at_utc=lineup_response.received_at_utc,
                    detail="official schedule exposed no complete projected lineup at T-minus-4",
                    raw_payload=lineup_response.body,
                )
                result["lineup_unavailable"] += 1
                continue
            if not isinstance(player_ids, list) or len(player_ids) != 9:
                raise SharedPAForwardRunnerError("parsed complete lineup is not exactly nine players")
            try:
                batch = fetch_stats_batch(player_ids, int(target.official_game_date[:4]))
            except Exception as exc:
                completed = _utc(clock())
                state = "source_error" if completed <= _parse_utc(target.entry_target_at_utc) else "missed_before_horizon"
                ledger.append_exclusion(
                    target=target,
                    side=side,
                    state=state,
                    observed_at_utc=_stamp(completed),
                    detail=f"official player stats batch failed ({type(exc).__name__})",
                    raw_payload=lineup_response.body,
                )
                result[state] += 1
                continue
            if set(batch.responses) | set(batch.errors) != set(player_ids):
                raise SharedPAForwardRunnerError("stats batch did not account for every planned player")
            if batch.errors:
                completed = _utc(clock())
                latest_receipt = max(
                    [_parse_utc(value.received_at_utc) for value in batch.responses.values()] or [completed]
                )
                state = "source_error" if max(completed, latest_receipt) <= _parse_utc(target.entry_target_at_utc) else "missed_before_horizon"
                ledger.append_exclusion(
                    target=target,
                    side=side,
                    state=state,
                    observed_at_utc=_stamp(max(completed, latest_receipt)),
                    detail="official player stats missing for planned lineup IDs: " + ",".join(str(value) for value in sorted(batch.errors)),
                    raw_payload=lineup_response.body,
                    additional_raw_payloads=[value.body for value in batch.responses.values()],
                )
                result[state] += 1
                continue
            try:
                players = [
                    build_projected_player_snapshot(
                        plan=plan,
                        target=target,
                        side=side,
                        source_slot=slot,
                        player_id=player_id,
                        home_team_id=int(parsed["home_team_id"]),
                        away_team_id=int(parsed["away_team_id"]),
                        lineup_response=lineup_response,
                        stats_response=batch.responses[player_id],
                        loaded_contract=loaded_contract,
                        collector_instance_id=collector_instance_id,
                        collector_code_sha256=collector_code_sha256,
                        runtime_manifest_sha256=runtime_manifest_sha256,
                    )
                    for slot, player_id in enumerate(player_ids, start=1)
                ]
                ledger.append_complete(
                    target=target,
                    side=side,
                    players=players,
                    lineup_raw=lineup_response.body,
                    stats_raw_by_sha256={
                        response.sha256: response.body for response in batch.responses.values()
                    },
                    committed_utc=_stamp(_utc(clock())),
                )
                result["captured_complete"] += 1
            except Exception as exc:
                latest = max(
                    [_parse_utc(lineup_response.received_at_utc)]
                    + [_parse_utc(value.received_at_utc) for value in batch.responses.values()]
                )
                state = "source_error" if latest <= _parse_utc(target.entry_target_at_utc) else "missed_before_horizon"
                retain_raw = not isinstance(exc, SharedPAForwardUnsafePayloadError)
                ledger.append_exclusion(
                    target=target,
                    side=side,
                    state=state,
                    observed_at_utc=_stamp(latest),
                    detail=f"shared PA snapshot construction failed ({type(exc).__name__})",
                    raw_payload=lineup_response.body if retain_raw else None,
                    additional_raw_payloads=(
                        [value.body for value in batch.responses.values()] if retain_raw else None
                    ),
                )
                result[state] += 1
    return result
