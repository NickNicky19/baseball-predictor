"""Capture immutable official 2023 schedule/feed bytes for PA-volume repair.

The capture is historical, research-only, outcome-source reconstruction.  It
cannot generate probabilities or prospective evidence.  Partial feed work is
resumable but is never authoritative until exact coverage finalizes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Iterable, Mapping, Protocol
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.capture_direct_batter_pa_source_transport_v2 import (
    CapturedResponse,
    HTTPSHistoricalTransport,
    RuntimeAuthorization,
    authorize_runtime,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from src.evaluation.pa_volume_historical_source_access_v1 import (
    VerifiedHistoricalSourceAccess,
    verify_historical_source_access_authorization,
)


SCHEDULE_SCHEMA = "pa-volume-official-schedule-capture-v1"
PLAN_SCHEMA = "pa-volume-official-feed-capture-plan-v1"
FEED_SCHEMA = "pa-volume-official-feed-capture-v1"
RECEIPT_SCHEMA = "pa-volume-official-http-receipt-v1"
SCHEDULE_INDEX_SCHEMA = "pa-volume-2023-schedule-candidates-v1"
AUTHORIZATION = "RESEARCH_ONLY_NO_BETTING"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
EXPECTED_GAMES = 2430
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
SCHEDULE_QUERY = {
    "gameType": "R",
    "hydrate": "team",
    "season": "2023",
    "sportId": "1",
}
SCHEDULE_FULL_URL = f"{SCHEDULE_URL}?{urlencode(SCHEDULE_QUERY)}"
FEED_FIELDS = (
    "gamePk,gameData,datetime,officialDate,game,type,status,abstractGameState,"
    "codedGameState,teams,away,home,id,liveData,boxscore,team,players,person,"
    "battingOrder,stats,batting,plateAppearances"
)
PROTECTED = {
    "may_2026_accessed": False,
    "selection_2024_accessed": False,
    "spent_hr_confirmation_2025_accessed": False,
    "prices_accessed": False,
    "prospective_evidence_accessed": False,
    "prospective_backfill_performed": False,
}


def capture_source_files() -> list[dict[str, str]]:
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "scripts/capture_direct_batter_pa_source_transport_v2.py",
        Path(__file__).resolve(),
    ]
    return [
        {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
        for path in paths
    ]


def capture_source_bundle_sha256() -> str:
    return sha256_bytes(canonical_json_bytes(capture_source_files()))


class OfficialSourceCaptureError(ValueError):
    """A transport, scope, identity, or immutable-byte gate failed."""


class Transport(Protocol):
    def fetch(
        self, request: Mapping[str, Any], *, timeout_seconds: float, max_bytes: int
    ) -> CapturedResponse: ...


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise OfficialSourceCaptureError(f"{label} must be a lowercase SHA-256")
    return value


def _require_source_access(
    runtime: RuntimeAuthorization,
    source_access: VerifiedHistoricalSourceAccess,
    source_bundle_sha256: str,
) -> None:
    if not isinstance(source_access, VerifiedHistoricalSourceAccess):
        raise OfficialSourceCaptureError(
            "externally anchored historical source access is required"
        )
    if (
        source_access.runtime_policy_sha256 != runtime.policy_sha256
        or source_access.source_bundle_sha256
        != _sha(source_bundle_sha256, "source bundle")
    ):
        raise OfficialSourceCaptureError(
            "historical source access binds different runtime or source bytes"
        )


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise OfficialSourceCaptureError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise OfficialSourceCaptureError(f"{label} must be canonical UTC") from exc
    if parsed.tzinfo != timezone.utc or parsed.isoformat().replace("+00:00", "Z") != value:
        raise OfficialSourceCaptureError(f"{label} must be canonical UTC")
    return parsed


def _safe_root(path: Path, *, must_exist: bool) -> Path:
    value = Path(os.path.abspath(os.fspath(path)))
    if ".." in path.parts or value.parent == value:
        raise OfficialSourceCaptureError("capture path is unsafe")
    if must_exist and (not value.is_dir() or value.is_symlink()):
        raise OfficialSourceCaptureError("capture directory is missing or unsafe")
    cursor = value if value.exists() else value.parent
    while not cursor.exists():
        cursor = cursor.parent
    while True:
        if cursor.is_symlink():
            raise OfficialSourceCaptureError("capture path traverses a symlink")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return value


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise OfficialSourceCaptureError(f"refusing to overwrite {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise OfficialSourceCaptureError("stale temporary capture file exists")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _safe_new_file(path: Path, label: str) -> Path:
    value = Path(os.path.abspath(os.fspath(path)))
    if ".." in path.parts or value.parent == value or value.exists() or value.is_symlink():
        raise OfficialSourceCaptureError(f"{label} output is unsafe or exists")
    _safe_root(value.parent, must_exist=value.parent.exists())
    return value


def _request(request_id: str, full_url: str, expected: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "method": "GET",
        "full_url": full_url,
        "expected": dict(expected),
    }


def _validate_response(
    response: CapturedResponse, request: Mapping[str, Any], *, max_bytes: int
) -> None:
    requested = _utc(response.requested_at_utc, "requested_at_utc")
    observed = _utc(response.observed_at_utc, "observed_at_utc")
    if observed < requested:
        raise OfficialSourceCaptureError("transport timestamps are reversed")
    if response.status != 200 or response.final_url != request["full_url"]:
        raise OfficialSourceCaptureError("official response status or URL differs")
    if not isinstance(response.body, bytes) or not response.body or len(response.body) > max_bytes:
        raise OfficialSourceCaptureError("official response body is empty or oversized")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != "application/json":
        raise OfficialSourceCaptureError("official response is not JSON")
    encoding = response.headers.get("content-encoding", "identity").lower()
    if encoding not in {"", "identity"}:
        raise OfficialSourceCaptureError("compressed response is forbidden")


def _receipt(
    *, request: Mapping[str, Any], response: CapturedResponse,
    runtime: RuntimeAuthorization, source_access: VerifiedHistoricalSourceAccess,
    source_bundle_sha256: str, body_path: str,
) -> dict[str, Any]:
    return {
        "schema_version": RECEIPT_SCHEMA,
        "authorization": AUTHORIZATION,
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "model_fitting_performed": False,
        "probabilities_generated": False,
        "protected_data": PROTECTED,
        "request": request,
        "response": {
            "status": response.status,
            "final_url": response.final_url,
            "requested_at_utc": response.requested_at_utc,
            "observed_at_utc": response.observed_at_utc,
            "headers": dict(response.headers),
            "body_path": body_path,
            "body_bytes": len(response.body),
            "body_sha256": sha256_bytes(response.body),
        },
        "runtime_attestation_sha256": runtime.attestation_sha256,
        "source_access_authorization_id": source_access.authorization_id,
        "source_access_authorization_sha256": (
            source_access.authorization_file_sha256
        ),
        "source_bundle_sha256": _sha(source_bundle_sha256, "source bundle"),
    }


def _manifest_digest(value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned["observed_capture_digest"] = None
    return sha256_bytes(canonical_json_bytes(unsigned))


def _exact_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise OfficialSourceCaptureError("capture contains a symlink")
        if path.is_file():
            files.add(path.relative_to(root).as_posix())
    return files


def _verify_receipt(root: Path, receipt: Mapping[str, Any], request: Mapping[str, Any]) -> bytes:
    receipt_keys = {
        "schema_version", "authorization", "season", "research_only",
        "betting_authorized", "model_fitting_performed", "probabilities_generated",
        "protected_data", "request", "response", "runtime_attestation_sha256",
        "source_access_authorization_id", "source_access_authorization_sha256",
        "source_bundle_sha256",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != receipt_keys:
        raise OfficialSourceCaptureError("retained receipt positive schema differs")
    if receipt.get("schema_version") != RECEIPT_SCHEMA or receipt.get("request") != request:
        raise OfficialSourceCaptureError("retained receipt identity differs")
    if (
        receipt.get("authorization") != AUTHORIZATION
        or receipt.get("season") != 2023
        or receipt.get("research_only") is not True
        or receipt.get("betting_authorized") is not False
        or receipt.get("model_fitting_performed") is not False
        or receipt.get("probabilities_generated") is not False
        or receipt.get("protected_data") != PROTECTED
    ):
        raise OfficialSourceCaptureError("retained receipt scope or safety state differs")
    if not isinstance(receipt.get("source_access_authorization_id"), str) or not receipt[
        "source_access_authorization_id"
    ]:
        raise OfficialSourceCaptureError("retained source-access identity is invalid")
    _sha(receipt.get("source_access_authorization_sha256"), "source access")
    _sha(receipt.get("runtime_attestation_sha256"), "runtime attestation")
    _sha(receipt.get("source_bundle_sha256"), "source bundle")
    response = receipt.get("response")
    if not isinstance(response, Mapping):
        raise OfficialSourceCaptureError("retained response receipt is malformed")
    if set(response) != {
        "status", "final_url", "requested_at_utc", "observed_at_utc", "headers",
        "body_path", "body_bytes", "body_sha256",
    }:
        raise OfficialSourceCaptureError("retained response positive schema differs")
    relative = response.get("body_path")
    if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise OfficialSourceCaptureError("retained body path is unsafe")
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise OfficialSourceCaptureError("retained body is missing or unsafe")
    raw = path.read_bytes()
    if response.get("body_bytes") != len(raw) or response.get("body_sha256") != sha256_bytes(raw):
        raise OfficialSourceCaptureError("retained body bytes differ")
    captured = CapturedResponse(
        int(response.get("status")), raw, response.get("headers") or {},
        str(response.get("final_url")), str(response.get("requested_at_utc")),
        str(response.get("observed_at_utc")),
    )
    _validate_response(captured, request, max_bytes=5_000_000)
    return raw


def capture_schedule(
    *, output_dir: Path, runtime: RuntimeAuthorization,
    source_access: VerifiedHistoricalSourceAccess, source_bundle_sha256: str,
    transport: Transport, timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    _require_source_access(runtime, source_access, source_bundle_sha256)
    output = _safe_root(output_dir, must_exist=False)
    request = _request(
        "official-mlb-2023-regular-schedule", SCHEDULE_FULL_URL,
        {"season": 2023, "game_type": "R"},
    )
    if output.exists():
        existing = verify_schedule_capture(output)
        if existing.get("runtime_attestation_sha256") != runtime.attestation_sha256:
            raise OfficialSourceCaptureError("existing schedule used a different runtime")
        if existing.get("source_bundle_sha256") != source_bundle_sha256:
            raise OfficialSourceCaptureError("existing schedule used different source bytes")
        if existing.get("source_access_authorization_sha256") != source_access.authorization_file_sha256:
            raise OfficialSourceCaptureError("existing schedule used different source access")
        return existing
    response = transport.fetch(request, timeout_seconds=timeout_seconds, max_bytes=5_000_000)
    _validate_response(response, request, max_bytes=5_000_000)
    staging = output.with_name(output.name + ".staging")
    if staging.exists():
        raise OfficialSourceCaptureError("stale schedule staging directory exists")
    staging.mkdir(parents=True)
    try:
        _write_new(staging / "response.json", response.body)
        receipt = _receipt(
            request=request, response=response, runtime=runtime,
            source_access=source_access,
            source_bundle_sha256=source_bundle_sha256, body_path="response.json",
        )
        _write_new(staging / "receipt.json", canonical_json_bytes(receipt))
        manifest = {
            "schema_version": SCHEDULE_SCHEMA,
            "status": "COMPLETE_IMMUTABLE_OFFICIAL_SCHEDULE_CAPTURE",
            "authorization": AUTHORIZATION,
            "season": 2023,
            "research_only": True,
            "betting_authorized": False,
            "protected_data": PROTECTED,
            "runtime_attestation_sha256": runtime.attestation_sha256,
            "source_access_authorization_id": source_access.authorization_id,
            "source_access_authorization_sha256": (
                source_access.authorization_file_sha256
            ),
            "source_bundle_sha256": _sha(source_bundle_sha256, "source bundle"),
            "request": request,
            "body_sha256": sha256_bytes(response.body),
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt)),
            "observed_capture_digest": None,
        }
        manifest["observed_capture_digest"] = _manifest_digest(manifest)
        _write_new(staging / "manifest.json", canonical_json_bytes(manifest))
        os.replace(staging, output)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_schedule_capture(root: Path, expected_digest: str | None = None) -> dict[str, Any]:
    value = _safe_root(root, must_exist=True)
    if _exact_files(value) != {"manifest.json", "receipt.json", "response.json"}:
        raise OfficialSourceCaptureError("schedule capture exact file set differs")
    manifest = json.loads((value / "manifest.json").read_bytes())
    if not isinstance(manifest, Mapping) or set(manifest) != {
        "schema_version", "status", "authorization", "season", "research_only",
        "betting_authorized", "protected_data", "runtime_attestation_sha256",
        "source_access_authorization_id", "source_access_authorization_sha256",
        "source_bundle_sha256", "request", "body_sha256", "receipt_sha256",
        "observed_capture_digest",
    }:
        raise OfficialSourceCaptureError("schedule capture positive schema differs")
    if (
        manifest.get("schema_version") != SCHEDULE_SCHEMA
        or manifest.get("status") != "COMPLETE_IMMUTABLE_OFFICIAL_SCHEDULE_CAPTURE"
        or manifest.get("authorization") != AUTHORIZATION
        or manifest.get("season") != 2023
        or manifest.get("research_only") is not True
        or manifest.get("betting_authorized") is not False
        or manifest.get("protected_data") != PROTECTED
    ):
        raise OfficialSourceCaptureError("schedule capture schema differs")
    _sha(manifest.get("runtime_attestation_sha256"), "runtime attestation")
    _sha(manifest.get("source_access_authorization_sha256"), "source access")
    if not isinstance(manifest.get("source_access_authorization_id"), str) or not manifest["source_access_authorization_id"]:
        raise OfficialSourceCaptureError("schedule source-access identity is invalid")
    _sha(manifest.get("source_bundle_sha256"), "source bundle")
    if _manifest_digest(manifest) != manifest.get("observed_capture_digest"):
        raise OfficialSourceCaptureError("schedule manifest digest differs")
    if expected_digest is not None and _sha(expected_digest, "expected schedule digest") != manifest["observed_capture_digest"]:
        raise OfficialSourceCaptureError("schedule capture differs from external digest")
    request = manifest.get("request")
    receipt = json.loads((value / "receipt.json").read_bytes())
    raw = _verify_receipt(value, receipt, request)
    if (
        receipt.get("runtime_attestation_sha256") != manifest["runtime_attestation_sha256"]
        or receipt.get("source_access_authorization_id") != manifest["source_access_authorization_id"]
        or receipt.get("source_access_authorization_sha256") != manifest["source_access_authorization_sha256"]
        or receipt.get("source_bundle_sha256") != manifest["source_bundle_sha256"]
    ):
        raise OfficialSourceCaptureError("schedule receipt authority differs")
    if sha256_bytes(raw) != manifest.get("body_sha256"):
        raise OfficialSourceCaptureError("schedule body differs from manifest")
    if sha256_bytes(canonical_json_bytes(receipt)) != manifest.get("receipt_sha256"):
        raise OfficialSourceCaptureError("schedule receipt differs from manifest")
    return manifest


def build_feed_plan(
    *, schedule_capture_dir: Path, expected_schedule_capture_digest: str,
) -> dict[str, Any]:
    manifest = verify_schedule_capture(
        schedule_capture_dir, expected_schedule_capture_digest
    )
    root = _safe_root(schedule_capture_dir, must_exist=True)
    document = json.loads((root / "response.json").read_bytes())
    dates = document.get("dates") if isinstance(document, Mapping) else None
    if not isinstance(dates, list):
        raise OfficialSourceCaptureError("official schedule response lacks dates")
    candidates: dict[int, dict[str, Any]] = {}
    for group in dates:
        if not isinstance(group, Mapping) or not isinstance(group.get("games"), list):
            raise OfficialSourceCaptureError("official schedule date group is malformed")
        for raw in group["games"]:
            if not isinstance(raw, Mapping) or raw.get("gameType") != "R":
                raise OfficialSourceCaptureError("official schedule game type differs")
            game_pk = raw.get("gamePk")
            if isinstance(game_pk, bool) or not isinstance(game_pk, int) or game_pk <= 0:
                raise OfficialSourceCaptureError("official schedule gamePk is invalid")
            teams = raw.get("teams") or {}
            away = ((teams.get("away") or {}).get("team") or {}).get("id")
            home = ((teams.get("home") or {}).get("team") or {}).get("id")
            if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (away, home)) or away == home:
                raise OfficialSourceCaptureError("official schedule team identity is invalid")
            candidate = {"game_pk": game_pk, "away_team_id": away, "home_team_id": home}
            prior = candidates.get(game_pk)
            if prior is not None and prior != candidate:
                raise OfficialSourceCaptureError("repeated schedule game identity contradicts")
            candidates[game_pk] = candidate
    if len(candidates) != EXPECTED_GAMES:
        raise OfficialSourceCaptureError(
            f"2023 regular-season schedule coverage differs: {len(candidates)}"
        )
    requests = []
    for game_pk in sorted(candidates):
        full_url = (
            f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live?"
            + urlencode({"fields": FEED_FIELDS})
        )
        requests.append(
            _request(f"game-{game_pk}", full_url, candidates[game_pk])
        )
    return {
        "schema_version": PLAN_SCHEMA,
        "status": "LOCKED_OFFICIAL_2023_FINAL_FEED_CAPTURE_PLAN",
        "authorization": AUTHORIZATION,
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "protected_data": PROTECTED,
        "schedule_capture_digest": manifest["observed_capture_digest"],
        "feed_fields": FEED_FIELDS,
        "requests": requests,
    }


def _plan(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != PLAN_SCHEMA
        or value.get("status") != "LOCKED_OFFICIAL_2023_FINAL_FEED_CAPTURE_PLAN"
        or value.get("authorization") != AUTHORIZATION
        or value.get("season") != 2023
        or value.get("research_only") is not True
        or value.get("betting_authorized") is not False
        or value.get("protected_data") != PROTECTED
        or value.get("feed_fields") != FEED_FIELDS
    ):
        raise OfficialSourceCaptureError("feed capture plan scope or schema differs")
    _sha(value.get("schedule_capture_digest"), "schedule capture digest")
    requests = value.get("requests")
    if not isinstance(requests, list) or len(requests) != EXPECTED_GAMES:
        raise OfficialSourceCaptureError("feed plan does not contain the exact game universe")
    ids: list[str] = []
    for request in requests:
        if not isinstance(request, Mapping):
            raise OfficialSourceCaptureError("feed request is malformed")
        game_pk = (request.get("expected") or {}).get("game_pk")
        expected_url = (
            f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live?"
            + urlencode({"fields": FEED_FIELDS})
        )
        if request != _request(f"game-{game_pk}", expected_url, request["expected"]):
            raise OfficialSourceCaptureError("feed request endpoint or identity differs")
        ids.append(request["request_id"])
    if ids != sorted(ids, key=lambda x: int(x.split("-")[1])) or len(set(ids)) != EXPECTED_GAMES:
        raise OfficialSourceCaptureError("feed requests are not unique and sorted")
    return [dict(request) for request in requests]


def capture_feeds(
    *, plan: Mapping[str, Any], output_dir: Path, work_dir: Path,
    runtime: RuntimeAuthorization, source_access: VerifiedHistoricalSourceAccess,
    source_bundle_sha256: str, transport: Transport,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    _require_source_access(runtime, source_access, source_bundle_sha256)
    requests = _plan(plan)
    output = _safe_root(output_dir, must_exist=False)
    work = _safe_root(work_dir, must_exist=False)
    if output.exists():
        existing = verify_feed_capture(output)
        if existing.get("runtime_attestation_sha256") != runtime.attestation_sha256:
            raise OfficialSourceCaptureError("existing feed capture used a different runtime")
        if existing.get("source_bundle_sha256") != source_bundle_sha256:
            raise OfficialSourceCaptureError("existing feed capture used different source bytes")
        if existing.get("source_access_authorization_sha256") != source_access.authorization_file_sha256:
            raise OfficialSourceCaptureError("existing feed capture used different source access")
        return existing
    if output == work or output in work.parents or work in output.parents:
        raise OfficialSourceCaptureError("work and final paths overlap")
    work.mkdir(parents=True, exist_ok=True)
    _write_new(work / "plan.json", canonical_json_bytes(plan)) if not (work / "plan.json").exists() else None
    if (work / "plan.json").read_bytes() != canonical_json_bytes(plan):
        raise OfficialSourceCaptureError("resumable work uses a different plan")
    for request in requests:
        request_id = request["request_id"]
        base = work / "feeds" / request_id
        raw_path = base / "response.json"
        receipt_path = base / "receipt.json"
        if raw_path.exists() or receipt_path.exists():
            if not (raw_path.is_file() and receipt_path.is_file()):
                raise OfficialSourceCaptureError("partial retained feed pair is contradictory")
            receipt = json.loads(receipt_path.read_bytes())
            _verify_receipt(work, receipt, request)
            if receipt.get("runtime_attestation_sha256") != runtime.attestation_sha256:
                raise OfficialSourceCaptureError("resumed feed used a different runtime")
            if receipt.get("source_bundle_sha256") != source_bundle_sha256:
                raise OfficialSourceCaptureError("resumed feed used different source bytes")
            if receipt.get("source_access_authorization_sha256") != source_access.authorization_file_sha256:
                raise OfficialSourceCaptureError("resumed feed used different source access")
            continue
        response = transport.fetch(request, timeout_seconds=timeout_seconds, max_bytes=5_000_000)
        _validate_response(response, request, max_bytes=5_000_000)
        relative = f"feeds/{request_id}/response.json"
        receipt = _receipt(
            request=request, response=response, runtime=runtime,
            source_access=source_access,
            source_bundle_sha256=source_bundle_sha256, body_path=relative,
        )
        # A directory rename publishes the raw/receipt pair together.  A hard
        # crash can leave an inert staging directory, but never a half-valid
        # authoritative pair.
        request_staging = base.with_name(".staging-" + request_id)
        if request_staging.exists():
            raise OfficialSourceCaptureError(
                "interrupted request staging requires explicit review"
            )
        request_staging.mkdir(parents=True)
        _write_new(request_staging / "response.json", response.body)
        _write_new(request_staging / "receipt.json", canonical_json_bytes(receipt))
        os.replace(request_staging, base)
    entries = []
    for request in requests:
        base = work / "feeds" / request["request_id"]
        raw = _verify_receipt(work, json.loads((base / "receipt.json").read_bytes()), request)
        entries.append({
            "request_id": request["request_id"],
            "response_sha256": sha256_bytes(raw),
            "receipt_sha256": sha256_file(base / "receipt.json"),
        })
    manifest = {
        "schema_version": FEED_SCHEMA,
        "status": "COMPLETE_IMMUTABLE_OFFICIAL_2023_FEED_CAPTURE",
        "authorization": AUTHORIZATION,
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "protected_data": PROTECTED,
        "schedule_capture_digest": plan["schedule_capture_digest"],
        "plan_sha256": sha256_bytes(canonical_json_bytes(plan)),
        "runtime_attestation_sha256": runtime.attestation_sha256,
        "source_access_authorization_id": source_access.authorization_id,
        "source_access_authorization_sha256": (
            source_access.authorization_file_sha256
        ),
        "source_bundle_sha256": _sha(source_bundle_sha256, "source bundle"),
        "game_count": len(entries),
        "entries": entries,
        "observed_capture_digest": None,
    }
    manifest["observed_capture_digest"] = _manifest_digest(manifest)
    _write_new(work / "manifest.json", canonical_json_bytes(manifest))
    os.replace(work, output)
    return manifest


def verify_feed_capture(root: Path, expected_digest: str | None = None) -> dict[str, Any]:
    value = _safe_root(root, must_exist=True)
    manifest = json.loads((value / "manifest.json").read_bytes())
    if not isinstance(manifest, Mapping) or set(manifest) != {
        "schema_version", "status", "authorization", "season", "research_only",
        "betting_authorized", "protected_data", "schedule_capture_digest",
        "plan_sha256", "runtime_attestation_sha256", "source_bundle_sha256",
        "source_access_authorization_id", "source_access_authorization_sha256",
        "game_count", "entries", "observed_capture_digest",
    }:
        raise OfficialSourceCaptureError("feed capture positive schema differs")
    if (
        manifest.get("schema_version") != FEED_SCHEMA
        or manifest.get("status") != "COMPLETE_IMMUTABLE_OFFICIAL_2023_FEED_CAPTURE"
        or manifest.get("authorization") != AUTHORIZATION
        or manifest.get("season") != 2023
        or manifest.get("research_only") is not True
        or manifest.get("betting_authorized") is not False
        or manifest.get("protected_data") != PROTECTED
        or manifest.get("game_count") != EXPECTED_GAMES
    ):
        raise OfficialSourceCaptureError("feed capture manifest schema or coverage differs")
    for field in ("schedule_capture_digest", "plan_sha256", "runtime_attestation_sha256", "source_access_authorization_sha256", "source_bundle_sha256"):
        _sha(manifest.get(field), field)
    if not isinstance(manifest.get("source_access_authorization_id"), str) or not manifest["source_access_authorization_id"]:
        raise OfficialSourceCaptureError("feed source-access identity is invalid")
    if _manifest_digest(manifest) != manifest.get("observed_capture_digest"):
        raise OfficialSourceCaptureError("feed manifest digest differs")
    if expected_digest is not None and _sha(expected_digest, "expected feed digest") != manifest["observed_capture_digest"]:
        raise OfficialSourceCaptureError("feed capture differs from external digest")
    plan = json.loads((value / "plan.json").read_bytes())
    requests = _plan(plan)
    expected_files = {"manifest.json", "plan.json"}
    for request in requests:
        expected_files.add(f"feeds/{request['request_id']}/response.json")
        expected_files.add(f"feeds/{request['request_id']}/receipt.json")
    if _exact_files(value) != expected_files:
        raise OfficialSourceCaptureError("feed capture exact file set differs")
    entries = []
    for request in requests:
        base = value / "feeds" / request["request_id"]
        receipt = json.loads((base / "receipt.json").read_bytes())
        raw = _verify_receipt(value, receipt, request)
        if (
            receipt.get("runtime_attestation_sha256") != manifest["runtime_attestation_sha256"]
            or receipt.get("source_access_authorization_id") != manifest["source_access_authorization_id"]
            or receipt.get("source_access_authorization_sha256") != manifest["source_access_authorization_sha256"]
            or receipt.get("source_bundle_sha256") != manifest["source_bundle_sha256"]
        ):
            raise OfficialSourceCaptureError("feed receipt authority differs")
        entries.append({
            "request_id": request["request_id"],
            "response_sha256": sha256_bytes(raw),
            "receipt_sha256": sha256_file(base / "receipt.json"),
        })
    if entries != manifest.get("entries") or sha256_bytes(canonical_json_bytes(plan)) != manifest.get("plan_sha256"):
        raise OfficialSourceCaptureError("feed capture exact receipts or plan differ")
    return manifest


def _load_json_file(path: Path, label: str) -> Mapping[str, Any]:
    value = Path(os.path.abspath(os.fspath(path)))
    if ".." in path.parts or not value.is_file() or value.is_symlink():
        raise OfficialSourceCaptureError(f"{label} is missing or unsafe")
    try:
        document = json.loads(value.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialSourceCaptureError(f"{label} is not valid JSON") from exc
    if not isinstance(document, Mapping):
        raise OfficialSourceCaptureError(f"{label} root is malformed")
    return document


def _add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-policy", required=True, type=Path)
    parser.add_argument("--runtime-attestation", required=True, type=Path)
    parser.add_argument("--expected-runtime-attestation-sha256", required=True)
    parser.add_argument("--source-access-authorization", required=True, type=Path)
    parser.add_argument("--expected-source-access-authorization-sha256", required=True)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    schedule = sub.add_parser("capture-schedule")
    _add_runtime_arguments(schedule)
    schedule.add_argument("--output-dir", required=True, type=Path)
    schedule.add_argument("--timeout-seconds", default=30.0, type=float)

    plan = sub.add_parser("build-feed-plan")
    plan.add_argument("--schedule-capture-dir", required=True, type=Path)
    plan.add_argument("--expected-schedule-capture-digest", required=True)
    plan.add_argument("--output-plan", required=True, type=Path)

    feeds = sub.add_parser("capture-feeds")
    _add_runtime_arguments(feeds)
    feeds.add_argument("--plan", required=True, type=Path)
    feeds.add_argument("--output-dir", required=True, type=Path)
    feeds.add_argument("--work-dir", required=True, type=Path)
    feeds.add_argument("--timeout-seconds", default=30.0, type=float)

    verify_schedule = sub.add_parser("verify-schedule")
    verify_schedule.add_argument("--capture-dir", required=True, type=Path)
    verify_schedule.add_argument("--expected-capture-digest", required=True)

    verify_feeds = sub.add_parser("verify-feeds")
    verify_feeds.add_argument("--capture-dir", required=True, type=Path)
    verify_feeds.add_argument("--expected-capture-digest", required=True)
    return parser.parse_args(argv)


def _authorize_from_args(
    args: argparse.Namespace,
) -> tuple[RuntimeAuthorization, VerifiedHistoricalSourceAccess]:
    runtime = authorize_runtime(
        attestation_path=args.runtime_attestation,
        expected_attestation_sha256=args.expected_runtime_attestation_sha256,
        policy_path=args.runtime_policy,
    )
    access_time = datetime.now(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    source_access = verify_historical_source_access_authorization(
        authorization_path=args.source_access_authorization,
        expected_authorization_sha256=(
            args.expected_source_access_authorization_sha256
        ),
        expected_runtime_policy_sha256=runtime.policy_sha256,
        expected_source_bundle_sha256=capture_source_bundle_sha256(),
        access_time_utc=access_time,
    )
    return runtime, source_access


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "verify-schedule":
        result = verify_schedule_capture(args.capture_dir, args.expected_capture_digest)
    elif args.command == "verify-feeds":
        result = verify_feed_capture(args.capture_dir, args.expected_capture_digest)
    elif args.command == "build-feed-plan":
        result = build_feed_plan(
            schedule_capture_dir=args.schedule_capture_dir,
            expected_schedule_capture_digest=args.expected_schedule_capture_digest,
        )
        _write_new(_safe_new_file(args.output_plan, "feed plan"), canonical_json_bytes(result))
    elif args.command == "capture-schedule":
        authority, source_access = _authorize_from_args(args)
        result = capture_schedule(
            output_dir=args.output_dir,
            runtime=authority,
            source_access=source_access,
            source_bundle_sha256=capture_source_bundle_sha256(),
            transport=HTTPSHistoricalTransport(),
            timeout_seconds=args.timeout_seconds,
        )
    elif args.command == "capture-feeds":
        authority, source_access = _authorize_from_args(args)
        plan = _load_json_file(args.plan, "feed plan")
        result = capture_feeds(
            plan=plan,
            output_dir=args.output_dir,
            work_dir=args.work_dir,
            runtime=authority,
            source_access=source_access,
            source_bundle_sha256=capture_source_bundle_sha256(),
            transport=HTTPSHistoricalTransport(),
            timeout_seconds=args.timeout_seconds,
        )
    else:  # pragma: no cover - argparse enforces the command set.
        raise OfficialSourceCaptureError("unsupported command")
    print(json.dumps({
        "schema_version": result.get("schema_version"),
        "status": result.get("status", "FEED_PLAN_WRITTEN"),
        "observed_capture_digest": result.get("observed_capture_digest"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
