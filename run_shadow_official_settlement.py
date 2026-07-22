#!/usr/bin/env python3
"""Resolve forward Hits entries from retained official MLB final-game facts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from run_shadow_close_collector import load_prestart_reference_bundle
from src.data.mlb_api import MLBStatsAPI
from src.evaluation.official_game_completion import game_completion_row
from src.evaluation.shadow_ledger import ForwardShadowLedger, ShadowEntry
from src.evaluation.shadow_live_provider import artifact_bytes
from src.evaluation.shadow_official_hits import official_hits_disposition
from src.evaluation.shadow_provider_adapter import build_hits_resolution
from src.evaluation.shadow_target_capture import load_resolved_quotes
from src.utils.provenance import sha256_file


class ShadowOfficialSettlementError(ValueError):
    """Raised when official settlement evidence conflicts or is malformed."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _publish_once(path: Path, payload: object) -> bool:
    data = artifact_bytes(payload)
    if path.exists():
        if path.read_bytes() != data:
            raise ShadowOfficialSettlementError(f"immutable official artifact conflict: {path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return True


def _close_evidence(
    *,
    entry: ShadowEntry,
    close_root: Path,
) -> dict[str, Any]:
    # Close artifacts are target-scoped, while entries are player-scoped. The
    # target id is not in the ledger, so locate only bundles on the exact date
    # and reject any ambiguous hard MARKET_KEY match.
    matches: list[dict[str, Any]] = []
    for candidate in sorted((close_root / entry.game_date).glob("*/prestart_reference_bundle.json")):
        payload = load_prestart_reference_bundle(candidate)
        if any(
            int(row.get("mlb_game_pk", 0)) == entry.mlb_game_pk
            and int(row.get("player_id", 0)) == entry.player_id
            and str(row.get("category")) == entry.category
            and float(row.get("line")) == entry.line
            and str(row.get("sportsbook")) == entry.sportsbook
            for row in payload.get("coverage_rows", [])
        ):
            matches.append(payload)
    if len(matches) > 1:
        raise ShadowOfficialSettlementError("entry MARKET_KEY appears in multiple prestart bundles")
    if not matches:
        return {}
    payload = matches[0]
    rows = [
        row for row in payload["coverage_rows"]
        if int(row["mlb_game_pk"]) == entry.mlb_game_pk
        and int(row["player_id"]) == entry.player_id
        and str(row["category"]) == entry.category
        and float(row["line"]) == entry.line
        and str(row["sportsbook"]) == entry.sportsbook
    ]
    if len(rows) != 1:
        raise ShadowOfficialSettlementError("prestart coverage row is absent or ambiguous")
    row = rows[0]
    if row["status"] != "resolved_prestart_reference":
        return {}
    quote_hash = str(row.get("close_quote_sha256", ""))
    resolved_path = Path(payload["resolved_quote_artifact_path"])
    quotes = load_resolved_quotes(resolved_path)
    if len([quote for quote in quotes if quote.quote_sha256 == quote_hash]) != 1:
        raise ShadowOfficialSettlementError("prestart coverage quote hash is absent or ambiguous")
    return {
        "close_raw_provider_artifact": payload["raw_provider_artifact_path"],
        "close_resolved_quote_artifact": payload["resolved_quote_artifact_path"],
        "close_quote_sha256": quote_hash,
    }


def settle_final_entries(
    *,
    official_date: str,
    ledger_path: str | Path,
    close_root: str | Path,
    official_root: str | Path,
    settlement_rule_artifact: str | Path,
    api: MLBStatsAPI,
    clock: Callable[[], str] = _now,
) -> dict[str, int]:
    """Append only final-game resolutions; pending games remain untouched."""

    ledger = ForwardShadowLedger(ledger_path)
    unresolved = [entry for entry in ledger.unresolved_entries() if entry.game_date == official_date]
    by_game: dict[int, list[ShadowEntry]] = defaultdict(list)
    for entry in unresolved:
        by_game[entry.mlb_game_pk].append(entry)
    final_games = set(api.get_final_game_pks(official_date)) if by_game else set()
    counts: Counter[str] = Counter()
    resolutions = []
    official_base = Path(official_root).resolve() / official_date
    close_base = Path(close_root).resolve()

    for game_pk, entries in sorted(by_game.items()):
        if game_pk not in final_games:
            counts["pending_game_not_final"] += len(entries)
            continue
        feed = api.get_completed_game_feed(game_pk)
        feed_path = official_base / str(game_pk) / "mlb_final_feed.json"
        _publish_once(feed_path, feed)
        feed_sha = sha256_file(feed_path)
        completion = game_completion_row(game_pk, official_date, feed).iloc[0].to_dict()
        regular_completed = bool(completion["regular_game_completed"])
        try:
            roles = api.completed_game_batting_roles_from_feed(game_pk, feed)
            role_error = ""
        except Exception as exc:
            roles = {}
            role_error = type(exc).__name__
        hitting, _ = api.game_boxscore_stats_from_feed(feed)

        for entry in sorted(entries, key=lambda value: value.entry_id):
            role = roles.get(entry.player_id)
            stats = hitting.get(entry.player_id)
            disposition = official_hits_disposition(
                entry=entry,
                official_role=role,
                official_pa=None if stats is None else stats.pa,
                official_hits=None if stats is None else stats.hits,
                regular_game_completed=regular_completed,
                settlement_rule_artifact=settlement_rule_artifact,
                official_game_feed_artifact_sha256=feed_sha,
            )
            if role_error and disposition["settlement_status"] == "unscored":
                disposition["official_role_parser_error_type"] = role_error
            outcome_path = official_base / str(game_pk) / "entries" / f"{entry.entry_id}.json"
            _publish_once(outcome_path, disposition)
            close = _close_evidence(entry=entry, close_root=close_base)
            resolution = build_hits_resolution(
                entry=entry,
                settled_at_utc=clock(),
                official_outcome_artifact=outcome_path,
                **close,
            )
            resolutions.append(resolution)
            counts[f"resolution_{resolution.settlement_status}"] += 1
    if resolutions:
        ledger.append_resolutions(resolutions)
    counts["unresolved_input"] = len(unresolved)
    counts["resolved_now"] = len(resolutions)
    counts["betting_authorized"] = 0
    return dict(sorted(counts.items()))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--ledger", default="data/learning/shadow/service/ledger/forward_ledger.jsonl")
    parser.add_argument("--close-root", default="data/learning/shadow/service/close")
    parser.add_argument("--official-root", default="data/learning/shadow/service/official")
    parser.add_argument(
        "--settlement-rule",
        default="config/shadow_draftkings_hits_reference_settlement.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        counts = settle_final_entries(
            official_date=args.date,
            ledger_path=args.ledger,
            close_root=args.close_root,
            official_root=args.official_root,
            settlement_rule_artifact=args.settlement_rule,
            api=MLBStatsAPI(),
        )
    except (OSError, ValueError) as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("vendor result used: FALSE; betting authorized: FALSE", file=sys.stderr)
        return 2
    print(f"OFFICIAL HITS SETTLEMENT {args.date}: {counts}")
    print("modeled DraftKings reference only; execution-product settlement verified: FALSE")
    print("betting authorized: FALSE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
