#!/usr/bin/env python3
"""Capture immutable, receipt-complete 2023 historical source bytes.

This module is deliberately network-only.  It does not parse baseball outcomes,
build features, fit models, produce probabilities, or authorize downstream use.
The existing v1 offline builder remains the semantic-validation authority.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import stat
import tempfile
from typing import Any, Iterable, Mapping, Protocol
import urllib.error
import urllib.request

from scripts import build_direct_batter_pa_source_release as offline
from scripts import verify_direct_batter_pa_source_runtime_authority as runtime_authority


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "config/direct_batter_pa_source_release_v1.json"
DEFAULT_RUNTIME_POLICY = ROOT / "config/direct_batter_pa_source_runtime_authority_v1.json"
SCHEMA_MANIFEST = "direct-batter-pa-historical-transport-capture-v2"
SCHEMA_RECEIPT = "direct-batter-pa-historical-transport-receipt-v2"
AUTHORIZATION = "RESEARCH_ONLY_2023_HISTORICAL_SOURCE_CAPTURE"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
REQUEST_HEADERS = {
    "Accept": "application/json,text/csv,application/octet-stream;q=0.9",
    "Accept-Encoding": "identity",
    "User-Agent": "baseball-predictor-historical-source-capture/2.0",
}
SAFE_RESPONSE_HEADERS = {
    "cache-control", "content-encoding", "content-length", "content-type",
    "date", "etag", "last-modified",
}
FORBIDDEN_PROXY_ENV = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
)
MAX_BYTES = {
    "baseball_savant_statcast_csv": 64 * 1024 * 1024,
    "mlb_statsapi_feed_live": 32 * 1024 * 1024,
}
PROTECTED_STATE = dict(offline.PROTECTED_STATE)


class CaptureError(ValueError):
    """The historical source capture boundary was violated."""


class TransportFailure(CaptureError):
    """Sanitized transport failure suitable for bounded retry decisions."""

    def __init__(
        self,
        *,
        error_kind: str,
        retryable: bool,
        requested_at_utc: str,
        observed_at_utc: str,
        status: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(f"source request failed closed: {error_kind}")
        self.error_kind = error_kind
        self.retryable = retryable
        self.requested_at_utc = requested_at_utc
        self.observed_at_utc = observed_at_utc
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class RuntimeAuthorization:
    attestation_sha256: str
    attestation: Mapping[str, Any]
    source_sha256: str
    policy_sha256: str


@dataclass(frozen=True)
class CapturedResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    final_url: str
    requested_at_utc: str
    observed_at_utc: str


class Transport(Protocol):
    def fetch(self, request: Mapping[str, Any], *, timeout_seconds: float, max_bytes: int) -> CapturedResponse: ...


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    attrs = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _guard_existing_ancestors(path: Path, context: str) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path)))
    cursor = lexical
    while not cursor.exists() and cursor != cursor.parent:
        cursor = cursor.parent
    while True:
        if _is_link_or_reparse(cursor):
            raise CaptureError(f"{context} contains a symlink, junction, or reparse ancestor")
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    return lexical


def _input_file(path: Path, context: str) -> Path:
    value = _guard_existing_ancestors(path, context)
    if not value.is_file() or _is_link_or_reparse(value):
        raise CaptureError(f"{context} must be an existing regular file")
    return value


def _output_path(path: Path, context: str) -> Path:
    value = _guard_existing_ancestors(path, context)
    if value.exists() and _is_link_or_reparse(value):
        raise CaptureError(f"{context} cannot be a symlink, junction, or reparse point")
    return value


def _child(root: Path, relative: str, context: str) -> Path:
    candidate = _guard_existing_ancestors(root / Path(relative), context)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CaptureError(f"{context} escapes the capture root") from exc
    return candidate


def _canonical_utc(value: Any, context: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CaptureError(f"{context} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CaptureError(f"{context} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CaptureError(f"{context} must be UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if value != canonical:
        raise CaptureError(f"{context} is not canonical")
    return parsed


def _strict_keys(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    if set(value) != expected:
        raise CaptureError(f"{context} has missing or unexpected fields")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _strict_attestation(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != "direct-batter-pa-source-linux-runtime-attestation-v1":
        raise CaptureError("runtime attestation schema changed")
    if value.get("status") != "EXACT_LOCAL_RUNTIME_ATTESTED_NETWORK_STILL_BLOCKED":
        raise CaptureError("runtime attestation status changed")
    if value.get("research_only") is not True or value.get("betting_authorized") is not False:
        raise CaptureError("runtime attestation safety state changed")
    if value.get("model_fitting_permitted") is not False or value.get("network_fetch_authorized") is not False:
        raise CaptureError("runtime attestation overstates authority")
    if value.get("external_expected_attestation_digest_supplied") is not False:
        raise CaptureError("runtime attestation is self-authorizing")
    if value.get("target") != runtime_authority.TARGET:
        raise CaptureError("runtime attestation target changed")
    if value.get("site_user_enabled") is not False or value.get("forbidden_environment_present") != []:
        raise CaptureError("runtime attestation contains path or user-site injection")


def authorize_runtime(
    *, attestation_path: Path, expected_attestation_sha256: str,
    policy_path: Path = DEFAULT_RUNTIME_POLICY,
) -> RuntimeAuthorization:
    if SHA256_RE.fullmatch(expected_attestation_sha256 or "") is None:
        raise CaptureError("an externally supplied runtime-attestation SHA-256 is required")
    path = _input_file(attestation_path, "runtime attestation")
    payload = path.read_bytes()
    if sha256_bytes(payload) != expected_attestation_sha256:
        raise CaptureError("runtime attestation differs from the external expected digest")
    try:
        supplied = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureError("runtime attestation is not valid JSON") from exc
    if not isinstance(supplied, Mapping):
        raise CaptureError("runtime attestation root is malformed")
    _strict_attestation(supplied)
    policy = runtime_authority.load_policy(_input_file(policy_path, "runtime policy"))
    observed = runtime_authority.linux_runtime_attestation(policy)
    if canonical_json_bytes(observed) != payload:
        raise CaptureError("active runtime differs from the externally approved attestation")
    injected = sorted(name for name in FORBIDDEN_PROXY_ENV if os.environ.get(name))
    if injected:
        raise CaptureError(f"proxy environment is forbidden for source capture: {injected}")
    return RuntimeAuthorization(
        attestation_sha256=expected_attestation_sha256,
        attestation=supplied,
        source_sha256=sha256_file(Path(__file__)),
        policy_sha256=sha256_file(Path(policy_path)),
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise CaptureError("source redirect is forbidden")


class HTTPSHistoricalTransport:
    def __init__(self) -> None:
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(), urllib.request.HTTPSHandler(context=context)
        )

    def fetch(self, request: Mapping[str, Any], *, timeout_seconds: float, max_bytes: int) -> CapturedResponse:
        requested = _now_utc()
        req = urllib.request.Request(
            str(request["full_url"]), method="GET", headers=REQUEST_HEADERS,
        )
        try:
            with self._opener.open(req, timeout=timeout_seconds) as response:
                status = int(response.status)
                final_url = str(response.geturl())
                headers = _safe_headers(dict(response.headers.items()))
                body = _read_limited(response, max_bytes=max_bytes)
        except CaptureError:
            raise
        except urllib.error.HTTPError as exc:
            observed = _now_utc()
            status = int(exc.code)
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            raise TransportFailure(
                error_kind=f"http_{status}",
                retryable=status in {408, 425, 429, 500, 502, 503, 504},
                requested_at_utc=requested,
                observed_at_utc=observed,
                status=status,
                retry_after=retry_after,
            ) from exc
        except urllib.error.URLError as exc:
            observed = _now_utc()
            reason = exc.reason
            tls_failure = isinstance(reason, ssl.SSLError)
            raise TransportFailure(
                error_kind="tls_failure" if tls_failure else "transport_io",
                retryable=not tls_failure,
                requested_at_utc=requested,
                observed_at_utc=observed,
            ) from exc
        except ssl.SSLError as exc:
            raise TransportFailure(
                error_kind="tls_failure",
                retryable=False,
                requested_at_utc=requested,
                observed_at_utc=_now_utc(),
            ) from exc
        except OSError as exc:
            raise TransportFailure(
                error_kind="transport_io",
                retryable=True,
                requested_at_utc=requested,
                observed_at_utc=_now_utc(),
            ) from exc
        observed = _now_utc()
        return CapturedResponse(status, body, headers, final_url, requested, observed)


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).strip().lower()
        if name not in SAFE_RESPONSE_HEADERS:
            continue
        if name in normalized:
            raise CaptureError("response contains a duplicate safety-relevant header")
        value = str(raw_value).strip()
        if "\r" in value or "\n" in value:
            raise CaptureError("response header contains a line break")
        normalized[name] = value
    return {name: normalized[name] for name in sorted(normalized)}


def _read_limited(handle, *, max_bytes: int) -> bytes:  # noqa: ANN001
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = handle.read(min(1 << 20, max_bytes + 1 - total))
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise CaptureError("source response did not return bytes")
        total += len(chunk)
        if total > max_bytes:
            raise CaptureError("source response exceeds the fixed byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _content_type(headers: Mapping[str, str]) -> str:
    value = headers.get("content-type")
    if not isinstance(value, str) or not value:
        raise CaptureError("source response lacks content type")
    return value.split(";", 1)[0].strip().lower()


def _validate_response(response: CapturedResponse, request: Mapping[str, Any], contract: Mapping[str, Any]) -> None:
    requested = _canonical_utc(response.requested_at_utc, "requested_at_utc")
    observed = _canonical_utc(response.observed_at_utc, "observed_at_utc")
    if observed < requested or observed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise CaptureError("source response timestamps are contradictory")
    if response.status != 200:
        raise CaptureError("source response status is not 200")
    if response.final_url != request["full_url"]:
        raise CaptureError("source response redirected or changed final URL")
    if not isinstance(response.body, bytes) or not response.body:
        raise CaptureError("source response body is empty")
    encoding = response.headers.get("content-encoding", "identity").lower()
    if encoding not in {"", "identity"}:
        raise CaptureError("compressed source responses are forbidden")
    declared_length = response.headers.get("content-length")
    if declared_length is not None:
        if not declared_length.isascii() or not declared_length.isdigit() or int(declared_length) != len(response.body):
            raise CaptureError("source response content length disagrees with raw bytes")
    allowed = contract["sources"][request["source_kind"]]["content_type_prefixes"]
    if _content_type(response.headers) not in allowed:
        raise CaptureError("source response content type is not allowlisted")


def _enumerate_files(root: Path, *, exclude_manifest: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if _is_link_or_reparse(path):
            raise CaptureError("capture contains a symlink, junction, or reparse point")
        relative = path.relative_to(root).as_posix()
        if exclude_manifest and relative == "manifest.json":
            continue
        rows.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return rows


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    value = dict(manifest)
    value["observed_capture_digest"] = None
    return sha256_bytes(canonical_json_bytes(value))


def capture_plan(
    *, request_plan_path: Path, source_contract_path: Path, runtime: RuntimeAuthorization,
    output_dir: Path, transport: Transport, timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 120:
        raise CaptureError("timeout must be numeric and in [1,120] seconds")
    contract_path = _input_file(source_contract_path, "source contract")
    plan_path = _input_file(request_plan_path, "request plan")
    contract = offline.load_contract(contract_path)
    plan = offline.load_plan(plan_path, contract)
    destination = _output_path(output_dir, "capture output")
    if destination.exists():
        existing = verify_capture_bundle(destination, expected_capture_digest=None)
        if existing.get("runtime_attestation_sha256") != runtime.attestation_sha256:
            raise CaptureError("existing capture used a different runtime attestation")
        if existing.get("capture_source_sha256") != runtime.source_sha256:
            raise CaptureError("existing capture used different capture source bytes")
        return existing
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        (staging / "transport").mkdir()
        (staging / "request_plan.json").write_bytes(canonical_json_bytes(plan))
        (staging / "source_contract.json").write_bytes(contract_path.read_bytes())
        (staging / "runtime_attestation.json").write_bytes(canonical_json_bytes(runtime.attestation))
        source_counts = {name: 0 for name in contract["sources"]}
        for request in plan["requests"]:
            response = transport.fetch(
                request, timeout_seconds=float(timeout_seconds), max_bytes=MAX_BYTES[request["source_kind"]],
            )
            _validate_response(response, request, contract)
            extension = "csv" if request["source_kind"] == "baseball_savant_statcast_csv" else "json"
            base = f"transport/{request['source_kind']}/{request['request_id']}"
            raw_relative = f"{base}/response.{extension}"
            receipt_relative = f"{base}/receipt.json"
            raw_path = _child(staging, raw_relative, "raw response")
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(response.body)
            receipt = {
                "schema_version": SCHEMA_RECEIPT,
                "authorization": AUTHORIZATION,
                "season": 2023,
                "research_only": True,
                "betting_authorized": False,
                "model_fitting_performed": False,
                "probabilities_generated": False,
                "protected_data": PROTECTED_STATE,
                "request": request,
                "request_headers": REQUEST_HEADERS,
                "response": {
                    "status": response.status,
                    "final_url": response.final_url,
                    "requested_at_utc": response.requested_at_utc,
                    "observed_at_utc": response.observed_at_utc,
                    "headers": dict(response.headers),
                    "body_path": raw_relative,
                    "body_bytes": len(response.body),
                    "body_sha256": sha256_bytes(response.body),
                },
                "runtime_attestation_sha256": runtime.attestation_sha256,
                "capture_source_sha256": runtime.source_sha256,
            }
            receipt_path = _child(staging, receipt_relative, "transport receipt")
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_bytes(canonical_json_bytes(receipt))
            source_counts[request["source_kind"]] += 1
        files = _enumerate_files(staging)
        manifest = {
            "schema_version": SCHEMA_MANIFEST,
            "status": "IMMUTABLE_RAW_CAPTURE_AWAITING_OFFLINE_SEMANTIC_RELEASE",
            "authorization": AUTHORIZATION,
            "season": 2023,
            "research_only": True,
            "betting_authorized": False,
            "model_fitting_performed": False,
            "probabilities_generated": False,
            "protected_data": PROTECTED_STATE,
            "runtime_attestation_sha256": runtime.attestation_sha256,
            "capture_source_sha256": runtime.source_sha256,
            "source_contract_sha256": sha256_file(staging / "source_contract.json"),
            "request_plan_sha256": sha256_file(staging / "request_plan.json"),
            "source_counts": source_counts,
            "files": files,
            "observed_capture_digest": None,
        }
        manifest["observed_capture_digest"] = _manifest_digest(manifest)
        (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        os.replace(staging, destination)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_capture_bundle(capture_dir: Path, expected_capture_digest: str | None) -> dict[str, Any]:
    root = _output_path(capture_dir, "capture bundle")
    if not root.is_dir() or _is_link_or_reparse(root):
        raise CaptureError("capture bundle must be an existing regular directory")
    manifest_path = _child(root, "manifest.json", "capture manifest")
    if not manifest_path.is_file():
        raise CaptureError("capture manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != SCHEMA_MANIFEST:
        raise CaptureError("capture manifest is malformed")
    _strict_keys(
        manifest,
        {"schema_version", "status", "authorization", "season", "research_only",
         "betting_authorized", "model_fitting_performed", "probabilities_generated",
         "protected_data", "runtime_attestation_sha256", "capture_source_sha256",
         "source_contract_sha256", "request_plan_sha256", "source_counts", "files",
         "observed_capture_digest"},
        "capture manifest",
    )
    if manifest.get("status") != "IMMUTABLE_RAW_CAPTURE_AWAITING_OFFLINE_SEMANTIC_RELEASE":
        raise CaptureError("capture manifest status changed")
    if manifest.get("season") != 2023 or manifest.get("protected_data") != PROTECTED_STATE:
        raise CaptureError("capture manifest scope changed")
    if manifest.get("research_only") is not True or manifest.get("betting_authorized") is not False:
        raise CaptureError("capture manifest safety state changed")
    if manifest.get("model_fitting_performed") is not False or manifest.get("probabilities_generated") is not False:
        raise CaptureError("capture manifest makes a prohibited modeling claim")
    observed = _manifest_digest(manifest)
    if observed != manifest.get("observed_capture_digest"):
        raise CaptureError("capture manifest digest is internally inconsistent")
    if expected_capture_digest is not None:
        if SHA256_RE.fullmatch(expected_capture_digest or "") is None or observed != expected_capture_digest:
            raise CaptureError("capture bundle differs from the external expected digest")
    if _enumerate_files(root) != manifest.get("files"):
        raise CaptureError("capture exact file set, bytes, or hashes changed")
    contract_path = _child(root, "source_contract.json", "retained source contract")
    plan_path = _child(root, "request_plan.json", "retained request plan")
    attestation_path = _child(root, "runtime_attestation.json", "retained runtime attestation")
    if sha256_file(contract_path) != manifest.get("source_contract_sha256"):
        raise CaptureError("retained source contract binding changed")
    if sha256_file(plan_path) != manifest.get("request_plan_sha256"):
        raise CaptureError("retained request plan binding changed")
    if sha256_file(attestation_path) != manifest.get("runtime_attestation_sha256"):
        raise CaptureError("retained runtime attestation binding changed")
    contract = offline.load_contract(contract_path)
    plan = offline.load_plan(plan_path, contract, allow_canonical_requests=True)
    expected_counts = {name: 0 for name in contract["sources"]}
    for request in plan["requests"]:
        extension = "csv" if request["source_kind"] == "baseball_savant_statcast_csv" else "json"
        base = f"transport/{request['source_kind']}/{request['request_id']}"
        raw_relative = f"{base}/response.{extension}"
        receipt = json.loads(_child(root, f"{base}/receipt.json", "retained receipt").read_text(encoding="utf-8"))
        raw = _child(root, raw_relative, "retained raw response").read_bytes()
        if not isinstance(receipt, Mapping):
            raise CaptureError("retained receipt is malformed")
        _strict_keys(
            receipt,
            {"schema_version", "authorization", "season", "research_only", "betting_authorized",
             "model_fitting_performed", "probabilities_generated", "protected_data", "request",
             "request_headers", "response", "runtime_attestation_sha256", "capture_source_sha256"},
            "retained receipt",
        )
        if receipt.get("schema_version") != SCHEMA_RECEIPT or receipt.get("request") != request:
            raise CaptureError("retained receipt identity changed")
        if receipt.get("protected_data") != PROTECTED_STATE or receipt.get("season") != 2023:
            raise CaptureError("retained receipt scope changed")
        if receipt.get("research_only") is not True or receipt.get("betting_authorized") is not False:
            raise CaptureError("retained receipt safety state changed")
        if receipt.get("model_fitting_performed") is not False or receipt.get("probabilities_generated") is not False:
            raise CaptureError("retained receipt makes a prohibited modeling claim")
        if receipt.get("request_headers") != REQUEST_HEADERS:
            raise CaptureError("retained request headers changed")
        if receipt.get("runtime_attestation_sha256") != manifest.get("runtime_attestation_sha256"):
            raise CaptureError("retained receipt runtime binding changed")
        if receipt.get("capture_source_sha256") != manifest.get("capture_source_sha256"):
            raise CaptureError("retained receipt source binding changed")
        response = receipt.get("response")
        if not isinstance(response, Mapping):
            raise CaptureError("retained response receipt is malformed")
        _strict_keys(
            response,
            {"status", "final_url", "requested_at_utc", "observed_at_utc", "headers",
             "body_path", "body_bytes", "body_sha256"},
            "retained response receipt",
        )
        if response.get("body_path") != raw_relative or response.get("body_bytes") != len(raw):
            raise CaptureError("retained response path or size changed")
        if response.get("body_sha256") != sha256_bytes(raw):
            raise CaptureError("retained raw response hash changed")
        captured = CapturedResponse(
            int(response.get("status")), raw, response.get("headers") or {}, str(response.get("final_url")),
            str(response.get("requested_at_utc")), str(response.get("observed_at_utc")),
        )
        _validate_response(captured, request, contract)
        expected_counts[request["source_kind"]] += 1
    if manifest.get("source_counts") != expected_counts:
        raise CaptureError("capture source counts changed")
    return dict(manifest)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--request-plan", required=True, type=Path)
    capture.add_argument("--source-contract", default=DEFAULT_CONTRACT, type=Path)
    capture.add_argument("--runtime-policy", default=DEFAULT_RUNTIME_POLICY, type=Path)
    capture.add_argument("--runtime-attestation", required=True, type=Path)
    capture.add_argument("--expected-runtime-attestation-sha256", required=True)
    capture.add_argument("--output-dir", required=True, type=Path)
    capture.add_argument("--timeout-seconds", type=float, default=30.0)
    verify = sub.add_parser("verify")
    verify.add_argument("--capture-dir", required=True, type=Path)
    verify.add_argument("--expected-capture-digest", required=True)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "verify":
        manifest = verify_capture_bundle(args.capture_dir, args.expected_capture_digest)
        print(json.dumps({"status": manifest["status"]}, sort_keys=True))
        return 0
    authority = authorize_runtime(
        attestation_path=args.runtime_attestation,
        expected_attestation_sha256=args.expected_runtime_attestation_sha256,
        policy_path=args.runtime_policy,
    )
    manifest = capture_plan(
        request_plan_path=args.request_plan,
        source_contract_path=args.source_contract,
        runtime=authority,
        output_dir=args.output_dir,
        transport=HTTPSHistoricalTransport(),
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps({"status": manifest["status"], "observed_capture_digest": manifest["observed_capture_digest"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
