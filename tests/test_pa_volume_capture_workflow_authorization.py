from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.capture_pa_volume_official_source_v1 import (
    capture_source_bundle_sha256,
)
from src.evaluation.pa_volume_historical_source_access_v1 import (
    HistoricalSourceAccessError,
    verify_historical_source_access_authorization,
)


ROOT = Path(__file__).resolve().parents[1]
AUTHORIZATION = (
    ROOT
    / "config"
    / "pa_volume_historical_source_access_authorization_20260730_v1.json"
)
V2_AUTHORIZATION = (
    ROOT
    / "config"
    / "pa_volume_historical_source_access_authorization_20260730_v2.json"
)
V3_AUTHORIZATION = (
    ROOT
    / "config"
    / "pa_volume_historical_source_access_authorization_20260730_v3.json"
)
V4_AUTHORIZATION = (
    ROOT
    / "config"
    / "pa_volume_historical_source_access_authorization_20260730_v4.json"
)
CURRENT_AUTHORIZATION = (
    ROOT
    / "config"
    / "pa_volume_historical_source_access_authorization_20260731_v5.json"
)
POLICY = ROOT / "config" / "pa_volume_source_runtime_authority_v1.json"
EXPECTED_AUTHORIZATION_SHA256 = (
    "6f07bf2f50444c7059295dda146b07ffb0648834917b8f1220168fba7bd5b5ab"
)
EXPECTED_V2_AUTHORIZATION_SHA256 = (
    "1f0e787743a427f36a17fd36703da8b607b49b33b425c3aa44cf9ccadd3355e7"
)
EXPECTED_V3_AUTHORIZATION_SHA256 = (
    "fc0f0bfb233ccec1cd27c4708ccfc77b9fb2202a28fd0152402e48d3794bb300"
)
EXPECTED_V4_AUTHORIZATION_SHA256 = (
    "94c980b1ff8db84312eb325bae6ebc56ab44e1dfdb33fe86973308f469fca949"
)
EXPECTED_CURRENT_AUTHORIZATION_SHA256 = (
    "d30da13a24d8ee01b1840603b907d8b088f3b2402223b061c2e737b486ad2d27"
)


def test_prior_capture_authorization_cannot_authorize_repaired_source_bytes() -> None:
    assert hashlib.sha256(AUTHORIZATION.read_bytes()).hexdigest() == (
        EXPECTED_AUTHORIZATION_SHA256
    )
    with pytest.raises(
        HistoricalSourceAccessError,
        match="different runtime or source bytes",
    ):
        verify_historical_source_access_authorization(
            authorization_path=AUTHORIZATION,
            expected_authorization_sha256=EXPECTED_AUTHORIZATION_SHA256,
            expected_runtime_policy_sha256=hashlib.sha256(POLICY.read_bytes()).hexdigest(),
            expected_source_bundle_sha256=capture_source_bundle_sha256(),
            access_time_utc="2026-07-30T17:00:00.000000Z",
        )


def test_v2_capture_authorization_cannot_authorize_date_semantics_repair() -> None:
    assert hashlib.sha256(V2_AUTHORIZATION.read_bytes()).hexdigest() == (
        EXPECTED_V2_AUTHORIZATION_SHA256
    )
    with pytest.raises(
        HistoricalSourceAccessError,
        match="different runtime or source bytes",
    ):
        verify_historical_source_access_authorization(
            authorization_path=V2_AUTHORIZATION,
            expected_authorization_sha256=EXPECTED_V2_AUTHORIZATION_SHA256,
            expected_runtime_policy_sha256=hashlib.sha256(POLICY.read_bytes()).hexdigest(),
            expected_source_bundle_sha256=capture_source_bundle_sha256(),
            access_time_utc="2026-07-30T20:00:00.000000Z",
        )


def test_v3_capture_authorization_cannot_authorize_duplicate_listing_repair() -> None:
    assert hashlib.sha256(V3_AUTHORIZATION.read_bytes()).hexdigest() == (
        EXPECTED_V3_AUTHORIZATION_SHA256
    )
    with pytest.raises(
        HistoricalSourceAccessError,
        match="different runtime or source bytes",
    ):
        verify_historical_source_access_authorization(
            authorization_path=V3_AUTHORIZATION,
            expected_authorization_sha256=EXPECTED_V3_AUTHORIZATION_SHA256,
            expected_runtime_policy_sha256=hashlib.sha256(POLICY.read_bytes()).hexdigest(),
            expected_source_bundle_sha256=capture_source_bundle_sha256(),
            access_time_utc="2026-07-30T20:45:00.000000Z",
        )


def test_v4_capture_authorization_binds_duplicate_listing_repair() -> None:
    assert hashlib.sha256(V4_AUTHORIZATION.read_bytes()).hexdigest() == (
        EXPECTED_V4_AUTHORIZATION_SHA256
    )
    verified = verify_historical_source_access_authorization(
        authorization_path=V4_AUTHORIZATION,
        expected_authorization_sha256=EXPECTED_V4_AUTHORIZATION_SHA256,
        expected_runtime_policy_sha256=hashlib.sha256(POLICY.read_bytes()).hexdigest(),
        expected_source_bundle_sha256=capture_source_bundle_sha256(),
        access_time_utc="2026-07-30T21:45:00.000000Z",
    )
    assert verified.authorization_id.endswith("-v4")


def test_v5_capture_authorization_renews_same_repaired_source_scope() -> None:
    assert hashlib.sha256(CURRENT_AUTHORIZATION.read_bytes()).hexdigest() == (
        EXPECTED_CURRENT_AUTHORIZATION_SHA256
    )
    verified = verify_historical_source_access_authorization(
        authorization_path=CURRENT_AUTHORIZATION,
        expected_authorization_sha256=EXPECTED_CURRENT_AUTHORIZATION_SHA256,
        expected_runtime_policy_sha256=hashlib.sha256(POLICY.read_bytes()).hexdigest(),
        expected_source_bundle_sha256=capture_source_bundle_sha256(),
        access_time_utc="2026-07-31T10:45:00.000000Z",
    )
    assert verified.authorization_id.endswith("-v5")


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
    assert "Retain rejected schedule bytes for identity diagnosis" in workflow
    assert "rejected-official-2023-schedule-${{ github.sha }}" in workflow
    assert (
        "config/pa_volume_historical_source_access_authorization_20260731_v5.json"
        in workflow
    )
    runtime_workflow = (
        ROOT / ".github" / "workflows" / "pa-volume-source-runtime-linux.yml"
    ).read_text(encoding="utf-8")
    assert "--no-compile" in runtime_workflow
