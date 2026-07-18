#!/usr/bin/env python3
"""Capture one future DraftKings Hits target with retained provider evidence.

This is the provider-facing worker intended for an always-on host.  A durable
scheduler invokes it shortly before each plan target.  It never places a bet,
never opens May 2026, and never retries a target after a terminal receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from src.evaluation.shadow_capture_plan import (
    CaptureAttempt,
    ShadowCapturePlan,
    load_capture_plan,
)
from src.evaluation.shadow_live_provider import (
    SOURCE_NAME,
    ShadowLiveProviderError,
    TheOddsAPIShadowClient,
    artifact_bytes,
    artifact_sha256,
    exact_game_identity,
    resolve_hits_snapshot,
    validate_resolved_hits_payload,
)
from src.evaluation.shadow_prediction_snapshot import load_shadow_prediction_snapshot
from src.evaluation.shadow_target_capture import (
    ShadowTargetCaptureError,
    finalize_target_capture,
    load_target_capture_bundle,
    publish_bundle,
)
from src.utils.provenance import sha256_file


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _publish_once(path: Path, data: bytes) -> bool:
    if path.exists():
        if path.read_bytes() != data:
            raise ShadowLiveProviderError(f"immutable artifact conflict: {path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return True


def _publish_json(path: Path, value: object) -> bool:
    return _publish_once(path, artifact_bytes(value))


def _schedule_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"schedule": payload.get("schedule")},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _target(plan: ShadowCapturePlan, target_id: str):
    matches = [value for value in plan.targets if value.target_id == target_id]
    if len(matches) != 1:
        raise ShadowLiveProviderError("target id is absent or ambiguous in capture plan")
    return matches[0]


def _redacted_error(exc: BaseException, api_key_env: str) -> dict[str, Any]:
    message = str(exc)
    secret = os.environ.get(api_key_env, "")
    if secret:
        message = message.replace(secret, "[REDACTED]")
    return {
        "schema_version": "shadow-live-source-error-v1",
        "error_type": type(exc).__name__,
        "redacted_message": message,
        "credential_present": bool(secret),
        "credential_value_recorded": False,
        "betting_authorized": False,
    }


def _receipt(response, *, phase: str) -> dict[str, Any]:
    return {
        "schema_version": "shadow-live-provider-receipt-v1",
        "source_name": SOURCE_NAME,
        "phase": phase,
        "received_at_utc": response.received_at_utc,
        "http_status": response.status_code,
        "quota": response.quota,
        "credential_recorded": False,
        "body_sha256": hashlib.sha256(response.body).hexdigest(),
        "betting_authorized": False,
    }


def _publish_success_receipts(bundle, *, target_root: Path) -> None:
    _publish_json(target_root / "terminal_attempt.json", bundle.attempt)
    _publish_json(target_root / "capture_status.json", {
        "schema_version": "shadow-live-capture-status-v1",
        "target_id": bundle.target["target_id"],
        "plan_sha256": bundle.plan_sha256,
        "outcome": bundle.attempt["outcome"],
        "bundle_sha256": bundle.bundle_sha256,
        "resolved_quote_count": len(bundle.quote_sha256),
        "betting_authorized": False,
    })


def capture_target(
    *,
    plan_path: Path,
    target_id: str,
    schedule_snapshot_path: Path,
    prediction_archive: Path,
    output_root: Path,
    client: TheOddsAPIShadowClient,
    api_key_env: str,
    max_early_seconds: int,
    clock: Callable[[], str] = _now,
    on_bundle_published: Callable[[Path], None] | None = None,
) -> Path:
    plan = load_capture_plan(plan_path)
    target = _target(plan, target_id)
    target_root = output_root / target.official_game_date / target.target_id
    bundle_path = target_root / "target_bundle.json"
    terminal_path = target_root / "terminal_attempt.json"
    if bundle_path.exists():
        existing_bundle = load_target_capture_bundle(bundle_path)
        if on_bundle_published is not None:
            on_bundle_published(bundle_path)
        _publish_success_receipts(existing_bundle, target_root=target_root)
        return bundle_path
    if terminal_path.exists():
        raise ShadowLiveProviderError(
            "target already has a terminal source-error receipt; refusing a second attempt"
        )

    if isinstance(max_early_seconds, bool) or int(max_early_seconds) <= 0:
        raise ShadowLiveProviderError("max_early_seconds must be a positive integer")
    started = clock()
    earliest = _utc_dt(target.entry_target_at_utc) - timedelta(seconds=int(max_early_seconds))
    if _utc_dt(started) < earliest:
        raise ShadowLiveProviderError(
            "target capture started before its locked operational window"
        )
    if _utc_dt(started) > _utc_dt(target.entry_target_at_utc):
        raise ShadowLiveProviderError("target is already due; forward evidence cannot be backfilled")
    snapshot = load_shadow_prediction_snapshot(prediction_archive)
    if snapshot.game_date != target.official_game_date:
        raise ShadowLiveProviderError("prediction archive date differs from target")
    if _utc_dt(snapshot.captured_at_utc) > _utc_dt(target.entry_target_at_utc):
        raise ShadowLiveProviderError("prediction snapshot was captured after target")

    schedule_payload = json.loads(schedule_snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(schedule_payload, dict):
        raise ShadowLiveProviderError("schedule snapshot root must be an object")
    if _schedule_digest(schedule_payload) != plan.schedule_snapshot_sha256:
        raise ShadowLiveProviderError("schedule snapshot hash differs from capture plan")

    attempt_id = started.replace(":", "").replace("-", "") + "." + uuid.uuid4().hex
    attempt_root = target_root / "attempts" / attempt_id
    bundle_published = False
    try:
        # Keep every success input inside the target tree.  The bundle still
        # records its original absolute paths, but an independent machine can
        # relocate those paths below target_id and verify the same hashes.
        plan_copy = attempt_root / "capture_plan.json"
        schedule_copy = attempt_root / "schedule_snapshot.json"
        prediction_copy = attempt_root / "prediction_snapshot.json"
        _publish_once(plan_copy, plan_path.read_bytes())
        _publish_once(schedule_copy, schedule_snapshot_path.read_bytes())
        _publish_once(prediction_copy, prediction_archive.read_bytes())
        if sha256_file(plan_copy) != sha256_file(plan_path):
            raise ShadowLiveProviderError("published capture plan copy drifted")
        if sha256_file(schedule_copy) != sha256_file(schedule_snapshot_path):
            raise ShadowLiveProviderError("published schedule snapshot copy drifted")
        if sha256_file(prediction_copy) != sha256_file(prediction_archive):
            raise ShadowLiveProviderError("published prediction snapshot copy drifted")

        events_response = client.fetch_events(target.official_game_date)
        if _utc_dt(events_response.received_at_utc) > _utc_dt(target.entry_target_at_utc):
            raise ShadowLiveProviderError("events response completed after target")
        events_raw = attempt_root / "events_raw.json"
        _publish_once(events_raw, events_response.body)
        _publish_json(attempt_root / "events_receipt.json", _receipt(events_response, phase="events"))
        events = events_response.json()
        if not isinstance(events, list):
            raise ShadowLiveProviderError("provider events response must be a list")
        game_identity = exact_game_identity(
            target=target,
            schedule_snapshot=schedule_payload,
            provider_events=events,
        )
        game_identity_path = attempt_root / "game_identity.json"
        _publish_json(game_identity_path, game_identity)
        game_identity_sha = artifact_sha256(game_identity)

        odds_response = client.fetch_hits(str(game_identity["source_event_id"]))
        if _utc_dt(odds_response.received_at_utc) > _utc_dt(target.entry_target_at_utc):
            raise ShadowLiveProviderError("entry odds response completed after target")
        raw_odds_path = attempt_root / "entry_raw.json"
        _publish_once(raw_odds_path, odds_response.body)
        _publish_json(attempt_root / "entry_receipt.json", _receipt(odds_response, phase="entry"))

        player_identity, resolved = resolve_hits_snapshot(
            target=target,
            prediction_archive=prediction_copy,
            raw_provider_artifact=raw_odds_path,
            game_identity=game_identity,
            game_identity_artifact_sha256=game_identity_sha,
        )
        validate_resolved_hits_payload(resolved)
        player_identity_path = attempt_root / "player_identity.json"
        resolved_path = attempt_root / "entry_resolved.json"
        _publish_json(player_identity_path, player_identity)
        if sha256_file(player_identity_path) != resolved["player_identity_artifact_sha256"]:
            raise ShadowLiveProviderError("published player identity hash drifted")
        _publish_json(resolved_path, resolved)

        completed = clock()
        if _utc_dt(completed) > _utc_dt(target.entry_target_at_utc):
            raise ShadowLiveProviderError("capture finalization completed after target")
        bundle = finalize_target_capture(
            plan=plan,
            target_id=target.target_id,
            started_at_utc=started,
            completed_at_utc=completed,
            source_name=SOURCE_NAME,
            expected_config_sha256=snapshot.effective_config_sha256,
            prediction_archive=prediction_copy,
            raw_provider_artifact=raw_odds_path,
            resolved_quote_artifact=resolved_path,
        )
        publish_bundle(bundle, bundle_path)
        bundle_published = True
        if on_bundle_published is not None:
            on_bundle_published(bundle_path)
        _publish_success_receipts(bundle, target_root=target_root)
        return bundle_path
    except Exception as exc:
        completed = clock()
        error_payload = _redacted_error(exc, api_key_env)
        error_payload.update({
            "target_id": target.target_id,
            "plan_sha256": plan.plan_sha256,
            "started_at_utc": started,
            "completed_at_utc": completed,
            "completed_by_target": _utc_dt(completed) <= _utc_dt(target.entry_target_at_utc),
        })
        error_path = attempt_root / "source_error.json"
        _publish_json(error_path, error_payload)
        if bundle_published:
            _publish_json(target_root / "lifecycle_error.json", {
                **error_payload,
                "schema_version": "shadow-live-lifecycle-error-v1",
                "bundle_sha256": sha256_file(bundle_path),
                "retry_permitted_only_before_entry_target": True,
            })
            raise
        attempt = CaptureAttempt(
            target_id=target.target_id,
            plan_sha256=plan.plan_sha256,
            started_at_utc=started,
            completed_at_utc=completed,
            source_name=SOURCE_NAME,
            source_payload_sha256=sha256_file(error_path),
            outcome="source_error",
            resolved_two_sided_quote_count=0,
            detail=type(exc).__name__,
        )
        _publish_json(terminal_path, attempt.to_dict())
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--schedule-snapshot", required=True)
    parser.add_argument("--prediction-archive", required=True)
    parser.add_argument("--out-root", default="data/learning/shadow/live")
    parser.add_argument("--api-key-env", default="ODDS_API_KEY")
    parser.add_argument("--base-url", default="https://api.the-odds-api.com/v4")
    parser.add_argument("--timeout-seconds", type=int, default=25)
    parser.add_argument(
        "--max-early-seconds",
        type=int,
        required=True,
        help="Locked operational window; capture may not start earlier than this before T-4h",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        path = capture_target(
            plan_path=Path(args.plan),
            target_id=args.target_id,
            schedule_snapshot_path=Path(args.schedule_snapshot),
            prediction_archive=Path(args.prediction_archive),
            output_root=Path(args.out_root),
            client=TheOddsAPIShadowClient(
                api_key_env=args.api_key_env,
                base_url=args.base_url,
                timeout_seconds=args.timeout_seconds,
            ),
            api_key_env=args.api_key_env,
            max_early_seconds=args.max_early_seconds,
        )
    except (OSError, ValueError, ShadowLiveProviderError, ShadowTargetCaptureError) as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("betting authorized: FALSE", file=sys.stderr)
        return 2
    print(f"LIVE SHADOW TARGET COMPLETE: {path}")
    print("ledger appended: FALSE; betting authorized: FALSE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
