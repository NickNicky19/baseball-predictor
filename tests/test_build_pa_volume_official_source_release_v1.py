from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import scripts.build_pa_volume_official_source_release_v1 as release
import scripts.capture_pa_volume_official_source_v1 as capture
from scripts.capture_direct_batter_pa_source_transport_v2 import CapturedResponse, RuntimeAuthorization
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


class FakeTransport:
    def __init__(self, bodies: dict[str, bytes]): self.bodies = bodies
    def fetch(self, request, *, timeout_seconds: float, max_bytes: int):
        return CapturedResponse(
            200, self.bodies[request["full_url"]],
            {"content-type": "application/json", "content-encoding": "identity"},
            request["full_url"], "2026-07-29T12:00:00Z", "2026-07-29T12:00:00Z",
        )


def schedule_body() -> bytes:
    return json.dumps({"dates": [{"date": "2023-03-30", "games": [{
        "gamePk": 1, "officialDate": "2023-03-30", "gameType": "R",
        "teams": {"away": {"team": {"id": 10}}, "home": {"team": {"id": 20}}},
    }]}]}).encode()


def feed_body() -> bytes:
    teams = {}
    for side, base in (("away", 100), ("home", 200)):
        teams[side] = {"team": {"id": 10 if side == "away" else 20}, "players": {
            f"ID{base + slot}": {
                "person": {"id": base + slot}, "battingOrder": f"{slot}00",
                "stats": {"batting": {"plateAppearances": 4}},
            }
            for slot in range(1, 10)
        }}
    return json.dumps({
        "gamePk": 1,
        "gameData": {
            "datetime": {"officialDate": "2023-03-30"}, "game": {"type": "R"},
            "status": {"codedGameState": "F", "abstractGameState": "Final"},
            "teams": {"away": {"id": 10}, "home": {"id": 20}},
        },
        "liveData": {"boxscore": {"teams": teams}},
    }).encode()


