from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from scripts import capture_shared_pa_statcast_source_v1 as capture
from src.evaluation.shared_pa_statcast_historical_source_access_v1 import (
    VerifiedStatcastHistoricalSourceAccess,
)


ROOT = Path(__file__).resolve().parents[1]
INCIDENT = ROOT / "config/shared_pa_statcast_sample_attempt_history_20260731_v1.json"


def _access() -> VerifiedStatcastHistoricalSourceAccess:
    return VerifiedStatcastHistoricalSourceAccess(
        authorization_id="synthetic",
        authorization_file_sha256="a" * 64,
        carrier_commit="b" * 40,
        runtime_policy_sha256="c" * 64,
        source_bundle_sha256="d" * 64,
        source_contract_sha256="e" * 64,
        request_plan_sha256="f" * 64,
        output_path="data/source/sample",
        attempt_history_sha256=capture.sha256_file(INCIDENT),
        prior_attempts_consumed=1,
        remaining_lifetime_attempts=3,
    )


def test_capture_requires_explicit_transport() -> None:
    with pytest.raises(TypeError):
        capture.capture(  # type: ignore[call-arg]
            Path("plan"), Path("contract"), Path("output"),
            runtime_authorization=object(), source_access=object(),
            carrier_commit="b" * 40, attempt_history_path=INCIDENT,
        )


def test_real_transport_requires_explicit_hash_bound_gate() -> None:
    with pytest.raises(TypeError):
        capture._transport("https://example.invalid", 1.0, 1024)  # type: ignore[call-arg]
    gate = capture.TransportExecutionAuthorization("", "", "", "", "", "", "not-a-commit")
    with pytest.raises(capture.CaptureError, match="hash-bound authorization"):
        gate.validate()


def test_lower_layer_network_guard_blocks_socket_escape() -> None:
    with capture.network_denial_guard():
        with pytest.raises(capture.CaptureError, match="lower-layer network guard"):
            socket.create_connection(("example.invalid", 443), timeout=0.01)
        with pytest.raises(capture.CaptureError, match="lower-layer network guard"):
            socket.socket().connect(("example.invalid", 443))


def test_attempt_one_cannot_be_erased_or_reset(tmp_path: Path) -> None:
    value = json.loads(INCIDENT.read_text(encoding="utf-8"))
    value["attempt_number"] = 0
    mutated = tmp_path / "incident.json"
    mutated.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(capture.CaptureError, match="attempt history differs from authorization"):
        capture._validate_attempt_history(
            mutated, request_id="statcast-2023-07-25", source_access=_access(), maximum_attempts=4
        )


def test_attempt_history_preserves_quarantine_state() -> None:
    assert capture._validate_attempt_history(
        INCIDENT, request_id="statcast-2023-07-25", source_access=_access(), maximum_attempts=4
    ) == 1
