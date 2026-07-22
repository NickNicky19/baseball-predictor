#!/usr/bin/env python3
"""Dependency-free regression and mutation checks for the AWS shared PA release."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_aws_shared_pa_forward_tick import load_runtime, run_all  # noqa: E402
from src.evaluation.shadow_capture_plan import plan_from_schedule  # noqa: E402
from src.evaluation.shared_pa_forward_collector import (  # noqa: E402
    RawPregameResponse,
    SharedPAForwardCollectorError,
    pa_counts_from_hitting_stats,
    projected_lineups_from_schedule,
)
from src.evaluation.shared_pa_forward_evidence import (  # noqa: E402
    SharedPAForwardEvidenceError,
    load_forward_contract,
    snapshot_sha256,
    validate_player_snapshot,
)
from src.evaluation.shared_pa_forward_ledger import SharedPAForwardLedger  # noqa: E402
from src.evaluation.shared_pa_forward_runner import StatsBatch, run_tick  # noqa: E402


START = datetime(2026, 7, 30, tzinfo=timezone.utc)
HORIZON = START - timedelta(hours=4)


def must_fail(callable_, error, label: str) -> None:
    try:
        callable_()
    except error:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def plan():
    return plan_from_schedule(
        official_game_date="2026-07-29",
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot=[{
            "gamePk": 123456,
            "officialDate": "2026-07-29",
            "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "teams": {
                "home": {"team": {"name": "Home"}},
                "away": {"team": {"name": "Away"}},
            },
        }],
    )


def lineup() -> RawPregameResponse:
    body = json.dumps({
        "dates": [{"games": [{
            "gamePk": 123456,
            "officialDate": "2026-07-29",
            "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "teams": {
                "home": {"team": {"id": 111, "name": "Home"}},
                "away": {"team": {"id": 112, "name": "Away"}},
            },
            "lineups": {
                "homePlayers": [{"id": value} for value in range(201, 210)],
                "awayPlayers": [{"id": value} for value in range(101, 110)],
            },
        }]}],
    }, sort_keys=True).encode("utf-8")
    return RawPregameResponse(body=body, received_at_utc=HORIZON.isoformat())


def stats(player_id: int, *, impossible: bool = False) -> RawPregameResponse:
    stat = {
        "plateAppearances": 100, "atBats": 88, "hits": 28,
        "doubles": 5, "triples": 1, "homeRuns": 6,
        "baseOnBalls": 10, "strikeOuts": 20,
    }
    if impossible:
        stat["doubles"] = 30
    body = json.dumps({
        "people": [{
            "id": player_id,
            "stats": [{
                "group": {"displayName": "hitting"},
                "type": {"displayName": "season"},
                "splits": [{"stat": stat}],
            }],
        }],
    }, sort_keys=True).encode("utf-8")
    return RawPregameResponse(body=body, received_at_utc=HORIZON.isoformat())


def main() -> int:
    runtime_path = ROOT / "config/shared_pa_forward_runtime_v1.json"
    runtime, runtime_sha = load_runtime(runtime_path)
    loaded = load_forward_contract(
        root=ROOT,
        contract_path=ROOT / runtime["contract"]["path"],
    )
    current_plan = plan()
    with tempfile.TemporaryDirectory(prefix="shared_pa_forward_offline_") as temporary:
        ledger = SharedPAForwardLedger(
            Path(temporary) / "ledger",
            plan=current_plan,
            contract_sha256=loaded["contract_sha256"],
            runtime_manifest_sha256=runtime_sha,
            collector_code_sha256="b" * 64,
        )
        outcome = run_tick(
            plan=current_plan,
            ledger=ledger,
            loaded_contract=loaded,
            max_early_seconds=120,
            fetch_lineup_schedule=lambda _: lineup(),
            fetch_stats_batch=lambda players, _: StatsBatch(
                responses={player: stats(player) for player in players}, errors={}
            ),
            collector_instance_id=runtime["collector_instance_id"],
            collector_code_sha256="b" * 64,
            runtime_manifest_sha256=runtime_sha,
            now=HORIZON,
            completed_clock=lambda: HORIZON,
        )
        assert outcome["captured_complete"] == 2
        assert ledger.verify(require_complete_coverage=True)["captured_complete"] == 2
        print("[OK] both game sides publish nine hash-bound player snapshots")

        terminal = next((Path(temporary) / "ledger/terminal").glob("*.json"))
        entry = json.loads(terminal.read_text(encoding="utf-8"))
        player = copy.deepcopy(entry["players"][0])
        player["effective_lineup_slot"] = player["source_lineup_slot"]
        player["pa_distribution_scope"] = f"confirmed_slot_{player['source_lineup_slot']}"
        player["snapshot_sha256"] = snapshot_sha256(player)
        must_fail(
            lambda: validate_player_snapshot(player),
            SharedPAForwardEvidenceError,
            "projected lineup slot consumption",
        )

    must_fail(
        lambda: pa_counts_from_hitting_stats(response=stats(201, impossible=True), player_id=201),
        SharedPAForwardCollectorError,
        "impossible official count partition",
    )

    unsafe_lineup = json.loads(lineup().body)
    unsafe_lineup["dates"][0]["games"][0]["status"] = {"codedGameState": "F"}
    unsafe_response = RawPregameResponse(
        body=json.dumps(unsafe_lineup, sort_keys=True).encode("utf-8"),
        received_at_utc=HORIZON.isoformat(),
    )
    must_fail(
        lambda: projected_lineups_from_schedule(
            response=unsafe_response, plan=current_plan, target=current_plan.targets[0]
        ),
        SharedPAForwardCollectorError,
        "unapproved outcome field",
    )

    with tempfile.TemporaryDirectory(prefix="shared_pa_forward_late_") as temporary:
        late_ledger = SharedPAForwardLedger(
            Path(temporary) / "ledger",
            plan=current_plan,
            contract_sha256=loaded["contract_sha256"],
            runtime_manifest_sha256=runtime_sha,
            collector_code_sha256="b" * 64,
        )
        late = run_tick(
            plan=current_plan,
            ledger=late_ledger,
            loaded_contract=loaded,
            max_early_seconds=120,
            fetch_lineup_schedule=lambda _: (_ for _ in ()).throw(AssertionError("late fetch")),
            fetch_stats_batch=lambda *_: (_ for _ in ()).throw(AssertionError("late stats")),
            collector_instance_id=runtime["collector_instance_id"],
            collector_code_sha256="b" * 64,
            runtime_manifest_sha256=runtime_sha,
            now=HORIZON + timedelta(seconds=1),
        )
        assert late["missed_before_horizon"] == 2
        assert late_ledger.verify(require_complete_coverage=True)["missed_before_horizon"] == 2
        print("[OK] late service records permanent misses without fetching or backfilling")

    with tempfile.TemporaryDirectory(prefix="shared_pa_forward_date_scope_") as temporary:
        plan_dir = Path(temporary) / "plans"
        plan_dir.mkdir()
        (plan_dir / "2026-07-21.plan.json").write_bytes(b"must-not-be-opened")
        old = run_all(
            plan_dir=plan_dir,
            ledger_root=Path(temporary) / "ledger",
            runtime_path=runtime_path,
            now=datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
        )
        assert old["collector_state"] == "awaiting_published_plan"
        assert not (Path(temporary) / "ledger").exists()
        print("[OK] old plan files are not opened or backfilled")

    with tempfile.TemporaryDirectory(prefix="shared_pa_forward_may_scope_") as temporary:
        plan_dir = Path(temporary) / "plans"
        plan_dir.mkdir()
        (plan_dir / "2026-05-12.plan.json").write_bytes(b"must-not-be-opened")
        may = run_all(
            plan_dir=plan_dir,
            ledger_root=Path(temporary) / "ledger",
            runtime_path=runtime_path,
            now=datetime(2026, 5, 12, 12, tzinfo=timezone.utc),
        )
        assert may["collector_state"] == "sealed_may_no_access"
        assert not (Path(temporary) / "ledger").exists()
        print("[OK] May 2026 is skipped before plan access or artifact write")
    print("8/8 shared PA release checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
