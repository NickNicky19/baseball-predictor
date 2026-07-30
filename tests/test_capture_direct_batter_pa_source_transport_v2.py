from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl
import urllib.error

import pytest

from scripts import capture_direct_batter_pa_source_transport_v2 as capture


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config/direct_batter_pa_source_release_v1.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def plan() -> dict:
    return {
        "schema_version": "direct-batter-pa-source-request-plan-v1",
        "season": 2023,
        "evidence_class": capture.offline.EVIDENCE_CLASS,
        "research_only": True,
        "betting_authorized": False,
        "prospective_evidence_claimed": False,
        "model_fitting_permitted": False,
        "protected_data": dict(capture.PROTECTED_STATE),
        "requests": [
            {
                "request_id": "mlb-feed-718780-2023-04-01",
                "source_kind": "mlb_statsapi_feed_live",
                "method": "GET",
                "url": "https://statsapi.mlb.com/api/v1.1/game/718780/feed/live",
                "query": {},
                "expected": {
                    "game_pk": 718780,
                    "official_date": "2023-04-01",
                    "away_team_id": 10,
                    "home_team_id": 20,
                    "zero_pa_player_ids": [],
                },
            },
            {
                "request_id": "statcast-2023-04-01",
                "source_kind": "baseball_savant_statcast_csv",
                "method": "GET",
                "url": "https://baseballsavant.mlb.com/statcast_search/csv",
                "query": {
                    "all": "true",
                    "game_date_gt": "2023-04-01",
                    "game_date_lt": "2023-04-01",
                    "hfGT": "R|",
                    "player_type": "pitcher",
                    "type": "details",
                },
                "expected": {"date_start": "2023-04-01", "date_end": "2023-04-01"},
            },
        ],
    }


