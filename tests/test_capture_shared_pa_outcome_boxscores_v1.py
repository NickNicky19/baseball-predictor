from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.capture_shared_pa_outcome_boxscores_v1 as capture
from scripts.capture_direct_batter_pa_source_transport_v2 import (
    CapturedResponse,
    RuntimeAuthorization,
)
from src.evaluation.pa_volume_historical_source_access_v1 import (
    VerifiedHistoricalSourceAccess,
)
from src.evaluation.shared_pa_outcome_historical_source_access_v1 import (
    AUTHORIZED_ACTIONS,
    PROTECTED_BOUNDARIES,
    SCHEMA,
    SOURCE_SCOPE,
    STATUS,
    OutcomeHistoricalSourceAccessError,
    authorization_semantic_sha256,
    canonical_json_bytes,
    verify_outcome_historical_source_access_authorization,
)


def _schedule_index(count: int = 2) -> dict:
    return {
        "schema_version": capture.CERTIFIED_SCHEDULE_INDEX_SCHEMA,
        "season": 2023,
        "fields": [
            "game_pk",
            "official_date",
            "away_team_id",
            "home_team_id",
            "game_type",
        ],
        "games": [
            {
                "game_pk": index,
                "official_date": "2023-03-30",
                "away_team_id": 10 + index,
                "home_team_id": 20 + index,
                "game_type": "R",
            }
            for index in range(1, count + 1)
        ],
    }


def test_plan_reuses_exact_certified_schedule_without_schedule_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture, "EXPECTED_GAMES", 2)
    source = tmp_path / "schedule_index.json"
    source.write_bytes(canonical_json_bytes(_schedule_index()))
    plan = capture.build_boxscore_plan(certified_schedule_index=source)
    requests = capture.validate_boxscore_plan(plan)
    assert len(requests) == 2
    assert all(row["full_url"].endswith("/boxscore") for row in requests)
    assert all("?" not in row["full_url"] for row in requests)
    assert not any("schedule" in row["full_url"] for row in requests)
    assert plan["schedule_capture_digest"] == plan["certified_schedule_index_sha256"]


def _authorization(*, runtime: str, source: str, plan: str) -> dict:
    value = {
        "schema_version": SCHEMA,
        "authorization_id": "synthetic-user-authorization",
        "status": STATUS,
        "authorized_at_utc": "2026-07-31T12:00:00.000000Z",
        "valid_from_utc": "2026-07-31T12:01:00.000000Z",
        "expires_at_utc": "2026-08-01T12:00:00.000000Z",
        "research_only": True,
        "betting_authorized": False,
        "network_fetch_authorized": True,
        "source_scope": SOURCE_SCOPE,
        "authorized_actions": AUTHORIZED_ACTIONS,
        "protected_boundaries": PROTECTED_BOUNDARIES,
        "runtime_policy_sha256": runtime,
        "source_bundle_sha256": source,
        "request_plan_sha256": plan,
    }
    value["authorization_sha256"] = authorization_semantic_sha256(value)
    return value


def test_authorization_is_hash_bound_and_rejects_scope_expansion(
    tmp_path: Path,
) -> None:
    runtime, source, plan = "a" * 64, "b" * 64, "c" * 64
    path = tmp_path / "authorization.json"
    value = _authorization(runtime=runtime, source=source, plan=plan)
    path.write_bytes(canonical_json_bytes(value))
    digest = capture.sha256_file(path)
    verified = verify_outcome_historical_source_access_authorization(
        authorization_path=path,
        expected_authorization_sha256=digest,
        expected_runtime_policy_sha256=runtime,
        expected_source_bundle_sha256=source,
        expected_request_plan_sha256=plan,
        access_time_utc="2026-07-31T12:02:00.000000Z",
    )
    assert verified.request_plan_sha256 == plan

    widened = dict(value)
    widened["authorized_actions"] = {
        **AUTHORIZED_ACTIONS,
        "model_fitting": True,
    }
    widened["authorization_sha256"] = authorization_semantic_sha256(widened)
    path.write_bytes(canonical_json_bytes(widened))
    with pytest.raises(OutcomeHistoricalSourceAccessError, match="safety scope"):
        verify_outcome_historical_source_access_authorization(
            authorization_path=path,
            expected_authorization_sha256=capture.sha256_file(path),
            expected_runtime_policy_sha256=runtime,
            expected_source_bundle_sha256=source,
            expected_request_plan_sha256=plan,
            access_time_utc="2026-07-31T12:02:00.000000Z",
        )


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 31, 12, 2, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class _Transport:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.requested: list[datetime] = []

    def fetch(self, request, *, timeout_seconds: float, max_bytes: int):
        del timeout_seconds, max_bytes
        requested = self.clock.now()
        self.requested.append(requested)
        stamp = requested.isoformat().replace("+00:00", "Z")
        body = json.dumps({"teams": {"away": {}, "home": {}}}).encode()
        return CapturedResponse(
            status=200,
            body=body,
            headers={
                "content-type": "application/json",
                "content-encoding": "identity",
            },
            final_url=request["full_url"],
            requested_at_utc=stamp,
            observed_at_utc=stamp,
        )


def test_capture_is_distinct_raw_only_and_uses_1_10_second_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture, "EXPECTED_GAMES", 2)
    source = tmp_path / "schedule_index.json"
    source.write_bytes(canonical_json_bytes(_schedule_index()))
    plan = capture.build_boxscore_plan(certified_schedule_index=source)
    runtime = RuntimeAuthorization("a" * 64, {}, "b" * 64, "c" * 64)
    access = VerifiedHistoricalSourceAccess(
        authorization_id="synthetic",
        authorization_path=tmp_path / "authorization.json",
        authorization_file_sha256="d" * 64,
        runtime_policy_sha256="c" * 64,
        source_bundle_sha256=capture.source_bundle_sha256(),
        authorized_at_utc="2026-07-31T12:00:00.000000Z",
        valid_from_utc="2026-07-31T12:01:00.000000Z",
        expires_at_utc="2026-08-01T12:00:00.000000Z",
    )
    clock = _Clock()
    transport = _Transport(clock)
    manifest = capture.capture_boxscores(
        plan=plan,
        output_dir=tmp_path / "release",
        work_dir=tmp_path / "work",
        runtime=runtime,
        source_access=access,
        source_bundle_sha256=capture.source_bundle_sha256(),
        transport=transport,
        minimum_request_interval_seconds=1.10,
        overall_timeout_seconds=300.0,
        clock=clock.now,
        sleeper=clock.sleep,
    )
    assert manifest["schema_version"] == capture.CAPTURE_SCHEMA
    assert manifest["game_count"] == 2
    assert transport.requested[1] - transport.requested[0] >= timedelta(seconds=1.10)
    assert not (tmp_path / "release" / "features.json").exists()
    assert not (tmp_path / "release" / "predictions.json").exists()
    capture.verify_boxscore_capture(
        tmp_path / "release", manifest["observed_capture_digest"]
    )
