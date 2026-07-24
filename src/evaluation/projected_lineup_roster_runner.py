"""One-tick, future-only T-minus-4 active-roster collection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from src.evaluation.projected_lineup_official_roster import RawOfficialRosterResponse, parse_active_roster_receipt
from src.evaluation.projected_lineup_roster_ledger import CAPTURED, ProjectedLineupRosterLedger, roster_side_target_id
from src.evaluation.shadow_capture_plan import ShadowCapturePlan
from src.evaluation.shared_pa_forward_collector import RawPregameResponse, projected_lineups_from_schedule


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collector clock must include timezone")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def run_tick(*, plan: ShadowCapturePlan, ledger: ProjectedLineupRosterLedger, max_early_seconds: int, fetch_schedule: Callable[[str], RawPregameResponse], fetch_roster: Callable[[int, str], RawOfficialRosterResponse], now: datetime) -> dict[str, int]:
    """Capture each due side once; all late work is terminally missed, never backfilled."""
    current = _utc(now); terminal = ledger.terminal_side_ids(); due = []; missed = []
    for target in plan.targets:
        horizon = _parse(target.entry_target_at_utc)
        sides = [side for side in ("away", "home") if roster_side_target_id(plan=plan, target=target, side=side) not in terminal]
        if not sides: continue
        if current > horizon: missed.append((target, sides))
        elif current >= horizon - timedelta(seconds=max_early_seconds): due.append((target, sides))
    result = {CAPTURED: 0, "source_error": 0, "roster_malformed": 0, "missed_before_horizon": 0, "game_identity_ambiguous": 0}
    for target, sides in missed:
        for side in sides:
            ledger.append_exclusion(target=target, side=side, state="missed_before_horizon", observed_at_utc=_stamp(current), detail="collector tick occurred after T-minus-4; no backfill attempted")
            result["missed_before_horizon"] += 1
    if not due:
        result["future"] = 2 * len(plan.targets) - len(terminal) - result["missed_before_horizon"]; return result
    try:
        schedule = fetch_schedule(plan.official_game_date)
    except Exception as exc:
        for target, sides in due:
            for side in sides:
                ledger.append_exclusion(target=target, side=side, state="source_error", observed_at_utc=_stamp(current), detail=f"official schedule fetch failed ({type(exc).__name__})")
                result["source_error"] += 1
        result["future"] = 0; return result
    for target, sides in due:
        try:
            game = projected_lineups_from_schedule(response=schedule, plan=plan, target=target)
        except Exception as exc:
            state = "game_identity_ambiguous" if _parse(schedule.received_at_utc) <= _parse(target.entry_target_at_utc) else "missed_before_horizon"
            for side in sides:
                ledger.append_exclusion(target=target, side=side, state=state, observed_at_utc=schedule.received_at_utc, detail=f"official schedule identity failed ({type(exc).__name__})", schedule_raw=schedule.body)
                result[state] += 1
            continue
        for side in sides:
            team_id = int(game[f"{side}_team_id"])
            try:
                roster = fetch_roster(team_id, target.official_game_date)
                record = parse_active_roster_receipt(response=roster, requested_date=target.official_game_date, team_id=team_id, target_horizon_utc=target.entry_target_at_utc)
                ledger.append_capture(target=target, side=side, team_id=team_id, roster_record=record, schedule_raw=schedule.body, roster_raw=roster.body, committed_utc=roster.received_at_utc)
                result[CAPTURED] += 1
            except Exception as exc:
                state = "roster_malformed"
                if isinstance(exc, ValueError) and "after T-minus-4" in str(exc): state = "missed_before_horizon"
                ledger.append_exclusion(target=target, side=side, state=state, observed_at_utc=schedule.received_at_utc, detail=f"official active roster failed ({type(exc).__name__})", schedule_raw=schedule.body)
                result[state] += 1
    result["future"] = 2 * len(plan.targets) - len(terminal) - sum(result[key] for key in (CAPTURED, "source_error", "roster_malformed", "missed_before_horizon", "game_identity_ambiguous"))
    return result
