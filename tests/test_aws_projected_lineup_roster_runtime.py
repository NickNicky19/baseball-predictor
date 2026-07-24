from __future__ import annotations

import json
from pathlib import Path

import pytest

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
