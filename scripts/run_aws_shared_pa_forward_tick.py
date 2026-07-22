#!/usr/bin/env python3
"""Run one AWS-primary tick for the separate shared batter PA control ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shadow_capture_plan import ShadowCapturePlan  # noqa: E402
from src.evaluation.shared_pa_forward_collector import (  # noqa: E402
    RawPregameResponse,
    validate_schedule_input_surface,
    validate_stats_input_surface,
)
from src.evaluation.shared_pa_forward_evidence import load_forward_contract  # noqa: E402
from src.evaluation.shared_pa_forward_ledger import SharedPAForwardLedger  # noqa: E402
from src.evaluation.shared_pa_forward_runner import StatsBatch, run_tick  # noqa: E402

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux runtime boundary
    fcntl = None  # type: ignore[assignment]


class AWSSharedPAForwardError(ValueError):
    """The isolated AWS shared-PA service cannot prove its runtime contract."""


_CODE_PATHS = (
    "scripts/run_aws_shared_pa_forward_tick.py",
    "src/evaluation/shadow_capture_plan.py",
    "src/evaluation/shared_pa_forward_collector.py",
    "src/evaluation/shared_pa_forward_evidence.py",
    "src/evaluation/shared_pa_forward_ledger.py",
    "src/evaluation/shared_pa_forward_runner.py",
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collector_code_sha256(root: Path = ROOT) -> str:
    files = {path: _sha256_file(root / path) for path in _CODE_PATHS}
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def load_runtime(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AWSSharedPAForwardError("runtime manifest is not valid JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "shared-pa-forward-runtime-v1":
        raise AWSSharedPAForwardError("runtime manifest schema changed")
    if payload.get("scope") != "future_only_batter_probability_control":
        raise AWSSharedPAForwardError("runtime scope changed")
    contract = payload.get("contract")
    sources = payload.get("sources")
    scheduler = payload.get("scheduler")
    invariants = payload.get("invariants")
    if not all(isinstance(value, dict) for value in (contract, sources, scheduler, invariants)):
        raise AWSSharedPAForwardError("runtime manifest is incomplete")
    if contract != {
        "path": "config/shared_pa_forward_evidence_contract_v1.json",
        "sha256": "b93827a465e5145d1e725f84cbed53d413f0c2e8601e41021221e3d13d079401",
    }:
        raise AWSSharedPAForwardError("shared PA contract binding changed")
    if sources != {
        "lineup_schedule": {
            "base_url": "https://statsapi.mlb.com/api/v1/schedule",
            "sport_id": 1,
            "hydrate": "team,lineups",
            "fields": "dates,games,gamePk,officialDate,gameDate,teams,home,away,team,id,name,lineups,homePlayers,awayPlayers",
        },
        "player_stats": {
            "base_url_template": "https://statsapi.mlb.com/api/v1/people/{player_id}",
            "hydrate_template": "stats(group=[hitting],type=[season],season={season})",
            "fields": "people,id,stats,group,displayName,type,splits,stat,plateAppearances,atBats,hits,doubles,triples,homeRuns,baseOnBalls,strikeOuts",
        },
    }:
        raise AWSSharedPAForwardError("official source contract changed")
    if scheduler != {
        "entry_hours": 4,
        "max_early_seconds": 120,
        "tick_seconds": 60,
        "request_timeout_seconds": 20,
        "stats_workers": 12,
    }:
        raise AWSSharedPAForwardError("scheduler or transport contract changed")
    expected_invariants = {
        "read_existing_pitcher_plan_only": True,
        "separate_ledger_root": True,
        "retain_raw_responses": True,
        "outcomes_forbidden": True,
        "prices_forbidden": True,
        "pitcher_probability_inputs_forbidden": True,
        "late_backfill_forbidden": True,
        "may_2026_forbidden": True,
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
    }
    if invariants != expected_invariants:
        raise AWSSharedPAForwardError("runtime safety invariants changed")
    if not isinstance(payload.get("collector_instance_id"), str) or not payload["collector_instance_id"].strip():
        raise AWSSharedPAForwardError("collector instance identity is blank")
    return payload, hashlib.sha256(raw).hexdigest()


def _response(url: str, *, user_agent: str, timeout: int) -> RawPregameResponse:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout) as response:  # nosec B310: URLs are locked official MLB endpoints
        if response.status != 200:
            raise AWSSharedPAForwardError(f"official MLB source returned HTTP {response.status}")
        body = response.read()
    return RawPregameResponse(
        body=body,
        received_at_utc=datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    )


def source_fetchers(runtime: dict[str, Any]):
    sources = runtime["sources"]
    timeout = int(runtime["scheduler"]["request_timeout_seconds"])

    def lineups(official_date: str) -> RawPregameResponse:
        source = sources["lineup_schedule"]
        query = urlencode({
            "sportId": str(source["sport_id"]),
            "date": official_date,
            "hydrate": source["hydrate"],
            "fields": source["fields"],
        })
        response = _response(
            f"{source['base_url']}?{query}",
            user_agent="baseball-predictor-shared-pa-forward/1.0",
            timeout=timeout,
        )
        validate_schedule_input_surface(response)
        return response

    def one_stat(player_id: int, season: int) -> RawPregameResponse:
        source = sources["player_stats"]
        url = source["base_url_template"].format(player_id=int(player_id))
        hydrate = source["hydrate_template"].format(season=int(season))
        response = _response(
            f"{url}?{urlencode({'hydrate': hydrate, 'fields': source['fields']})}",
            user_agent="baseball-predictor-shared-pa-forward/1.0",
            timeout=timeout,
        )
        validate_stats_input_surface(response)
        return response

    def stats_batch(player_ids: list[int], season: int) -> StatsBatch:
        if len(player_ids) != 9 or len(set(player_ids)) != 9:
            raise AWSSharedPAForwardError("stats batch requires exactly nine unique lineup players")
        responses: dict[int, RawPregameResponse] = {}
        errors: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=int(runtime["scheduler"]["stats_workers"])) as pool:
            futures = {pool.submit(one_stat, player_id, season): player_id for player_id in player_ids}
            for future in as_completed(futures):
                player_id = futures[future]
                try:
                    responses[player_id] = future.result()
                except Exception as exc:
                    errors[player_id] = f"official_stats_fetch_failed_{type(exc).__name__}"
        return StatsBatch(responses=responses, errors=errors)

    return lineups, stats_batch


def _plan(path: Path) -> ShadowCapturePlan:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AWSSharedPAForwardError(f"cannot load immutable plan {path.name}") from exc
    if not isinstance(payload, dict):
        raise AWSSharedPAForwardError("immutable plan root must be an object")
    return ShadowCapturePlan.from_mapping(payload)


def run_all(
    *,
    plan_dir: Path,
    ledger_root: Path,
    runtime_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    runtime, runtime_sha = load_runtime(runtime_path)
    contract_path = ROOT / runtime["contract"]["path"]
    if _sha256_file(contract_path) != runtime["contract"]["sha256"]:
        raise AWSSharedPAForwardError("shared PA contract file hash changed")
    loaded = load_forward_contract(root=ROOT, contract_path=contract_path)
    lineups, stats_batch = source_fetchers(runtime)
    current = now or datetime.now(timezone.utc)
    official_date = current.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    if official_date.startswith("2026-05-"):
        return {
            "schema_version": "aws-shared-pa-forward-tick-v1",
            "tick_at_utc": current.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "runtime_manifest_sha256": runtime_sha,
            "collector_code_sha256": collector_code_sha256(),
            "plans": [],
            "collector_state": "sealed_may_no_access",
            "research_only": True,
            "betting_authorized": False,
            "production_changed": False,
            "outcomes_prices_and_settlement_accessed": False,
        }
    results = []
    if not plan_dir.is_dir():
        raise AWSSharedPAForwardError("pitcher receipt plan directory is unavailable")
    path = plan_dir / f"{official_date}.plan.json"
    if path.is_file():
        plan = _plan(path)
        if plan.official_game_date != official_date or plan.entry_hours != 4:
            raise AWSSharedPAForwardError("current pitcher plan date or horizon differs")
        ledger = SharedPAForwardLedger(
            ledger_root / plan.official_game_date / plan.plan_sha256,
            plan=plan,
            contract_sha256=loaded["contract_sha256"],
            runtime_manifest_sha256=runtime_sha,
            collector_code_sha256=collector_code_sha256(),
        )
        if not plan.targets:
            results.append({
                "plan": path.name,
                "plan_sha256": plan.plan_sha256,
                "no_scheduled_targets": True,
                "captured_complete": 0,
                "lineup_unavailable": 0,
                "source_error": 0,
                "missed_before_horizon": 0,
                "future": 0,
            })
        else:
            tick = run_tick(
                plan=plan,
                ledger=ledger,
                loaded_contract=loaded,
                max_early_seconds=int(runtime["scheduler"]["max_early_seconds"]),
                fetch_lineup_schedule=lineups,
                fetch_stats_batch=stats_batch,
                collector_instance_id=runtime["collector_instance_id"],
                collector_code_sha256=collector_code_sha256(),
                runtime_manifest_sha256=runtime_sha,
                now=current,
            )
            results.append({"plan": path.name, "plan_sha256": plan.plan_sha256, **tick})
    return {
        "schema_version": "aws-shared-pa-forward-tick-v1",
        "tick_at_utc": current.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_manifest_sha256": runtime_sha,
        "collector_code_sha256": collector_code_sha256(),
        "plans": results,
        "collector_state": "awaiting_published_plan" if not results else "processed",
        "research_only": True,
        "betting_authorized": False,
        "production_changed": False,
        "outcomes_prices_and_settlement_accessed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--ledger-root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=ROOT / "config/shared_pa_forward_runtime_v1.json")
    parser.add_argument("--lock-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if fcntl is None:
            raise AWSSharedPAForwardError("AWS shared PA service requires Linux advisory locks")
        args.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with args.lock_file.open("a+", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AWSSharedPAForwardError("another shared PA tick is still running") from exc
            print(json.dumps(run_all(
                plan_dir=args.plan_dir,
                ledger_root=args.ledger_root,
                runtime_path=args.runtime,
            ), sort_keys=True))
    except (OSError, ValueError, RuntimeError, AWSSharedPAForwardError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
