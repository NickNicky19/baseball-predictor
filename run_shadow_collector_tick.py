#!/usr/bin/env python3
"""Run one unattended, fail-closed forward-shadow service tick.

A system timer calls this once per minute.  The tick builds or verifies the
future plan, prepares a provenance-bearing model snapshot before each target,
captures DraftKings Hits inside the locked pre-T-4h operational window, and
commits only the locked research selections to the forward ledger before the
horizon. It never backfills, opens May, or places a wager.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from src.data.mlb_api import MLBStatsAPI
from src.evaluation.shadow_capture_plan import (
    CaptureTarget,
    ShadowCapturePlan,
    ShadowCapturePlanError,
    canonical_schedule_records,
    load_capture_plan,
    plan_from_schedule,
)
from src.evaluation.shadow_live_provider import TheOddsAPIShadowClient, artifact_bytes
from src.evaluation.shadow_lifecycle import commit_target_entries
from src.evaluation.shadow_prediction_snapshot import load_shadow_prediction_snapshot
from src.evaluation.shadow_provider_adapter import load_research_selection_policy
from src.evaluation.shadow_target_capture import load_target_capture_bundle
from src.evaluation.forward_evidence_era import validate_evidence_scope
from src.utils.provenance import sha256_file
from run_shadow_close_collector import capture_prestart_reference
from run_shadow_official_settlement import settle_final_entries
from run_shadow_primary_collector import capture_target


RUNTIME_SCHEMA = "shadow-collector-runtime-v1"
Action = Literal["future", "prepare", "wait_capture", "capture", "complete", "missed"]


class ShadowCollectorServiceError(ValueError):
    """Raised when an unattended tick cannot preserve the evidence contract."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ShadowCollectorServiceError(f"{label} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ShadowCollectorServiceError(f"{label} must be a positive integer") from exc
    if parsed <= 0:
        raise ShadowCollectorServiceError(f"{label} must be a positive integer")
    return parsed


@dataclass(frozen=True)
class RuntimeConfig:
    prediction_lead_seconds: int
    capture_max_early_seconds: int
    service_timer_seconds: int
    max_parallel_targets: int
    provider_timeout_seconds: int
    api_key_environment: str
    provider_base_url: str
    sha256: str


def load_runtime_config(path: Path) -> RuntimeConfig:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != RUNTIME_SCHEMA:
        raise ShadowCollectorServiceError("collector runtime has an unknown schema")
    if payload.get("status") != "RESEARCH_ONLY" or payload.get("betting_authorized") is not False:
        raise ShadowCollectorServiceError("collector runtime must remain explicitly research-only")
    api_key_environment = str(payload.get("api_key_environment", "")).strip()
    base_url = str(payload.get("provider_base_url", "")).strip()
    if api_key_environment != "ODDS_API_KEY":
        raise ShadowCollectorServiceError("collector runtime must use the isolated ODDS_API_KEY environment")
    if base_url != "https://api.the-odds-api.com/v4":
        raise ShadowCollectorServiceError("collector runtime provider URL differs from the locked official v4 endpoint")
    runtime = RuntimeConfig(
        prediction_lead_seconds=_positive_int(payload.get("prediction_lead_seconds"), "prediction_lead_seconds"),
        capture_max_early_seconds=_positive_int(
            payload.get("capture_max_early_seconds"), "capture_max_early_seconds"
        ),
        service_timer_seconds=_positive_int(payload.get("service_timer_seconds"), "service_timer_seconds"),
        max_parallel_targets=_positive_int(payload.get("max_parallel_targets"), "max_parallel_targets"),
        provider_timeout_seconds=_positive_int(
            payload.get("provider_timeout_seconds"), "provider_timeout_seconds"
        ),
        api_key_environment=api_key_environment,
        provider_base_url=base_url,
        sha256=sha256_file(path),
    )
    if runtime.service_timer_seconds > runtime.capture_max_early_seconds:
        raise ShadowCollectorServiceError(
            "service timer interval exceeds the locked capture window"
        )
    if runtime.prediction_lead_seconds <= runtime.capture_max_early_seconds:
        raise ShadowCollectorServiceError(
            "prediction lead must begin before the provider capture window"
        )
    return runtime