def write_plan(tmp_path: Path, value: dict | None = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "request_plan_2023.json"
    path.write_text(json.dumps(value or plan(), sort_keys=True), encoding="utf-8")
    return path


def attestation() -> dict:
    return {
        "schema_version": "direct-batter-pa-source-linux-runtime-attestation-v1",
        "status": "EXACT_LOCAL_RUNTIME_ATTESTED_NETWORK_STILL_BLOCKED",
        "research_only": True,
        "betting_authorized": False,
        "model_fitting_permitted": False,
        "network_fetch_authorized": False,
        "external_expected_attestation_digest_supplied": False,
        "target": dict(capture.runtime_authority.TARGET),
        "site_user_enabled": False,
        "forbidden_environment_present": [],
    }


def runtime() -> capture.RuntimeAuthorization:
    value = attestation()
    return capture.RuntimeAuthorization(
        attestation_sha256=capture.sha256_bytes(capture.canonical_json_bytes(value)),
        attestation=value,
        source_sha256=capture.sha256_file(Path(capture.__file__)),
        policy_sha256="c" * 64,
    )


class FakeTransport:
    def __init__(self, *, redirect: bool = False, encoding: str = "identity", status: int = 200) -> None:
        self.calls = 0
        self.redirect = redirect
        self.encoding = encoding
        self.status = status

    def fetch(self, request, *, timeout_seconds: float, max_bytes: int):
        del timeout_seconds, max_bytes
        self.calls += 1
        body = b"game_date,game_type,batter,pitcher,game_pk,at_bat_number,pitch_number\n" if request["source_kind"].startswith("baseball") else b"{}"
        content_type = "text/csv" if request["source_kind"].startswith("baseball") else "application/json"
        return capture.CapturedResponse(
            status=self.status,
            body=body,
            headers={"content-type": content_type, "content-length": str(len(body)), "content-encoding": self.encoding},
            final_url=request["full_url"] + ("&redirected=1" if self.redirect else ""),
            requested_at_utc=now(),
            observed_at_utc=now(),
        )


def build(tmp_path: Path, transport: FakeTransport | None = None) -> tuple[Path, dict]:
    output = tmp_path / "capture-2023"
    result = capture.capture_plan(
        request_plan_path=write_plan(tmp_path),
        source_contract_path=CONTRACT,
        runtime=runtime(),
        output_dir=output,
        transport=transport or FakeTransport(),
        timeout_seconds=10,
    )
    return output, result


def reanchor(root: Path) -> None:
    manifest_path = root / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["files"] = capture._enumerate_files(root)
    value["observed_capture_digest"] = capture._manifest_digest(value)
    manifest_path.write_bytes(capture.canonical_json_bytes(value))


def test_capture_is_immutable_receipt_complete_and_revalidates(tmp_path: Path) -> None:
    output, manifest = build(tmp_path)
    assert manifest["status"] == "IMMUTABLE_RAW_CAPTURE_AWAITING_OFFLINE_SEMANTIC_RELEASE"
    assert manifest["source_counts"] == {
        "baseball_savant_statcast_csv": 1,
        "mlb_statsapi_feed_live": 1,
    }
    verified = capture.verify_capture_bundle(output, manifest["observed_capture_digest"])
    assert verified == manifest
    assert len(list(output.glob("transport/*/*/receipt.json"))) == 2
    assert len(list(output.glob("transport/*/*/response.*"))) == 2


def test_existing_valid_capture_is_safe_idempotent_resume(tmp_path: Path) -> None:
    output, first = build(tmp_path)

    class MustNotFetch:
        def fetch(self, *args, **kwargs):
            raise AssertionError("completed capture must not perform another request")

    second = capture.capture_plan(
        request_plan_path=write_plan(tmp_path), source_contract_path=CONTRACT,
        runtime=runtime(), output_dir=output, transport=MustNotFetch(), timeout_seconds=10,
    )
    assert second == first


def test_existing_capture_rejects_runtime_or_source_drift(tmp_path: Path) -> None:
    output, _ = build(tmp_path)
    changed = capture.RuntimeAuthorization(
        "0" * 64,
        attestation(),
        runtime().source_sha256,
        runtime().policy_sha256,
    )
    with pytest.raises(capture.CaptureError, match="different runtime attestation"):
        capture.capture_plan(
            request_plan_path=write_plan(tmp_path), source_contract_path=CONTRACT,
            runtime=changed, output_dir=output, transport=FakeTransport(),
        )


def test_raw_byte_and_reanchored_semantic_mutations_fail(tmp_path: Path) -> None:
    output, manifest = build(tmp_path)
    raw = next(output.glob("transport/*/*/response.*"))
    raw.write_bytes(raw.read_bytes() + b"mutation")
    with pytest.raises(capture.CaptureError, match="exact file set"):
        capture.verify_capture_bundle(output, manifest["observed_capture_digest"])

    output2, _ = build(tmp_path / "second")
    receipt = next(output2.glob("transport/*/*/receipt.json"))
    value = json.loads(receipt.read_text(encoding="utf-8"))
    value["unknown"] = True
    receipt.write_bytes(capture.canonical_json_bytes(value))
    reanchor(output2)
    with pytest.raises(capture.CaptureError, match="missing or unexpected"):
        capture.verify_capture_bundle(output2, None)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda value: value.update({"season": 2024}), "non-2023|exactly 2023"),
        (lambda value: value["protected_data"].update({"may_2026_opened": True}), "May 2026"),
        (lambda value: value["requests"][0].update({"request_id": "may-2026"}), "May 2026"),
        (lambda value: value["requests"][1].update({"url": "http://baseballsavant.mlb.com/statcast_search/csv"}), "source identity"),
        (lambda value: value["requests"][0].update({"url": "https://example.com/api/v1.1/game/718780/feed/live"}), "source identity"),
    ],
)
def test_scope_may_and_endpoint_mutations_fail_before_transport(tmp_path: Path, mutate, match: str) -> None:
    value = plan()
    mutate(value)
    request_path = write_plan(tmp_path, value)
    transport = FakeTransport()
    with pytest.raises((capture.CaptureError, capture.offline.SourceReleaseError), match=match):
        capture.capture_plan(
            request_plan_path=request_path, source_contract_path=CONTRACT,
            runtime=runtime(), output_dir=tmp_path / "out", transport=transport,
        )
    assert transport.calls == 0


@pytest.mark.parametrize(
    "transport,match",
    [
        (FakeTransport(redirect=True), "redirected"),
        (FakeTransport(encoding="gzip"), "compressed"),
        (FakeTransport(status=503), "status"),
    ],
)
def test_transport_failures_are_terminal_and_leave_no_authoritative_output(
    tmp_path: Path, transport: FakeTransport, match: str,
) -> None:
    output = tmp_path / "capture-2023"
    with pytest.raises(capture.CaptureError, match=match):
        capture.capture_plan(
            request_plan_path=write_plan(tmp_path), source_contract_path=CONTRACT,
            runtime=runtime(), output_dir=output, transport=transport,
        )
    assert not output.exists()


