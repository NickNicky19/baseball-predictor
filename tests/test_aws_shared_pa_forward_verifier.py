"""Independent AWS copy verifier must never fetch or infer missing evidence."""

from __future__ import annotations

import json
import os
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.run_aws_shared_pa_forward_tick import collector_code_sha256, load_runtime
from scripts.verify_aws_shared_pa_forward_tree import verify_tree
from src.evaluation.shadow_capture_plan import plan_from_schedule
from src.evaluation.shared_pa_forward_evidence import load_forward_contract
from src.evaluation.shared_pa_forward_ledger import SharedPAForwardLedger
from src.evaluation.shared_pa_forward_ledger import _atomic_publish_once


ROOT = Path(__file__).resolve().parents[1]


def test_atomic_publication_sets_group_read_only_mode_before_link(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "evidence.json"
    with patch(
        "src.evaluation.shared_pa_forward_ledger.os.fchmod",
        wraps=os.fchmod,
    ) as chmod:
        assert _atomic_publish_once(path, b"immutable\n") is True
    chmod.assert_called_once()
    assert chmod.call_args.args[1] == 0o640
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o640


def test_may_rejected_before_opening_invalid_plan(tmp_path: Path) -> None:
    plan = tmp_path / "plans"
    plan.mkdir()
    (plan / "2026-05-12.plan.json").write_bytes(b"must-never-be-opened")
    with pytest.raises(ValueError, match="May 2026"):
        verify_tree(
            evidence_root=tmp_path,
            official_date="2026-05-12",
            assessed_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
        )


def test_empty_slate_exact_tree_verifies_without_sources(tmp_path: Path) -> None:
    plan = plan_from_schedule(
        official_game_date="2026-07-29",
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot=[],
    )
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "2026-07-29.plan.json").write_text(json.dumps(plan.to_dict()), encoding="utf-8")
    runtime, runtime_sha = load_runtime(ROOT / "config/shared_pa_forward_runtime_v1.json")
    loaded = load_forward_contract(root=ROOT, contract_path=ROOT / runtime["contract"]["path"])
    SharedPAForwardLedger(
        tmp_path / "shared-pa-forward" / "ledgers" / "2026-07-29" / plan.plan_sha256,
        plan=plan,
        contract_sha256=loaded["contract_sha256"],
        runtime_manifest_sha256=runtime_sha,
        collector_code_sha256=collector_code_sha256(),
    )
    report = verify_tree(
        evidence_root=tmp_path,
        official_date="2026-07-29",
        assessed_at=datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
    )
    assert report["state"] == "verified"
    assert report["replacement_data_fetched"] is False
    assert report["replay_compatibility"] == {"applied": False}


def test_certified_permission_only_code_transition_replays_without_republishing(
    tmp_path: Path,
) -> None:
    plan = plan_from_schedule(
        official_game_date="2026-07-29",
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot=[],
    )
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "2026-07-29.plan.json").write_text(json.dumps(plan.to_dict()), encoding="utf-8")
    runtime, runtime_sha = load_runtime(ROOT / "config/shared_pa_forward_runtime_v1.json")
    loaded = load_forward_contract(root=ROOT, contract_path=ROOT / runtime["contract"]["path"])
    root = tmp_path / "shared-pa-forward" / "ledgers" / "2026-07-29" / plan.plan_sha256
    SharedPAForwardLedger(
        root,
        plan=plan,
        contract_sha256=loaded["contract_sha256"],
        runtime_manifest_sha256=runtime_sha,
        collector_code_sha256=collector_code_sha256(),
    )
    manifest_path = root / "ledger_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["collector_code_sha256"] = "8b3a713d90f85f03f9f11032e3fc8544494cb64647e6c0f48376bd65eda58489"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    before = manifest_path.read_bytes()
    report = verify_tree(
        evidence_root=tmp_path,
        official_date="2026-07-29",
        assessed_at=datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
    )
    assert report["state"] == "verified"
    assert report["collector_code_sha256"] == manifest["collector_code_sha256"]
    assert report["verifier_code_sha256"] == collector_code_sha256()
    assert report["replay_compatibility"]["applied"] is True
    assert manifest_path.read_bytes() == before


def test_uncertified_collector_code_transition_fails_closed(tmp_path: Path) -> None:
    plan = plan_from_schedule(
        official_game_date="2026-07-29",
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot=[],
    )
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "2026-07-29.plan.json").write_text(json.dumps(plan.to_dict()), encoding="utf-8")
    runtime, runtime_sha = load_runtime(ROOT / "config/shared_pa_forward_runtime_v1.json")
    loaded = load_forward_contract(root=ROOT, contract_path=ROOT / runtime["contract"]["path"])
    SharedPAForwardLedger(
        tmp_path / "shared-pa-forward" / "ledgers" / "2026-07-29" / plan.plan_sha256,
        plan=plan,
        contract_sha256=loaded["contract_sha256"],
        runtime_manifest_sha256=runtime_sha,
        collector_code_sha256="d" * 64,
    )
    with pytest.raises(ValueError, match="no unique hash-bound"):
        verify_tree(
            evidence_root=tmp_path,
            official_date="2026-07-29",
            assessed_at=datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
        )
