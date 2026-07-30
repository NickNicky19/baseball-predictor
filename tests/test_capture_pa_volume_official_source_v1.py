from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.capture_pa_volume_official_source_v1 as capture
from scripts.capture_direct_batter_pa_source_transport_v2 import (
    CapturedResponse,
    RuntimeAuthorization,
)
from src.evaluation.pa_volume_historical_source_access_v1 import (
    VerifiedHistoricalSourceAccess,
)


def runtime() -> RuntimeAuthorization:
    return RuntimeAuthorization(
        "a" * 64, {"runtime": "synthetic"}, "b" * 64, "c" * 64
    )


def source_access(
    *, expires_at_utc: str = "2026-07-29T14:00:00.000000Z"
) -> VerifiedHistoricalSourceAccess:
    return VerifiedHistoricalSourceAccess(
        authorization_id="synthetic-test-authorization",
        authorization_path=Path("synthetic-authorization.json"),
        authorization_file_sha256="d" * 64,
        runtime_policy_sha256="c" * 64,
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        authorized_at_utc="2026-07-29T10:00:00.000000Z",
        valid_from_utc="2026-07-29T10:01:00.000000Z",
        expires_at_utc=expires_at_utc,
    )


def stamp() -> str:
    return "2026-07-29T12:00:00Z"


