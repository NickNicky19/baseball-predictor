from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_aws_projected_lineup_roster_offline import validate_service_isolation
from scripts.run_aws_projected_lineup_roster_tick import AWSProjectedLineupRosterError, load_runtime


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_binds_the_contract_and_future_only_invariants():
    runtime, digest = load_runtime(ROOT / "config/projected_lineup_roster_runtime_v1.json")
    assert runtime["scheduler"]["entry_hours"] == 4
    assert len(digest) == 64


def test_runtime_mutation_is_rejected(tmp_path: Path):
    raw = json.loads((ROOT / "config/projected_lineup_roster_runtime_v1.json").read_text(encoding="utf-8"))
    raw["invariants"]["late_backfill_forbidden"] = False
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(AWSProjectedLineupRosterError, match="safety invariants"):
        load_runtime(path)


def test_aws_service_and_installer_are_isolated_from_live_collectors():
    service = (ROOT / "deploy/projected_lineup_roster/baseball-projected-lineup-roster-tick.service").read_text(encoding="utf-8")
    installer = (ROOT / "deploy/projected_lineup_roster/install_exact_release.sh").read_text(encoding="utf-8")
    assert "WorkingDirectory=/opt/baseball-predictor-projected-lineup-roster/current" in service
    assert "/srv/baseball-shadow/current" not in service
    assert "ExecStart=/usr/bin/python3 /opt/baseball-predictor-projected-lineup-roster/current/scripts/run_aws_projected_lineup_roster_tick.py" in service
    assert "/srv/baseball-shadow/venv/bin/python" not in service
    assert 'release_root="/opt/baseball-predictor-projected-lineup-roster"' in installer
    assert 'evidence_root="/srv/baseball-shadow/projected-lineup-roster-receipts"' in installer
    assert "baseball-pitcher-receipt" not in installer


def test_offline_release_gate_accepts_isolated_current_path():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_aws_projected_lineup_roster_offline.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_offline_release_gate_mutation_rejects_shared_current_path():
    service = (ROOT / "deploy/projected_lineup_roster/baseball-projected-lineup-roster-tick.service").read_text(encoding="utf-8")
    mutated = service.replace(
        "WorkingDirectory=/opt/baseball-predictor-projected-lineup-roster/current",
        "WorkingDirectory=/srv/baseball-shadow/current",
    )
    with pytest.raises(ValueError, match="must not use a shared live release tree"):
        validate_service_isolation(mutated)


def test_offline_release_gate_mutation_rejects_missing_python_runtime():
    service = (ROOT / "deploy/projected_lineup_roster/baseball-projected-lineup-roster-tick.service").read_text(encoding="utf-8")
    mutated = service.replace(
        "/usr/bin/python3 /opt/baseball-predictor-projected-lineup-roster/current/",
        "/srv/baseball-shadow/venv/bin/python ",
    )
    with pytest.raises(ValueError, match="must not depend on an undeployed virtual environment"):
        validate_service_isolation(mutated)
