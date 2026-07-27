#!/usr/bin/env python3
"""Run one research-only future batter-opportunity evidence tick.

The planner consumes an already verified T-minus-4 active-roster ledger.  It
never invents a roster, lineup, plan time, or historical receipt.  Final-game
transport is reduced to the positive opportunity projection before persistence.
No probability, prediction, price, settlement, or outcome score is produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.projected_lineup_contract import sha256_value
from src.evaluation.projected_lineup_roster_ledger import (
    CAPTURED as ROSTER_CAPTURED,
    ProjectedLineupRosterLedger,
)
from src.evaluation.prospective_batter_opportunity import (
    RawOpportunityResponse,
    build_history_capture_plan,
)
from src.evaluation.prospective_batter_opportunity_history import (
    PLANNING_MISSED,
    ProspectiveOpportunityHistoryLedger,
    run_history_tick,
)
from src.evaluation.shadow_capture_plan import ShadowCapturePlan


class ProspectiveBatterOpportunityRuntimeError(ValueError):
    """The future-only runtime identity or evidence boundary differs."""


_CODE_PATHS = (
    "scripts/run_prospective_batter_opportunity_tick.py",
    "src/evaluation/prospective_batter_opportunity.py",
    "src/evaluation/prospective_batter_opportunity_history.py",
    "src/evaluation/prospective_batter_opportunity_ledger.py",
    "src/evaluation/projected_lineup_official_roster.py",
    "src/evaluation/projected_lineup_roster_ledger.py",
    "src/evaluation/shadow_capture_plan.py",
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collector_code_sha256() -> str:
    manifest = {path: _file_sha256(ROOT / path) for path in _CODE_PATHS}
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_runtime(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProspectiveBatterOpportunityRuntimeError("runtime configuration is invalid JSON") from exc
    expected_scheduler = {
        "entry_hours": 4,
        "tick_seconds": 60,
        "request_timeout_seconds": 20,
        "capture_deadline_after_start_seconds": 86400,
    }
    expected_invariants = {
        "read_existing_t4_plan_and_roster_ledger_only": True,
        "persist_replayed_roster_proof_with_each_history_plan": True,
        "persist_replayed_roster_proof_with_each_planning_miss": True,
        "planning_miss_is_terminal_and_not_backfilled": True,
        "same_day_history_plan_created_before_first_pitch_only": True,
        "scan_only_non_may_prospective_history_ledgers": True,
        "full_transport_retention_forbidden": True,
        "source_invalid_transport_hash_and_size_only": True,
        "late_backfill_forbidden": True,
        "probability_generation_forbidden": True,
        "outcome_scoring_forbidden": True,
        "prices_and_economic_evidence_forbidden": True,
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
    }
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "prospective-batter-opportunity-runtime-v1"
        or value.get("status") != "RESEARCH_ONLY_NOT_DEPLOYED"
        or value.get("collector_instance_id") != "prospective-batter-opportunity-evidence-v1"
        or value.get("scheduler") != expected_scheduler
        or value.get("invariants") != expected_invariants
    ):
        raise ProspectiveBatterOpportunityRuntimeError("runtime safety identity changed")
    contract = value.get("contract")
    if (
        not isinstance(contract, dict)
        or set(contract) != {"path", "sha256"}
        or contract.get("path") != "config/prospective_batter_opportunity_contract_v1.json"
        or not isinstance(contract.get("sha256"), str)
        or len(contract["sha256"]) != 64
    ):
        raise ProspectiveBatterOpportunityRuntimeError("runtime contract binding changed")
    source_roster = value.get("source_roster_evidence")
    if (
        not isinstance(source_roster, dict)
        or set(source_roster) != {"contract_sha256", "collector_code_sha256"}
        or any(
            not isinstance(source_roster.get(label), str)
            or len(source_roster[label]) != 64
            or any(char not in "0123456789abcdef" for char in source_roster[label])
            for label in ("contract_sha256", "collector_code_sha256")
        )
    ):
        raise ProspectiveBatterOpportunityRuntimeError("source roster evidence binding changed")
    return value, hashlib.sha256(raw).hexdigest()


def build_evidence_scope(
    *,
    created_at_utc: str,
    collection_epoch_date: str,
    runtime_manifest_sha256: str,
    contract_sha256: str,
    collector_code_sha256_value: str,
    source_roster_contract_sha256: str,
    source_roster_collector_code_sha256: str,
) -> dict:
    try:
        epoch = date.fromisoformat(collection_epoch_date)
    except (TypeError, ValueError) as exc:
        raise ProspectiveBatterOpportunityRuntimeError(
            "scope collection epoch must be a canonical ISO date"
        ) from exc
    if epoch.isoformat() != collection_epoch_date or (epoch.year == 2026 and epoch.month == 5):
        raise ProspectiveBatterOpportunityRuntimeError(
            "scope collection epoch is noncanonical or sealed May 2026"
        )
    created = _utc(created_at_utc)
    created_date = created.astimezone(ZoneInfo("America/New_York")).date()
    if created_date.year == 2026 and created_date.month == 5:
        raise ProspectiveBatterOpportunityRuntimeError("scope creation in sealed May 2026 is forbidden")
    digests = {
        "runtime_manifest_sha256": runtime_manifest_sha256,
        "contract_sha256": contract_sha256,
        "collector_code_sha256": collector_code_sha256_value,
        "source_roster_contract_sha256": source_roster_contract_sha256,
        "source_roster_collector_code_sha256": source_roster_collector_code_sha256,
    }
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
        for value in digests.values()
    ):
        raise ProspectiveBatterOpportunityRuntimeError("scope release digest is invalid")
    unsigned = {
        "schema_version": "prospective-batter-opportunity-evidence-scope-v1",
        "created_at_utc": _stamp(created),
        "collection_epoch_date": collection_epoch_date,
        **digests,
        "research_only": True,
        "economic_evidence_eligible": False,
        "historical_backfill_authorized": False,
        "production_probability_consumption_authorized": False,
        "betting_authorized": False,
    }
    return {**unsigned, "evidence_scope_sha256": sha256_value(unsigned)}


def load_evidence_scope(
    path: Path,
    *,
    runtime: Mapping[str, object],
    runtime_sha256: str,
    code_sha256: str,
) -> tuple[dict, str]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProspectiveBatterOpportunityRuntimeError("evidence scope is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProspectiveBatterOpportunityRuntimeError("evidence scope must be an object")
    source_binding = runtime["source_roster_evidence"]
    rebuilt = build_evidence_scope(
        created_at_utc=value.get("created_at_utc"),
        collection_epoch_date=value.get("collection_epoch_date"),
        runtime_manifest_sha256=runtime_sha256,
        contract_sha256=runtime["contract"]["sha256"],
        collector_code_sha256_value=code_sha256,
        source_roster_contract_sha256=source_binding["contract_sha256"],
        source_roster_collector_code_sha256=source_binding["collector_code_sha256"],
    )
    if rebuilt != value:
        raise ProspectiveBatterOpportunityRuntimeError("evidence scope identity or release binding differs")
    return rebuilt, hashlib.sha256(raw).hexdigest()


def _load_plan(path: Path) -> ShadowCapturePlan:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return ShadowCapturePlan.from_mapping(value)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ProspectiveBatterOpportunityRuntimeError("T-minus-4 plan is unreadable") from exc


def _load_manifest(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProspectiveBatterOpportunityRuntimeError(f"{label} manifest is unreadable") from exc
    if not isinstance(value, dict):
        raise ProspectiveBatterOpportunityRuntimeError(f"{label} manifest must be an object")
    return value


def _utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise ProspectiveBatterOpportunityRuntimeError("runtime timestamp must be an ISO string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProspectiveBatterOpportunityRuntimeError("runtime timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveBatterOpportunityRuntimeError("runtime timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _official_fetch(plan: Mapping[str, object], timeout: int) -> RawOpportunityResponse:
    request = Request(
        str(plan["source_request_url"]),
        headers={"User-Agent": "baseball-predictor-opportunity-evidence/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:  # nosec B310 exact URL revalidated downstream
        body = response.read()
        content_type = response.headers.get("Content-Type", "")
        status = int(response.status)
    return RawOpportunityResponse(
        body=body,
        received_at_utc=_stamp(datetime.now(timezone.utc)),
        request_url=str(plan["source_request_url"]),
        http_status=status,
        content_type=content_type,
    )


def _history_ledgers(
    root: Path,
    *,
    collection_epoch: date,
    through_date: date,
    expected_contract_sha256: str,
    expected_collector_code_sha256: str,
    expected_evidence_scope_sha256: str,
) -> list[ProspectiveOpportunityHistoryLedger]:
    ledgers: list[ProspectiveOpportunityHistoryLedger] = []
    if not root.is_dir():
        return ledgers
    official_date = collection_epoch
    while official_date <= through_date:
        if official_date.year == 2026 and official_date.month == 5:
            official_date += timedelta(days=1)
            continue
        date_dir = root / official_date.isoformat()
        if not date_dir.is_dir():
            official_date += timedelta(days=1)
            continue
        for release_dir in sorted(path for path in date_dir.iterdir() if path.is_dir()):
            manifest_path = release_dir / "ledger_manifest.json"
            if not manifest_path.is_file():
                raise ProspectiveBatterOpportunityRuntimeError("history ledger directory lacks a manifest")
            manifest = _load_manifest(manifest_path, "history ledger")
            if (
                manifest.get("collection_epoch_date") != collection_epoch.isoformat()
                or manifest.get("contract_sha256") != expected_contract_sha256
                or manifest.get("collector_code_sha256") != expected_collector_code_sha256
                or manifest.get("evidence_scope_sha256") != expected_evidence_scope_sha256
            ):
                raise ProspectiveBatterOpportunityRuntimeError("history ledger release identity differs")
            ledgers.append(
                ProspectiveOpportunityHistoryLedger(
                    release_dir,
                    collection_epoch_date=manifest["collection_epoch_date"],
                    contract_sha256=manifest["contract_sha256"],
                    collector_code_sha256=manifest["collector_code_sha256"],
                    evidence_scope_sha256=manifest["evidence_scope_sha256"],
                )
            )
        official_date += timedelta(days=1)
    return ledgers


def run_all(
    *,
    plan_dir: Path,
    roster_ledger_root: Path,
    history_ledger_root: Path,
    runtime_path: Path,
    evidence_scope_path: Path,
    expected_evidence_scope_file_sha256: str,
    now: datetime | None = None,
    fetch_final: Callable[[Mapping[str, object]], RawOpportunityResponse] | None = None,
) -> dict:
    runtime, runtime_sha = load_runtime(runtime_path)
    contract_path = ROOT / runtime["contract"]["path"]
    if _file_sha256(contract_path) != runtime["contract"]["sha256"]:
        raise ProspectiveBatterOpportunityRuntimeError("runtime contract hash changed")
    code_sha = collector_code_sha256()
    scope, scope_file_sha = load_evidence_scope(
        evidence_scope_path,
        runtime=runtime,
        runtime_sha256=runtime_sha,
        code_sha256=code_sha,
    )
    if scope_file_sha != expected_evidence_scope_file_sha256:
        raise ProspectiveBatterOpportunityRuntimeError("evidence scope file hash differs")
    epoch = date.fromisoformat(scope["collection_epoch_date"])
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    scope_created = _utc(scope["created_at_utc"])
    if current < scope_created:
        raise ProspectiveBatterOpportunityRuntimeError("collector clock predates evidence scope creation")
    official_date = current.astimezone(ZoneInfo("America/New_York")).date()
    if official_date.year == 2026 and official_date.month == 5:
        return {
            "schema_version": "prospective-batter-opportunity-tick-v1",
            "collector_state": "sealed_may_no_access",
            "research_only": True,
            "betting_authorized": False,
            "production_changed": False,
        }
    if official_date < epoch:
        raise ProspectiveBatterOpportunityRuntimeError("runtime date predates collection epoch")
    planning = {
        "created": 0,
        "already_planned": 0,
        "missed_before_plan": 0,
        "already_terminal_missed": 0,
        "roster_unavailable": 0,
        "source_plans_seen": 0,
        "source_ledgers_missing": 0,
        "source_terminal_coverage_missing": 0,
    }
    candidate_date = epoch
    while candidate_date <= official_date:
        # The seal is enforced before even constructing a May filesystem path.
        if candidate_date.year == 2026 and candidate_date.month == 5:
            candidate_date += timedelta(days=1)
            continue
        plan_path = plan_dir / f"{candidate_date.isoformat()}.plan.json"
        if not plan_path.is_file():
            candidate_date += timedelta(days=1)
            continue
        planning["source_plans_seen"] += 1
        source_plan = _load_plan(plan_path)
        if source_plan.official_game_date != candidate_date.isoformat() or source_plan.entry_hours != 4:
            raise ProspectiveBatterOpportunityRuntimeError("T-minus-4 plan date or horizon differs")
        source_root = roster_ledger_root / source_plan.official_game_date / source_plan.plan_sha256
        manifest_path = source_root / "ledger_manifest.json"
        if not manifest_path.is_file():
            planning["source_ledgers_missing"] += 1
            candidate_date += timedelta(days=1)
            continue
        manifest = _load_manifest(manifest_path, "source roster ledger")
        source_binding = runtime["source_roster_evidence"]
        if (
            manifest.get("contract_sha256") != source_binding["contract_sha256"]
            or manifest.get("collector_code_sha256") != source_binding["collector_code_sha256"]
        ):
            raise ProspectiveBatterOpportunityRuntimeError("source roster ledger release identity differs")
        roster_ledger = ProjectedLineupRosterLedger(
            source_root,
            plan=source_plan,
            contract_sha256=manifest["contract_sha256"],
            collector_code_sha256=manifest["collector_code_sha256"],
        )
        roster_counts = roster_ledger.verify()
        planning["source_terminal_coverage_missing"] += roster_counts["missing"]
        planning["roster_unavailable"] += roster_counts["missing"]
        history_root = history_ledger_root / source_plan.official_game_date / source_plan.plan_sha256
        history_ledger = ProspectiveOpportunityHistoryLedger(
            history_root,
            collection_epoch_date=scope["collection_epoch_date"],
            contract_sha256=runtime["contract"]["sha256"],
            collector_code_sha256=code_sha,
            evidence_scope_sha256=scope["evidence_scope_sha256"],
        )
        existing = {plan["roster_side_target_id"] for plan in history_ledger.plans()}
        terminally_missed = history_ledger.planning_exclusion_ids()
        terminal_dir = source_root / "terminal"
        for terminal_path in sorted(terminal_dir.glob("*.json")) if terminal_dir.is_dir() else []:
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
            if terminal.get("terminal_state") != ROSTER_CAPTURED:
                planning["roster_unavailable"] += 1
                continue
            side_id = str(terminal["side_target_id"])
            if side_id in existing:
                planning["already_planned"] += 1
                continue
            if side_id in terminally_missed:
                planning["already_terminal_missed"] += 1
                continue
            target = next(
                (item for item in source_plan.targets if item.target_id == terminal.get("target_id")),
                None,
            )
            if target is None:
                raise ProspectiveBatterOpportunityRuntimeError("roster terminal target is absent from plan")
            starts = _utc(target.official_start_time_utc)
            if current >= starts:
                history_ledger.append_planning_exclusion(
                    source_roster_ledger=roster_ledger,
                    target_id=target.target_id,
                    side=str(terminal["side"]),
                    observed_at_utc=_stamp(current),
                    detail=(
                        "receipt-proven roster side was discovered at or after first pitch; "
                        "no history capture plan was backdated"
                    ),
                )
                terminally_missed.add(side_id)
                planning["missed_before_plan"] += 1
                continue
            capture_plan = build_history_capture_plan(
                created_at_utc=_stamp(current),
                mlb_game_pk=target.mlb_game_pk,
                official_game_date=target.official_game_date,
                official_start_time_utc=target.official_start_time_utc,
                capture_deadline_utc=_stamp(
                    starts
                    + timedelta(
                        seconds=runtime["scheduler"]["capture_deadline_after_start_seconds"]
                    )
                ),
                side=str(terminal["side"]),
                team_id=int(terminal["team_id"]),
                source_t4_plan_sha256=source_plan.plan_sha256,
                roster_side_target_id=side_id,
                active_roster_receipt_sha256=sha256_value(terminal["roster"]),
            )
            history_ledger.append_plan(
                capture_plan,
                published_at_utc=_stamp(current),
                source_roster_ledger=roster_ledger,
            )
            existing.add(side_id)
            planning["created"] += 1
        candidate_date += timedelta(days=1)
    fetch = fetch_final or (
        lambda plan: _official_fetch(plan, runtime["scheduler"]["request_timeout_seconds"])
    )
    collection = {"captured": 0, "deadline_missing": 0, "source_invalid": 0, "pending": 0, "future": 0}
    for ledger in _history_ledgers(
        history_ledger_root,
        collection_epoch=epoch,
        through_date=official_date,
        expected_contract_sha256=runtime["contract"]["sha256"],
        expected_collector_code_sha256=code_sha,
        expected_evidence_scope_sha256=scope["evidence_scope_sha256"],
    ):
        ledger.verify()
        if any(_utc(plan["created_at_utc"]) < scope_created for plan in ledger.plans()):
            raise ProspectiveBatterOpportunityRuntimeError("history plan predates evidence scope creation")
        if any(
            _utc(entry["observed_at_utc"]) < scope_created
            for entry in ledger.planning_exclusions()
        ):
            raise ProspectiveBatterOpportunityRuntimeError("planning terminal predates evidence scope creation")
        tick = run_history_tick(ledger=ledger, fetch_final=fetch, now=current)
        for key, value in tick.items():
            collection[key] += value
    return {
        "schema_version": "prospective-batter-opportunity-tick-v1",
        "collector_state": (
            "blocked_upstream_roster_evidence"
            if planning["source_ledgers_missing"] or planning["source_terminal_coverage_missing"]
            else "processed"
        ),
        "official_date": official_date.isoformat(),
        "runtime_manifest_sha256": runtime_sha,
        "evidence_scope_sha256": scope["evidence_scope_sha256"],
        "evidence_scope_file_sha256": scope_file_sha,
        "collector_code_sha256": code_sha,
        "planning": planning,
        "collection": collection,
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--roster-ledger-root", type=Path, required=True)
    parser.add_argument("--history-ledger-root", type=Path, required=True)
    parser.add_argument("--evidence-scope", type=Path, required=True)
    parser.add_argument("--evidence-scope-sha256", required=True)
    parser.add_argument(
        "--runtime",
        type=Path,
        default=ROOT / "config/prospective_batter_opportunity_runtime_v1.json",
    )
    args = parser.parse_args(argv)
    try:
        result = run_all(
            plan_dir=args.plan_dir,
            roster_ledger_root=args.roster_ledger_root,
            history_ledger_root=args.history_ledger_root,
            runtime_path=args.runtime,
            evidence_scope_path=args.evidence_scope,
            expected_evidence_scope_file_sha256=args.evidence_scope_sha256,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return _exit_code_for_result(result)


def _exit_code_for_result(result: Mapping[str, object]) -> int:
    return 0 if result.get("collector_state") in {"processed", "sealed_may_no_access"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