def clock_stamp(clock) -> str:
    return clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def stable_capture_clock(monkeypatch) -> None:
    current = [datetime(2026, 7, 29, 12, tzinfo=timezone.utc)]
    monkeypatch.setattr(
        capture,
        "_clock_now",
        lambda: current[0],
    )

    def advance(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    monkeypatch.setattr(capture.time, "sleep", advance)


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += timedelta(seconds=seconds)


class FakeTransport:
    def __init__(self, bodies: dict[str, bytes], fail_after: int | None = None, clock=None):
        self.bodies = bodies
        self.calls = 0
        self.max_bytes_seen: list[int] = []
        self.fail_after = fail_after
        self.clock = clock or capture._clock_now

    def fetch(self, request, *, timeout_seconds: float, max_bytes: int):
        self.calls += 1
        self.max_bytes_seen.append(max_bytes)
        if self.fail_after is not None and self.calls > self.fail_after:
            raise capture.OfficialSourceCaptureError("synthetic transport failure")
        body = self.bodies[request["full_url"]]
        observed = clock_stamp(self.clock)
        return CapturedResponse(
            200, body, {"content-type": "application/json", "content-encoding": "identity"},
            request["full_url"], observed, observed,
        )


class SequencedTransport:
    def __init__(
        self,
        statuses: list[int],
        bodies: dict[str, bytes],
        *,
        retry_after: str = "3",
        clock=None,
    ):
        self.statuses = list(statuses)
        self.bodies = bodies
        self.retry_after = retry_after
        self.calls = 0
        self.clock = clock or capture._clock_now

    def fetch(self, request, *, timeout_seconds: float, max_bytes: int):
        del timeout_seconds, max_bytes
        status = self.statuses[self.calls]
        self.calls += 1
        headers = {
            "content-type": "application/json",
            "content-encoding": "identity",
        }
        if status == 429:
            headers["retry-after"] = self.retry_after
        observed = clock_stamp(self.clock)
        return CapturedResponse(
            status,
            self.bodies[request["full_url"]],
            headers,
            request["full_url"],
            observed,
            observed,
        )


def schedule_body(count: int = 2) -> bytes:
    games = []
    for game_pk in range(1, count + 1):
        games.append({
            "gamePk": game_pk, "officialDate": "2023-03-30", "gameType": "R",
            "teams": {
                "away": {"team": {"id": 10 + game_pk}},
                "home": {"team": {"id": 20 + game_pk}},
            },
        })
    return json.dumps({"dates": [{"date": "2023-03-30", "games": games}]}).encode()


def test_schedule_capture_is_immutable_and_revalidates(tmp_path: Path) -> None:
    transport = FakeTransport({capture.SCHEDULE_FULL_URL: schedule_body()})
    root = tmp_path / "schedule"
    first = capture.capture_schedule(
        output_dir=root, runtime=runtime(), source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=transport,
    )
    second = capture.capture_schedule(
        output_dir=root, runtime=runtime(), source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=transport,
    )
    assert first == second and transport.calls == 1
    assert transport.max_bytes_seen == [capture.SCHEDULE_MAX_RESPONSE_BYTES]
    (root / "response.json").write_bytes(b"{}")
    with pytest.raises(capture.OfficialSourceCaptureError, match="body bytes differ"):
        capture.verify_schedule_capture(root)


def test_missing_or_rebound_source_access_never_calls_transport(
    tmp_path: Path,
) -> None:
    transport = FakeTransport({capture.SCHEDULE_FULL_URL: schedule_body()})
    with pytest.raises(capture.OfficialSourceCaptureError, match="source access"):
        capture.capture_schedule(
            output_dir=tmp_path / "missing-access",
            runtime=runtime(),
            source_access=None,  # type: ignore[arg-type]
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=transport,
        )
    assert transport.calls == 0

    rebound = source_access()
    rebound = VerifiedHistoricalSourceAccess(
        **{
            **rebound.__dict__,
            "source_bundle_sha256": "0" * 64,
        }
    )
    with pytest.raises(capture.OfficialSourceCaptureError, match="different runtime"):
        capture.capture_schedule(
            output_dir=tmp_path / "rebound-access",
            runtime=runtime(),
            source_access=rebound,
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=transport,
        )
    assert transport.calls == 0


def test_schedule_builds_exact_sorted_feed_plan(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(capture, "EXPECTED_GAMES", 2)
    root = tmp_path / "schedule"
    manifest = capture.capture_schedule(
        output_dir=root, runtime=runtime(), source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport({capture.SCHEDULE_FULL_URL: schedule_body()}),
    )
    plan = capture.build_feed_plan(
        schedule_capture_dir=root,
        expected_schedule_capture_digest=manifest["observed_capture_digest"],
    )
    assert [row["request_id"] for row in plan["requests"]] == ["game-1", "game-2"]
    assert plan["requests"][0]["expected"] == {
        "game_pk": 1, "away_team_id": 11, "home_team_id": 21,
    }


def _small_plan(tmp_path: Path, monkeypatch) -> dict:
    monkeypatch.setattr(capture, "EXPECTED_GAMES", 2)
    root = tmp_path / "schedule"
    manifest = capture.capture_schedule(
        output_dir=root, runtime=runtime(), source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport({capture.SCHEDULE_FULL_URL: schedule_body()}),
    )
    return capture.build_feed_plan(
        schedule_capture_dir=root,
        expected_schedule_capture_digest=manifest["observed_capture_digest"],
    )


def test_feed_capture_resumes_partial_work_and_finalizes_atomically(tmp_path: Path, monkeypatch) -> None:
    plan = _small_plan(tmp_path, monkeypatch)
    bodies = {row["full_url"]: json.dumps({"gamePk": row["expected"]["game_pk"]}).encode() for row in plan["requests"]}
    output, work = tmp_path / "final", tmp_path / "work"
    clock = FakeClock()
    interrupted_sleeps = 0

    def interrupt_after_retained_result(seconds: float) -> None:
        nonlocal interrupted_sleeps
        clock.sleep(seconds)
        interrupted_sleeps += 1
        if interrupted_sleeps == 2:
            raise KeyboardInterrupt("synthetic hard stop after retained result")

    with pytest.raises(KeyboardInterrupt, match="retained result"):
        capture.capture_feeds(
            plan=plan, output_dir=output, work_dir=work, runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=SequencedTransport([200, 503], bodies, clock=clock.now),
            clock=clock.now, sleeper=interrupt_after_retained_result,
            jitter=lambda _request_id, _attempt, _base: 0.0,
        )
    assert not output.exists()
    assert (work / "feeds/game-1/response.json").is_file()
    resumed = FakeTransport(bodies, clock=clock.now)
    manifest = capture.capture_feeds(
        plan=plan, output_dir=output, work_dir=work, runtime=runtime(),
        source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(), transport=resumed,
        clock=clock.now, sleeper=clock.sleep,
        jitter=lambda _request_id, _attempt, _base: 0.0,
    )
    assert manifest["game_count"] == 2 and resumed.calls == 1
    assert resumed.max_bytes_seen == [capture.FEED_MAX_RESPONSE_BYTES]
    assert not work.exists() and output.is_dir()
    assert capture.verify_feed_capture(output)["observed_capture_digest"] == manifest["observed_capture_digest"]


def test_repeated_schedule_identity_must_agree(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(capture, "EXPECTED_GAMES", 1)
    body = json.loads(schedule_body(1))
    duplicate = json.loads(json.dumps(body["dates"][0]["games"][0]))
    duplicate["teams"]["home"]["team"]["id"] = 99
    body["dates"][0]["games"].append(duplicate)
    root = tmp_path / "schedule"
    manifest = capture.capture_schedule(
        output_dir=root, runtime=runtime(), source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport({capture.SCHEDULE_FULL_URL: json.dumps(body).encode()}),
    )
    with pytest.raises(capture.OfficialSourceCaptureError, match="contradicts"):
        capture.build_feed_plan(
            schedule_capture_dir=root,
            expected_schedule_capture_digest=manifest["observed_capture_digest"],
        )


def test_non_2023_or_wrong_game_type_never_enters_plan(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(capture, "EXPECTED_GAMES", 1)
    body = json.loads(schedule_body(1))
    body["dates"][0]["games"][0]["gameType"] = "S"
    root = tmp_path / "schedule"
    manifest = capture.capture_schedule(
        output_dir=root, runtime=runtime(), source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport({capture.SCHEDULE_FULL_URL: json.dumps(body).encode()}),
    )
    with pytest.raises(capture.OfficialSourceCaptureError, match="type differs"):
        capture.build_feed_plan(
            schedule_capture_dir=root,
            expected_schedule_capture_digest=manifest["observed_capture_digest"],
        )


@pytest.mark.parametrize(
    ("group_date", "official_date", "message"),
    [
        ("2026-05-01", "2026-05-01", "canonical 2023 date"),
        ("2023-03-30", "2026-05-01", "canonical 2023 date"),
        ("2023-03-30", "2023-03-31", "contradicts"),
        ("2023-3-30", "2023-03-30", "canonical 2023 date"),
    ],
)
def test_schedule_dates_must_be_canonical_matching_2023_dates(
    tmp_path: Path, monkeypatch, group_date: str, official_date: str,
    message: str,
) -> None:
    monkeypatch.setattr(capture, "EXPECTED_GAMES", 1)
    body = json.loads(schedule_body(1))
    body["dates"][0]["date"] = group_date
    body["dates"][0]["games"][0]["officialDate"] = official_date
    root = tmp_path / "schedule"
    manifest = capture.capture_schedule(
        output_dir=root, runtime=runtime(), source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport(
            {capture.SCHEDULE_FULL_URL: json.dumps(body).encode()}
        ),
    )
    with pytest.raises(capture.OfficialSourceCaptureError, match=message):
        capture.build_feed_plan(
            schedule_capture_dir=root,
            expected_schedule_capture_digest=manifest[
                "observed_capture_digest"
            ],
        )


def test_extra_files_and_interrupted_staging_fail_closed(tmp_path: Path, monkeypatch) -> None:
    plan = _small_plan(tmp_path, monkeypatch)
    bodies = {row["full_url"]: b"{}" for row in plan["requests"]}
    output, work = tmp_path / "final", tmp_path / "work"
    (work / "feeds/.staging-game-1").mkdir(parents=True)
    with pytest.raises(capture.OfficialSourceCaptureError, match="explicit review"):
        capture.capture_feeds(
            plan=plan, output_dir=output, work_dir=work, runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(), transport=FakeTransport(bodies),
        )


def test_feed_requests_are_paced_and_retry_after_is_hash_bound(
    tmp_path: Path, monkeypatch
) -> None:
    plan = _small_plan(tmp_path, monkeypatch)
    bodies = {
        row["full_url"]: json.dumps(
            {"gamePk": row["expected"]["game_pk"]}
        ).encode()
        for row in plan["requests"]
    }
    clock = FakeClock()
    transport = SequencedTransport([429, 200, 200], bodies, clock=clock.now)
    output = tmp_path / "final"
    capture.capture_feeds(
        plan=plan,
        output_dir=output,
        work_dir=tmp_path / "work",
        runtime=runtime(),
        source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=transport,
        clock=clock.now,
        sleeper=clock.sleep,
        jitter=lambda _request_id, _attempt, _base: 0.0,
    )
    assert transport.calls == 3
    assert clock.sleeps == [3.0, 1.0]
    first_receipt = json.loads(
        (output / "feeds/game-1/receipt.json").read_bytes()
    )
    assert first_receipt["request_policy"] == {
        "minimum_request_interval_seconds": 1.0,
        "maximum_attempts": 4,
        "retry_base_seconds": 1.0,
        "retry_max_seconds": 30.0,
        "overall_timeout_seconds": 21600.0,
    }
    assert first_receipt["attempts"][0] == {
        "attempt": 1,
        "requested_at_utc": stamp(),
        "observed_at_utc": stamp(),
        "outcome": "RETRYABLE_FAILURE",
        "status": 429,
        "error_kind": "http_429",
        "retry_after_header": "3",
        "retry_after_seconds": 3.0,
        "backoff_seconds": 3.0,
        "jitter_seconds": 0.0,
    }
    assert first_receipt["attempts"][1]["outcome"] == "SUCCESS"
    assert "full_url" not in first_receipt["attempts"][0]


def test_expiry_and_deadline_stop_before_retry(
    tmp_path: Path, monkeypatch
) -> None:
    plan = _small_plan(tmp_path, monkeypatch)
    bodies = {row["full_url"]: b"{}" for row in plan["requests"]}
    clock = FakeClock()
    expiring = SequencedTransport([429], bodies, clock=clock.now)
    with pytest.raises(
        capture.OfficialSourceCaptureError,
        match="authorization",
    ):
        capture.capture_feeds(
            plan=plan,
            output_dir=tmp_path / "expiry-final",
            work_dir=tmp_path / "expiry-work",
            runtime=runtime(),
            source_access=source_access(
                expires_at_utc="2026-07-29T12:00:30.500000Z"
            ),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=expiring,
            clock=clock.now,
            sleeper=clock.sleep,
            jitter=lambda _request_id, _attempt, _base: 0.0,
        )
    assert expiring.calls == 1

    deadline_clock = FakeClock()
    deadline = SequencedTransport(
        [429], bodies, retry_after="61", clock=deadline_clock.now
    )
    with pytest.raises(
        capture.OfficialSourceCaptureError, match="deadline"
    ):
        capture.capture_feeds(
            plan=plan,
            output_dir=tmp_path / "deadline-final",
            work_dir=tmp_path / "deadline-work",
            runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=deadline,
            overall_timeout_seconds=60.0,
            clock=deadline_clock.now,
            sleeper=deadline_clock.sleep,
            jitter=lambda _request_id, _attempt, _base: 0.0,
        )
    assert deadline.calls == 1


def test_exponential_backoff_is_bounded_and_nonretryable_status_stops(
    tmp_path: Path,
) -> None:
    bodies = {capture.SCHEDULE_FULL_URL: schedule_body()}
    clock = FakeClock()
    retried = SequencedTransport([503, 503, 200], bodies, clock=clock.now)
    output = tmp_path / "schedule"
    capture.capture_schedule(
        output_dir=output,
        runtime=runtime(),
        source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=retried,
        clock=clock.now,
        sleeper=clock.sleep,
        jitter=lambda _request_id, _attempt, _base: 0.0,
    )
    assert clock.sleeps == [1.0, 2.0]
    receipt = json.loads((output / "receipt.json").read_bytes())
    assert [row["backoff_seconds"] for row in receipt["attempts"]] == [
        1.0,
        2.0,
        0.0,
    ]

    exhausted_clock = FakeClock()
    exhausted = SequencedTransport(
        [503, 503, 503, 503], bodies, clock=exhausted_clock.now
    )
    with pytest.raises(
        capture.OfficialSourceCaptureError, match="exhausted after 4 attempts"
    ):
        capture.capture_schedule(
            output_dir=tmp_path / "exhausted",
            runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=exhausted,
            clock=exhausted_clock.now,
            sleeper=exhausted_clock.sleep,
            jitter=lambda _request_id, _attempt, _base: 0.0,
        )
    assert exhausted.calls == 4
    assert not (tmp_path / "exhausted").exists()
    blocked_after_exhaustion = FakeTransport(bodies)
    with pytest.raises(
        capture.OfficialSourceCaptureError, match="explicit review"
    ):
        capture.capture_schedule(
            output_dir=tmp_path / "exhausted",
            runtime=runtime(), source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=blocked_after_exhaustion,
            clock=exhausted_clock.now,
            sleeper=exhausted_clock.sleep,
            jitter=lambda _request_id, _attempt, _base: 0.0,
        )
    assert blocked_after_exhaustion.calls == 0

    nonretryable = SequencedTransport([400], bodies)
    with pytest.raises(
        capture.OfficialSourceCaptureError, match="nonretryable"
    ):
        capture.capture_schedule(
            output_dir=tmp_path / "nonretryable",
            runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=nonretryable,
            clock=FakeClock().now,
        )
    assert nonretryable.calls == 1


def test_hard_crash_after_fetch_entry_consumes_reservation_and_blocks_restart(
    tmp_path: Path,
) -> None:
    class CrashInsideTransport:
        def __init__(self) -> None:
            self.calls = 0

        def fetch(self, request, *, timeout_seconds: float, max_bytes: int):
            del request, timeout_seconds, max_bytes
            self.calls += 1
            raise KeyboardInterrupt("synthetic process death inside transport")

    root = tmp_path / "crashed"
    crashed = CrashInsideTransport()
    clock = FakeClock()
    with pytest.raises(KeyboardInterrupt, match="process death"):
        capture.capture_schedule(
            output_dir=root,
            runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=crashed,
            clock=clock.now,
            sleeper=clock.sleep,
        )
    assert crashed.calls == 1
    journal = tmp_path / "crashed.staging" / "attempts"
    assert (journal / "reservation-0001.json").is_file()
    assert not (journal / "result-0001.json").exists()

    replacement = FakeTransport(
        {capture.SCHEDULE_FULL_URL: schedule_body()}, clock=clock.now
    )
    with pytest.raises(
        capture.OfficialSourceCaptureError, match="in-flight.*explicit review"
    ):
        capture.capture_schedule(
            output_dir=root,
            runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=replacement,
            clock=clock.now,
            sleeper=clock.sleep,
        )
    assert replacement.calls == 0
    terminal = json.loads((journal / "terminal.json").read_bytes())
    assert terminal["reason"] == "IN_FLIGHT_RESERVATION_UNRESOLVED"
    assert terminal["consumed_attempt_count"] == 1
    assert len(terminal["reservation_files"]) == 1
    assert terminal["result_files"] == []


def test_rehashed_reservation_and_result_rebinding_is_rejected(
    tmp_path: Path,
) -> None:
    output = tmp_path / "schedule"
    capture.capture_schedule(
        output_dir=output,
        runtime=runtime(),
        source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport({capture.SCHEDULE_FULL_URL: schedule_body()}),
    )
    reservation_path = output / "attempts" / "reservation-0001.json"
    reservation = json.loads(reservation_path.read_bytes())
    reservation["reserved_at_utc"] = "2026-07-29T12:00:01Z"
    reservation_path.write_bytes(capture.canonical_json_bytes(reservation))
    result_path = output / "attempts" / "result-0001.json"
    result = json.loads(result_path.read_bytes())
    result["reservation_sha256"] = capture.sha256_file(reservation_path)
    result_path.write_bytes(capture.canonical_json_bytes(result))
    receipt_path = output / "receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    receipt["attempt_journal"]["reservation_files"][0][
        "sha256"
    ] = capture.sha256_file(reservation_path)
    receipt["attempt_journal"]["result_files"][0][
        "sha256"
    ] = capture.sha256_file(result_path)
    receipt_path.write_bytes(capture.canonical_json_bytes(receipt))
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["receipt_sha256"] = capture.sha256_file(receipt_path)
    manifest["observed_capture_digest"] = capture._manifest_digest(manifest)
    manifest_path.write_bytes(capture.canonical_json_bytes(manifest))
    with pytest.raises(
        capture.OfficialSourceCaptureError, match="reservation"
    ):
        capture.verify_schedule_capture(output)


def test_expired_authorization_stops_before_first_request(tmp_path: Path) -> None:
    transport = FakeTransport(
        {capture.SCHEDULE_FULL_URL: schedule_body()}
    )
    with pytest.raises(
        capture.OfficialSourceCaptureError, match="authorization expires"
    ):
        capture.capture_schedule(
            output_dir=tmp_path / "schedule",
            runtime=runtime(),
            source_access=source_access(
                expires_at_utc="2026-07-29T12:00:00.000000Z"
            ),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=transport,
            clock=FakeClock().now,
        )
    assert transport.calls == 0


def test_authorization_must_contain_request_and_response(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    never_called = FakeTransport(
        {capture.SCHEDULE_FULL_URL: schedule_body()}
    )
    with pytest.raises(
        capture.OfficialSourceCaptureError,
        match="authorization cannot contain the next request timeout",
    ):
        capture.capture_schedule(
            output_dir=tmp_path / "timeout-not-contained",
            runtime=runtime(),
            source_access=source_access(
                expires_at_utc="2026-07-29T12:00:30.000000Z"
            ),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=never_called,
            clock=clock.now,
        )
    assert never_called.calls == 0

    class ExpiringDuringFetch(FakeTransport):
        def fetch(self, request, *, timeout_seconds: float, max_bytes: int):
            response = super().fetch(
                request,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            )
            clock.sleep(31.0)
            return response

    expires_during_fetch = ExpiringDuringFetch(
        {capture.SCHEDULE_FULL_URL: schedule_body()}
    )
    with pytest.raises(
        capture.OfficialSourceCaptureError,
        match="authorization expired during request",
    ):
        capture.capture_schedule(
            output_dir=tmp_path / "expired-during-request",
            runtime=runtime(),
            source_access=source_access(
                expires_at_utc="2026-07-29T12:00:30.500000Z"
            ),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=expires_during_fetch,
            clock=clock.now,
        )
    assert expires_during_fetch.calls == 1
    assert not (tmp_path / "expired-during-request").exists()


def test_retry_after_http_date_uses_injected_clock() -> None:
    clock = FakeClock()
    assert capture._retry_after_seconds(
        "Wed, 29 Jul 2026 12:00:09 GMT", clock.now()
    ) == 9.0


def test_storage_lock_and_finalization_ambiguity_fail_before_transport(
    tmp_path: Path, monkeypatch
) -> None:
    plan = _small_plan(tmp_path, monkeypatch)
    bodies = {row["full_url"]: b"{}" for row in plan["requests"]}
    work = tmp_path / "locked-work"
    lock = work.with_name("." + work.name + ".capture.lock")
    lock.write_text("stale", encoding="utf-8")
    locked_transport = FakeTransport(bodies)
    with pytest.raises(capture.OfficialSourceCaptureError, match="lock"):
        capture.capture_feeds(
            plan=plan,
            output_dir=tmp_path / "locked-final",
            work_dir=work,
            runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=locked_transport,
        )
    assert locked_transport.calls == 0

    finalization_work = tmp_path / "finalization-work"
    finalization_work.mkdir()
    (finalization_work / "plan.json").write_bytes(
        capture.canonical_json_bytes(plan)
    )
    (finalization_work / "manifest.json").write_text("{}", encoding="utf-8")
    finalization_transport = FakeTransport(bodies)
    with pytest.raises(
        capture.OfficialSourceCaptureError, match="finalization.*explicit review"
    ):
        capture.capture_feeds(
            plan=plan,
            output_dir=tmp_path / "finalization-final",
            work_dir=finalization_work,
            runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=finalization_transport,
        )
    assert finalization_transport.calls == 0


def test_output_created_during_lock_race_fails_before_transport(
    tmp_path: Path, monkeypatch
) -> None:
    plan = _small_plan(tmp_path, monkeypatch)
    schedule_output = tmp_path / "raced-schedule"

    @contextmanager
    def schedule_race(_lock_path):
        schedule_output.mkdir()
        yield

    monkeypatch.setattr(capture, "_exclusive_writer", schedule_race)
    schedule_transport = FakeTransport(
        {capture.SCHEDULE_FULL_URL: schedule_body()}
    )
    with pytest.raises(
        capture.OfficialSourceCaptureError,
        match="schedule output appeared after preflight",
    ):
        capture.capture_schedule(
            output_dir=schedule_output,
            runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=schedule_transport,
        )
    assert schedule_transport.calls == 0

    feed_output = tmp_path / "raced-feeds"
    lock_entries = 0

    @contextmanager
    def feed_race(_lock_path):
        nonlocal lock_entries
        lock_entries += 1
        if lock_entries == 1:
            feed_output.mkdir()
        yield

    monkeypatch.setattr(capture, "_exclusive_writer", feed_race)
    feed_transport = FakeTransport(
        {row["full_url"]: b"{}" for row in plan["requests"]}
    )
    with pytest.raises(
        capture.OfficialSourceCaptureError,
        match="feed output appeared after preflight",
    ):
        capture.capture_feeds(
            plan=plan,
            output_dir=feed_output,
            work_dir=tmp_path / "raced-work",
            runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(),
            transport=feed_transport,
        )
    assert feed_transport.calls == 0


def test_storage_preflight_rejects_cross_filesystem_and_low_space(
    monkeypatch,
) -> None:
    monkeypatch.setattr(capture, "_existing_ancestor", lambda path: path)
    monkeypatch.setattr(
        capture.os,
        "stat",
        lambda path: SimpleNamespace(st_dev=1 if Path(path).name == "work" else 2),
    )
    with pytest.raises(capture.OfficialSourceCaptureError, match="same filesystem"):
        capture._storage_preflight(
            work_path=Path("work"),
            output_path=Path("output/final"),
            required_bytes=1,
            disk_usage=lambda _path: SimpleNamespace(free=10**12),
        )

    monkeypatch.setattr(
        capture.os, "stat", lambda _path: SimpleNamespace(st_dev=1)
    )
    with pytest.raises(capture.OfficialSourceCaptureError, match="free-space"):
        capture._storage_preflight(
            work_path=Path("work"),
            output_path=Path("output/final"),
            required_bytes=10,
            disk_usage=lambda _path: SimpleNamespace(free=10),
        )


def test_rehashed_attempt_metadata_mutation_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    plan = _small_plan(tmp_path, monkeypatch)
    bodies = {row["full_url"]: b"{}" for row in plan["requests"]}
    output = tmp_path / "final"
    capture.capture_feeds(
        plan=plan,
        output_dir=output,
        work_dir=tmp_path / "work",
        runtime=runtime(),
        source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport(bodies),
    )
    receipt_path = output / "feeds/game-1/receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    receipt["attempts"][0]["error_kind"] = "fabricated"
    receipt_path.write_bytes(capture.canonical_json_bytes(receipt))
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["entries"][0]["receipt_sha256"] = capture.sha256_file(
        receipt_path
    )
    manifest["observed_capture_digest"] = capture._manifest_digest(manifest)
    manifest_path.write_bytes(capture.canonical_json_bytes(manifest))
    with pytest.raises(
        capture.OfficialSourceCaptureError, match="final request attempt"
    ):
        capture.verify_feed_capture(output)


@pytest.mark.parametrize("mutation", ["window", "pacing", "backoff", "retry_after"])
def test_rehashed_attempt_timing_and_backoff_mutations_are_rejected(
    tmp_path: Path, mutation: str,
) -> None:
    clock = FakeClock()
    output = tmp_path / mutation
    capture.capture_schedule(
        output_dir=output,
        runtime=runtime(),
        source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=SequencedTransport(
            [429, 200],
            {capture.SCHEDULE_FULL_URL: schedule_body()},
            clock=clock.now,
        ),
        clock=clock.now,
        sleeper=clock.sleep,
        jitter=lambda _request_id, _attempt, _base: 0.0,
    )
    receipt_path = output / "receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    if mutation == "window":
        receipt["attempts"][0]["requested_at_utc"] = (
            "2026-07-29T09:59:59Z"
        )
        receipt["attempts"][0]["observed_at_utc"] = (
            "2026-07-29T09:59:59Z"
        )
    elif mutation == "pacing":
        receipt["attempts"][1]["requested_at_utc"] = (
            receipt["attempts"][0]["requested_at_utc"]
        )
        receipt["attempts"][1]["observed_at_utc"] = (
            receipt["attempts"][0]["observed_at_utc"]
        )
    elif mutation == "backoff":
        receipt["attempts"][0]["backoff_seconds"] = 2.0
    else:
        receipt["attempts"][0]["retry_after_header"] = "2"
    changed_index = 2 if mutation == "pacing" else 1
    attempt_path = output / "attempts" / f"result-{changed_index:04d}.json"
    wrapper = json.loads(attempt_path.read_bytes())
    wrapper["attempt"] = receipt["attempts"][changed_index - 1]
    attempt_path.write_bytes(capture.canonical_json_bytes(wrapper))
    receipt["attempt_journal"]["result_files"][changed_index - 1][
        "sha256"
    ] = capture.sha256_file(attempt_path)
    receipt_path.write_bytes(capture.canonical_json_bytes(receipt))
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["receipt_sha256"] = capture.sha256_file(receipt_path)
    manifest["observed_capture_digest"] = capture._manifest_digest(manifest)
    manifest_path.write_bytes(capture.canonical_json_bytes(manifest))
    with pytest.raises(capture.OfficialSourceCaptureError):
        capture.verify_schedule_capture(output)
