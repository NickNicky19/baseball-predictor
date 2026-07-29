from __future__ import annotations

import json
from pathlib import Path

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


def source_access() -> VerifiedHistoricalSourceAccess:
    return VerifiedHistoricalSourceAccess(
        authorization_id="synthetic-test-authorization",
        authorization_path=Path("synthetic-authorization.json"),
        authorization_file_sha256="d" * 64,
        runtime_policy_sha256="c" * 64,
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        authorized_at_utc="2026-07-29T10:00:00.000000Z",
        valid_from_utc="2026-07-29T10:01:00.000000Z",
        expires_at_utc="2026-07-29T14:00:00.000000Z",
    )


def stamp() -> str:
    return "2026-07-29T12:00:00Z"


class FakeTransport:
    def __init__(self, bodies: dict[str, bytes], fail_after: int | None = None):
        self.bodies = bodies
        self.calls = 0
        self.fail_after = fail_after

    def fetch(self, request, *, timeout_seconds: float, max_bytes: int):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise capture.OfficialSourceCaptureError("synthetic transport failure")
        body = self.bodies[request["full_url"]]
        return CapturedResponse(
            200, body, {"content-type": "application/json", "content-encoding": "identity"},
            request["full_url"], stamp(), stamp(),
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
    with pytest.raises(capture.OfficialSourceCaptureError, match="synthetic"):
        capture.capture_feeds(
            plan=plan, output_dir=output, work_dir=work, runtime=runtime(),
            source_access=source_access(),
            source_bundle_sha256=capture.capture_source_bundle_sha256(), transport=FakeTransport(bodies, fail_after=1),
        )
    assert not output.exists()
    assert (work / "feeds/game-1/response.json").is_file()
    resumed = FakeTransport(bodies)
    manifest = capture.capture_feeds(
        plan=plan, output_dir=output, work_dir=work, runtime=runtime(),
        source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(), transport=resumed,
    )
    assert manifest["game_count"] == 2 and resumed.calls == 1
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
