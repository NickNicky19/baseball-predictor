"""One-tick postgame label collection for receipted projected-lineup research."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Callable

from src.evaluation.projected_lineup_label_ledger import (
    CAPTURED,
    ProjectedLineupLabelLedger,
    ProjectedLineupLabelLedgerError,
)
from src.evaluation.projected_lineup_roster_ledger import (
    CAPTURED as ROSTER_CAPTURED,
    ProjectedLineupRosterLedger,
    roster_side_target_id,
)
from src.evaluation.shadow_capture_plan import ShadowCapturePlan


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collector clock must include timezone")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _is_final(feed: dict[str, object]) -> bool:
    return (
        isinstance(feed, dict)
        and feed.get("gameData", {}).get("status", {}).get("codedGameState") == "F"
    )


def _completed_game_original_batting_order_from_feed(
    game_pk: int, feed: dict[str, object]
) -> dict[str, list[int]]:
    teams = feed.get("liveData", {}).get("boxscore", {}).get("teams", {})
    if not isinstance(teams, dict):
        raise ProjectedLineupLabelLedgerError(
            f"game_pk={game_pk}: completed feed lacks boxscore teams"
        )
    orders: dict[str, list[int]] = {}
    for side in ("away", "home"):
        players = teams.get(side, {}).get("players", {})
        if not isinstance(players, dict):
            raise ProjectedLineupLabelLedgerError(
                f"game_pk={game_pk}: completed feed lacks {side} players"
            )
        by_slot: dict[int, int] = {}
        for player_key, player_data in players.items():
            if not isinstance(player_data, dict):
                continue
            raw = player_data.get("battingOrder")
            if raw is None:
                continue
            token = str(raw).strip()
            if not token.isdigit():
                raise ProjectedLineupLabelLedgerError(
                    f"game_pk={game_pk}: invalid battingOrder {raw!r} for {player_key}"
                )
            order = int(token)
            slot, sequence = divmod(order, 100)
            if not 1 <= slot <= 9:
                raise ProjectedLineupLabelLedgerError(
                    f"game_pk={game_pk}: battingOrder {raw!r} has invalid slot"
                )
            if sequence != 0:
                continue
            player_id = int(str(player_key).replace("ID", ""))
            if slot in by_slot:
                raise ProjectedLineupLabelLedgerError(
                    f"game_pk={game_pk}: multiple original starters in {side} slot {slot}"
                )
            by_slot[slot] = player_id
        expected_slots = set(range(1, 10))
        if set(by_slot) != expected_slots:
            raise ProjectedLineupLabelLedgerError(
                f"game_pk={game_pk}: incomplete original {side} batting order"
            )
        orders[side] = [by_slot[slot] for slot in range(1, 10)]
    return orders


def run_tick(
    *,
    plan: ShadowCapturePlan,
    roster_ledger: ProjectedLineupRosterLedger,
    label_ledger: ProjectedLineupLabelLedger,
    fetch_completed_feed: Callable[[int], bytes],
    now: datetime,
) -> dict[str, int]:
    """Bind completed-game original batting orders to prior roster receipts.

    The tick is repeatable while a game is unfinished. It becomes terminal only
    when a roster receipt is missing or a final official lineup label is either
    captured or explicitly quarantined.
    """

    current = _utc(now)
    roster_entries = roster_ledger.terminal_entries()
    terminal = label_ledger.terminal_side_ids()
    result = {
        CAPTURED: 0,
        "roster_receipt_missing": 0,
        "official_lineup_malformed": 0,
        "roster_label_mismatch": 0,
        "awaiting_game_completion": 0,
    }
    pending_by_game: dict[int, list[tuple[object, str, dict[str, object]]]] = {}
    for target in plan.targets:
        for side in ("away", "home"):
            side_id = roster_side_target_id(plan=plan, target=target, side=side)
            if side_id in terminal:
                continue
            roster_terminal = roster_entries.get(side_id)
            if roster_terminal is None:
                raise ProjectedLineupLabelLedgerError("roster ledger is missing an expected side target")
            state = roster_terminal.get("terminal_state")
            if state != ROSTER_CAPTURED:
                label_ledger.append_exclusion(
                    target=target,
                    side=side,
                    state="roster_receipt_missing",
                    observed_at_utc=_stamp(current),
                    detail=f"prior active-roster lane ended as {state}",
                    roster_terminal_ref=roster_ledger.terminal_reference(side_id),
                )
                result["roster_receipt_missing"] += 1
                continue
            pending_by_game.setdefault(target.mlb_game_pk, []).append((target, side, roster_terminal))
    for game_pk, items in pending_by_game.items():
        raw = fetch_completed_feed(game_pk)
        try:
            feed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            for target, side, roster_terminal in items:
                label_ledger.append_exclusion(
                    target=target,
                    side=side,
                    state="official_lineup_malformed",
                    observed_at_utc=_stamp(current),
                    detail="completed-game feed is not valid UTF-8 JSON",
                    roster_terminal_ref=roster_ledger.terminal_reference(
                        roster_side_target_id(plan=plan, target=target, side=side)
                    ),
                    final_feed_raw=raw,
                )
                result["official_lineup_malformed"] += 1
            continue
        if not _is_final(feed):
            result["awaiting_game_completion"] += len(items)
            continue
        try:
            orders = _completed_game_original_batting_order_from_feed(game_pk, feed)
        except Exception as exc:
            for target, side, roster_terminal in items:
                label_ledger.append_exclusion(
                    target=target,
                    side=side,
                    state="official_lineup_malformed",
                    observed_at_utc=_stamp(current),
                    detail=f"official original batting order failed ({type(exc).__name__})",
                    roster_terminal_ref=roster_ledger.terminal_reference(
                        roster_side_target_id(plan=plan, target=target, side=side)
                    ),
                    final_feed_raw=raw,
                )
                result["official_lineup_malformed"] += 1
            continue
        for target, side, roster_terminal in items:
            roster = roster_terminal["roster"]
            roster_players = roster.get("players") if isinstance(roster, dict) else None
            roster_ids = {
                int(item["player_id"])
                for item in roster_players or []
                if isinstance(item, dict) and isinstance(item.get("player_id"), int)
            }
            lineup_player_ids = orders[side]
            if set(lineup_player_ids) - roster_ids:
                label_ledger.append_exclusion(
                    target=target,
                    side=side,
                    state="roster_label_mismatch",
                    observed_at_utc=_stamp(current),
                    detail="official original batting order contains a player absent from the T-minus-4 active roster receipt",
                    roster_terminal_ref=roster_ledger.terminal_reference(
                        roster_side_target_id(plan=plan, target=target, side=side)
                    ),
                    final_feed_raw=raw,
                )
                result["roster_label_mismatch"] += 1
                continue
            label_ledger.append_capture(
                target=target,
                side=side,
                team_id=int(roster_terminal["team_id"]),
                roster_terminal_ref=roster_ledger.terminal_reference(
                    roster_side_target_id(plan=plan, target=target, side=side)
                ),
                lineup_player_ids=lineup_player_ids,
                final_feed_raw=raw,
                committed_utc=_stamp(current),
            )
            result[CAPTURED] += 1
    result["future"] = (2 * len(plan.targets)) - len(label_ledger.terminal_side_ids())
    return result
