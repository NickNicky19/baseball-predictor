"""Capture immutable official 2023 schedule/feed bytes for PA-volume repair.

The capture is historical, research-only, outcome-source reconstruction.  It
cannot generate probabilities or prospective evidence.  Partial feed work is
resumable but is never authoritative until exact coverage finalizes.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.capture_direct_batter_pa_source_transport_v2 import (
    CapturedResponse,
    HTTPSHistoricalTransport,
    RuntimeAuthorization,
    TransportFailure,
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
# The official full-season schedule is a single bounded response and is larger
# than an individual game feed.  Keep separate ceilings so accepting that
# legitimate schedule does not loosen the per-game feed boundary.
SCHEDULE_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
FEED_MAX_RESPONSE_BYTES = 5_000_000
MIN_REQUEST_INTERVAL_SECONDS = 1.0
MAX_REQUEST_ATTEMPTS = 4
RETRY_BASE_SECONDS = 1.0
RETRY_MAX_SECONDS = 30.0
DEFAULT_SCHEDULE_TIMEOUT_SECONDS = 600.0
DEFAULT_FEED_TIMEOUT_SECONDS = 21_600.0
STORAGE_RESERVE_BYTES = 128 * 1024 * 1024
CAPTURE_CONTEXT_SCHEMA = "pa-volume-capture-lifetime-context-v1"
ATTEMPT_JOURNAL_CONTEXT_SCHEMA = "pa-volume-request-attempt-context-v1"
ATTEMPT_RESERVATION_SCHEMA = "pa-volume-request-attempt-reservation-v1"
ATTEMPT_RESULT_SCHEMA = "pa-volume-request-attempt-result-v1"
TERMINAL_ATTEMPT_SCHEMA = "pa-volume-request-terminal-attempt-v1"
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


Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]
Jitter = Callable[[str, int, float], float]
DiskUsage = Callable[[Path], Any]


def _clock_now() -> datetime:
    return datetime.now(timezone.utc)


def _deterministic_jitter(
    request_id: str, attempt: int, base_delay: float
) -> float:
    seed = hashlib.sha256(f"{request_id}:{attempt}".encode("ascii")).digest()
    fraction = int.from_bytes(seed[:8], "big") / float(2**64 - 1)
    return fraction * min(1.0, base_delay * 0.25)


def _runtime_policy(
    *,
    minimum_request_interval_seconds: float,
    maximum_attempts: int,
    overall_timeout_seconds: float,
) -> dict[str, Any]:
    return {
        "minimum_request_interval_seconds": minimum_request_interval_seconds,
        "maximum_attempts": maximum_attempts,
        "retry_base_seconds": RETRY_BASE_SECONDS,
        "retry_max_seconds": RETRY_MAX_SECONDS,
        "overall_timeout_seconds": overall_timeout_seconds,
    }


def _validate_operational_policy(
    *,
    timeout_seconds: float,
    minimum_request_interval_seconds: float,
    maximum_attempts: int,
    overall_timeout_seconds: float,
) -> None:
    numeric = (int, float)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, numeric)
        or not 1 <= timeout_seconds <= 120
    ):
        raise OfficialSourceCaptureError("timeout must be numeric and in [1,120]")
    if (
        isinstance(minimum_request_interval_seconds, bool)
        or not isinstance(minimum_request_interval_seconds, numeric)
        or not MIN_REQUEST_INTERVAL_SECONDS
        <= minimum_request_interval_seconds
        <= 60
    ):
        raise OfficialSourceCaptureError(
            "minimum request interval is below the conservative floor"
        )
    if (
        isinstance(maximum_attempts, bool)
        or not isinstance(maximum_attempts, int)
        or not 1 <= maximum_attempts <= MAX_REQUEST_ATTEMPTS
    ):
        raise OfficialSourceCaptureError("maximum attempts must be in [1,4]")
    if (
        isinstance(overall_timeout_seconds, bool)
        or not isinstance(overall_timeout_seconds, numeric)
        or not 60 <= overall_timeout_seconds <= 86_400
    ):
        raise OfficialSourceCaptureError(
            "overall timeout must be numeric and in [60,86400] seconds"
        )


def _authorization_expiry(source_access: VerifiedHistoricalSourceAccess) -> datetime:
    try:
        value = datetime.fromisoformat(
            source_access.expires_at_utc[:-1] + "+00:00"
        )
    except (AttributeError, ValueError) as exc:
        raise OfficialSourceCaptureError(
            "source-access authorization expiry is invalid"
        ) from exc
    if not source_access.expires_at_utc.endswith("Z") or value.utcoffset() != timezone.utc.utcoffset(value):
        raise OfficialSourceCaptureError(
            "source-access authorization expiry is invalid"
        )
    return value.astimezone(timezone.utc)


def _existing_ancestor(path: Path) -> Path:
    value = path
    while not value.exists():
        if value == value.parent:
            raise OfficialSourceCaptureError("capture path has no existing ancestor")
        value = value.parent
    return value


def _storage_preflight(
    *,
    work_path: Path,
    output_path: Path,
    required_bytes: int,
    disk_usage: DiskUsage,
) -> None:
    work_anchor = _existing_ancestor(work_path)
    output_anchor = _existing_ancestor(output_path.parent)
    if os.stat(work_anchor).st_dev != os.stat(output_anchor).st_dev:
        raise OfficialSourceCaptureError(
            "work and final output must use the same filesystem"
        )
    available = int(disk_usage(work_anchor).free)
    if available < required_bytes + STORAGE_RESERVE_BYTES:
        raise OfficialSourceCaptureError(
            "capture filesystem lacks the predeclared free-space reserve"
        )


@contextmanager
def _exclusive_writer(lock_path: Path) -> Iterator[None]:
    try:
        descriptor = os.open(
            lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
    except FileExistsError as exc:
        raise OfficialSourceCaptureError(
            "stale or active capture lock requires explicit review"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(
                canonical_json_bytes(
                    {
                        "schema_version": "pa-volume-capture-exclusive-lock-v1",
                        "status": "CAPTURE_IN_PROGRESS_NOT_AUTHORITY",
                    }
                )
            )
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _retry_after_seconds(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.isascii() and stripped.isdigit():
        return float(int(stripped))
    try:
        parsed = parsedate_to_datetime(stripped)
    except (TypeError, ValueError) as exc:
        raise OfficialSourceCaptureError("Retry-After header is invalid") from exc
    if parsed.tzinfo is None:
        raise OfficialSourceCaptureError("Retry-After header is invalid")
    return max(0.0, (parsed.astimezone(timezone.utc) - now).total_seconds())


def _attempt_record(
    *,
    attempt: int,
    requested_at_utc: str,
    observed_at_utc: str,
    outcome: str,
    status: int | None,
    error_kind: str | None,
    retry_after_header: str | None,
    retry_after_seconds: float | None,
    backoff_seconds: float,
    jitter_seconds: float,
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "requested_at_utc": requested_at_utc,
        "observed_at_utc": observed_at_utc,
        "outcome": outcome,
        "status": status,
        "error_kind": error_kind,
        "retry_after_header": retry_after_header,
        "retry_after_seconds": retry_after_seconds,
        "backoff_seconds": backoff_seconds,
        "jitter_seconds": jitter_seconds,
    }


def _capture_context(
    *,
    plan_sha256: str,
    runtime: RuntimeAuthorization,
    source_access: VerifiedHistoricalSourceAccess,
    source_bundle_sha256: str,
    request_policy: Mapping[str, Any],
    started: datetime,
    deadline: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": CAPTURE_CONTEXT_SCHEMA,
        "status": "ACTIVE_OR_COMPLETE_IMMUTABLE_CAPTURE_CONTEXT",
        "plan_sha256": plan_sha256,
        "runtime_attestation_sha256": runtime.attestation_sha256,
        "source_access_authorization_id": source_access.authorization_id,
        "source_access_authorization_sha256": (
            source_access.authorization_file_sha256
        ),
        "source_bundle_sha256": source_bundle_sha256,
        "request_policy": dict(request_policy),
        "capture_started_at_utc": _microsecond_stamp(started),
        "deadline_at_utc": _microsecond_stamp(deadline),
        "authorization_valid_from_utc": source_access.valid_from_utc,
        "authorization_expires_at_utc": source_access.expires_at_utc,
    }


def _microsecond_stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _prepare_capture_context(
    *,
    path: Path,
    plan_sha256: str,
    runtime: RuntimeAuthorization,
    source_access: VerifiedHistoricalSourceAccess,
    source_bundle_sha256: str,
    request_policy: Mapping[str, Any],
    clock: Clock,
) -> tuple[Mapping[str, Any], datetime]:
    if path.exists():
        try:
            value = json.loads(path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OfficialSourceCaptureError(
                "capture lifetime context is invalid"
            ) from exc
        if not isinstance(value, Mapping):
            raise OfficialSourceCaptureError(
                "capture lifetime context is invalid"
            )
        started = _utc(
            value.get("capture_started_at_utc"), "capture context start"
        )
        deadline = _utc(value.get("deadline_at_utc"), "capture context deadline")
        expected = _capture_context(
            plan_sha256=plan_sha256,
            runtime=runtime,
            source_access=source_access,
            source_bundle_sha256=source_bundle_sha256,
            request_policy=request_policy,
            started=started,
            deadline=deadline,
        )
        if value != expected or deadline != started + timedelta(
            seconds=float(request_policy["overall_timeout_seconds"])
        ):
            raise OfficialSourceCaptureError(
                "capture lifetime context differs from exact inputs"
            )
        return value, deadline
    started = clock().astimezone(timezone.utc)
    deadline = started + timedelta(
        seconds=float(request_policy["overall_timeout_seconds"])
    )
    value = _capture_context(
        plan_sha256=plan_sha256,
        runtime=runtime,
        source_access=source_access,
        source_bundle_sha256=source_bundle_sha256,
        request_policy=request_policy,
        started=started,
        deadline=deadline,
    )
    _write_new(path, canonical_json_bytes(value))
    return value, deadline


def _request_context(
    *, run_context_sha256: str, request: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": ATTEMPT_JOURNAL_CONTEXT_SCHEMA,
        "status": "IMMUTABLE_REQUEST_ATTEMPT_JOURNAL",
        "run_context_sha256": run_context_sha256,
        "request_sha256": sha256_bytes(canonical_json_bytes(request)),
    }


def _load_request_journal(
    *,
    journal: Path,
    run_context: Mapping[str, Any],
    run_context_sha256: str,
    request: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected_context = _request_context(
        run_context_sha256=run_context_sha256, request=request
    )
    if not journal.exists():
        journal.mkdir(parents=True)
        _write_new(
            journal / "context.json", canonical_json_bytes(expected_context)
        )
        return []
    if not journal.is_dir() or journal.is_symlink():
        raise OfficialSourceCaptureError("request attempt journal is unsafe")
    try:
        context = json.loads((journal / "context.json").read_bytes())
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialSourceCaptureError(
            "request attempt journal context is invalid"
        ) from exc
    if context != expected_context:
        raise OfficialSourceCaptureError(
            "request attempt journal binds different exact inputs"
        )
    context_sha256 = sha256_bytes(canonical_json_bytes(expected_context))
    reservations: list[dict[str, Any]] = []
    reservation_paths: list[Path] = []
    attempts: list[dict[str, Any]] = []
    expected_files = {"context.json"}
    for index, path in enumerate(sorted(journal.glob("reservation-*.json")), 1):
        expected_name = f"reservation-{index:04d}.json"
        if path.name != expected_name or path.is_symlink():
            raise OfficialSourceCaptureError(
                "request attempt reservation order differs"
            )
        try:
            wrapper = json.loads(path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OfficialSourceCaptureError(
                "request attempt reservation is invalid"
            ) from exc
        if (
            not isinstance(wrapper, Mapping)
            or set(wrapper) != {
                "schema_version", "context_sha256", "attempt",
                "reserved_at_utc",
            }
            or wrapper.get("schema_version") != ATTEMPT_RESERVATION_SCHEMA
            or wrapper.get("context_sha256") != context_sha256
            or isinstance(wrapper.get("attempt"), bool)
            or wrapper.get("attempt") != index
        ):
            raise OfficialSourceCaptureError(
                "request attempt reservation binding differs"
            )
        reserved = _utc(wrapper.get("reserved_at_utc"), "attempt reservation")
        capture_started = _utc(
            run_context["capture_started_at_utc"], "capture context start"
        )
        deadline = _utc(run_context["deadline_at_utc"], "capture deadline")
        expires = _utc(
            run_context["authorization_expires_at_utc"], "authorization expiry"
        )
        if reserved < capture_started or reserved >= min(deadline, expires):
            raise OfficialSourceCaptureError(
                "request attempt reservation is outside capture authority"
            )
        reservations.append(dict(wrapper))
        reservation_paths.append(path)
        expected_files.add(expected_name)
    for index, path in enumerate(sorted(journal.glob("result-*.json")), 1):
        expected_name = f"result-{index:04d}.json"
        if path.name != expected_name or path.is_symlink() or index > len(reservations):
            raise OfficialSourceCaptureError("request attempt result order differs")
        try:
            wrapper = json.loads(path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OfficialSourceCaptureError("request attempt result is invalid") from exc
        if (
            not isinstance(wrapper, Mapping)
            or set(wrapper) != {
                "schema_version", "context_sha256", "reservation_sha256",
                "attempt",
            }
            or wrapper.get("schema_version") != ATTEMPT_RESULT_SCHEMA
            or wrapper.get("context_sha256") != context_sha256
            or wrapper.get("reservation_sha256")
            != sha256_file(reservation_paths[index - 1])
            or not isinstance(wrapper.get("attempt"), Mapping)
            or wrapper["attempt"].get("attempt") != index
        ):
            raise OfficialSourceCaptureError(
                "request attempt result binding differs"
            )
        attempts.append(dict(wrapper["attempt"]))
        expected_files.add(expected_name)
    terminal_path = journal / "terminal.json"
    if terminal_path.exists():
        expected_files.add("terminal.json")
        try:
            terminal = json.loads(terminal_path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OfficialSourceCaptureError(
                "terminal request attempt record is invalid"
            ) from exc
        expected_terminal = _terminal_value(
            journal=journal,
            context_sha256=context_sha256,
            reason=terminal.get("reason") if isinstance(terminal, Mapping) else None,
        )
        if (
            terminal != expected_terminal
            or terminal.get("reason")
            not in {
                "ATTEMPT_BUDGET_EXHAUSTED",
                "NONRETRYABLE_FAILURE",
                "CAPTURE_WINDOW_CLOSED",
                "POLICY_FAILURE",
                "IN_FLIGHT_RESERVATION_UNRESOLVED",
            }
        ):
            raise OfficialSourceCaptureError(
                "terminal request attempt record binding differs"
            )
        raise OfficialSourceCaptureError(
            "terminal request attempt record requires explicit review"
        )
    if len(attempts) != len(reservations):
        _persist_terminal(
            journal=journal,
            context_sha256=context_sha256,
            reason="IN_FLIGHT_RESERVATION_UNRESOLVED",
        )
        raise OfficialSourceCaptureError(
            "unresolved in-flight request reservation requires explicit review"
        )
    observed_files = {
        path.name for path in journal.iterdir() if path.is_file()
    }
    if observed_files != expected_files or any(
        path.is_dir() for path in journal.iterdir()
    ):
        raise OfficialSourceCaptureError(
            "request attempt journal exact file set differs"
        )
    if attempts:
        _validate_attempt_history(
            attempts=attempts,
            policy=run_context["request_policy"],
            capture_window={
                key: run_context[key]
                for key in (
                    "capture_started_at_utc",
                    "deadline_at_utc",
                    "authorization_valid_from_utc",
                    "authorization_expires_at_utc",
                )
            },
            require_success=False,
        )
        for reservation, attempt in zip(reservations, attempts):
            reserved = _utc(
                reservation["reserved_at_utc"], "attempt reservation"
            )
            requested = _utc(
                attempt["requested_at_utc"], "attempt requested_at_utc"
            )
            if reserved > requested:
                raise OfficialSourceCaptureError(
                    "attempt result predates its durable reservation"
                )
    return attempts


def _reserve_attempt(
    *, journal: Path, context_sha256: str, attempt: int,
    reserved_at_utc: str,
) -> Path:
    index = attempt
    if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
        raise OfficialSourceCaptureError("attempt journal index is invalid")
    wrapper = {
        "schema_version": ATTEMPT_RESERVATION_SCHEMA,
        "context_sha256": context_sha256,
        "attempt": index,
        "reserved_at_utc": reserved_at_utc,
    }
    path = journal / f"reservation-{index:04d}.json"
    _write_new(path, canonical_json_bytes(wrapper))
    return path


def _persist_attempt_result(
    *, journal: Path, context_sha256: str, reservation: Path,
    attempt: Mapping[str, Any],
) -> None:
    index = attempt.get("attempt")
    if isinstance(index, bool) or not isinstance(index, int) or index <= 0:
        raise OfficialSourceCaptureError("attempt result index is invalid")
    wrapper = {
        "schema_version": ATTEMPT_RESULT_SCHEMA,
        "context_sha256": context_sha256,
        "reservation_sha256": sha256_file(reservation),
        "attempt": dict(attempt),
    }
    _write_new(
        journal / f"result-{index:04d}.json", canonical_json_bytes(wrapper)
    )


def _journal_file_bindings(journal: Path, pattern: str) -> list[dict[str, str]]:
    return [
        {"path": path.name, "sha256": sha256_file(path)}
        for path in sorted(journal.glob(pattern))
    ]


def _terminal_value(
    *, journal: Path, context_sha256: str, reason: Any,
) -> dict[str, Any]:
    reservations = _journal_file_bindings(journal, "reservation-*.json")
    results = _journal_file_bindings(journal, "result-*.json")
    return {
        "schema_version": TERMINAL_ATTEMPT_SCHEMA,
        "status": "TERMINAL_REVIEW_REQUIRED",
        "context_sha256": context_sha256,
        "consumed_attempt_count": len(reservations),
        "reservation_files": reservations,
        "result_files": results,
        "reason": reason,
    }


def _persist_terminal(
    *,
    journal: Path,
    context_sha256: str,
    reason: str,
) -> None:
    value = _terminal_value(
        journal=journal, context_sha256=context_sha256, reason=reason
    )
    _write_new(journal / "terminal.json", canonical_json_bytes(value))


def _fetch_with_policy(
    *,
    request: Mapping[str, Any],
    transport: Transport,
    timeout_seconds: float,
    max_bytes: int,
    source_access: VerifiedHistoricalSourceAccess,
    deadline: datetime,
    minimum_request_interval_seconds: float,
    maximum_attempts: int,
    clock: Clock,
    sleeper: Sleeper,
    jitter: Jitter,
    last_attempt_started: datetime | None,
    existing_attempts: list[dict[str, Any]],
    reserve_attempt: Callable[[int, str], Path],
    persist_attempt_result: Callable[[Path, Mapping[str, Any]], None],
    persist_terminal: Callable[[list[dict[str, Any]], str], None],
) -> tuple[CapturedResponse, list[dict[str, Any]], datetime]:
    attempts = [dict(value) for value in existing_attempts]
    expiry = _authorization_expiry(source_access)
    required_delay = 0.0
    if attempts:
        last = attempts[-1]
        if last.get("outcome") != "RETRYABLE_FAILURE":
            persist_terminal(attempts, "POLICY_FAILURE")
            raise OfficialSourceCaptureError(
                "non-retryable prior attempt requires explicit review"
            )
        required_delay = float(last["backoff_seconds"])
        last_attempt_started = _utc(
            last["requested_at_utc"], "prior attempt requested_at_utc"
        )
    if len(attempts) >= maximum_attempts:
        persist_terminal(attempts, "ATTEMPT_BUDGET_EXHAUSTED")
        raise OfficialSourceCaptureError(
            "lifetime request attempt budget is exhausted"
        )

    def terminal(message: str, reason: str) -> None:
        persist_terminal(attempts, reason)
        raise OfficialSourceCaptureError(message)

    for attempt in range(len(attempts) + 1, maximum_attempts + 1):
        now = clock().astimezone(timezone.utc)
        pacing_delay = 0.0
        if last_attempt_started is not None:
            pacing_delay = max(
                0.0,
                minimum_request_interval_seconds
                - (now - last_attempt_started).total_seconds(),
            )
        wait_seconds = max(required_delay, pacing_delay)
        if now.timestamp() + wait_seconds >= min(deadline, expiry).timestamp():
            terminal(
                "capture deadline or source-access authorization expires before next request",
                "CAPTURE_WINDOW_CLOSED",
            )
        if wait_seconds:
            expected_wake = now + timedelta(seconds=wait_seconds)
            sleeper(wait_seconds)
            woke = clock().astimezone(timezone.utc)
            if woke < expected_wake:
                terminal(
                    "capture sleeper returned before the required pacing boundary",
                    "POLICY_FAILURE",
                )
        now = clock().astimezone(timezone.utc)
        if now >= deadline:
            terminal("capture overall deadline expired", "CAPTURE_WINDOW_CLOSED")
        if now >= expiry:
            terminal(
                "source-access authorization expired before request",
                "CAPTURE_WINDOW_CLOSED",
            )
        if now + timedelta(seconds=float(timeout_seconds)) >= deadline:
            terminal(
                "capture deadline cannot contain the next request timeout",
                "CAPTURE_WINDOW_CLOSED",
            )
        if now + timedelta(seconds=float(timeout_seconds)) >= expiry:
            terminal(
                "source-access authorization cannot contain the next request timeout",
                "CAPTURE_WINDOW_CLOSED",
            )
        last_attempt_started = now
        reservation = reserve_attempt(attempt, _microsecond_stamp(now))
        try:
            response = transport.fetch(
                request,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            )
            completed_at = clock().astimezone(timezone.utc)
            if completed_at >= deadline:
                terminal(
                    "capture overall deadline expired during request",
                    "CAPTURE_WINDOW_CLOSED",
                )
            if completed_at >= expiry:
                terminal(
                    "source-access authorization expired during request",
                    "CAPTURE_WINDOW_CLOSED",
                )
            if response.status in {408, 425, 429, 500, 502, 503, 504}:
                try:
                    _retry_after_seconds(
                        response.headers.get("retry-after"), clock()
                    )
                except OfficialSourceCaptureError as exc:
                    record = _attempt_record(
                        attempt=attempt,
                        requested_at_utc=response.requested_at_utc,
                        observed_at_utc=response.observed_at_utc,
                        outcome="NONRETRYABLE_FAILURE",
                        status=response.status,
                        error_kind="response_validation_failure",
                        retry_after_header=None,
                        retry_after_seconds=None,
                        backoff_seconds=0.0,
                        jitter_seconds=0.0,
                    )
                    persist_attempt_result(reservation, record)
                    attempts.append(record)
                    persist_terminal(attempts, "NONRETRYABLE_FAILURE")
                    raise OfficialSourceCaptureError(
                        "nonretryable Retry-After validation failure"
                    ) from exc
                failure = TransportFailure(
                    error_kind=f"http_{response.status}",
                    retryable=True,
                    requested_at_utc=response.requested_at_utc,
                    observed_at_utc=response.observed_at_utc,
                    status=response.status,
                    retry_after=response.headers.get("retry-after"),
                )
            else:
                try:
                    _validate_response(response, request, max_bytes=max_bytes)
                except OfficialSourceCaptureError as exc:
                    record = _attempt_record(
                        attempt=attempt,
                        requested_at_utc=response.requested_at_utc,
                        observed_at_utc=response.observed_at_utc,
                        outcome="NONRETRYABLE_FAILURE",
                        status=response.status,
                        error_kind="response_validation_failure",
                        retry_after_header=None,
                        retry_after_seconds=None,
                        backoff_seconds=0.0,
                        jitter_seconds=0.0,
                    )
                    persist_attempt_result(reservation, record)
                    attempts.append(record)
                    persist_terminal(attempts, "NONRETRYABLE_FAILURE")
                    raise OfficialSourceCaptureError(
                        "nonretryable response validation failure"
                    ) from exc
                record = _attempt_record(
                    attempt=attempt,
                    requested_at_utc=response.requested_at_utc,
                    observed_at_utc=response.observed_at_utc,
                    outcome="SUCCESS",
                    status=response.status,
                    error_kind=None,
                    retry_after_header=None,
                    retry_after_seconds=None,
                    backoff_seconds=0.0,
                    jitter_seconds=0.0,
                )
                persist_attempt_result(reservation, record)
                attempts.append(record)
                return response, attempts, last_attempt_started
        except TransportFailure as caught:
            failure = caught
        except OfficialSourceCaptureError:
            raise
        except Exception as exc:
            observed = _microsecond_stamp(clock())
            record = _attempt_record(
                attempt=attempt,
                requested_at_utc=_microsecond_stamp(last_attempt_started),
                observed_at_utc=observed,
                outcome="NONRETRYABLE_FAILURE",
                status=None,
                error_kind="nonretryable_transport_failure",
                retry_after_header=None,
                retry_after_seconds=None,
                backoff_seconds=0.0,
                jitter_seconds=0.0,
            )
            persist_attempt_result(reservation, record)
            attempts.append(record)
            persist_terminal(attempts, "NONRETRYABLE_FAILURE")
            raise OfficialSourceCaptureError(
                "nonretryable source request failure"
            ) from exc
        if not failure.retryable:
            record = _attempt_record(
                attempt=attempt,
                requested_at_utc=failure.requested_at_utc,
                observed_at_utc=failure.observed_at_utc,
                outcome="NONRETRYABLE_FAILURE",
                status=failure.status,
                error_kind=failure.error_kind,
                retry_after_header=None,
                retry_after_seconds=None,
                backoff_seconds=0.0,
                jitter_seconds=0.0,
            )
            persist_attempt_result(reservation, record)
            attempts.append(record)
            persist_terminal(attempts, "NONRETRYABLE_FAILURE")
            raise OfficialSourceCaptureError(
                f"nonretryable source request failure: {failure.error_kind}"
            ) from failure
        base_delay = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
        jitter_seconds = float(jitter(str(request["request_id"]), attempt, base_delay))
        if not 0 <= jitter_seconds <= base_delay:
            terminal("retry jitter is outside its safe bound", "POLICY_FAILURE")
        retry_after_seconds = _retry_after_seconds(
            failure.retry_after,
            _utc(failure.observed_at_utc, "failure observed_at_utc"),
        )
        required_delay = min(
            RETRY_MAX_SECONDS, base_delay + jitter_seconds
        )
        if retry_after_seconds is not None:
            required_delay = max(required_delay, retry_after_seconds)
        record = _attempt_record(
            attempt=attempt,
            requested_at_utc=failure.requested_at_utc,
            observed_at_utc=failure.observed_at_utc,
            outcome="RETRYABLE_FAILURE",
            status=failure.status,
            error_kind=failure.error_kind,
            retry_after_header=failure.retry_after,
            retry_after_seconds=retry_after_seconds,
            backoff_seconds=required_delay,
            jitter_seconds=jitter_seconds,
        )
        persist_attempt_result(reservation, record)
        attempts.append(record)
        if attempt >= maximum_attempts:
            persist_terminal(attempts, "ATTEMPT_BUDGET_EXHAUSTED")
            raise OfficialSourceCaptureError(
                f"transient source request exhausted after {attempt} attempts across lifetime"
            ) from failure
    raise OfficialSourceCaptureError("request attempt loop ended unexpectedly")


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
    canonical = {
        parsed.isoformat().replace("+00:00", "Z"),
        parsed.isoformat(timespec="microseconds").replace("+00:00", "Z"),
    }
    if parsed.tzinfo != timezone.utc or value not in canonical:
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
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        return
    descriptor = os.open(path, os.O_RDONLY | directory_flag)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
    attempts: list[dict[str, Any]], request_policy: Mapping[str, Any],
    capture_context: Mapping[str, Any],
    attempt_journal: Path,
    attempt_journal_relative: str,
) -> dict[str, Any]:
    reservation_files = _journal_file_bindings(
        attempt_journal, "reservation-*.json"
    )
    result_files = _journal_file_bindings(attempt_journal, "result-*.json")
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
        "request_policy": dict(request_policy),
        "attempts": attempts,
        "capture_window": {
            "capture_started_at_utc": capture_context[
                "capture_started_at_utc"
            ],
            "deadline_at_utc": capture_context["deadline_at_utc"],
            "authorization_valid_from_utc": capture_context[
                "authorization_valid_from_utc"
            ],
            "authorization_expires_at_utc": capture_context[
                "authorization_expires_at_utc"
            ],
        },
        "capture_context_sha256": sha256_bytes(
            canonical_json_bytes(capture_context)
        ),
        "attempt_journal": {
            "path": attempt_journal_relative,
            "context_sha256": sha256_file(
                attempt_journal / "context.json"
            ),
            "reservation_files": reservation_files,
            "result_files": result_files,
        },
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


def _validate_attempt_history(
    *,
    attempts: Any,
    policy: Mapping[str, Any],
    capture_window: Mapping[str, Any],
    require_success: bool,
) -> None:
    if not isinstance(attempts, list) or not attempts:
        raise OfficialSourceCaptureError("retained request attempts are missing")
    if set(capture_window) != {
        "capture_started_at_utc",
        "deadline_at_utc",
        "authorization_valid_from_utc",
        "authorization_expires_at_utc",
    }:
        raise OfficialSourceCaptureError("retained capture window differs")
    capture_started = _utc(
        capture_window["capture_started_at_utc"], "capture window start"
    )
    deadline = _utc(capture_window["deadline_at_utc"], "capture deadline")
    valid_from = _utc(
        capture_window["authorization_valid_from_utc"],
        "authorization valid-from",
    )
    expires = _utc(
        capture_window["authorization_expires_at_utc"],
        "authorization expiry",
    )
    if not valid_from <= capture_started < deadline or valid_from >= expires:
        raise OfficialSourceCaptureError("retained capture window chronology differs")
    expected_attempt_keys = {
        "attempt",
        "requested_at_utc",
        "observed_at_utc",
        "outcome",
        "status",
        "error_kind",
        "retry_after_header",
        "retry_after_seconds",
        "backoff_seconds",
        "jitter_seconds",
    }
    previous_requested: datetime | None = None
    previous_observed: datetime | None = None
    previous_backoff = 0.0
    for index, attempt in enumerate(attempts, 1):
        if not isinstance(attempt, Mapping) or set(attempt) != expected_attempt_keys:
            raise OfficialSourceCaptureError("retained request attempt schema differs")
        if isinstance(attempt.get("attempt"), bool) or attempt.get("attempt") != index:
            raise OfficialSourceCaptureError("retained request attempt order differs")
        requested = _utc(
            attempt.get("requested_at_utc"), "attempt requested_at_utc"
        )
        observed = _utc(
            attempt.get("observed_at_utc"), "attempt observed_at_utc"
        )
        if (
            observed < requested
            or requested < capture_started
            or requested < valid_from
            or observed >= deadline
            or observed >= expires
        ):
            raise OfficialSourceCaptureError(
                "retained request attempt falls outside its authorized capture window"
            )
        if previous_requested is not None and (
            requested
            < previous_requested
            + timedelta(seconds=float(policy["minimum_request_interval_seconds"]))
            or requested
            < previous_observed + timedelta(seconds=previous_backoff)
        ):
            raise OfficialSourceCaptureError(
                "retained request attempt violates pacing or backoff"
            )
        previous_requested = requested
        previous_observed = observed
        if attempt.get("outcome") == "SUCCESS":
            if (
                index != len(attempts)
                or attempt.get("status") != 200
                or attempt.get("error_kind") is not None
                or attempt.get("retry_after_header") is not None
                or attempt.get("retry_after_seconds") is not None
                or attempt.get("backoff_seconds") != 0.0
                or attempt.get("jitter_seconds") != 0.0
            ):
                raise OfficialSourceCaptureError(
                    "retained final request attempt differs"
                )
            previous_backoff = 0.0
            continue
        if attempt.get("outcome") == "NONRETRYABLE_FAILURE":
            status = attempt.get("status")
            error_kind = attempt.get("error_kind")
            valid_error = error_kind in {
                "tls_failure",
                "response_validation_failure",
                "nonretryable_transport_failure",
            } or (
                isinstance(status, int)
                and status not in {200, 408, 425, 429, 500, 502, 503, 504}
                and error_kind == f"http_{status}"
            )
            if (
                index != len(attempts)
                or not valid_error
                or attempt.get("retry_after_header") is not None
                or attempt.get("retry_after_seconds") is not None
                or attempt.get("backoff_seconds") != 0.0
                or attempt.get("jitter_seconds") != 0.0
            ):
                raise OfficialSourceCaptureError(
                    "retained nonretryable request attempt differs"
                )
            previous_backoff = 0.0
            continue
        status = attempt.get("status")
        error_kind = attempt.get("error_kind")
        backoff = attempt.get("backoff_seconds")
        retry_after = attempt.get("retry_after_seconds")
        retry_after_header = attempt.get("retry_after_header")
        jitter = attempt.get("jitter_seconds")
        valid_failure = (
            status is None and error_kind == "transport_io"
        ) or (
            status in {408, 425, 429, 500, 502, 503, 504}
            and error_kind == f"http_{status}"
        )
        if (
            attempt.get("outcome") != "RETRYABLE_FAILURE"
            or not valid_failure
            or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in (backoff, jitter)
            )
            or backoff <= 0
            or jitter < 0
            or (
                retry_after_header is not None
                and not isinstance(retry_after_header, str)
            )
            or (
                retry_after is not None
                and (
                    isinstance(retry_after, bool)
                    or not isinstance(retry_after, (int, float))
                    or not math.isfinite(float(retry_after))
                    or retry_after < 0
                )
            )
        ):
            raise OfficialSourceCaptureError(
                "retained retryable request attempt differs"
            )
        expected_retry_after = _retry_after_seconds(
            retry_after_header, observed
        )
        if retry_after != expected_retry_after:
            raise OfficialSourceCaptureError(
                "retained Retry-After value cannot be recomputed"
            )
        base = min(
            float(policy["retry_max_seconds"]),
            float(policy["retry_base_seconds"]) * (2 ** (index - 1)),
        )
        if jitter > base:
            raise OfficialSourceCaptureError("retained retry jitter differs")
        expected_backoff = min(
            float(policy["retry_max_seconds"]), base + float(jitter)
        )
        if retry_after is not None:
            expected_backoff = max(expected_backoff, float(retry_after))
        if not math.isclose(float(backoff), expected_backoff, abs_tol=1e-9):
            raise OfficialSourceCaptureError(
                "retained retry backoff cannot be recomputed"
            )
        previous_backoff = float(backoff)
    if len(attempts) > policy["maximum_attempts"]:
        raise OfficialSourceCaptureError("retained request attempts exceed policy")
    if require_success and attempts[-1].get("outcome") != "SUCCESS":
        raise OfficialSourceCaptureError("retained final request attempt differs")


def _verify_receipt(
    root: Path,
    receipt: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    max_bytes: int,
) -> bytes:
    receipt_keys = {
        "schema_version", "authorization", "season", "research_only",
        "betting_authorized", "model_fitting_performed", "probabilities_generated",
        "protected_data", "request", "response", "runtime_attestation_sha256",
        "source_access_authorization_id", "source_access_authorization_sha256",
        "source_bundle_sha256", "request_policy", "attempts",
        "capture_window", "capture_context_sha256", "attempt_journal",
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
    policy = receipt.get("request_policy")
    if (
        not isinstance(policy, Mapping)
        or set(policy)
        != {
            "minimum_request_interval_seconds",
            "maximum_attempts",
            "retry_base_seconds",
            "retry_max_seconds",
            "overall_timeout_seconds",
        }
        or policy.get("retry_base_seconds") != RETRY_BASE_SECONDS
        or policy.get("retry_max_seconds") != RETRY_MAX_SECONDS
    ):
        raise OfficialSourceCaptureError("retained request policy differs")
    _validate_operational_policy(
        timeout_seconds=30.0,
        minimum_request_interval_seconds=policy.get(
            "minimum_request_interval_seconds"
        ),
        maximum_attempts=policy.get("maximum_attempts"),
        overall_timeout_seconds=policy.get("overall_timeout_seconds"),
    )
    attempts = receipt.get("attempts")
    capture_window = receipt.get("capture_window")
    if not isinstance(capture_window, Mapping):
        raise OfficialSourceCaptureError("retained capture window differs")
    _validate_attempt_history(
        attempts=attempts,
        policy=policy,
        capture_window=capture_window,
        require_success=True,
    )

    context_path = root / "capture_context.json"
    if not context_path.is_file() or context_path.is_symlink():
        raise OfficialSourceCaptureError("retained capture context is missing or unsafe")
    try:
        context = json.loads(context_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialSourceCaptureError("retained capture context is invalid") from exc
    if (
        not isinstance(context, Mapping)
        or set(context) != {
            "schema_version", "status", "plan_sha256",
            "runtime_attestation_sha256", "source_access_authorization_id",
            "source_access_authorization_sha256", "source_bundle_sha256",
            "request_policy", "capture_started_at_utc", "deadline_at_utc",
            "authorization_valid_from_utc", "authorization_expires_at_utc",
        }
        or context.get("schema_version") != CAPTURE_CONTEXT_SCHEMA
        or context.get("status")
        != "ACTIVE_OR_COMPLETE_IMMUTABLE_CAPTURE_CONTEXT"
        or sha256_file(context_path) != receipt.get("capture_context_sha256")
        or context.get("runtime_attestation_sha256")
        != receipt.get("runtime_attestation_sha256")
        or context.get("source_access_authorization_id")
        != receipt.get("source_access_authorization_id")
        or context.get("source_access_authorization_sha256")
        != receipt.get("source_access_authorization_sha256")
        or context.get("source_bundle_sha256")
        != receipt.get("source_bundle_sha256")
        or context.get("request_policy") != policy
        or {
            key: context.get(key)
            for key in capture_window
        }
        != dict(capture_window)
    ):
        raise OfficialSourceCaptureError("retained capture context binding differs")
    started = _utc(context.get("capture_started_at_utc"), "capture context start")
    deadline = _utc(context.get("deadline_at_utc"), "capture context deadline")
    if deadline != started + timedelta(seconds=float(policy["overall_timeout_seconds"])):
        raise OfficialSourceCaptureError("retained capture context deadline differs")

    journal_binding = receipt.get("attempt_journal")
    if not isinstance(journal_binding, Mapping) or set(journal_binding) != {
        "path", "context_sha256", "reservation_files", "result_files"
    }:
        raise OfficialSourceCaptureError("retained attempt journal binding differs")
    relative_journal = journal_binding.get("path")
    if (
        not isinstance(relative_journal, str)
        or Path(relative_journal).is_absolute()
        or ".." in Path(relative_journal).parts
    ):
        raise OfficialSourceCaptureError("retained attempt journal path is unsafe")
    journal = root / relative_journal
    if not journal.is_dir() or journal.is_symlink() or (journal / "terminal.json").exists():
        raise OfficialSourceCaptureError("retained successful attempt journal differs")
    context_record = journal / "context.json"
    if (
        not context_record.is_file()
        or context_record.is_symlink()
        or sha256_file(context_record) != journal_binding.get("context_sha256")
    ):
        raise OfficialSourceCaptureError("retained attempt journal context differs")
    expected_request_context = _request_context(
        run_context_sha256=str(receipt.get("capture_context_sha256")),
        request=request,
    )
    try:
        observed_request_context = json.loads(context_record.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialSourceCaptureError("retained attempt journal context differs") from exc
    if observed_request_context != expected_request_context:
        raise OfficialSourceCaptureError("retained attempt journal context differs")
    reservation_files = journal_binding.get("reservation_files")
    result_files = journal_binding.get("result_files")
    if (
        not isinstance(reservation_files, list)
        or not isinstance(result_files, list)
        or len(reservation_files) != len(attempts)
        or len(result_files) != len(attempts)
    ):
        raise OfficialSourceCaptureError("retained attempt journal files differ")
    journal_attempts: list[dict[str, Any]] = []
    expected_journal_files = {"context.json"}
    request_context_sha256 = sha256_bytes(canonical_json_bytes(expected_request_context))
    reservation_paths: list[Path] = []
    reservation_times: list[datetime] = []
    for index, binding in enumerate(reservation_files, 1):
        name = f"reservation-{index:04d}.json"
        if (
            not isinstance(binding, Mapping)
            or set(binding) != {"path", "sha256"}
            or binding.get("path") != name
        ):
            raise OfficialSourceCaptureError("retained attempt journal files differ")
        path = journal / name
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256_file(path) != binding.get("sha256")
        ):
            raise OfficialSourceCaptureError("retained attempt journal files differ")
        try:
            reservation = json.loads(path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OfficialSourceCaptureError("retained attempt reservation differs") from exc
        if (
            not isinstance(reservation, Mapping)
            or set(reservation) != {
                "schema_version", "context_sha256", "attempt",
                "reserved_at_utc",
            }
            or reservation.get("schema_version") != ATTEMPT_RESERVATION_SCHEMA
            or reservation.get("context_sha256") != request_context_sha256
            or reservation.get("attempt") != index
        ):
            raise OfficialSourceCaptureError("retained attempt reservation differs")
        reservation_times.append(
            _utc(reservation.get("reserved_at_utc"), "attempt reservation")
        )
        reserved = reservation_times[-1]
        requested = _utc(
            attempts[index - 1]["requested_at_utc"], "attempt request"
        )
        if (
            reserved < started
            or reserved < _utc(
                capture_window["authorization_valid_from_utc"],
                "authorization valid-from",
            )
            or reserved >= deadline
            or reserved >= _utc(
                capture_window["authorization_expires_at_utc"],
                "authorization expiry",
            )
            or reserved > requested
        ):
            raise OfficialSourceCaptureError(
                "retained attempt reservation chronology differs"
            )
        if index > 1:
            prior = attempts[index - 2]
            if (
                reserved
                < _utc(prior["requested_at_utc"], "prior request")
                + timedelta(
                    seconds=float(policy["minimum_request_interval_seconds"])
                )
                or reserved
                < _utc(prior["observed_at_utc"], "prior response")
                + timedelta(seconds=float(prior["backoff_seconds"]))
            ):
                raise OfficialSourceCaptureError(
                    "retained attempt reservation violates pacing or backoff"
                )
        reservation_paths.append(path)
        expected_journal_files.add(name)
    for index, binding in enumerate(result_files, 1):
        name = f"result-{index:04d}.json"
        if (
            not isinstance(binding, Mapping)
            or set(binding) != {"path", "sha256"}
            or binding.get("path") != name
        ):
            raise OfficialSourceCaptureError("retained attempt journal files differ")
        path = journal / name
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256_file(path) != binding.get("sha256")
        ):
            raise OfficialSourceCaptureError("retained attempt journal files differ")
        try:
            result = json.loads(path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OfficialSourceCaptureError("retained attempt result differs") from exc
        if (
            not isinstance(result, Mapping)
            or set(result) != {
                "schema_version", "context_sha256", "reservation_sha256",
                "attempt",
            }
            or result.get("schema_version") != ATTEMPT_RESULT_SCHEMA
            or result.get("context_sha256") != request_context_sha256
            or result.get("reservation_sha256")
            != sha256_file(reservation_paths[index - 1])
            or not isinstance(result.get("attempt"), Mapping)
            or result["attempt"].get("attempt") != index
            or reservation_times[index - 1]
            > _utc(result["attempt"].get("requested_at_utc"), "attempt request")
        ):
            raise OfficialSourceCaptureError("retained attempt result differs")
        journal_attempts.append(dict(result["attempt"]))
        expected_journal_files.add(name)
    if journal_attempts != attempts or {
        path.name for path in journal.iterdir() if path.is_file()
    } != expected_journal_files or any(path.is_dir() for path in journal.iterdir()):
        raise OfficialSourceCaptureError("retained attempt journal exact file set differs")
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
    _validate_response(captured, request, max_bytes=max_bytes)
    if (
        attempts[-1]["requested_at_utc"] != response["requested_at_utc"]
        or attempts[-1]["observed_at_utc"] != response["observed_at_utc"]
    ):
        raise OfficialSourceCaptureError("retained successful attempt differs")
    return raw


def capture_schedule(
    *, output_dir: Path, runtime: RuntimeAuthorization,
    source_access: VerifiedHistoricalSourceAccess, source_bundle_sha256: str,
    transport: Transport, timeout_seconds: float = 30.0,
    minimum_request_interval_seconds: float = MIN_REQUEST_INTERVAL_SECONDS,
    maximum_attempts: int = MAX_REQUEST_ATTEMPTS,
    overall_timeout_seconds: float = DEFAULT_SCHEDULE_TIMEOUT_SECONDS,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
    jitter: Jitter | None = None,
    disk_usage: DiskUsage | None = None,
) -> dict[str, Any]:
    _validate_operational_policy(
        timeout_seconds=timeout_seconds,
        minimum_request_interval_seconds=minimum_request_interval_seconds,
        maximum_attempts=maximum_attempts,
        overall_timeout_seconds=overall_timeout_seconds,
    )
    _require_source_access(runtime, source_access, source_bundle_sha256)
    active_clock = clock or _clock_now
    active_sleeper = sleeper or time.sleep
    active_jitter = jitter or _deterministic_jitter
    active_disk_usage = disk_usage or shutil.disk_usage
    request_policy = _runtime_policy(
        minimum_request_interval_seconds=minimum_request_interval_seconds,
        maximum_attempts=maximum_attempts,
        overall_timeout_seconds=overall_timeout_seconds,
    )
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
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(output.name + ".staging")
    _storage_preflight(
        work_path=staging,
        output_path=output,
        required_bytes=SCHEDULE_MAX_RESPONSE_BYTES,
        disk_usage=active_disk_usage,
    )
    lock_path = output.with_name("." + output.name + ".capture.lock")
    with _exclusive_writer(lock_path):
        if output.exists():
            raise OfficialSourceCaptureError(
                "schedule output appeared after preflight; explicit review required"
            )
        if staging.exists() and (not staging.is_dir() or staging.is_symlink()):
            raise OfficialSourceCaptureError("schedule staging directory is unsafe")
        staging.mkdir(parents=True, exist_ok=True)
        capture_context, deadline = _prepare_capture_context(
            path=staging / "capture_context.json",
            plan_sha256=sha256_bytes(canonical_json_bytes(request)),
            runtime=runtime,
            source_access=source_access,
            source_bundle_sha256=source_bundle_sha256,
            request_policy=request_policy,
            clock=active_clock,
        )
        capture_context_sha256 = sha256_bytes(
            canonical_json_bytes(capture_context)
        )
        journal = staging / "attempts"
        existing_attempts = _load_request_journal(
            journal=journal,
            run_context=capture_context,
            run_context_sha256=capture_context_sha256,
            request=request,
        )
        request_context_sha256 = sha256_file(journal / "context.json")

        def reserve_attempt(index: int, reserved_at_utc: str) -> Path:
            return _reserve_attempt(
                journal=journal,
                context_sha256=request_context_sha256,
                attempt=index,
                reserved_at_utc=reserved_at_utc,
            )

        def persist_attempt_result(
            reservation: Path, value: Mapping[str, Any]
        ) -> None:
            _persist_attempt_result(
                journal=journal,
                context_sha256=request_context_sha256,
                reservation=reservation,
                attempt=value,
            )

        def persist_terminal(
            values: list[dict[str, Any]], reason: str
        ) -> None:
            _persist_terminal(
                journal=journal,
                context_sha256=request_context_sha256,
                reason=reason,
            )

        response, attempts, _ = _fetch_with_policy(
            request=request,
            transport=transport,
            timeout_seconds=timeout_seconds,
            max_bytes=SCHEDULE_MAX_RESPONSE_BYTES,
            source_access=source_access,
            deadline=deadline,
            minimum_request_interval_seconds=minimum_request_interval_seconds,
            maximum_attempts=maximum_attempts,
            clock=active_clock,
            sleeper=active_sleeper,
            jitter=active_jitter,
            last_attempt_started=None,
            existing_attempts=existing_attempts,
            reserve_attempt=reserve_attempt,
            persist_attempt_result=persist_attempt_result,
            persist_terminal=persist_terminal,
        )
        _write_new(staging / "response.json", response.body)
        receipt = _receipt(
            request=request, response=response, runtime=runtime,
            source_access=source_access,
            source_bundle_sha256=source_bundle_sha256,
            body_path="response.json", attempts=attempts,
            request_policy=request_policy,
            capture_context=capture_context,
            attempt_journal=journal,
            attempt_journal_relative="attempts",
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
            "source_access_authorization_id": (
                source_access.authorization_id
            ),
            "source_access_authorization_sha256": (
                source_access.authorization_file_sha256
            ),
            "source_bundle_sha256": _sha(
                source_bundle_sha256, "source bundle"
            ),
            "request": request,
            "body_sha256": sha256_bytes(response.body),
            "receipt_sha256": sha256_bytes(
                canonical_json_bytes(receipt)
            ),
            "observed_capture_digest": None,
        }
        manifest["observed_capture_digest"] = _manifest_digest(manifest)
        _write_new(staging / "manifest.json", canonical_json_bytes(manifest))
        _fsync_directory(staging)
        os.replace(staging, output)
        _fsync_directory(output.parent)
        return manifest


def verify_schedule_capture(root: Path, expected_digest: str | None = None) -> dict[str, Any]:
    value = _safe_root(root, must_exist=True)
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
    raw = _verify_receipt(
        value,
        receipt,
        request,
        max_bytes=SCHEDULE_MAX_RESPONSE_BYTES,
    )
    journal = receipt.get("attempt_journal") or {}
    expected_files = {
        "manifest.json", "receipt.json", "response.json",
        "capture_context.json",
        f"{journal.get('path')}/context.json",
    }
    for field in ("reservation_files", "result_files"):
        for binding in journal.get(field, []):
            expected_files.add(f"{journal.get('path')}/{binding.get('path')}")
    if _exact_files(value) != expected_files:
        raise OfficialSourceCaptureError("schedule capture exact file set differs")
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
        group_date = _official_2023_date(
            group.get("date"), "official schedule date-group date"
        )
        for raw in group["games"]:
            if not isinstance(raw, Mapping) or raw.get("gameType") != "R":
                raise OfficialSourceCaptureError("official schedule game type differs")
            official_date = _official_2023_date(
                raw.get("officialDate"), "official schedule game officialDate"
            )
            game_pk = raw.get("gamePk")
            if isinstance(game_pk, bool) or not isinstance(game_pk, int) or game_pk <= 0:
                raise OfficialSourceCaptureError("official schedule gamePk is invalid")
            teams = raw.get("teams") or {}
            away = ((teams.get("away") or {}).get("team") or {}).get("id")
            home = ((teams.get("home") or {}).get("team") or {}).get("id")
            if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (away, home)) or away == home:
                raise OfficialSourceCaptureError("official schedule team identity is invalid")
            # MLB may list a postponed, suspended, or resumed game under a
            # schedule-group date that differs from its canonical
            # ``officialDate``. Retain both distinct source fields. The final
            # feed must confirm ``schedule_official_date`` before release.
            candidate = {
                "game_pk": game_pk,
                "schedule_official_date": official_date.isoformat(),
                "away_team_id": away,
                "home_team_id": home,
                "schedule_group_dates": [group_date.isoformat()],
            }
            prior = candidates.get(game_pk)
            if prior is None:
                candidates[game_pk] = candidate
                continue
            for field in (
                "game_pk",
                "schedule_official_date",
                "away_team_id",
                "home_team_id",
            ):
                if prior.get(field) != candidate[field]:
                    raise OfficialSourceCaptureError(
                        "repeated schedule game identity contradicts"
                    )
            group_text = group_date.isoformat()
            prior_group_dates = prior.get("schedule_group_dates")
            if (
                not isinstance(prior_group_dates, list)
                or group_text in prior_group_dates
            ):
                raise OfficialSourceCaptureError(
                    "repeated schedule listing is not uniquely attributable"
                )
            prior_group_dates.append(group_text)
            prior_group_dates.sort()
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


def _official_2023_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise OfficialSourceCaptureError(f"{label} is not a canonical 2023 date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise OfficialSourceCaptureError(
            f"{label} is not a canonical 2023 date"
        ) from exc
    if parsed.isoformat() != value or parsed.year != 2023:
        raise OfficialSourceCaptureError(f"{label} is not a canonical 2023 date")
    return parsed


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


def _preflight_resumable_work(
    *,
    work: Path,
    requests: Iterable[Mapping[str, Any]],
    runtime: RuntimeAuthorization,
    source_access: VerifiedHistoricalSourceAccess,
    source_bundle_sha256: str,
    request_policy: Mapping[str, Any],
    capture_context: Mapping[str, Any],
    capture_context_sha256: str,
) -> set[str]:
    if (work / "manifest.json").exists():
        raise OfficialSourceCaptureError(
            "interrupted finalization requires explicit review"
        )
    allowed_files = {"plan.json", "capture_context.json"}
    allowed_directories = {"feeds"}
    completed: set[str] = set()
    for request in requests:
        request_id = str(request["request_id"])
        base = work / "feeds" / request_id
        allowed_directories.add(f"feeds/{request_id}")
        active_journal = work / "feeds" / (".attempt-" + request_id)
        raw_path = base / "response.json"
        receipt_path = base / "receipt.json"
        if raw_path.exists() or receipt_path.exists():
            if not (raw_path.is_file() and receipt_path.is_file()):
                raise OfficialSourceCaptureError(
                    "partial retained feed pair is contradictory"
                )
            receipt = json.loads(receipt_path.read_bytes())
            _verify_receipt(
                work,
                receipt,
                request,
                max_bytes=FEED_MAX_RESPONSE_BYTES,
            )
            if receipt.get("runtime_attestation_sha256") != runtime.attestation_sha256:
                raise OfficialSourceCaptureError(
                    "resumed feed used a different runtime"
                )
            if receipt.get("source_bundle_sha256") != source_bundle_sha256:
                raise OfficialSourceCaptureError(
                    "resumed feed used different source bytes"
                )
            if (
                receipt.get("source_access_authorization_sha256")
                != source_access.authorization_file_sha256
            ):
                raise OfficialSourceCaptureError(
                    "resumed feed used different source access"
                )
            if receipt.get("request_policy") != request_policy:
                raise OfficialSourceCaptureError(
                    "resumed feed used a different request policy"
                )
            allowed_files.update(
                {
                    f"feeds/{request_id}/response.json",
                    f"feeds/{request_id}/receipt.json",
                }
            )
            journal_binding = receipt.get("attempt_journal") or {}
            journal_relative = journal_binding.get("path")
            if not isinstance(journal_relative, str):
                raise OfficialSourceCaptureError(
                    "resumed feed attempt journal differs"
                )
            allowed_directories.add(journal_relative)
            allowed_files.add(f"{journal_relative}/context.json")
            for field in ("reservation_files", "result_files"):
                for binding in journal_binding.get(field, []):
                    allowed_files.add(
                        f"{journal_relative}/{binding.get('path')}"
                    )
            if active_journal.exists():
                raise OfficialSourceCaptureError(
                    "completed feed has contradictory active attempt journal"
                )
            completed.add(request_id)
        elif active_journal.exists():
            attempts = _load_request_journal(
                journal=active_journal,
                run_context=capture_context,
                run_context_sha256=capture_context_sha256,
                request=request,
            )
            if attempts and attempts[-1].get("outcome") != "RETRYABLE_FAILURE":
                raise OfficialSourceCaptureError(
                    "interrupted successful request requires explicit review"
                )
            relative_journal = active_journal.relative_to(work).as_posix()
            allowed_directories.add(relative_journal)
            allowed_files.add(f"{relative_journal}/context.json")
            for index in range(1, len(attempts) + 1):
                allowed_files.add(
                    f"{relative_journal}/reservation-{index:04d}.json"
                )
                allowed_files.add(
                    f"{relative_journal}/result-{index:04d}.json"
                )
    for path in work.rglob("*"):
        if path.is_symlink():
            raise OfficialSourceCaptureError("resumable work contains a symlink")
        relative = path.relative_to(work).as_posix()
        if path.name.startswith(".staging-"):
            raise OfficialSourceCaptureError(
                "interrupted request staging requires explicit review"
            )
        if path.is_file() and relative not in allowed_files:
            raise OfficialSourceCaptureError(
                "resumable work contains an unexpected file"
            )
        if path.is_dir() and relative not in allowed_directories:
            raise OfficialSourceCaptureError(
                "resumable work contains an unexpected directory"
            )
    return completed


def capture_feeds(
    *, plan: Mapping[str, Any], output_dir: Path, work_dir: Path,
    runtime: RuntimeAuthorization, source_access: VerifiedHistoricalSourceAccess,
    source_bundle_sha256: str, transport: Transport,
    timeout_seconds: float = 30.0,
    minimum_request_interval_seconds: float = MIN_REQUEST_INTERVAL_SECONDS,
    maximum_attempts: int = MAX_REQUEST_ATTEMPTS,
    overall_timeout_seconds: float = DEFAULT_FEED_TIMEOUT_SECONDS,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
    jitter: Jitter | None = None,
    disk_usage: DiskUsage | None = None,
) -> dict[str, Any]:
    _validate_operational_policy(
        timeout_seconds=timeout_seconds,
        minimum_request_interval_seconds=minimum_request_interval_seconds,
        maximum_attempts=maximum_attempts,
        overall_timeout_seconds=overall_timeout_seconds,
    )
    _require_source_access(runtime, source_access, source_bundle_sha256)
    active_clock = clock or _clock_now
    active_sleeper = sleeper or time.sleep
    active_jitter = jitter or _deterministic_jitter
    active_disk_usage = disk_usage or shutil.disk_usage
    request_policy = _runtime_policy(
        minimum_request_interval_seconds=minimum_request_interval_seconds,
        maximum_attempts=maximum_attempts,
        overall_timeout_seconds=overall_timeout_seconds,
    )
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
    output.parent.mkdir(parents=True, exist_ok=True)
    work.parent.mkdir(parents=True, exist_ok=True)
    _storage_preflight(
        work_path=work,
        output_path=output,
        required_bytes=0,
        disk_usage=active_disk_usage,
    )
    output_lock = output.with_name("." + output.name + ".capture.lock")
    work_lock = work.with_name("." + work.name + ".capture.lock")
    with _exclusive_writer(output_lock), _exclusive_writer(work_lock):
        if output.exists():
            raise OfficialSourceCaptureError(
                "feed output appeared after preflight; explicit review required"
            )
        work.mkdir(parents=True, exist_ok=True)
        if not (work / "plan.json").exists():
            _write_new(work / "plan.json", canonical_json_bytes(plan))
        if (work / "plan.json").read_bytes() != canonical_json_bytes(plan):
            raise OfficialSourceCaptureError(
                "resumable work uses a different plan"
            )
        capture_context, deadline = _prepare_capture_context(
            path=work / "capture_context.json",
            plan_sha256=sha256_bytes(canonical_json_bytes(plan)),
            runtime=runtime,
            source_access=source_access,
            source_bundle_sha256=source_bundle_sha256,
            request_policy=request_policy,
            clock=active_clock,
        )
        capture_context_sha256 = sha256_bytes(
            canonical_json_bytes(capture_context)
        )
        completed = _preflight_resumable_work(
            work=work,
            requests=requests,
            runtime=runtime,
            source_access=source_access,
            source_bundle_sha256=source_bundle_sha256,
            request_policy=request_policy,
            capture_context=capture_context,
            capture_context_sha256=capture_context_sha256,
        )
        remaining = len(requests) - len(completed)
        _storage_preflight(
            work_path=work,
            output_path=output,
            required_bytes=remaining * FEED_MAX_RESPONSE_BYTES,
            disk_usage=active_disk_usage,
        )
        last_attempt_started: datetime | None = None
        for request in requests:
            request_id = request["request_id"]
            if request_id in completed:
                prior_receipt = json.loads(
                    (work / "feeds" / request_id / "receipt.json").read_bytes()
                )
                last_attempt_started = _utc(
                    prior_receipt["attempts"][-1]["requested_at_utc"],
                    "prior completed request timestamp",
                )
                continue
            base = work / "feeds" / request_id
            journal = work / "feeds" / (".attempt-" + request_id)
            existing_attempts = _load_request_journal(
                journal=journal,
                run_context=capture_context,
                run_context_sha256=capture_context_sha256,
                request=request,
            )
            request_context_sha256 = sha256_file(journal / "context.json")

            def reserve_attempt(index: int, reserved_at_utc: str) -> Path:
                return _reserve_attempt(
                    journal=journal,
                    context_sha256=request_context_sha256,
                    attempt=index,
                    reserved_at_utc=reserved_at_utc,
                )

            def persist_attempt_result(
                reservation: Path, value: Mapping[str, Any]
            ) -> None:
                _persist_attempt_result(
                    journal=journal,
                    context_sha256=request_context_sha256,
                    reservation=reservation,
                    attempt=value,
                )

            def persist_terminal(
                values: list[dict[str, Any]], reason: str
            ) -> None:
                _persist_terminal(
                    journal=journal,
                    context_sha256=request_context_sha256,
                    reason=reason,
                )

            response, attempts, last_attempt_started = _fetch_with_policy(
                request=request,
                transport=transport,
                timeout_seconds=timeout_seconds,
                max_bytes=FEED_MAX_RESPONSE_BYTES,
                source_access=source_access,
                deadline=deadline,
                minimum_request_interval_seconds=(
                    minimum_request_interval_seconds
                ),
                maximum_attempts=maximum_attempts,
                clock=active_clock,
                sleeper=active_sleeper,
                jitter=active_jitter,
                last_attempt_started=last_attempt_started,
                existing_attempts=existing_attempts,
                reserve_attempt=reserve_attempt,
                persist_attempt_result=persist_attempt_result,
                persist_terminal=persist_terminal,
            )
            relative = f"feeds/{request_id}/response.json"
            receipt = _receipt(
                request=request, response=response, runtime=runtime,
                source_access=source_access,
                source_bundle_sha256=source_bundle_sha256,
                body_path=relative, attempts=attempts,
                request_policy=request_policy,
                capture_context=capture_context,
                attempt_journal=journal,
                attempt_journal_relative=f"feeds/{request_id}/attempts",
            )
            # A directory rename publishes the raw/receipt pair together.  A
            # hard crash can leave an inert staging directory, but never a
            # half-valid authoritative pair.
            request_staging = base.with_name(".staging-" + request_id)
            if request_staging.exists():
                raise OfficialSourceCaptureError(
                    "interrupted request staging requires explicit review"
                )
            request_staging.mkdir(parents=True)
            _write_new(request_staging / "response.json", response.body)
            _write_new(
                request_staging / "receipt.json", canonical_json_bytes(receipt)
            )
            os.replace(journal, request_staging / "attempts")
            _fsync_directory(request_staging)
            os.replace(request_staging, base)
            _fsync_directory(base.parent)
        entries = []
        for request in requests:
            base = work / "feeds" / request["request_id"]
            raw = _verify_receipt(
                work,
                json.loads((base / "receipt.json").read_bytes()),
                request,
                max_bytes=FEED_MAX_RESPONSE_BYTES,
            )
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
            "source_bundle_sha256": _sha(
                source_bundle_sha256, "source bundle"
            ),
            "game_count": len(entries),
            "entries": entries,
            "observed_capture_digest": None,
        }
        manifest["observed_capture_digest"] = _manifest_digest(manifest)
        _write_new(work / "manifest.json", canonical_json_bytes(manifest))
        _fsync_directory(work)
        os.replace(work, output)
        _fsync_directory(output.parent)
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
    expected_files = {"manifest.json", "plan.json", "capture_context.json"}
    entries = []
    prior_attempt_started: datetime | None = None
    for request in requests:
        base = value / "feeds" / request["request_id"]
        receipt = json.loads((base / "receipt.json").read_bytes())
        raw = _verify_receipt(
            value,
            receipt,
            request,
            max_bytes=FEED_MAX_RESPONSE_BYTES,
        )
        journal_binding = receipt.get("attempt_journal") or {}
        journal_relative = journal_binding.get("path")
        expected_files.add(f"feeds/{request['request_id']}/response.json")
        expected_files.add(f"feeds/{request['request_id']}/receipt.json")
        expected_files.add(f"{journal_relative}/context.json")
        for field in ("reservation_files", "result_files"):
            for binding in journal_binding.get(field, []):
                expected_files.add(f"{journal_relative}/{binding.get('path')}")
        attempts = receipt["attempts"]
        first_attempt_started = _utc(
            attempts[0]["requested_at_utc"], "request pacing timestamp"
        )
        if prior_attempt_started is not None and first_attempt_started < (
            prior_attempt_started
            + timedelta(
                seconds=float(
                    receipt["request_policy"][
                        "minimum_request_interval_seconds"
                    ]
                )
            )
        ):
            raise OfficialSourceCaptureError(
                "retained feed requests violate global pacing"
            )
        prior_attempt_started = _utc(
            attempts[-1]["requested_at_utc"], "request pacing timestamp"
        )
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
    if _exact_files(value) != expected_files:
        raise OfficialSourceCaptureError("feed capture exact file set differs")
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
    schedule.add_argument(
        "--minimum-request-interval-seconds",
        default=MIN_REQUEST_INTERVAL_SECONDS,
        type=float,
    )
    schedule.add_argument(
        "--maximum-attempts", default=MAX_REQUEST_ATTEMPTS, type=int
    )
    schedule.add_argument(
        "--overall-timeout-seconds",
        default=DEFAULT_SCHEDULE_TIMEOUT_SECONDS,
        type=float,
    )

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
    feeds.add_argument(
        "--minimum-request-interval-seconds",
        default=MIN_REQUEST_INTERVAL_SECONDS,
        type=float,
    )
    feeds.add_argument(
        "--maximum-attempts", default=MAX_REQUEST_ATTEMPTS, type=int
    )
    feeds.add_argument(
        "--overall-timeout-seconds",
        default=DEFAULT_FEED_TIMEOUT_SECONDS,
        type=float,
    )

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
            minimum_request_interval_seconds=(
                args.minimum_request_interval_seconds
            ),
            maximum_attempts=args.maximum_attempts,
            overall_timeout_seconds=args.overall_timeout_seconds,
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
            minimum_request_interval_seconds=(
                args.minimum_request_interval_seconds
            ),
            maximum_attempts=args.maximum_attempts,
            overall_timeout_seconds=args.overall_timeout_seconds,
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
