"""Pure planning and archive identities for separate T-4 and T-1 lanes."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.evaluation.shadow_capture_plan import ShadowCapturePlan, plan_from_schedule
from src.evaluation.shared_pa_forward_evidence import sha256_value


class SharedPADualHorizonError(ValueError):
    """Dual-horizon planning would weaken an immutable evidence boundary."""


LANES = {"projected_t4": 4, "confirmed_t1": 1}


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise SharedPADualHorizonError(f"{label} must be a lowercase SHA-256")
    return value


def load_lane_contract(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "shared-pa-dual-horizon-lanes-v1"
        or value.get("status") != "RESEARCH_ONLY_NOT_DEPLOYED"
        or value.get("model_id") != "shared_pa_candidate_v1"
        or {name: row.get("entry_hours") for name, row in value.get("lanes", {}).items()} != LANES
        or any(item is not False for item in value.get("protected_boundaries", {}).values())
    ):
        raise SharedPADualHorizonError("dual-horizon lane contract changed")
    return value, hashlib.sha256(raw).hexdigest()


def build_dual_horizon_plans(
    *,
    official_game_date: str,
    schedule_snapshot: Iterable[Mapping[str, Any]],
    contract_path: Path,
) -> dict[str, ShadowCapturePlan]:
    target_date = date.fromisoformat(official_game_date)
    if target_date.isoformat() != official_game_date:
        raise SharedPADualHorizonError("official date must be canonical")
    if target_date.year == 2026 and target_date.month == 5:
        raise SharedPADualHorizonError("May 2026 is sealed")
    _, contract_sha256 = load_lane_contract(contract_path)
    retained = [dict(row) for row in schedule_snapshot]
    plans = {
        lane: plan_from_schedule(
            official_game_date=official_game_date,
            entry_hours=hours,
            policy_sha256=contract_sha256,
            schedule_snapshot=retained,
        )
        for lane, hours in LANES.items()
    }
    projected = {target.mlb_game_pk: target for target in plans["projected_t4"].targets}
    confirmed = {target.mlb_game_pk: target for target in plans["confirmed_t1"].targets}
    if set(projected) != set(confirmed) or any(
        projected[game_pk].official_start_time_utc != confirmed[game_pk].official_start_time_utc
        for game_pk in projected
    ):
        raise SharedPADualHorizonError("lane schedule identities differ")
    if {target.target_id for plan in plans.values() for target in plan.targets}.__len__() != sum(
        len(plan.targets) for plan in plans.values()
    ):
        raise SharedPADualHorizonError("lane target identities collide")
    return plans


def archive_relative_path(
    *, lane: str, official_game_date: str, mlb_game_pk: int,
    side: str, player_id: int, model_id: str,
) -> Path:
    if lane not in LANES:
        raise SharedPADualHorizonError("unknown prediction lane")
    target_date = date.fromisoformat(official_game_date)
    if target_date.isoformat() != official_game_date or (
        target_date.year == 2026 and target_date.month == 5
    ):
        raise SharedPADualHorizonError("archive date is forbidden or noncanonical")
    if side not in {"home", "away"}:
        raise SharedPADualHorizonError("side must be home or away")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (mlb_game_pk, player_id)):
        raise SharedPADualHorizonError("game and player identities must be positive integers")
    if model_id != "shared_pa_candidate_v1":
        raise SharedPADualHorizonError("model identity differs")
    return Path(lane) / official_game_date / str(mlb_game_pk) / side / str(player_id) / f"{model_id}.json"


def bind_prediction_archive_to_lane(
    *, lane: str, plan: ShadowCapturePlan, archive: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a replayed candidate archive to exactly one immutable horizon."""
    if lane not in LANES or plan.entry_hours != LANES[lane]:
        raise SharedPADualHorizonError("plan horizon does not match lane")
    if archive.get("model_id") != "shared_pa_candidate_v1":
        raise SharedPADualHorizonError("archive model identity differs")
    if archive.get("game_date") != plan.official_game_date:
        raise SharedPADualHorizonError("archive date differs from lane plan")
    if archive.get("research_only") is not True or archive.get("betting_authorized") is not False:
        raise SharedPADualHorizonError("archive research boundary changed")
    targets = {target.mlb_game_pk: target for target in plan.targets}
    for prediction in archive.get("predictions", []):
        game_pk = prediction.get("mlb_game_pk")
        if game_pk not in targets:
            raise SharedPADualHorizonError("prediction game is absent from lane plan")
        target = targets[game_pk]
        horizon = datetime.fromisoformat(
            str(prediction.get("decision_horizon_utc", "")).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        expected = datetime.fromisoformat(
            target.entry_target_at_utc.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        if horizon != expected:
            raise SharedPADualHorizonError("prediction horizon differs from lane target")
        if lane == "confirmed_t1" and prediction.get("input_health", {}).get("lineup_state") != "official_confirmed":
            raise SharedPADualHorizonError("confirmed lane requires official confirmed-lineup evidence")
    unsigned = {
        "schema_version": "integrated-shared-pa-lane-archive-v1",
        "lane_id": lane,
        "entry_hours": LANES[lane],
        "plan_sha256": plan.plan_sha256,
        "schedule_snapshot_sha256": plan.schedule_snapshot_sha256,
        "model_id": "shared_pa_candidate_v1",
        "official_game_date": plan.official_game_date,
        "candidate_archive": dict(archive),
        "research_only": True,
        "betting_authorized": False,
    }
    return {**unsigned, "lane_archive_sha256": sha256_value(unsigned)}
