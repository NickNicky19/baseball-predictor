import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import run_aws_shared_pa_comparator_tick as tick
from src.evaluation.shadow_capture_plan import canonical_schedule_records, plan_from_schedule
from src.evaluation.shared_pa_comparator_runtime import (
    SharedPAComparatorRuntimeError,
    canonical_bytes,
)


def _tree(root: Path):
    day = "2026-07-30"
    game = {
        "gamePk": 901,
        "officialDate": day,
        "gameDate": "2026-07-30T23:10:00Z",
        "teams": {
            "home": {"team": {"id": 1, "name": "Home Club"}},
            "away": {"team": {"id": 2, "name": "Away Club"}},
        },
    }
    source = json.dumps({"dates": [{"games": [game]}]}, sort_keys=True).encode()
    records = canonical_schedule_records([game])
    plan = plan_from_schedule(
        official_game_date=day, entry_hours=4, policy_sha256="a" * 64,
        schedule_snapshot=records,
    )
    plan_dir = root / "plans"
    receipt_root = root / "plan-receipts"
    plan_dir.mkdir(parents=True)
    (receipt_root / "plans").mkdir(parents=True)
    (receipt_root / "raw").mkdir(parents=True)
    (plan_dir / f"{day}.plan.json").write_bytes(canonical_bytes(plan.to_dict()))
    source_sha = hashlib.sha256(source).hexdigest()
    receipt = {
        "schema_version": "aws-pitcher-receipt-plan-receipt-v1",
        "official_game_date": day,
        "plan_sha256": plan.plan_sha256,
        "runtime_sha256": "b" * 64,
        "source_name": "mlb_statsapi_schedule",
        "source_payload_sha256": source_sha,
        "received_at_utc": "2026-07-29T10:00:00Z",
        "targets": 1,
        "research_only": True,
        "betting_authorized": False,
        "model_or_market_accessed": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(canonical_bytes(receipt)).hexdigest()
    (receipt_root / "plans" / f"{day}.{plan.plan_sha256}.json").write_bytes(canonical_bytes(receipt))
    (receipt_root / "raw" / f"{day}.{source_sha}.json").write_bytes(source)
    return day, plan, plan_dir, receipt_root


def test_existing_plan_receipt_and_raw_schedule_must_reproduce_same_plan(tmp_path: Path) -> None:
    day, plan, plan_dir, receipt_root = _tree(tmp_path)
    loaded, schedule = tick._load_plan_and_schedule(
        official_date=day, plan_dir=plan_dir, plan_receipt_root=receipt_root
    )
    assert loaded.plan_sha256 == plan.plan_sha256
    assert schedule["schedule"][0]["gamePk"] == 901


def test_mutated_raw_plan_source_fails_closed(tmp_path: Path) -> None:
    day, plan, plan_dir, receipt_root = _tree(tmp_path)
    receipt = json.loads(
        (receipt_root / "plans" / f"{day}.{plan.plan_sha256}.json").read_text()
    )
    raw = receipt_root / "raw" / f"{day}.{receipt['source_payload_sha256']}.json"
    raw.write_bytes(raw.read_bytes() + b" ")
    with pytest.raises(SharedPAComparatorRuntimeError, match="hash changed"):
        tick._load_plan_and_schedule(
            official_date=day, plan_dir=plan_dir, plan_receipt_root=receipt_root
        )


def test_may_tick_returns_without_touching_plan_paths(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    result = tick.run_all(
        mode="capture-markets",
        plan_dir=tmp_path / "forbidden-plans",
        plan_receipt_root=tmp_path / "forbidden-receipts",
        evidence_root=tmp_path / "forbidden-output",
        runtime_path=root / "config/shared_pa_comparator_runtime_v1.json",
        now=datetime(2026, 5, 12, 12, tzinfo=timezone.utc),
    )
    assert result["state"] == "sealed_may_no_access"
    assert not any(tmp_path.iterdir())