def target_action(
    target: CaptureTarget,
    *,
    now_utc: str,
    prediction_ready: bool,
    terminal_exists: bool,
    prediction_lead_seconds: int,
    capture_max_early_seconds: int,
) -> Action:
    """Pure scheduling state; no target can silently move backward in time."""

    if terminal_exists:
        return "complete"
    now = _utc_dt(now_utc)
    target_at = _utc_dt(target.entry_target_at_utc)
    if now > target_at:
        return "missed"
    if not prediction_ready:
        if now >= target_at - timedelta(seconds=prediction_lead_seconds):
            return "prepare"
        return "future"
    if now >= target_at - timedelta(seconds=capture_max_early_seconds):
        return "capture"
    return "wait_capture"


def _publish_once(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise ShadowCollectorServiceError(f"immutable service artifact conflict: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _schedule_payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schedule": records}


def _schedule_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def ensure_plan(
    *,
    official_date: str,
    service_root: Path,
    policy_path: Path,
    schedule_loader: Callable[[str], list[dict[str, Any]]],
    now_utc: str,
) -> tuple[ShadowCapturePlan, Path, Path]:
    policy_sha = sha256_file(policy_path)
    policy = load_research_selection_policy(policy_path, expected_sha256=policy_sha)
    for phase in ("live", "lifecycle", "close", "official"):
        (service_root / phase / official_date).mkdir(parents=True, exist_ok=True)
    ledger_path = service_root / "ledger" / "forward_ledger.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.touch(exist_ok=True)
    plan_dir = service_root / "plans" / official_date
    plan_path = plan_dir / "plan.json"
    schedule_path = plan_dir / "schedule.json"
    if plan_path.exists():
        plan = load_capture_plan(plan_path)
        if plan.policy_sha256 != policy_sha or plan.entry_hours != policy.entry_hours:
            raise ShadowCollectorServiceError("active plan differs from the locked research policy")
        payload = json.loads(schedule_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or _schedule_digest(payload) != plan.schedule_snapshot_sha256:
            raise ShadowCollectorServiceError("active plan schedule snapshot is missing or tampered")
        current_records = canonical_schedule_records(schedule_loader(official_date))
        current_payload = _schedule_payload(current_records)
        if _schedule_digest(current_payload) == plan.schedule_snapshot_sha256:
            return plan, plan_path, schedule_path

        terminal_exists = any(
            (service_root / "live" / official_date / target.target_id / "terminal_attempt.json").is_file()
            for target in plan.targets
        )
        target_due = any(
            _utc_dt(target.entry_target_at_utc) <= _utc_dt(now_utc) for target in plan.targets
        )
        drift = {
            "schema_version": "shadow-schedule-drift-v1",
            "observed_at_utc": now_utc,
            "old_plan_sha256": plan.plan_sha256,
            "old_schedule_sha256": plan.schedule_snapshot_sha256,
            "new_schedule_sha256": _schedule_digest(current_payload),
            "terminal_evidence_exists": terminal_exists,
            "old_target_due": target_due,
            "betting_authorized": False,
        }
        drift_path = plan_dir / "schedule_drift.json"
        if not drift_path.exists():
            _publish_once(drift_path, artifact_bytes(drift))
        else:
            existing_drift = json.loads(drift_path.read_text(encoding="utf-8"))
            if (
                existing_drift.get("old_plan_sha256") != plan.plan_sha256
                or existing_drift.get("new_schedule_sha256") != drift["new_schedule_sha256"]
            ):
                raise ShadowCollectorServiceError("conflicting schedule drift evidence already exists")
        if terminal_exists or target_due:
            raise ShadowCollectorServiceError(
                "official schedule identity changed after evidence existed or a target was due; "
                "the slate is blocked rather than retroactively retimed"
            )

        replacement = plan_from_schedule(
            official_game_date=official_date,
            entry_hours=policy.entry_hours,
            policy_sha256=policy_sha,
            schedule_snapshot=current_records,
        )
        replacement_due = any(
            _utc_dt(target.entry_target_at_utc) <= _utc_dt(now_utc) for target in replacement.targets
        )
        if replacement_due:
            raise ShadowCollectorServiceError(
                "schedule changed before old T-4h, but the replacement already has a due target"
            )
        superseded = plan_dir / "superseded" / plan.plan_sha256
        _publish_once(superseded / "plan.json", plan_path.read_bytes())
        _publish_once(superseded / "schedule.json", schedule_path.read_bytes())
        _publish_once(superseded / "supersession.json", artifact_bytes({
            **drift,
            "schema_version": "shadow-plan-supersession-v1",
            "new_plan_sha256": replacement.plan_sha256,
            "reason": "official schedule identity changed before all old targets were due and before evidence existed",
        }))
        plan_path.unlink()
        schedule_path.unlink()
        drift_path.unlink()
        _publish_once(schedule_path, artifact_bytes(current_payload))
        _publish_once(plan_path, artifact_bytes(replacement.to_dict()))
        return load_capture_plan(plan_path), plan_path, schedule_path

    records = canonical_schedule_records(schedule_loader(official_date))
    plan = plan_from_schedule(
        official_game_date=official_date,
        entry_hours=policy.entry_hours,
        policy_sha256=policy_sha,
        schedule_snapshot=records,
    )
    due = [target for target in plan.targets if _utc_dt(target.entry_target_at_utc) <= _utc_dt(now_utc)]
    if due:
        raise ShadowCollectorServiceError(
            "cannot create a missing plan after one or more T-4h targets are due"
        )
    payload = _schedule_payload(records)
    if _schedule_digest(payload) != plan.schedule_snapshot_sha256:
        raise ShadowCollectorServiceError("schedule digest drifted before plan publication")
    _publish_once(schedule_path, artifact_bytes(payload))
    _publish_once(plan_path, artifact_bytes(plan.to_dict()))
    (service_root / "live" / official_date).mkdir(parents=True, exist_ok=True)
    return load_capture_plan(plan_path), plan_path, schedule_path


def _prepare_predictions(
    *,
    official_date: str,
    targets: list[CaptureTarget],
    service_root: Path,
    model_config: Path,
    runtime: RuntimeConfig,
    project_root: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    clock: Callable[[], str] = _now,
) -> None:
    preparation_started_at = clock()
    stamp = preparation_started_at.replace(":", "").replace("-", "")
    log_dir = service_root / "service_logs" / official_date
    log_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(project_root / "run_slate.py"),
        "--date", official_date,
        "--config", str(model_config),
        "--include-projected-lineups",
        "--refresh",
    ]
    result = runner(command, cwd=project_root, text=True, capture_output=True, check=False)
    _publish_once(log_dir / f"{stamp}.prediction.stdout.txt", result.stdout.encode("utf-8"))
    _publish_once(log_dir / f"{stamp}.prediction.stderr.txt", result.stderr.encode("utf-8"))
    if result.returncode != 0:
        raise ShadowCollectorServiceError(
            f"prediction preparation failed with exit code {result.returncode}; see retained service logs"
        )
    source = project_root / "data" / "learning" / "predictions" / f"predictions_{official_date}.json"
    snapshot = load_shadow_prediction_snapshot(source)
    if snapshot.game_date != official_date:
        raise ShadowCollectorServiceError("prepared prediction archive has the wrong official date")
    if _utc_dt(snapshot.captured_at_utc) < _utc_dt(preparation_started_at):
        raise ShadowCollectorServiceError(
            "prediction command returned without publishing a new provenance timestamp"
        )
    if any(_utc_dt(snapshot.captured_at_utc) > _utc_dt(target.entry_target_at_utc) for target in targets):
        raise ShadowCollectorServiceError("prepared prediction archive is after a target")
    for target in targets:
        target_dir = service_root / "prepared" / official_date / target.target_id
        prediction_path = target_dir / "prediction.json"
        _publish_once(prediction_path, source.read_bytes())
        receipt = {
            "schema_version": "shadow-prepared-prediction-receipt-v1",
            "target_id": target.target_id,
            "runtime_config_sha256": runtime.sha256,
            "model_config_path": str(model_config),
            "model_config_sha256": sha256_file(model_config),
            "prediction_artifact_sha256": sha256_file(prediction_path),
            "captured_at_utc": snapshot.captured_at_utc,
            "use_projected_lineups": True,
            "betting_authorized": False,
        }
        _publish_once(target_dir / "receipt.json", artifact_bytes(receipt))


def run_tick(
    *,
    official_date: str,
    service_root: Path,
    policy_path: Path,
    runtime_path: Path,
    model_config: Path,
    now_utc: str,
    schedule_loader: Callable[[str], list[dict[str, Any]]],
    project_root: Path,
    official_api: MLBStatsAPI | None = None,
) -> dict[str, int]:
    if "2026-05-01" <= official_date <= "2026-05-31":
        raise ShadowCollectorServiceError("May 2026 is sealed and cannot enter the forward collector")
    runtime = load_runtime_config(runtime_path)
    plan, _, schedule_path = ensure_plan(
        official_date=official_date,
        service_root=service_root,
        policy_path=policy_path,
        schedule_loader=schedule_loader,
        now_utc=now_utc,
    )
    counts = {name: 0 for name in (
        "future", "prepare", "wait_capture", "capture", "complete", "missed",
        "prestart_future", "prestart_capture", "prestart_complete",
        "prestart_not_applicable", "prestart_missed",
    )}
    actions: dict[str, Action] = {}
    for target in plan.targets:
        prepared = service_root / "prepared" / official_date / target.target_id / "prediction.json"
        terminal = service_root / "live" / official_date / target.target_id / "terminal_attempt.json"
        action = target_action(
            target,
            now_utc=now_utc,
            prediction_ready=prepared.is_file(),
            terminal_exists=terminal.is_file(),
            prediction_lead_seconds=runtime.prediction_lead_seconds,
            capture_max_early_seconds=runtime.capture_max_early_seconds,
        )
        actions[target.target_id] = action
        counts[action] += 1
    if counts["missed"]:
        raise ShadowCollectorServiceError(
            f"{counts['missed']} targets are past T-4h without terminal evidence; no backfill is permitted"
        )

    prepare_targets = [target for target in plan.targets if actions[target.target_id] == "prepare"]
    if prepare_targets:
        _prepare_predictions(
            official_date=official_date,
            targets=prepare_targets,
            service_root=service_root,
            model_config=model_config,
            runtime=runtime,
            project_root=project_root,
        )
        # A prediction build can consume several minutes. Recompute with the
        # real clock before any provider call; stale CLI time cannot grant access.
        now_utc = _now()

    capture_targets: list[CaptureTarget] = []
    for target in plan.targets:
        prepared = service_root / "prepared" / official_date / target.target_id / "prediction.json"
        terminal = service_root / "live" / official_date / target.target_id / "terminal_attempt.json"
        action = target_action(
            target,
            now_utc=now_utc,
            prediction_ready=prepared.is_file(),
            terminal_exists=terminal.is_file(),
            prediction_lead_seconds=runtime.prediction_lead_seconds,
            capture_max_early_seconds=runtime.capture_max_early_seconds,
        )
        if action == "missed":
            raise ShadowCollectorServiceError("prediction preparation overran T-4h; no backfill is permitted")
        if action == "capture":
            capture_targets.append(target)

    failures: list[str] = []

    def collect(target: CaptureTarget) -> None:
        capture_target(
            plan_path=service_root / "plans" / official_date / "plan.json",
            target_id=target.target_id,
            schedule_snapshot_path=schedule_path,
            prediction_archive=service_root / "prepared" / official_date / target.target_id / "prediction.json",
            output_root=service_root / "live",
            client=TheOddsAPIShadowClient(
                api_key_env=runtime.api_key_environment,
                base_url=runtime.provider_base_url,
                timeout_seconds=runtime.provider_timeout_seconds,
            ),
            api_key_env=runtime.api_key_environment,
            max_early_seconds=runtime.capture_max_early_seconds,
            on_bundle_published=lambda bundle_path: commit_target_entries(
                bundle_path=bundle_path,
                selection_policy_path=policy_path,
                ledger_path=service_root / "ledger" / "forward_ledger.jsonl",
                artifact_root=service_root / "lifecycle",
            ),
        )

    if capture_targets:
        with ThreadPoolExecutor(max_workers=min(runtime.max_parallel_targets, len(capture_targets))) as pool:
            futures = {pool.submit(collect, target): target for target in capture_targets}
            for future in as_completed(futures):
                target = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    failures.append(f"game_pk={target.mlb_game_pk} {type(exc).__name__}")
    if failures:
        raise ShadowCollectorServiceError(
            "one or more target captures failed; retained terminal evidence must be inspected: "
            + ", ".join(sorted(failures))
        )

    # Prestart reference is a separate, exact operational phase.  It is not
    # called a fill or guaranteed last sportsbook price.  Entry source errors
    # and no-market targets remain explicit and do not trigger extra quota.
    now_utc = _now()
    close_targets: list[CaptureTarget] = []
    close_failures: list[str] = []
    for target in plan.targets:
        target_root = service_root / "live" / official_date / target.target_id
        entry_bundle_path = target_root / "target_bundle.json"
        entry_terminal_path = target_root / "terminal_attempt.json"
        close_root = service_root / "close" / official_date / target.target_id
        close_bundle_path = close_root / "prestart_reference_bundle.json"
        close_error_path = close_root / "prestart_terminal_error.json"
        if not entry_bundle_path.is_file():
            if entry_terminal_path.is_file():
                counts["prestart_not_applicable"] += 1
            continue
        entry_bundle = load_target_capture_bundle(entry_bundle_path)
        if entry_bundle.attempt["outcome"] == "no_eligible_market":
            counts["prestart_not_applicable"] += 1
            continue
        if close_bundle_path.is_file():
            capture_prestart_reference(
                entry_bundle_path=entry_bundle_path,
                output_root=service_root / "close",
                client=TheOddsAPIShadowClient(
                    api_key_env=runtime.api_key_environment,
                    base_url=runtime.provider_base_url,
                    timeout_seconds=runtime.provider_timeout_seconds,
                ),
                api_key_env=runtime.api_key_environment,
                max_early_seconds=runtime.capture_max_early_seconds,
            )
            counts["prestart_complete"] += 1
            continue
        if close_error_path.is_file():
            close_failures.append(f"game_pk={target.mlb_game_pk} terminal_prestart_source_error")
            continue
        now = _utc_dt(now_utc)
        start = _utc_dt(target.official_start_time_utc)
        if now >= start:
            counts["prestart_missed"] += 1
        elif now >= start - timedelta(seconds=runtime.capture_max_early_seconds):
            counts["prestart_capture"] += 1
            close_targets.append(target)
        else:
            counts["prestart_future"] += 1
    def collect_close(target: CaptureTarget) -> None:
        capture_prestart_reference(
            entry_bundle_path=(
                service_root / "live" / official_date / target.target_id / "target_bundle.json"
            ),
            output_root=service_root / "close",
            client=TheOddsAPIShadowClient(
                api_key_env=runtime.api_key_environment,
                base_url=runtime.provider_base_url,
                timeout_seconds=runtime.provider_timeout_seconds,
            ),
            api_key_env=runtime.api_key_environment,
            max_early_seconds=runtime.capture_max_early_seconds,
        )

    if close_targets:
        with ThreadPoolExecutor(max_workers=min(runtime.max_parallel_targets, len(close_targets))) as pool:
            futures = {pool.submit(collect_close, target): target for target in close_targets}
            for future in as_completed(futures):
                target = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    close_failures.append(f"game_pk={target.mlb_game_pk} {type(exc).__name__}")
    settlement_counts = settle_final_entries(
        official_date=official_date,
        ledger_path=service_root / "ledger" / "forward_ledger.jsonl",
        close_root=service_root / "close",
        official_root=service_root / "official",
        settlement_rule_artifact=project_root / "config" / "shadow_draftkings_hits_reference_settlement.json",
        api=official_api or MLBStatsAPI(),
    )
    counts.update({f"settlement_{key}": value for key, value in settlement_counts.items()})
    # A missed or source-error prestart phase remains an operational failure and
    # can never be backfilled.  It must not, however, suppress later official
    # outcome retention and the immutable unscored ledger resolution.  Running
    # settlement first preserves the complete failure lifecycle while this tick
    # still exits non-zero and the independent verifier remains red.
    if counts["prestart_missed"]:
        raise ShadowCollectorServiceError(
            f"{counts['prestart_missed']} captured targets lack a prestart reference; "
            "official settlement was attempted, but no backfill is permitted"
        )
    if close_failures:
        raise ShadowCollectorServiceError(
            "one or more prestart reference captures failed; official settlement was attempted and "
            "retained evidence must be inspected: " + ", ".join(sorted(close_failures))
        )
    return counts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Official MLB date; default uses America/New_York")
    parser.add_argument("--service-root", default="data/learning/shadow/service")
    parser.add_argument("--policy", default="config/shadow_hits_research_policy.json")
    parser.add_argument("--runtime", default="config/shadow_collector_runtime.json")
    parser.add_argument("--model-config", default="config/config.kbb.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    official_date = args.date or datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    project_root = Path(__file__).resolve().parent
    service_root = Path(args.service_root).resolve()
    try:
        evidence_scope = validate_evidence_scope(
            service_root / "evidence_scope.json",
            root=project_root,
        )
        api = MLBStatsAPI()
        counts = run_tick(
            official_date=official_date,
            service_root=service_root,
            policy_path=Path(args.policy).resolve(),
            runtime_path=Path(args.runtime).resolve(),
            model_config=Path(args.model_config).resolve(),
            now_utc=_now(),
            schedule_loader=api.get_schedule,
            project_root=project_root,
            official_api=api,
        )
    except (OSError, ValueError, ShadowCapturePlanError, ShadowCollectorServiceError) as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("backfilled: FALSE; betting authorized: FALSE", file=sys.stderr)
        return 2
    print(f"SHADOW COLLECTOR TICK {official_date}: {counts}")
    print(
        f"evidence scope: {evidence_scope['mode']}; "
        f"economic evidence eligible: {str(evidence_scope['economic_evidence_eligible']).upper()}"
    )
    print("research selection funnel and ledger commit verified; betting authorized: FALSE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
