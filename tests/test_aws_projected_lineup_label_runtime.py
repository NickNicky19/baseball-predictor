from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_aws_projected_lineup_label_offline import validate_service_isolation
from scripts.run_aws_projected_lineup_label_tick import AWSProjectedLineupLabelError, load_runtime


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_binds_contract_and_label_invariants():
    runtime, digest = load_runtime(ROOT / "config/projected_lineup_label_runtime_v1.json")
    assert runtime["scheduler"]["tick_minutes"] == 15
    assert len(digest) == 64


def test_runtime_mutation_is_rejected(tmp_path: Path):
    raw = json.loads((ROOT / "config/projected_lineup_label_runtime_v1.json").read_text(encoding="utf-8"))
    raw["invariants"]["research_only"] = False
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(AWSProjectedLineupLabelError, match="safety invariants"):
        load_runtime(path)


def test_aws_service_and_installer_are_isolated_from_live_collectors():
    service = (ROOT / "deploy/projected_lineup_label/baseball-projected-lineup-label-tick.service").read_text(encoding="utf-8")
    installer = (ROOT / "deploy/projected_lineup_label/install_exact_release.sh").read_text(encoding="utf-8")
    assert "WorkingDirectory=/opt/baseball-predictor-projected-lineup-label/current" in service
    assert "/srv/baseball-shadow/current" not in service
    assert "scripts/run_aws_projected_lineup_label_tick.py" in service
    assert "/srv/baseball-shadow/venv/bin/python" not in service
    assert 'release_root="/opt/baseball-predictor-projected-lineup-label"' in installer
    assert 'evidence_root="/srv/baseball-shadow/projected-lineup-label-receipts"' in installer
    assert 'roster_root="/srv/baseball-shadow/projected-lineup-roster-receipts"' in installer


def test_offline_release_gate_accepts_isolated_current_path():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_aws_projected_lineup_label_offline.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_offline_release_gate_rejects_shared_release_tree():
    service = (ROOT / "deploy/projected_lineup_label/baseball-projected-lineup-label-tick.service").read_text(encoding="utf-8")
    mutated = service.replace(
        "WorkingDirectory=/opt/baseball-predictor-projected-lineup-label/current",
        "WorkingDirectory=/srv/baseball-shadow/current",
    )
    with pytest.raises(ValueError, match="must not use a shared live release tree"):
        validate_service_isolation(mutated)
