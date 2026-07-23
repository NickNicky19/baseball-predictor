from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("audit_shared_pa_core_attribution_2023", ROOT / "scripts" / "audit_shared_pa_core_attribution_2023.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PROTOCOL = ROOT / "config" / "shared_pa_core_attribution_2023_v1.json"


def test_locked_attribution_protocol_is_batter_only_and_sealed():
    contract = MODULE._load_protocol(PROTOCOL)
    assert contract["evidence_boundary"]["development_years"] == [2023]
    forbidden = " ".join(contract["evidence_boundary"]["forbidden"]).lower()
    for token in ("2024", "2025", "may 2026", "pitcher", "lineup", "market", "realized pa"):
        assert token in forbidden
    flattened = [value for group in contract["feature_groups"].values() for value in group]
    assert len(flattened) == len(set(flattened)) == 50


def test_feature_groups_exactly_partition_fixed_core():
    contract = MODULE._load_protocol(PROTOCOL)
    declared = [value for group in contract["feature_groups"].values() for value in group]
    frame = pd.DataFrame(columns=[*declared, "history_pa_age_days_mean", "history_pa_age_days_sd", "not_a_history_feature"])
    groups = MODULE._feature_sets(frame, contract)
    assert groups["full_core"] == declared
    for name, features in groups.items():
        if name != "full_core":
            assert len(features) < len(declared)


def test_duplicate_or_omitted_feature_contract_fails_closed(tmp_path: Path):
    payload = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    payload["feature_groups"]["pitch_shape"].append("history_pa")
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="overlap"):
        MODULE._load_protocol(path)


def test_existing_attribution_output_cannot_be_overwritten(tmp_path: Path):
    report = tmp_path / "report.json"
    report.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        MODULE.run(panel=tmp_path / "missing.csv.gz", manifest=tmp_path / "missing.json", protocol=PROTOCOL, report=report, predictions=tmp_path / "predictions.csv")
