from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.capture_pa_volume_official_source_v1 import (
    capture_source_bundle_sha256,
)
from src.evaluation.pa_volume_historical_source_access_v1 import (
    verify_historical_source_access_authorization,
)


ROOT = Path(__file__).resolve().parents[1]
AUTHORIZATION = (
    ROOT
    / "config"
    / "pa_volume_historical_source_access_authorization_20260730_v1.json"
)
POLICY = ROOT / "config" / "pa_volume_source_runtime_authority_v1.json"
EXPECTED_AUTHORIZATION_SHA256 = (
    "6f07bf2f50444c7059295dda146b07ffb0648834917b8f1220168fba7bd5b5ab"
)


def test_capture_authorization_binds_only_exact_2023_source_bytes() -> None:
    assert hashlib.sha256(AUTHORIZATION.read_bytes()).hexdigest() == (
        EXPECTED_AUTHORIZATION_SHA256
    )
    verified = verify_historical_source_access_authorization(
        authorization_path=AUTHORIZATION,
        expected_authorization_sha256=EXPECTED_AUTHORIZATION_SHA256,
        expected_runtime_policy_sha256=hashlib.sha256(POLICY.read_bytes()).hexdigest(),
        expected_source_bundle_sha256=capture_source_bundle_sha256(),
        access_time_utc="2026-07-30T17:00:00.000000Z",
    )
    assert verified.authorization_id == (
        "user-authorized-2023-official-mlb-source-20260730-v1"
    )


def test_capture_workflow_is_manual_and_never_fits_or_predicts() -> None:
    workflow = (
        ROOT / ".github" / "workflows" / "pa-volume-official-2023-capture.yml"
    ).read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow
    assert "capture-schedule" in workflow
    assert "capture-feeds" in workflow
    assert "build-source-release" in workflow
    assert "build-pa-artifact" not in workflow
    assert "run_slate.py" not in workflow
    assert "--no-compile" in workflow
    assert "Retain rejected pre-request attestation for diagnosis" in workflow
    runtime_workflow = (
        ROOT / ".github" / "workflows" / "pa-volume-source-runtime-linux.yml"
    ).read_text(encoding="utf-8")
    assert "--no-compile" in runtime_workflow
