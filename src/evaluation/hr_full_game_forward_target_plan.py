"""Fail-closed expected-target coverage for a future HR full-game release."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


class HRFullGameForwardTargetPlanError(ValueError):
    """Raised when an expected-target or terminal-coverage boundary is unsafe."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HRFullGameForwardTargetPlanError(f"{label} must be an object")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise HRFullGameForwardTargetPlanError(f"{label} must be an ISO UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HRFullGameForwardTargetPlanError(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise HRFullGameForwardTargetPlanError(f"{label} must include an offset")
    return parsed.astimezone(timezone.utc)


def _target_key(target: Mapping[str, Any]) -> str:
    try:
        game = int(target["mlb_game_pk"])
        player = int(target["player_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HRFullGameForwardTargetPlanError("target needs hard integer game/player IDs") from exc
    expected = f"{game}:{player}:home_runs:0.5:over"
    if target.get("hard_model_key") != expected:
        raise HRFullGameForwardTargetPlanError("target hard_model_key does not match its fixed HR target")
    return expected


def load_target_plan_contract(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    try:
        payload = _object(json.loads(source.read_text(encoding="utf-8")), "target-plan contract")
    except (OSError, json.JSONDecodeError) as exc:
        raise HRFullGameForwardTargetPlanError("target-plan contract is unreadable") from exc
    if payload.get("schema_version") != "hr-full-game-forward-target-plan-contract-v1":
        raise HRFullGameForwardTargetPlanError("target-plan contract has an unknown schema")
    if payload.get("status") != "PREREGISTERED_NOT_DEPLOYED":
        raise HRFullGameForwardTargetPlanError("target-plan contract cannot be deployed or promoted")
    if any(payload.get(key) is not False for key in ("betting_authorized", "economic_evidence_eligible")):
        raise HRFullGameForwardTargetPlanError("target-plan contract must remain non-economic")
    if payload.get("may_2026_eligible_as_untouched_holdout") is not False or payload.get("separate_from_running_v13_operational_smoke") is not True:
        raise HRFullGameForwardTargetPlanError("May/v13 separation was weakened")
    base = _object(payload.get("base_input_contract"), "base_input_contract")
    if not isinstance(base.get("path"), str) or not isinstance(base.get("sha256"), str) or len(base["sha256"]) != 64:
        raise HRFullGameForwardTargetPlanError("base input contract binding is incomplete")
    required = _object(payload.get("target_plan"), "target_plan")
    if required.get("schema_version") != "hr-full-game-forward-target-plan-v1":
        raise HRFullGameForwardTargetPlanError("target-plan schema changed")
    if required.get("targets_must_be_unique") is not True or required.get("all_planned_targets_require_one_terminal_ledger_record") is not True or required.get("unplanned_terminal_records_forbidden") is not True:
        raise HRFullGameForwardTargetPlanError("target coverage guard was weakened")
    if not isinstance(payload.get("required_mutations"), list) or len(payload["required_mutations"]) < 9:
        raise HRFullGameForwardTargetPlanError("target-plan mutation coverage was weakened")
    return dict(payload)


def load_target_plan(*, plan_path: str | Path, target_plan_contract_path: str | Path) -> set[str]:
    """Validate a pre-horizon immutable target plan and return its hard keys."""
    contract = load_target_plan_contract(target_plan_contract_path)
    base = contract["base_input_contract"]
    contract_file = Path(target_plan_contract_path).resolve()
    base_file = (contract_file.parent.parent / str(base["path"])).resolve()
    if not base_file.is_file() or _sha256(base_file) != str(base["sha256"]).lower():
        raise HRFullGameForwardTargetPlanError("base HR input contract is missing or hash-mismatched")
    source = Path(plan_path).resolve()
    try:
        plan = _object(json.loads(source.read_text(encoding="utf-8")), "target plan")
    except (OSError, json.JSONDecodeError) as exc:
        raise HRFullGameForwardTargetPlanError("target plan is unreadable") from exc
    required = contract["target_plan"]
    if plan.get("schema_version") != required["schema_version"]:
        raise HRFullGameForwardTargetPlanError("target plan has an unknown schema")
    for field in required["required_plan_fields"]:
        if field not in plan:
            raise HRFullGameForwardTargetPlanError(f"target plan missing {field}")
    if plan.get("base_contract_sha256") != str(base["sha256"]).lower():
        raise HRFullGameForwardTargetPlanError("target plan binds a different base contract")
    plan_receipt = _utc(plan["plan_receipt_utc"], "plan_receipt_utc")
    targets = plan.get("targets")
    if not isinstance(targets, list) or not targets:
        raise HRFullGameForwardTargetPlanError("target plan must contain targets")
    keys: set[str] = set()
    for target in targets:
        mapping = _object(target, "target")
        for field in required["required_target_fields"]:
            if field not in mapping:
                raise HRFullGameForwardTargetPlanError(f"target missing {field}")
        key = _target_key(mapping)
        start = _utc(mapping["official_start_utc"], "official_start_utc")
        horizon = _utc(mapping["target_horizon_utc"], "target_horizon_utc")
        observed = _utc(mapping["source_observation_utc"], "source_observation_utc")
        if horizon != start - timedelta(hours=4):
            raise HRFullGameForwardTargetPlanError("target horizon is not exact T-4h")
        if plan_receipt > horizon or observed > horizon:
            raise HRFullGameForwardTargetPlanError("target plan contains post-horizon information")
        if key in keys:
            raise HRFullGameForwardTargetPlanError("target plan contains a duplicate hard target")
        keys.add(key)
    return keys


def verify_terminal_coverage(*, target_keys: set[str], ledger_root: str | Path) -> int:
    """Require exactly one terminal record for every planned target, no more or less."""
    index_path = Path(ledger_root).resolve() / "terminal_index.json"
    try:
        index = _object(json.loads(index_path.read_text(encoding="utf-8")), "terminal index")
    except (OSError, json.JSONDecodeError) as exc:
        raise HRFullGameForwardTargetPlanError("terminal index is unreadable") from exc
    targets = index.get("targets")
    if not isinstance(targets, Mapping):
        raise HRFullGameForwardTargetPlanError("terminal index targets are invalid")
    actual = set(str(key) for key in targets)
    if actual != target_keys:
        missing = sorted(target_keys - actual)
        extra = sorted(actual - target_keys)
        raise HRFullGameForwardTargetPlanError(
            f"terminal coverage differs from immutable target plan: missing={missing} extra={extra}"
        )
    return len(actual)
