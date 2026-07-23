"""Fail-closed scheduling primitives for the isolated AWS comparator service.

The module has no outcome reader.  It permits prediction generation only in a
predeclared pre-horizon window, permits provider collection only before T-4,
and turns a missed window into an immutable terminal fact rather than a retry
or backfill opportunity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from src.evaluation.shadow_live_provider import (
    ProviderResponse,
    artifact_bytes,
    exact_game_identity,
)
from src.evaluation.shared_pa_comparator_capture import (
    MARKET_SPEC,
    build_frozen_comparator,
    build_market_comparators,
    publish_comparator_record,
)
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan


RUNTIME_SCHEMA = "shared-pa-comparator-runtime-v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")


class SharedPAComparatorRuntimeError(ValueError):
    """Runtime configuration, timing, or immutable evidence is unsafe."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _utc(value: datetime | str, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise SharedPAComparatorRuntimeError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SharedPAComparatorRuntimeError(f"{label} lacks timezone")
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return _utc(value, "timestamp").isoformat(timespec="microseconds").replace("+00:00", "Z")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise SharedPAComparatorRuntimeError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def read_object_bytes(raw: bytes, label: str) -> Mapping[str, Any]:
    if not raw:
        raise SharedPAComparatorRuntimeError(f"{label} is empty")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedPAComparatorRuntimeError(f"{label} is not unique-key UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise SharedPAComparatorRuntimeError(f"{label} root is not an object")
    return value


def load_runtime(path: str | Path, *, repository_root: str | Path) -> tuple[dict[str, Any], str]:
    source = Path(path).resolve()
    repository = Path(repository_root).resolve()
    try:
        source.relative_to(repository)
        raw = source.read_bytes()
    except (OSError, ValueError) as exc:
        raise SharedPAComparatorRuntimeError("runtime manifest is unreadable or outside repository") from exc
    payload = dict(read_object_bytes(raw, "runtime manifest"))
    expected_scheduler = {
        "entry_hours": 4,
        "prediction_window_opens_seconds_before_horizon": 2700,
        "prediction_window_closes_seconds_before_horizon": 300,
        "market_window_opens_seconds_before_horizon": 180,
        "finalize_grace_seconds_after_horizon": 300,
        "prediction_tick_seconds": 120,
        "market_tick_seconds": 15,
        "finalize_tick_seconds": 30,
    }
    expected_provider = {
        "api_key_environment_variable": "ODDS_API_KEY",
        "markets": ["batter_hits", "batter_home_runs", "batter_total_bases"],
        "bookmaker": "draftkings",
        "maximum_event_start_delta_seconds": 60,
    }
    expected_invariants = {
        "read_pitcher_receipts_only": True,
        "read_shared_pa_snapshots_only": True,
        "separate_evidence_root": True,
        "immutable_publication": True,
        "post_horizon_prediction_or_market_fetch_forbidden": True,
        "late_backfill_forbidden": True,
        "may_2026_forbidden": True,
        "outcomes_and_settlement_forbidden": True,
        "historical_executability_claim_forbidden": True,
        "existing_collectors_untouched": True,
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
    }
    if (
        payload.get("schema_version") != RUNTIME_SCHEMA
        or payload.get("scope") != "future_only_three_market_comparator_capture"
        or payload.get("contract") != {
            "path": "config/shared_pa_comparator_capture_v1.json",
            "sha256": "0db198cc31b127b80d6ac84f2a944722cb649b068552bb6c069cada635fe37f0",
        }
        or payload.get("scheduler") != expected_scheduler
        or payload.get("provider") != expected_provider
        or payload.get("invariants") != expected_invariants
    ):
        raise SharedPAComparatorRuntimeError("runtime manifest contract changed")
    contract_path = repository / payload["contract"]["path"]
    try:
        contract_sha = hashlib.sha256(contract_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise SharedPAComparatorRuntimeError("comparator contract is unreadable") from exc
    if contract_sha != payload["contract"]["sha256"]:
        raise SharedPAComparatorRuntimeError("comparator contract bytes changed")
    return payload, hashlib.sha256(raw).hexdigest()


def publish_once(path: str | Path, payload: bytes) -> bool:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if output.read_bytes() != payload:
            raise SharedPAComparatorRuntimeError(f"immutable artifact differs: {output.name}")
        return False
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, output)
        except FileExistsError:
            if output.read_bytes() != payload:
                raise SharedPAComparatorRuntimeError(
                    f"concurrent immutable artifact differs: {output.name}"
                )
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def publish_tree_once(path: str | Path, files: Mapping[str, bytes]) -> bool:
    """Atomically publish a complete immutable directory or verify its retry."""
    output = Path(path)
    if not files or any(
        not name or Path(name).name != name or not isinstance(payload, bytes)
        for name, payload in files.items()
    ):
        raise SharedPAComparatorRuntimeError("immutable tree file map is invalid")
    if output.exists():
        actual = {item.name: item.read_bytes() for item in output.iterdir() if item.is_file()}
        if actual != dict(files) or any(item.is_dir() for item in output.iterdir()):
            raise SharedPAComparatorRuntimeError(f"immutable artifact tree differs: {output.name}")
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        for name, payload in files.items():
            destination = temporary / name
            with destination.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            os.rename(temporary, output)
        except FileExistsError:
            actual = {item.name: item.read_bytes() for item in output.iterdir() if item.is_file()}
            if actual != dict(files) or any(item.is_dir() for item in output.iterdir()):
                raise SharedPAComparatorRuntimeError(
                    f"concurrent immutable artifact tree differs: {output.name}"
                )
            return False
        return True
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def horizon_batch_id(plan: ShadowCapturePlan, target: CaptureTarget) -> str:
    if target.target_id not in {item.target_id for item in plan.targets}:
        raise SharedPAComparatorRuntimeError("target does not belong to plan")
    return hashlib.sha256(canonical_bytes({
        "schema_version": "shared-pa-comparator-horizon-batch-v1",
        "plan_sha256": plan.plan_sha256,
        "target_horizon_utc": target.entry_target_at_utc,
    })).hexdigest()


def targets_by_horizon(plan: ShadowCapturePlan) -> dict[str, tuple[CaptureTarget, ...]]:
    grouped: dict[str, list[CaptureTarget]] = {}
    for target in plan.targets:
        grouped.setdefault(target.entry_target_at_utc, []).append(target)
    return {
        horizon: tuple(sorted(targets, key=lambda item: item.target_id))
        for horizon, targets in sorted(grouped.items())
    }


@dataclass(frozen=True)
class PredictionArtifacts:
    frozen_archive: bytes
    total_bases_archive: bytes

    def __post_init__(self) -> None:
        read_object_bytes(self.frozen_archive, "frozen archive")
        read_object_bytes(self.total_bases_archive, "Total Bases archive")


def _prediction_manifest(
    *, plan: ShadowCapturePlan, target: CaptureTarget, artifacts: PredictionArtifacts,
    completed_at: datetime, runtime_sha256: str,
) -> dict[str, Any]:
    horizon = _utc(target.entry_target_at_utc, "target horizon")
    completed = _utc(completed_at, "prediction completion")
    if completed > horizon:
        raise SharedPAComparatorRuntimeError("prediction generation completed after T-4")
    return {
        "schema_version": "shared-pa-comparator-prediction-batch-v1",
        "plan_sha256": plan.plan_sha256,
        "horizon_batch_id": horizon_batch_id(plan, target),
        "target_horizon_utc": target.entry_target_at_utc,
        "completed_at_utc": _stamp(completed),
        "frozen_archive_sha256": hashlib.sha256(artifacts.frozen_archive).hexdigest(),
        "total_bases_archive_sha256": hashlib.sha256(artifacts.total_bases_archive).hexdigest(),
        "runtime_manifest_sha256": runtime_sha256,
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
    }


def prepare_prediction_batches(
    *, plan: ShadowCapturePlan, evidence_root: str | Path,
    scheduler: Mapping[str, Any], runtime_sha256: str, now: datetime,
    generate: Callable[[str], tuple[PredictionArtifacts, datetime]],
) -> dict[str, int]:
    """Generate once per horizon only in the locked pre-horizon window."""
    if plan.official_game_date.startswith("2026-05-"):
        raise SharedPAComparatorRuntimeError("May 2026 is sealed")
    current = _utc(now, "tick clock")
    root = Path(evidence_root) / "pregame" / plan.official_game_date / plan.plan_sha256 / "prediction_batches"
    counts = {"prepared": 0, "verified_retry": 0, "future": 0, "missed": 0}
    generated_this_tick: tuple[PredictionArtifacts, datetime] | None = None
    for horizon_text, targets in targets_by_horizon(plan).items():
        target = targets[0]
        horizon = _utc(horizon_text, "target horizon")
        batch = root / horizon_batch_id(plan, target)
        manifest_path = batch / "manifest.json"
        missed_path = batch / "terminal_missed.json"
        opens = horizon - timedelta(seconds=int(scheduler["prediction_window_opens_seconds_before_horizon"]))
        closes = horizon - timedelta(seconds=int(scheduler["prediction_window_closes_seconds_before_horizon"]))
        if manifest_path.exists():
            manifest = read_object_bytes(manifest_path.read_bytes(), "prediction batch manifest")
            if manifest.get("horizon_batch_id") != horizon_batch_id(plan, target):
                raise SharedPAComparatorRuntimeError("prediction batch manifest identity changed")
            counts["verified_retry"] += 1
            continue
        if missed_path.exists():
            counts["missed"] += 1
            continue
        if current < opens:
            counts["future"] += 1
            continue
        if current > closes:
            terminal = {
                "schema_version": "shared-pa-comparator-prediction-terminal-v1",
                "terminal_state": "missed_before_prediction_window_closed",
                "plan_sha256": plan.plan_sha256,
                "horizon_batch_id": horizon_batch_id(plan, target),
                "target_horizon_utc": horizon_text,
                "observed_at_utc": _stamp(current),
                "late_backfill_attempted": False,
                "research_only": True,
                "betting_authorized": False,
            }
            publish_once(missed_path, canonical_bytes(terminal))
            counts["missed"] += 1
            continue
        if generated_this_tick is None:
            generated_this_tick = generate(plan.official_game_date)
        artifacts, completed = generated_this_tick
        manifest = _prediction_manifest(
            plan=plan, target=target, artifacts=artifacts,
            completed_at=completed, runtime_sha256=runtime_sha256,
        )
        publish_tree_once(batch, {
            "frozen_predictions.json": artifacts.frozen_archive,
            "total_bases_predictions.json": artifacts.total_bases_archive,
            "manifest.json": canonical_bytes(manifest),
        })
        counts["prepared"] += 1
    return counts


def _attempt(
    *, directory: Path, target: CaptureTarget, observed: datetime,
    state: str, detail: str,
) -> None:
    safe_detail = str(detail).strip()
    if not safe_detail or any(token in safe_detail.casefold() for token in ("apikey", "api_key", "http://", "https://")):
        raise SharedPAComparatorRuntimeError("attempt detail is blank or may disclose a credential/URL")
    body = {
        "schema_version": "shared-pa-comparator-market-attempt-v1",
        "target_id": target.target_id,
        "official_game_date": target.official_game_date,
        "target_horizon_utc": target.entry_target_at_utc,
        "observed_at_utc": _stamp(observed),
        "state": state,
        "detail": safe_detail,
        "research_only": True,
        "betting_authorized": False,
    }
    digest = hashlib.sha256(canonical_bytes(body)).hexdigest()
    publish_once(directory / "attempts" / f"{digest}.json", canonical_bytes(body))


def capture_market_artifacts(
    *, plan: ShadowCapturePlan, schedule_snapshot: Mapping[str, Any],
    evidence_root: str | Path, scheduler: Mapping[str, Any],
    runtime_sha256: str, now: datetime,
    fetch_events: Callable[[str], ProviderResponse],
    fetch_markets: Callable[[str], ProviderResponse],
) -> dict[str, int]:
    """Capture one three-market response per due game, never after T-4."""
    if plan.official_game_date.startswith("2026-05-"):
        raise SharedPAComparatorRuntimeError("May 2026 is sealed")
    if not isinstance(schedule_snapshot.get("schedule"), list):
        raise SharedPAComparatorRuntimeError("schedule snapshot is missing canonical schedule rows")
    current = _utc(now, "market tick clock")
    root = Path(evidence_root) / "pregame" / plan.official_game_date / plan.plan_sha256 / "markets"
    counts = {"captured": 0, "verified_retry": 0, "future": 0, "attempt_failed": 0, "missed": 0}
    due: list[CaptureTarget] = []
    for target in plan.targets:
        directory = root / target.target_id
        capture_dir = directory / "capture"
        manifest_path = capture_dir / "manifest.json"
        missed_path = directory / "terminal_missed.json"
        if manifest_path.exists():
            manifest = read_object_bytes(manifest_path.read_bytes(), "market manifest")
            if manifest.get("target_id") != target.target_id:
                raise SharedPAComparatorRuntimeError("market manifest identity changed")
            counts["verified_retry"] += 1
            continue
        if missed_path.exists():
            counts["missed"] += 1
            continue
        horizon = _utc(target.entry_target_at_utc, "target horizon")
        opens = horizon - timedelta(seconds=int(scheduler["market_window_opens_seconds_before_horizon"]))
        if current < opens:
            counts["future"] += 1
            continue
        if current > horizon:
            terminal = {
                "schema_version": "shared-pa-comparator-market-terminal-v1",
                "terminal_state": "missed_before_t4",
                "target_id": target.target_id,
                "official_game_date": target.official_game_date,
                "target_horizon_utc": target.entry_target_at_utc,
                "observed_at_utc": _stamp(current),
                "late_backfill_attempted": False,
                "research_only": True,
                "betting_authorized": False,
            }
            publish_once(missed_path, canonical_bytes(terminal))
            counts["missed"] += 1
            continue
        due.append(target)
    if not due:
        return counts
    try:
        events_response = fetch_events(plan.official_game_date)
        events_received = _utc(events_response.received_at_utc, "provider events receipt")
        events = events_response.json()
        if not isinstance(events, list):
            raise SharedPAComparatorRuntimeError("provider events root is not a list")
    except Exception as exc:
        observed = datetime.now(timezone.utc)
        for target in due:
            _attempt(
                directory=root / target.target_id, target=target, observed=observed,
                state="source_error", detail=f"provider events fetch failed ({type(exc).__name__})",
            )
            counts["attempt_failed"] += 1
        return counts
    for target in due:
        directory = root / target.target_id
        horizon = _utc(target.entry_target_at_utc, "target horizon")
        try:
            if events_received > horizon:
                raise SharedPAComparatorRuntimeError("provider events response arrived after T-4")
            identity = exact_game_identity(
                target=target,
                schedule_snapshot=schedule_snapshot,
                provider_events=events,
                max_event_start_delta_seconds=60,
            )
            event_id = str(identity["source_event_id"])
            odds_response = fetch_markets(event_id)
            odds_received = _utc(odds_response.received_at_utc, "provider market receipt")
            if odds_received > horizon:
                raise SharedPAComparatorRuntimeError("provider market response arrived after T-4")
            odds = odds_response.json()
            if not isinstance(odds, Mapping) or odds.get("id") != event_id:
                raise SharedPAComparatorRuntimeError("provider market event identity differs")
            identity_raw = artifact_bytes(identity)
            events_sha = hashlib.sha256(events_response.body).hexdigest()
            odds_sha = hashlib.sha256(odds_response.body).hexdigest()
            identity_sha = hashlib.sha256(identity_raw).hexdigest()
            manifest = {
                "schema_version": "shared-pa-comparator-market-capture-v1",
                "terminal_state": "captured_pre_t4",
                "plan_sha256": plan.plan_sha256,
                "target_id": target.target_id,
                "official_game_date": target.official_game_date,
                "target_horizon_utc": target.entry_target_at_utc,
                "events_received_at_utc": _stamp(events_received),
                "market_received_at_utc": _stamp(odds_received),
                "events_artifact_sha256": events_sha,
                "game_identity_artifact_sha256": identity_sha,
                "market_artifact_sha256": odds_sha,
                "provider_event_id": event_id,
                "runtime_manifest_sha256": runtime_sha256,
                "markets_requested_together": [
                    "batter_hits", "batter_home_runs", "batter_total_bases"
                ],
                "executable_or_fill_claimed": False,
                "research_only": True,
                "betting_authorized": False,
            }
            publish_tree_once(capture_dir, {
                "events.raw.json": events_response.body,
                "game_identity.json": identity_raw,
                "markets.raw.json": odds_response.body,
                "manifest.json": canonical_bytes(manifest),
            })
            counts["captured"] += 1
        except Exception as exc:
            _attempt(
                directory=directory, target=target, observed=datetime.now(timezone.utc),
                state="source_or_identity_error",
                detail=f"three-market capture failed ({type(exc).__name__})",
            )
            counts["attempt_failed"] += 1
    return counts


def finalize_target(
    *, target: CaptureTarget, player_snapshots: list[Mapping[str, Any]],
    pitcher_context_record: Mapping[str, Any], frozen_archive: bytes,
    total_bases_archive: bytes, loaded_contract: Mapping[str, Any],
    output_root: str | Path, finalized_at: datetime,
    automation_runtime_sha256: str,
    dependency_lock_sha256: str,
    provider_bytes: bytes | None = None,
    provider_received_at_utc: str | None = None,
    game_identity_bytes: bytes | None = None,
) -> dict[str, int]:
    """Build and atomically publish one target's model/market comparator set.

    All records are constructed before the first publication.  A malformed
    prerequisite therefore cannot leave a misleading partial target tree.
    Missing market capture is retained as an explicit non-probability state;
    it never suppresses a valid frozen model comparator.
    """
    if target.official_game_date.startswith("2026-05-"):
        raise SharedPAComparatorRuntimeError("May 2026 is sealed")
    if not _SHA.fullmatch(str(automation_runtime_sha256)) or not _SHA.fullmatch(str(dependency_lock_sha256)):
        raise SharedPAComparatorRuntimeError("finalizer runtime/dependency hash is invalid")
    if not player_snapshots:
        raise SharedPAComparatorRuntimeError("finalizer has no receipt-bound player snapshots")
    player_ids = [snapshot.get("player_id") for snapshot in player_snapshots]
    if len(set(player_ids)) != len(player_ids):
        raise SharedPAComparatorRuntimeError("finalizer player snapshots repeat identity")
    frozen_records = [
        build_frozen_comparator(
            archive_bytes=frozen_archive,
            total_bases_archive_bytes=total_bases_archive,
            target=target,
            player_snapshot=snapshot,
            pitcher_context_record=pitcher_context_record,
            loaded_contract=loaded_contract,
        )
        for snapshot in player_snapshots
    ]
    for record in frozen_records:
        record["automation_runtime_sha256"] = automation_runtime_sha256
        record["dependency_lock_sha256"] = dependency_lock_sha256
    supplied_market = (
        provider_bytes is not None,
        provider_received_at_utc is not None,
        game_identity_bytes is not None,
    )
    if any(supplied_market) and not all(supplied_market):
        raise SharedPAComparatorRuntimeError("market artifact triple is incomplete")
    if all(supplied_market):
        market_records = build_market_comparators(
            provider_bytes=provider_bytes or b"",
            received_at_utc=str(provider_received_at_utc),
            game_identity_bytes=game_identity_bytes or b"",
            target=target,
            frozen_comparators=frozen_records,
            pitcher_context_record=pitcher_context_record,
            loaded_contract=loaded_contract,
        )
    else:
        market_records = []
        for frozen in frozen_records:
            for market, (_, _, lines) in MARKET_SPEC.items():
                for line in lines:
                    market_records.append({
                    "terminal_state": "source_or_integrity_failure",
                    "market": market,
                    "line": line,
                    "mlb_game_pk": target.mlb_game_pk,
                    "side": frozen["side"],
                    "team_id": frozen["team_id"],
                    "player_id": frozen["player_id"],
                    "target_id": target.target_id,
                    "official_game_date": target.official_game_date,
                    "official_start_utc": target.official_start_time_utc,
                    "target_horizon_utc": target.entry_target_at_utc,
                    "selection_evidence": {},
                    "binary_probabilities": {},
                    "probability_consumed": False,
                    "detail": "no timely three-market provider artifact",
                    "research_only": True,
                    "executable_price_claimed": False,
                    "betting_authorized": False,
                    })
    for record in market_records:
        record["automation_runtime_sha256"] = automation_runtime_sha256
        record["dependency_lock_sha256"] = dependency_lock_sha256
    root = Path(output_root) / target.official_game_date / target.target_id
    published = 0
    for record in frozen_records:
        path = root / "frozen" / str(record["side"]) / f"{record['player_id']}.json"
        published += int(publish_comparator_record(
            record_type="frozen_bundle", record=record, path=path
        ))
    for index, record in enumerate(market_records):
        player = "unmatched" if record.get("player_id") is None else str(record["player_id"])
        suffix = hashlib.sha256(canonical_bytes({
            "market": record.get("market"),
            "player_id": record.get("player_id"),
            "line": record.get("line"),
            "provider_description_sha256": record.get("provider_description_sha256"),
            "ordinal": index,
        })).hexdigest()
        path = root / "markets" / str(record["market"]) / player / f"{suffix}.json"
        published += int(publish_comparator_record(
            record_type="market_record", record=record, path=path
        ))
    summary = {
        "official_game_date": target.official_game_date,
        "target_id": target.target_id,
        "terminal_state": "finalized",
        "finalized_at_utc": _stamp(finalized_at),
        "frozen_player_records": len(frozen_records),
        "market_records": len(market_records),
        "timely_market_artifact_available": all(supplied_market),
        "outcomes_or_settlement_accessed": False,
        "research_only": True,
        "betting_authorized": False,
    }
    published += int(publish_comparator_record(
        record_type="target_terminal", record=summary, path=root / "terminal.json"
    ))
    return {
        "frozen_records": len(frozen_records),
        "market_records": len(market_records),
        "published": published,
    }


def publish_target_failure(
    *, target: CaptureTarget, output_root: str | Path,
    observed_at: datetime, detail: str,
) -> bool:
    """Permanently account for a target whose prerequisites never arrived."""
    clean = str(detail).strip()
    if not clean:
        raise SharedPAComparatorRuntimeError("target failure detail is blank")
    record = {
        "official_game_date": target.official_game_date,
        "target_id": target.target_id,
        "terminal_state": "source_or_integrity_failure",
        "observed_at_utc": _stamp(observed_at),
        "detail": clean,
        "probability_consumed": False,
        "late_backfill_attempted": False,
        "outcomes_or_settlement_accessed": False,
        "research_only": True,
        "betting_authorized": False,
    }
    path = Path(output_root) / target.official_game_date / target.target_id / "terminal.json"
    return publish_comparator_record(record_type="target_terminal", record=record, path=path)
