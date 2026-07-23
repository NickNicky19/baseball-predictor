#!/usr/bin/env python3
"""Offline release gate for the isolated AWS comparator service."""

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

from src.evaluation.shadow_capture_plan import plan_from_schedule  # noqa: E402
from src.evaluation.shared_pa_comparator_runtime import (  # noqa: E402
    PredictionArtifacts,
    SharedPAComparatorRuntimeError,
    load_runtime,
    prepare_prediction_batches,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    runtime, runtime_sha = load_runtime(
        ROOT / "config/shared_pa_comparator_runtime_v1.json", repository_root=ROOT
    )
    deploy = ROOT / "deploy/shared_pa_comparators"
    units = {
        path.name: path.read_text(encoding="utf-8")
        for path in deploy.glob("*.service")
    }
    require(len(units) == 4, "expected exactly four isolated comparator services")
    joined = "\n".join(units.values())
    require("systemctl restart" not in joined, "service may not restart another collector")
    require("ReadOnlyPaths=/srv/baseball-shadow/pitcher-receipts" in joined, "pitcher evidence is not read-only")
    require("ReadWritePaths=/srv/baseball-shadow/shared-pa-comparators" in joined, "separate comparator root is not writable")
    require("run_slate.py" not in joined, "normal operational prediction entry point was altered/reused")

    start = datetime(2026, 7, 30, 23, 10, tzinfo=timezone.utc)
    horizon = start - timedelta(hours=4)
    plan = plan_from_schedule(
        official_game_date="2026-07-30", entry_hours=4, policy_sha256="a" * 64,
        schedule_snapshot=[{
            "gamePk": 901, "officialDate": "2026-07-30",
            "gameDate": start.isoformat(),
            "teams": {
                "home": {"team": {"name": "Home Club"}},
                "away": {"team": {"name": "Away Club"}},
            },
        }],
    )
    body = json.dumps({"game_date": "2026-07-30"}).encode()
    with tempfile.TemporaryDirectory() as temporary:
        called = False

        def forbidden(_):
            nonlocal called
            called = True
            return PredictionArtifacts(body, body), horizon

        result = prepare_prediction_batches(
            plan=plan, evidence_root=temporary,
            scheduler=runtime["scheduler"], runtime_sha256=runtime_sha,
            now=horizon - timedelta(minutes=4), generate=forbidden,
        )
        require(result["missed"] == 1 and not called, "late prediction was backfilled")
    mutated = copy.deepcopy(runtime)
    mutated["invariants"]["late_backfill_forbidden"] = False
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "runtime.json"
        path.write_text(json.dumps(mutated), encoding="utf-8")
        try:
            load_runtime(path, repository_root=temporary)
        except SharedPAComparatorRuntimeError:
            pass
        else:
            raise RuntimeError("weakened runtime manifest was accepted")
    print(json.dumps({
        "schema_version": "aws-shared-pa-comparator-offline-check-v1",
        "runtime_manifest_sha256": runtime_sha,
        "late_backfill_mutation_rejected": True,
        "existing_collectors_modified": False,
        "outcomes_accessed": False,
        "research_only": True,
        "betting_authorized": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