def test_limited_reader_rejects_oversized_body() -> None:
    class Reader:
        def __init__(self) -> None:
            self.done = False

        def read(self, size: int) -> bytes:
            if self.done:
                return b""
            self.done = True
            return b"x" * size

    with pytest.raises(capture.CaptureError, match="byte limit"):
        capture._read_limited(Reader(), max_bytes=10)


def test_runtime_authority_requires_external_digest_and_exact_current_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = attestation()
    payload = capture.canonical_json_bytes(value)
    path = tmp_path / "runtime-attestation.json"
    path.write_bytes(payload)
    monkeypatch.setattr(capture.runtime_authority, "load_policy", lambda path: {"target": capture.runtime_authority.TARGET})
    monkeypatch.setattr(capture.runtime_authority, "linux_runtime_attestation", lambda policy: value)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    digest = hashlib.sha256(payload).hexdigest()
    with pytest.raises(capture.CaptureError, match="externally supplied"):
        capture.authorize_runtime(attestation_path=path, expected_attestation_sha256="")
    with pytest.raises(capture.CaptureError, match="external expected"):
        capture.authorize_runtime(attestation_path=path, expected_attestation_sha256="0" * 64)
    authority = capture.authorize_runtime(attestation_path=path, expected_attestation_sha256=digest)
    assert authority.attestation_sha256 == digest
    monkeypatch.setattr(capture.runtime_authority, "linux_runtime_attestation", lambda policy: {**value, "site_user_enabled": True})
    with pytest.raises(capture.CaptureError, match="active runtime differs"):
        capture.authorize_runtime(attestation_path=path, expected_attestation_sha256=digest)


def test_proxy_environment_blocks_runtime_authorization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = attestation()
    payload = capture.canonical_json_bytes(value)
    path = tmp_path / "runtime-attestation.json"
    path.write_bytes(payload)
    monkeypatch.setattr(capture.runtime_authority, "load_policy", lambda path: {"target": capture.runtime_authority.TARGET})
    monkeypatch.setattr(capture.runtime_authority, "linux_runtime_attestation", lambda policy: value)
    for name in capture.FORBIDDEN_PROXY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    with pytest.raises(capture.CaptureError, match="proxy environment"):
        capture.authorize_runtime(
            attestation_path=path,
            expected_attestation_sha256=hashlib.sha256(payload).hexdigest(),
        )


def test_invalid_timeout_fails_before_reading_inputs(tmp_path: Path) -> None:
    with pytest.raises(capture.CaptureError, match="timeout"):
        capture.capture_plan(
            request_plan_path=tmp_path / "missing.json", source_contract_path=CONTRACT,
            runtime=runtime(), output_dir=tmp_path / "out", transport=FakeTransport(), timeout_seconds=0,
        )


def test_https_transport_exposes_only_sanitized_retry_metadata() -> None:
    class HTTP429Opener:
        def open(self, request, timeout):
            del timeout
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "secret upstream text",
                {"Retry-After": "7", "Set-Cookie": "secret"},
                None,
            )

    transport = capture.HTTPSHistoricalTransport.__new__(
        capture.HTTPSHistoricalTransport
    )
    transport._opener = HTTP429Opener()
    request = {
        "full_url": "https://statsapi.mlb.com/example",
    }
    with pytest.raises(capture.TransportFailure) as raised:
        transport.fetch(request, timeout_seconds=10, max_bytes=100)
    failure = raised.value
    assert failure.retryable is True
    assert failure.status == 429
    assert failure.retry_after == "7"
    assert failure.error_kind == "http_429"
    assert "secret" not in str(failure)
    assert not hasattr(failure, "headers")


def test_tls_failures_are_explicitly_nonretryable() -> None:
    class TLSFailureOpener:
        def open(self, request, timeout):
            del request, timeout
            raise urllib.error.URLError(
                ssl.SSLCertVerificationError("synthetic certificate failure")
            )

    transport = capture.HTTPSHistoricalTransport.__new__(
        capture.HTTPSHistoricalTransport
    )
    transport._opener = TLSFailureOpener()
    with pytest.raises(capture.TransportFailure) as raised:
        transport.fetch(
            {"full_url": "https://statsapi.mlb.com/example"},
            timeout_seconds=10,
            max_bytes=100,
        )
    assert raised.value.retryable is False
    assert raised.value.error_kind == "tls_failure"
