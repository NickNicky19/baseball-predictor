"""Dependency-minimal AWS runtime and no-plan safety tests."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.run_aws_shared_pa_forward_tick import (
    AWSSharedPAForwardError,
    collector_code_sha256,
    load_runtime,
    run_all,
)
from scripts.check_shared_pa_forward_release_manifest import verify_manifest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "config/shared_pa_forward_runtime_v1.json"


def test_runtime_and_code_identity_are_hash_bound() -> None:
    runtime, digest = load_runtime(RUNTIME)
    assert len(digest) == 64
    assert len(collector_code_sha256(ROOT)) == 64
    assert runtime["invariants"]["outcomes_forbidden"] is True
    assert runtime["invariants"]["prices_forbidden"] is True
    assert runtime["invariants"]["pitcher_probability_inputs_forbidden"] is True
    assert runtime["invariants"]["betting_authorized"] is False
    assert "score" not in runtime["sources"]["lineup_schedule"]["fields"]
    assert "status" not in runtime["sources"]["lineup_schedule"]["fields"]
    assert "plateAppearances" in runtime["sources"]["player_stats"]["fields"]


def test_no_published_plan_performs_no_source_fetch(tmp_path: Path) -> None:
    plan_dir = tmp_path / "plans"
    plan_dir.mkdir()
    payload = run_all(
        plan_dir=plan_dir,
        ledger_root=tmp_path / "ledger",
        runtime_path=RUNTIME,
    )
    assert payload["collector_state"] == "awaiting_published_plan"
    assert payload["plans"] == []
    assert payload["outcomes_prices_and_settlement_accessed"] is False


def test_runtime_mutation_is_terminal(tmp_path: Path) -> None:
    payload = json.loads(RUNTIME.read_text(encoding="utf-8"))
    payload["invariants"]["betting_authorized"] = True
    mutated = tmp_path / "runtime.json"
    mutated.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AWSSharedPAForwardError, match="safety invariants"):
        load_runtime(mutated)


def test_old_plan_files_are_not_opened_or_backfilled(tmp_path: Path) -> None:
    plan_dir = tmp_path / "plans"
    plan_dir.mkdir()
    (plan_dir / "2026-07-21.plan.json").write_bytes(b"not-json-and-must-not-be-opened")
    payload = run_all(
        plan_dir=plan_dir,
        ledger_root=tmp_path / "ledger",
        runtime_path=RUNTIME,
        now=datetime(2026, 7, 22, 12, tzinfo=timezone.utc),
    )
    assert payload["collector_state"] == "awaiting_published_plan"
    assert not (tmp_path / "ledger").exists()


def test_may_is_skipped_before_plan_access_or_ledger_write(tmp_path: Path) -> None:
    plan_dir = tmp_path / "plans"
    plan_dir.mkdir()
    (plan_dir / "2026-05-12.plan.json").write_bytes(b"must-never-be-opened")
    payload = run_all(
        plan_dir=plan_dir,
        ledger_root=tmp_path / "ledger",
        runtime_path=RUNTIME,
        now=datetime(2026, 5, 12, 12, tzinfo=timezone.utc),
    )
    assert payload["collector_state"] == "sealed_may_no_access"
    assert not (tmp_path / "ledger").exists()


def test_deployment_is_separate_and_cannot_run_prediction_or_pitcher_units() -> None:
    deploy = ROOT / "deploy/shared_pa_forward"
    tick = (deploy / "baseball-shared-pa-forward-tick.service").read_text(encoding="utf-8")
    health = (deploy / "baseball-shared-pa-forward-health.service").read_text(encoding="utf-8")
    installer = (deploy / "install_exact_release.sh").read_text(encoding="utf-8")
    assert "run_aws_shared_pa_forward_tick.py" in tick
    assert "ReadOnlyPaths=/srv/baseball-shadow/pitcher-receipts/plans" in tick
    assert "ReadWritePaths=/srv/baseball-shadow/shared-pa-forward" in tick
    assert "run_slate.py" not in tick + health + installer
    assert not re.search(
        r"systemctl\s+(?:start|stop|restart|enable|disable)[^\n]*baseball-pitcher-receipt",
        installer,
    )
    assert 'verifier_user="shadow-verifier"' in installer
    assert 'chgrp -R "$service_group" "$evidence_root"' in installer
    assert 'find "$evidence_root" -type d -exec chmod 0750 {} +' in installer
    assert 'find "$evidence_root" -type f -exec chmod 0640 {} +' in installer
    assert 'sudo -u "$verifier_user" test ! -w "$evidence_root"' in installer


def test_secondary_workflow_is_read_only_and_never_fetches_replacement_data() -> None:
    workflow = (ROOT / ".github/workflows/shared-pa-forward-verifier.yml").read_text(encoding="utf-8")
    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "curl " not in workflow and "wget " not in workflow
    assert "No replacement input was fetched" in workflow


def test_release_hash_manifest_reproduces_every_bound_file() -> None:
    report = verify_manifest()
    assert report["state"] == "verified"
    assert report["checked_file_bindings"] >= 29
    assert report["betting_authorized"] is False
