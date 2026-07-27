from __future__ import annotations

import ast
import json
from datetime import datetime
from pathlib import Path

import pytest

from src.omega_contracts.chronology import (
    assert_may_safe_path,
    parse_canonical_utc,
    require_event_eligibility,
    require_load_freshness,
)
from src.omega_contracts.errors import ContractError
from src.omega_contracts.production import (
    RETAINED_LIVE_PLAN_GIT_BLOB_OID,
    RETAINED_LIVE_PLAN_PATH,
    RETAINED_LIVE_PLAN_SHA256,
    verify_candidate_and_production_boundaries,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-27T12:00:00",
        "2026-07-27 12:00:00Z",
        "2026-07-27T07:00:00-05:00",
        "20260727T120000Z",
        datetime(2026, 7, 27, 12, 0),
    ],
)
def test_external_utc_contract_accepts_no_alternate_or_naive_form(value) -> None:
    with pytest.raises(ContractError):
        parse_canonical_utc(value)


def test_event_and_freshness_equality_policy_is_exact() -> None:
    observed, horizon, captured, event = require_event_eligibility(
        observed_at="2026-07-27T11:59:59Z",
        decision_horizon="2026-07-27T12:00:00Z",
        captured_at="2026-07-27T12:00:00Z",
        event_start="2026-07-27T12:00:01Z",
    )
    assert observed < horizon == captured < event
    assert (
        require_load_freshness(
            captured_at=captured,
            now_utc="2026-07-27T12:10:00Z",
            maximum_age_seconds=600,
        )
        == 600
    )
    with pytest.raises(ContractError, match="stale"):
        require_load_freshness(
            captured_at=captured,
            now_utc="2026-07-27T12:10:00.000001Z",
            maximum_age_seconds=600,
        )


@pytest.mark.parametrize(
    "name",
    [
        "05-10-2026.json",
        "05/10/2026.json",
        "May-10-2026.json",
        "10-May-2026.json",
        "May2026.json",
        "May%252D10%252D2026.json",
        "２０２６－０５－１０.json",
        "2026 5 10.json",
        "20260510.json",
    ],
)
def test_noncanonical_and_encoded_may_paths_cannot_bypass_authority(
    tmp_path: Path, name: str
) -> None:
    with pytest.raises(ContractError, match="sealed May"):
        assert_may_safe_path(tmp_path / name, allowed_root=tmp_path)


def test_dashboard_has_no_independent_chronology_parser_or_may_check() -> None:
    prohibited_calls = {"fromisoformat", "strptime"}
    for path in sorted((ROOT / "dashboard").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in prohibited_calls, (path, node.func.attr)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"date", "datetime"}, (path, node.func.id)
        assert (
            "2026" not in source or path.name == "app.py" and "source_commit" in source
        )
    contracts = (ROOT / "dashboard/contracts.py").read_text(encoding="utf-8")
    loader = (ROOT / "dashboard/snapshot_store.py").read_text(encoding="utf-8")
    assert "from src.omega_contracts.chronology import" in contracts
    assert "from src.omega_contracts.chronology import" in loader


def test_retained_live_plan_path_sha_and_git_blob_are_exact() -> None:
    assert RETAINED_LIVE_PLAN_PATH == "src/evaluation/shadow_capture_plan.py"
    assert (
        RETAINED_LIVE_PLAN_SHA256
        == "491a73556cab02b8295eee695f0474345defbc15f5d9bd01aae011cdff2b17e0"
    )
    assert RETAINED_LIVE_PLAN_GIT_BLOB_OID == "0ca57fbb06bf04edbc28ddd6a1e0512be0e68ee0"
    boundary = json.loads(
        (ROOT / "config/omega_production_boundary_v1.json").read_text(encoding="utf-8")
    )
    assert boundary["files"][RETAINED_LIVE_PLAN_PATH] == RETAINED_LIVE_PLAN_SHA256
    assert verify_candidate_and_production_boundaries(ROOT)["verified_files"] == 19