def captured(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(capture, "EXPECTED_GAMES", 1)
    fixed_clock = lambda: datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    schedule_root = tmp_path / "schedule"
    schedule_manifest = capture.capture_schedule(
        output_dir=schedule_root, runtime=runtime(), source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport({capture.SCHEDULE_FULL_URL: schedule_body()}),
        clock=fixed_clock,
    )
    plan = capture.build_feed_plan(
        schedule_capture_dir=schedule_root,
        expected_schedule_capture_digest=schedule_manifest["observed_capture_digest"],
    )
    feed_root = tmp_path / "feeds"
    feed_manifest = capture.capture_feeds(
        plan=plan, output_dir=feed_root, work_dir=tmp_path / "work", runtime=runtime(),
        source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport({plan["requests"][0]["full_url"]: feed_body()}),
        clock=fixed_clock,
    )
    return schedule_root, schedule_manifest, feed_root, feed_manifest


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_two_stage_release_requires_external_anchor(tmp_path: Path, monkeypatch) -> None:
    schedule_root, schedule_manifest, feed_root, feed_manifest = captured(tmp_path, monkeypatch)
    lock = tmp_path / "requirements.lock"
    lock.write_text("example==1.0\n", encoding="utf-8")
    source_root = tmp_path / "source"
    manifest = release.build_source_release(
        schedule_capture_dir=schedule_root,
        expected_schedule_capture_digest=schedule_manifest["observed_capture_digest"],
        feed_capture_dir=feed_root,
        expected_feed_capture_digest=feed_manifest["observed_capture_digest"],
        dependency_lock_path=lock, output_dir=source_root,
    )
    assert manifest["status"] == "AWAITING_INDEPENDENT_EXTERNAL_VERIFICATION"
    assert manifest["game_count"] == 1 and manifest["row_count"] == 18
    verification = {
        "schema_version": release.EXTERNAL_VERIFICATION_SCHEMA,
        "status": "INDEPENDENT_SOURCE_RELEASE_VERIFIED",
        "source_manifest_sha256": digest(source_root / "source_manifest.json"),
        "projection_sha256": digest(source_root / "projection.json"),
        "schedule_capture_observed_digest": manifest["schedule_capture_observed_digest"],
        "feed_capture_observed_digest": manifest["feed_capture_observed_digest"],
        "source_access_authorization_id": manifest["source_access_authorization_id"],
        "source_access_authorization_sha256": manifest[
            "source_access_authorization_sha256"
        ],
        "dependency_lock_sha256": digest(lock),
        "reviewed_source_bundle_sha256": manifest["reviewed_source_bundle_sha256"],
        "protected_data": capture.PROTECTED,
    }
    verification_path = tmp_path / "external-verification.json"
    verification_path.write_bytes(release.canonical_json_bytes(verification))
    output = tmp_path / "pa.json"
    artifact = release.build_pa_artifact_after_external_verification(
        source_release_dir=source_root, external_verification_path=verification_path,
        expected_external_verification_sha256=digest(verification_path),
        dependency_lock_path=lock, output_path=output,
    )
    assert artifact["source"]["fit_rows"] == 18
    assert artifact["source"]["bindings"]["source_release_external_verification_sha256"] == digest(verification_path)


def test_rehashed_or_mutated_external_verification_fails(tmp_path: Path, monkeypatch) -> None:
    schedule_root, schedule_manifest, feed_root, feed_manifest = captured(tmp_path, monkeypatch)
    lock = tmp_path / "requirements.lock"
    lock.write_text("example==1.0\n", encoding="utf-8")
    source_root = tmp_path / "source"
    release.build_source_release(
        schedule_capture_dir=schedule_root,
        expected_schedule_capture_digest=schedule_manifest["observed_capture_digest"],
        feed_capture_dir=feed_root,
        expected_feed_capture_digest=feed_manifest["observed_capture_digest"],
        dependency_lock_path=lock, output_dir=source_root,
    )
    bad = tmp_path / "bad.json"
    bad.write_text("{}\n", encoding="utf-8")
    with pytest.raises(release.PAVolumeSourceReleaseError, match="positive schema"):
        release.build_pa_artifact_after_external_verification(
            source_release_dir=source_root, external_verification_path=bad,
            expected_external_verification_sha256=digest(bad), dependency_lock_path=lock,
            output_path=tmp_path / "pa.json",
        )


def test_source_release_rejects_feed_official_date_that_differs_from_schedule(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(capture, "EXPECTED_GAMES", 1)
    fixed_clock = lambda: datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    schedule_root = tmp_path / "schedule"
    schedule_manifest = capture.capture_schedule(
        output_dir=schedule_root,
        runtime=runtime(),
        source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport({capture.SCHEDULE_FULL_URL: schedule_body()}),
        clock=fixed_clock,
    )
    plan = capture.build_feed_plan(
        schedule_capture_dir=schedule_root,
        expected_schedule_capture_digest=schedule_manifest["observed_capture_digest"],
    )
    mismatched_feed = json.loads(feed_body())
    mismatched_feed["gameData"]["datetime"]["officialDate"] = "2023-03-31"
    feed_root = tmp_path / "feeds"
    feed_manifest = capture.capture_feeds(
        plan=plan,
        output_dir=feed_root,
        work_dir=tmp_path / "work",
        runtime=runtime(),
        source_access=source_access(),
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport(
            {plan["requests"][0]["full_url"]: json.dumps(mismatched_feed).encode()}
        ),
        clock=fixed_clock,
    )
    lock = tmp_path / "requirements.lock"
    lock.write_text("example==1.0\n", encoding="utf-8")
    with pytest.raises(
        release.PAVolumeSourceReleaseError,
        match="differs from schedule officialDate",
    ):
        release.build_source_release(
            schedule_capture_dir=schedule_root,
            expected_schedule_capture_digest=schedule_manifest[
                "observed_capture_digest"
            ],
            feed_capture_dir=feed_root,
            expected_feed_capture_digest=feed_manifest["observed_capture_digest"],
            dependency_lock_path=lock,
            output_dir=tmp_path / "source",
        )
