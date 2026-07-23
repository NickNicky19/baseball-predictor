#!/usr/bin/env python3
"""Run one isolated AWS comparator preparation or market-capture tick."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.forward_pitcher_context import games_from_raw_schedule_response  # noqa: E402
from src.evaluation.shadow_capture_plan import (  # noqa: E402
    ShadowCapturePlan,
    canonical_schedule_records,
    plan_from_schedule,
)
from src.evaluation.shadow_live_provider import TheOddsAPIShadowClient  # noqa: E402
from src.evaluation.shared_pa_comparator_runtime import (  # noqa: E402
    PredictionArtifacts,
    SharedPAComparatorRuntimeError,
    canonical_bytes,
    capture_market_artifacts,
    finalize_target,
    horizon_batch_id,
    load_runtime,
    publish_target_failure,
    prepare_prediction_batches,
    read_object_bytes,
)
from src.evaluation.shared_pa_comparator_capture import load_comparator_contract  # noqa: E402
from src.evaluation.shared_pa_forward_ledger import (  # noqa: E402
    SharedPAForwardLedger,
    side_target_id,
)
from src.evaluation.forward_pitcher_context_ledger import ForwardPitcherContextLedger  # noqa: E402

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux service boundary
    fcntl = None  # type: ignore[assignment]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_plan_and_schedule(
    *, official_date: str, plan_dir: Path, plan_receipt_root: Path,
) -> tuple[ShadowCapturePlan, Mapping[str, Any]]:
    if official_date.startswith("2026-05-"):
        raise SharedPAComparatorRuntimeError("May 2026 is sealed before plan/source access")
    plan_path = plan_dir / f"{official_date}.plan.json"
    try:
        plan_raw = plan_path.read_bytes()
        plan = ShadowCapturePlan.from_mapping(read_object_bytes(plan_raw, "pitcher plan"))
    except (OSError, ValueError) as exc:
        raise SharedPAComparatorRuntimeError("current immutable pitcher plan is unavailable") from exc
    if plan.official_game_date != official_date or plan.entry_hours != 4:
        raise SharedPAComparatorRuntimeError("pitcher plan date or horizon differs")
    receipt_path = plan_receipt_root / "plans" / f"{official_date}.{plan.plan_sha256}.json"
    try:
        receipt = dict(read_object_bytes(receipt_path.read_bytes(), "pitcher plan receipt"))
    except OSError as exc:
        raise SharedPAComparatorRuntimeError("pitcher plan receipt is unavailable") from exc
    unsigned = dict(receipt)
    receipt_sha = unsigned.pop("receipt_sha256", None)
    if (
        receipt.get("schema_version") != "aws-pitcher-receipt-plan-receipt-v1"
        or receipt.get("official_game_date") != official_date
        or receipt.get("plan_sha256") != plan.plan_sha256
        or receipt.get("research_only") is not True
        or receipt.get("betting_authorized") is not False
        or receipt_sha != _sha(canonical_bytes(unsigned))
    ):
        raise SharedPAComparatorRuntimeError("pitcher plan receipt binding changed")
    source_sha = str(receipt.get("source_payload_sha256", ""))
    raw_path = plan_receipt_root / "raw" / f"{official_date}.{source_sha}.json"
    try:
        source_raw = raw_path.read_bytes()
    except OSError as exc:
        raise SharedPAComparatorRuntimeError("pitcher plan raw schedule is unavailable") from exc
    if _sha(source_raw) != source_sha:
        raise SharedPAComparatorRuntimeError("pitcher plan raw schedule hash changed")
    source = read_object_bytes(source_raw, "pitcher plan raw schedule")
    records = canonical_schedule_records(
        games_from_raw_schedule_response(source, allow_empty_date=True)
    )
    rebuilt = plan_from_schedule(
        official_game_date=official_date,
        entry_hours=4,
        policy_sha256=plan.policy_sha256,
        schedule_snapshot=records,
    )
    if rebuilt.plan_sha256 != plan.plan_sha256:
        raise SharedPAComparatorRuntimeError("raw schedule cannot reproduce immutable plan")
    return plan, {"schedule": records}


def _prediction_generator(*, evidence_root: Path):
    work_root = evidence_root / "work"
    work_root.mkdir(parents=True, exist_ok=True)

    def generate(official_date: str) -> tuple[PredictionArtifacts, datetime]:
        with tempfile.TemporaryDirectory(prefix="prediction-", dir=work_root) as temporary:
            work = Path(temporary)
            frozen_dir = work / "frozen"
            total_bases_dir = work / "total_bases"
            commands = (
                [sys.executable, str(ROOT / "run_shared_pa_frozen.py"), "--date", official_date, "--archive-dir", str(frozen_dir), "--refresh"],
                [sys.executable, str(ROOT / "run_shared_pa_total_bases.py"), "--date", official_date, "--archive-dir", str(total_bases_dir), "--refresh"],
            )
            for command in commands:
                completed = subprocess.run(
                    command, cwd=work, capture_output=True, text=True,
                    timeout=1500, check=False,
                )
                if completed.returncode != 0:
                    raise SharedPAComparatorRuntimeError(
                        f"prediction subprocess failed with exit {completed.returncode}"
                    )
            frozen = (frozen_dir / f"predictions_{official_date}.json").read_bytes()
            total_bases = (total_bases_dir / f"predictions_{official_date}.json").read_bytes()
            return PredictionArtifacts(frozen, total_bases), datetime.now(timezone.utc)

    return generate


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        return dict(read_object_bytes(path.read_bytes(), label))
    except OSError as exc:
        raise SharedPAComparatorRuntimeError(f"{label} is unavailable") from exc


def _verify_target_terminal(path: Path, target) -> None:
    envelope = _object(path, "comparator target terminal")
    record = envelope.get("record")
    if not isinstance(record, Mapping):
        raise SharedPAComparatorRuntimeError("comparator terminal record is malformed")
    record_sha = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if (
        envelope.get("schema_version") != "shared-pa-comparator-record-v1"
        or envelope.get("record_type") != "target_terminal"
        or envelope.get("record_sha256") != record_sha
        or record.get("target_id") != target.target_id
        or record.get("official_game_date") != target.official_game_date
        or record.get("research_only") is not True
        or record.get("betting_authorized") is not False
    ):
        raise SharedPAComparatorRuntimeError("comparator target terminal hash or identity changed")


def _verified_shared_pa_players(
    *, plan: ShadowCapturePlan, target, ledger_root: Path,
) -> tuple[list[Mapping[str, Any]] | None, str]:
    root = ledger_root / plan.official_game_date / plan.plan_sha256
    if not (root / "ledger_manifest.json").is_file():
        return None, "shared PA ledger manifest unavailable"
    manifest = _object(root / "ledger_manifest.json", "shared PA ledger manifest")
    ledger = SharedPAForwardLedger(
        root, plan=plan,
        contract_sha256=str(manifest.get("contract_sha256", "")),
        runtime_manifest_sha256=str(manifest.get("runtime_manifest_sha256", "")),
        collector_code_sha256=str(manifest.get("collector_code_sha256", "")),
    )
    ledger.verify(require_complete_coverage=False)
    entries = []
    for side in ("away", "home"):
        identifier = side_target_id(plan=plan, target=target, side=side)
        path = root / "terminal" / f"{identifier}.json"
        if not path.is_file():
            return None, f"shared PA {side} terminal unavailable"
        entry = _object(path, f"shared PA {side} terminal")
        entries.append(entry)
    excluded = [f"{entry.get('side')}={entry.get('terminal_state')}" for entry in entries if entry.get("terminal_state") != "captured_complete"]
    if excluded:
        return None, "shared PA prerequisite excluded: " + ",".join(excluded)
    players = [player for entry in entries for player in entry.get("players", [])]
    if len(players) != 18:
        raise SharedPAComparatorRuntimeError("complete shared PA target does not contain 18 players")
    return players, ""


def _verified_pitcher_context(
    *, plan: ShadowCapturePlan, target, ledger_root: Path, assessed_at: datetime,
) -> tuple[Mapping[str, Any] | None, str]:
    root = ledger_root / plan.official_game_date / plan.plan_sha256
    if not (root / "manifest.json").is_file() or not (root / "terminal_index.json").is_file():
        return None, "pitcher receipt ledger unavailable"
    manifest = _object(root / "manifest.json", "pitcher ledger manifest")
    ledger = ForwardPitcherContextLedger(root, plan, str(manifest.get("runtime_sha256", "")))
    ledger.verify(assessed_at_utc=assessed_at.isoformat())
    index = _object(root / "terminal_index.json", "pitcher terminal index")
    row = index.get("targets", {}).get(target.target_id)
    if not isinstance(row, Mapping):
        return None, "pitcher receipt terminal unavailable"
    record = _object(root / str(row.get("record_path", "")), "pitcher receipt record")
    if record.get("terminal_state") != "captured":
        return None, f"pitcher receipt prerequisite excluded: {record.get('terminal_state')}"
    context_path = root / str(record.get("context_path", ""))
    context = _object(context_path, "pitcher context")
    if context.get("context_sha256") != record.get("context_sha256"):
        raise SharedPAComparatorRuntimeError("pitcher context hash differs from ledger record")
    return context, ""


def _prediction_artifacts(
    *, plan: ShadowCapturePlan, target, evidence_root: Path,
) -> tuple[bytes, bytes] | None:
    batch = (
        evidence_root / "pregame" / plan.official_game_date / plan.plan_sha256
        / "prediction_batches" / horizon_batch_id(plan, target)
    )
    manifest_path = batch / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = _object(manifest_path, "prediction batch manifest")
    frozen = (batch / "frozen_predictions.json").read_bytes()
    total_bases = (batch / "total_bases_predictions.json").read_bytes()
    if (
        _sha(frozen) != manifest.get("frozen_archive_sha256")
        or _sha(total_bases) != manifest.get("total_bases_archive_sha256")
    ):
        raise SharedPAComparatorRuntimeError("prediction batch artifact hash changed")
    return frozen, total_bases


def _market_artifacts(
    *, plan: ShadowCapturePlan, target, evidence_root: Path,
) -> tuple[bytes, str, bytes] | None:
    directory = (
        evidence_root / "pregame" / plan.official_game_date / plan.plan_sha256
        / "markets" / target.target_id
    )
    capture = directory / "capture"
    manifest_path = capture / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = _object(manifest_path, "market capture manifest")
    markets = (capture / "markets.raw.json").read_bytes()
    identity = (capture / "game_identity.json").read_bytes()
    if (
        _sha(markets) != manifest.get("market_artifact_sha256")
        or _sha(identity) != manifest.get("game_identity_artifact_sha256")
        or manifest.get("target_id") != target.target_id
    ):
        raise SharedPAComparatorRuntimeError("market artifact hash or target changed")
    return markets, str(manifest.get("market_received_at_utc")), identity


def _finalize(
    *, plan: ShadowCapturePlan, evidence_root: Path,
    shared_pa_ledger_root: Path, pitcher_ledger_root: Path,
    scheduler: Mapping[str, Any], current: datetime,
    runtime_sha256: str,
) -> dict[str, int]:
    loaded_contract = load_comparator_contract(
        root=ROOT, path=ROOT / "config/shared_pa_comparator_capture_v1.json"
    )
    output = evidence_root / "records"
    dependency_lock_sha256 = _sha((ROOT / "requirements-aws-comparator.lock").read_bytes())
    counts = {"finalized": 0, "failed_terminal": 0, "future": 0, "verified_retry": 0}
    for target in plan.targets:
        terminal = output / target.official_game_date / target.target_id / "terminal.json"
        if terminal.is_file():
            _verify_target_terminal(terminal, target)
            counts["verified_retry"] += 1
            continue
        horizon = datetime.fromisoformat(target.entry_target_at_utc.replace("Z", "+00:00"))
        ready = horizon + timedelta(seconds=int(scheduler["finalize_grace_seconds_after_horizon"]))
        if current < ready:
            counts["future"] += 1
            continue
        players, player_reason = _verified_shared_pa_players(
            plan=plan, target=target, ledger_root=shared_pa_ledger_root
        )
        context, context_reason = _verified_pitcher_context(
            plan=plan, target=target, ledger_root=pitcher_ledger_root,
            assessed_at=current,
        )
        predictions = _prediction_artifacts(
            plan=plan, target=target, evidence_root=evidence_root
        )
        reasons = [reason for reason in (
            player_reason,
            context_reason,
            "prediction batch unavailable" if predictions is None else "",
        ) if reason]
        if reasons:
            publish_target_failure(
                target=target, output_root=output, observed_at=current,
                detail="; ".join(reasons),
            )
            counts["failed_terminal"] += 1
            continue
        assert players is not None and context is not None and predictions is not None
        market = _market_artifacts(plan=plan, target=target, evidence_root=evidence_root)
        kwargs = {}
        if market is not None:
            kwargs = {
                "provider_bytes": market[0],
                "provider_received_at_utc": market[1],
                "game_identity_bytes": market[2],
            }
        finalize_target(
            target=target, player_snapshots=players,
            pitcher_context_record=context,
            frozen_archive=predictions[0], total_bases_archive=predictions[1],
            loaded_contract=loaded_contract, output_root=output,
            finalized_at=current,
            automation_runtime_sha256=runtime_sha256,
            dependency_lock_sha256=dependency_lock_sha256,
            **kwargs,
        )
        counts["finalized"] += 1
    return counts


def run_all(
    *, mode: str, plan_dir: Path, plan_receipt_root: Path,
    evidence_root: Path, runtime_path: Path,
    shared_pa_ledger_root: Path | None = None,
    pitcher_ledger_root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    runtime, runtime_sha = load_runtime(runtime_path, repository_root=ROOT)
    current = now or datetime.now(timezone.utc)
    official_date = current.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    if official_date.startswith("2026-05-"):
        return {
            "schema_version": "aws-shared-pa-comparator-tick-v1",
            "mode": mode,
            "official_game_date": official_date,
            "state": "sealed_may_no_access",
            "research_only": True,
            "betting_authorized": False,
        }
    plan, schedule = _load_plan_and_schedule(
        official_date=official_date,
        plan_dir=plan_dir,
        plan_receipt_root=plan_receipt_root,
    )
    if mode == "prepare-predictions":
        result = prepare_prediction_batches(
            plan=plan, evidence_root=evidence_root,
            scheduler=runtime["scheduler"], runtime_sha256=runtime_sha,
            now=current, generate=_prediction_generator(evidence_root=evidence_root),
        )
    elif mode == "capture-markets":
        client = TheOddsAPIShadowClient(
            api_key_env=runtime["provider"]["api_key_environment_variable"]
        )
        result = capture_market_artifacts(
            plan=plan, schedule_snapshot=schedule, evidence_root=evidence_root,
            scheduler=runtime["scheduler"], runtime_sha256=runtime_sha,
            now=current, fetch_events=client.fetch_events,
            fetch_markets=client.fetch_shared_pa_markets,
        )
    elif mode == "finalize":
        if shared_pa_ledger_root is None or pitcher_ledger_root is None:
            raise SharedPAComparatorRuntimeError("finalize ledger roots are required")
        result = _finalize(
            plan=plan, evidence_root=evidence_root,
            shared_pa_ledger_root=shared_pa_ledger_root,
            pitcher_ledger_root=pitcher_ledger_root,
            scheduler=runtime["scheduler"], current=current,
            runtime_sha256=runtime_sha,
        )
    else:
        raise SharedPAComparatorRuntimeError("unknown comparator tick mode")
    return {
        "schema_version": "aws-shared-pa-comparator-tick-v1",
        "mode": mode,
        "official_game_date": official_date,
        "plan_sha256": plan.plan_sha256,
        "runtime_manifest_sha256": runtime_sha,
        "result": result,
        "outcomes_or_settlement_accessed": False,
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare-predictions", "capture-markets", "finalize"))
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--plan-receipt-root", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--shared-pa-ledger-root", type=Path)
    parser.add_argument("--pitcher-ledger-root", type=Path)
    parser.add_argument(
        "--runtime", type=Path,
        default=ROOT / "config/shared_pa_comparator_runtime_v1.json",
    )
    parser.add_argument("--lock-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if fcntl is None:
            raise SharedPAComparatorRuntimeError("AWS comparator service requires Linux locks")
        args.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with args.lock_file.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SharedPAComparatorRuntimeError("another comparator tick of this mode is running") from exc
            print(json.dumps(run_all(
                mode=args.mode, plan_dir=args.plan_dir,
                plan_receipt_root=args.plan_receipt_root,
                evidence_root=args.evidence_root, runtime_path=args.runtime,
                shared_pa_ledger_root=args.shared_pa_ledger_root,
                pitcher_ledger_root=args.pitcher_ledger_root,
            ), sort_keys=True))
    except (OSError, ValueError, RuntimeError, SharedPAComparatorRuntimeError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
