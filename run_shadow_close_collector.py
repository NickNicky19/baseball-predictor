#!/usr/bin/env python3
"""Capture one exact prestart DraftKings Hits reference snapshot.

The snapshot is called a prestart reference—not an actual fill and not a
guaranteed sportsbook close.  It reuses the entry capture's hard event/player
identity, retains the raw provider bytes, accounts for every entry market key,
and must finish before official first pitch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from src.evaluation.shadow_live_provider import (
    SOURCE_NAME,
    ShadowLiveProviderError,
    TheOddsAPIShadowClient,
    artifact_bytes,
    resolve_hits_snapshot,
    validate_resolved_hits_payload,
)
from src.evaluation.shadow_capture_plan import CaptureTarget
from src.evaluation.shadow_target_capture import (
    load_resolved_quotes,
    load_target_capture_bundle,
)
from src.utils.provenance import sha256_file


CLOSE_BUNDLE_SCHEMA = "shadow-prestart-reference-bundle-v1"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_dt(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _publish_once(path: Path, data: bytes) -> bool:
    if path.exists():
        if path.read_bytes() != data:
            raise ShadowLiveProviderError(f"immutable prestart artifact conflict: {path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return True


def _publish_json(path: Path, value: object) -> bool:
    return _publish_once(path, artifact_bytes(value))


def _receipt(response) -> dict[str, Any]:
    return {
        "schema_version": "shadow-live-provider-receipt-v1",
        "source_name": SOURCE_NAME,
        "phase": "prestart_reference",
        "received_at_utc": response.received_at_utc,
        "http_status": response.status_code,
        "quota": response.quota,
        "credential_recorded": False,
        "body_sha256": hashlib.sha256(response.body).hexdigest(),
        "betting_authorized": False,
    }


def _market_key(quote) -> tuple[int, int, str, str, str]:
    return quote.market_key


def _load_existing(path: Path) -> Path:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ShadowLiveProviderError("prestart reference bundle is malformed") from exc
    if payload.get("schema_version") != CLOSE_BUNDLE_SCHEMA:
        raise ShadowLiveProviderError("prestart reference bundle has an unknown schema")
    for path_field, hash_field in (
        ("raw_provider_artifact_path", "raw_provider_artifact_sha256"),
        ("resolved_quote_artifact_path", "resolved_quote_artifact_sha256"),
    ):
        artifact = Path(str(payload.get(path_field, "")))
        if not artifact.is_file() or sha256_file(artifact) != payload.get(hash_field):
            raise ShadowLiveProviderError("prestart reference bound artifact is missing or tampered")
    load_resolved_quotes(payload["resolved_quote_artifact_path"])
    return path


def load_prestart_reference_bundle(path: str | Path) -> dict[str, Any]:
    """Load and revalidate a published prestart reference bundle."""

    source = Path(path).resolve()
    _load_existing(source)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not payload.get("all_entry_markets_accounted_for"):
        raise ShadowLiveProviderError("prestart reference coverage funnel does not reconcile")
    counts = Counter(row.get("status") for row in payload.get("coverage_rows", []))
    if dict(sorted(counts.items())) != payload.get("status_counts"):
        raise ShadowLiveProviderError("prestart reference status counts do not reconcile")
    return payload


def capture_prestart_reference(
    *,
    entry_bundle_path: str | Path,
    output_root: str | Path,
    client: TheOddsAPIShadowClient,
    api_key_env: str,
    max_early_seconds: int,
    clock: Callable[[], str] = _now,
) -> Path:
    """Capture once inside the locked prestart window; never retry after error."""

    entry_path = Path(entry_bundle_path).resolve()
    entry_bundle = load_target_capture_bundle(entry_path)
    target = entry_bundle.target
    target_id = str(target["target_id"])
    root = Path(output_root).resolve() / str(target["official_game_date"]) / target_id
    bundle_path = root / "prestart_reference_bundle.json"
    terminal_error = root / "prestart_terminal_error.json"
    if bundle_path.exists():
        return _load_existing(bundle_path)
    if terminal_error.exists():
        raise ShadowLiveProviderError("prestart reference already has a terminal source error")
    if isinstance(max_early_seconds, bool) or int(max_early_seconds) <= 0:
        raise ShadowLiveProviderError("max_early_seconds must be a positive integer")

    started = clock()
    official_start = _utc_dt(str(target["official_start_time_utc"]))
    if _utc_dt(started) < official_start - timedelta(seconds=int(max_early_seconds)):
        raise ShadowLiveProviderError("prestart reference began before its locked operational window")
    if _utc_dt(started) >= official_start:
        raise ShadowLiveProviderError("official first pitch is due; a prestart reference cannot be backfilled")

    entry_resolved_path = Path(entry_bundle.resolved_quote_artifact_path)
    entry_payload = json.loads(entry_resolved_path.read_text(encoding="utf-8"))
    validate_resolved_hits_payload(entry_payload)
    game_identity = dict(entry_payload["game_identity"])
    event_id = str(game_identity.get("source_event_id", "")).strip()
    attempt_id = started.replace(":", "").replace("-", "") + "." + uuid.uuid4().hex
    attempt_root = root / "prestart_attempts" / attempt_id
    try:
        response = client.fetch_hits(event_id)
        if _utc_dt(response.received_at_utc) >= official_start:
            raise ShadowLiveProviderError("prestart provider response completed at/after official first pitch")
        raw_path = attempt_root / "prestart_raw.json"
        _publish_once(raw_path, response.body)
        _publish_json(attempt_root / "prestart_receipt.json", _receipt(response))
        player_identity, resolved = resolve_hits_snapshot(
            target=CaptureTarget.from_mapping(target),
            prediction_archive=entry_bundle.prediction_artifact_path,
            raw_provider_artifact=raw_path,
            game_identity=game_identity,
            game_identity_artifact_sha256=str(entry_payload["game_identity_artifact_sha256"]),
            observation_phase="prestart_reference",
            observation_cutoff_at_utc=str(target["official_start_time_utc"]),
        )
        validate_resolved_hits_payload(resolved)
        player_identity_path = attempt_root / "prestart_player_identity.json"
        resolved_path = attempt_root / "prestart_resolved.json"
        _publish_json(player_identity_path, player_identity)
        if sha256_file(player_identity_path) != resolved["player_identity_artifact_sha256"]:
            raise ShadowLiveProviderError("published prestart player identity hash drifted")
        _publish_json(resolved_path, resolved)

        completed = clock()
        if _utc_dt(completed) >= official_start:
            raise ShadowLiveProviderError("prestart reference finalization completed at/after first pitch")
        entry_quotes = load_resolved_quotes(entry_resolved_path)
        close_quotes = load_resolved_quotes(resolved_path)
        by_close = {_market_key(quote): quote for quote in close_quotes}
        if len(by_close) != len(close_quotes):
            raise ShadowLiveProviderError("prestart reference repeats a final MARKET_KEY")
        coverage: list[dict[str, Any]] = []
        for entry_quote in sorted(entry_quotes, key=_market_key):
            close = by_close.get(_market_key(entry_quote))
            coverage.append({
                "mlb_game_pk": entry_quote.mlb_game_pk,
                "player_id": entry_quote.player_id,
                "category": entry_quote.category,
                "line": entry_quote.line,
                "sportsbook": entry_quote.sportsbook,
                "entry_quote_sha256": entry_quote.quote_sha256,
                "status": "resolved_prestart_reference" if close is not None else "missing_prestart_reference",
                "close_quote_sha256": close.quote_sha256 if close is not None else None,
            })
        counts = dict(sorted(Counter(row["status"] for row in coverage).items()))
        bundle = {
            "schema_version": CLOSE_BUNDLE_SCHEMA,
            "target_id": target_id,
            "entry_bundle_sha256": entry_bundle.bundle_sha256,
            "official_start_time_utc": target["official_start_time_utc"],
            "started_at_utc": started,
            "completed_at_utc": completed,
            "observation_label": "exact_prestart_reference_not_guaranteed_last_available_price_or_fill",
            "raw_provider_artifact_path": str(raw_path.resolve()),
            "raw_provider_artifact_sha256": sha256_file(raw_path),
            "resolved_quote_artifact_path": str(resolved_path.resolve()),
            "resolved_quote_artifact_sha256": sha256_file(resolved_path),
            "entry_market_count": len(entry_quotes),
            "resolved_prestart_market_count": counts.get("resolved_prestart_reference", 0),
            "status_counts": counts,
            "coverage_rows": coverage,
            "all_entry_markets_accounted_for": len(coverage) == len(entry_quotes),
            "research_only": True,
            "actual_fill": False,
            "betting_authorized": False,
        }
        _publish_json(bundle_path, bundle)
        return _load_existing(bundle_path)
    except Exception as exc:
        secret = os.environ.get(api_key_env, "")
        message = str(exc).replace(secret, "[REDACTED]") if secret else str(exc)
        error = {
            "schema_version": "shadow-prestart-reference-error-v1",
            "target_id": target_id,
            "started_at_utc": started,
            "completed_at_utc": clock(),
            "error_type": type(exc).__name__,
            "redacted_message": message,
            "credential_present": bool(secret),
            "credential_recorded": False,
            "retry_permitted": False,
            "backfilled": False,
            "betting_authorized": False,
        }
        _publish_json(terminal_error, error)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry-bundle", required=True)
    parser.add_argument("--out-root", default="data/learning/shadow/service/close")
    parser.add_argument("--api-key-env", default="ODDS_API_KEY")
    parser.add_argument("--base-url", default="https://api.the-odds-api.com/v4")
    parser.add_argument("--timeout-seconds", type=int, default=25)
    parser.add_argument("--max-early-seconds", type=int, default=120)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = capture_prestart_reference(
            entry_bundle_path=args.entry_bundle,
            output_root=args.out_root,
            client=TheOddsAPIShadowClient(
                api_key_env=args.api_key_env,
                base_url=args.base_url,
                timeout_seconds=args.timeout_seconds,
            ),
            api_key_env=args.api_key_env,
            max_early_seconds=args.max_early_seconds,
        )
    except (OSError, ValueError) as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("backfilled: FALSE; actual fill: FALSE; betting authorized: FALSE", file=sys.stderr)
        return 2
    print(f"verified prestart reference bundle: {result}")
    print("actual fill: FALSE; betting authorized: FALSE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
