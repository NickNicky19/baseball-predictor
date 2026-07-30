from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

import scripts.build_pa_volume_official_source_release_v1 as source_release
import scripts.capture_pa_volume_official_source_v1 as capture
import scripts.qualify_pa_volume_official_source_v1 as qualify
from scripts.capture_direct_batter_pa_source_transport_v2 import (
    CapturedResponse,
    RuntimeAuthorization,
)
from src.evaluation import pa_volume_historical_source_access_v1 as historical_access
from src.evaluation.shared_pa_pa_volume_source_authority_v1 import (
    verify_pa_volume_source_authority,
)


REQUEST_TIME = "2026-07-29T12:00:00Z"
QUALIFIED_TIME = "2026-07-29T13:00:00Z"
OBSERVED_TIME = "2026-07-29T13:01:00Z"
DECISION_TIME = "2026-07-29T13:02:00Z"


@pytest.mark.parametrize(
    "relative_script",
    [
        "scripts/capture_pa_volume_official_source_v1.py",
        "scripts/build_pa_volume_official_source_release_v1.py",
        "scripts/qualify_pa_volume_official_source_v1.py",
    ],
)
def test_source_chain_clis_start_under_isolated_python(
    tmp_path: Path, relative_script: str
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(repository_root / relative_script),
            "--help",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout.lower()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(qualify.canonical_json_bytes(value))


def runtime(policy_sha256: str) -> RuntimeAuthorization:
    return RuntimeAuthorization(
        "a" * 64,
        {"runtime": "synthetic"},
        "b" * 64,
        policy_sha256,
    )


def source_access(
    tmp_path: Path,
) -> tuple[Path, Path, historical_access.VerifiedHistoricalSourceAccess]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    policy_path = tmp_path / "runtime-policy.json"
    policy_path.write_bytes(
        (
            Path(__file__).resolve().parents[1]
            / "config/direct_batter_pa_source_runtime_authority_v1.json"
        ).read_bytes()
    )
    policy_sha = digest(policy_path)
    unsigned = {
        "schema_version": historical_access.SCHEMA,
        "authorization_id": "synthetic-official-2023-source-access",
        "status": historical_access.STATUS,
        "authorized_at_utc": "2026-07-29T11:00:00.000000Z",
        "valid_from_utc": "2026-07-29T11:00:00.000000Z",
        "expires_at_utc": "2026-07-30T11:00:00.000000Z",
        "research_only": True,
        "betting_authorized": False,
        "network_fetch_authorized": True,
        "source_scope": historical_access.SOURCE_SCOPE,
        "authorized_actions": historical_access.AUTHORIZED_ACTIONS,
        "protected_boundaries": historical_access.PROTECTED_BOUNDARIES,
        "runtime_policy_sha256": policy_sha,
        "source_bundle_sha256": capture.capture_source_bundle_sha256(),
    }
    authorization = {
        **unsigned,
        "authorization_sha256": (
            historical_access.authorization_semantic_sha256(unsigned)
        ),
    }
    authorization_path = tmp_path / "external-source-access.json"
    write_json(authorization_path, authorization)
    verified = historical_access.verify_historical_source_access_authorization(
        authorization_path=authorization_path,
        expected_authorization_sha256=digest(authorization_path),
        expected_runtime_policy_sha256=policy_sha,
        expected_source_bundle_sha256=capture.capture_source_bundle_sha256(),
        access_time_utc="2026-07-29T12:00:00.000000Z",
    )
    return policy_path, authorization_path, verified


class FakeTransport:
    def __init__(self, bodies: dict[str, bytes]):
        self.bodies = bodies

    def fetch(self, request, *, timeout_seconds: float, max_bytes: int):
        return CapturedResponse(
            200,
            self.bodies[request["full_url"]],
            {
                "content-type": "application/json",
                "content-encoding": "identity",
            },
            request["full_url"],
            REQUEST_TIME,
            REQUEST_TIME,
        )


def schedule_body() -> bytes:
    return json.dumps(
        {
            "dates": [
                {
                    "date": "2023-03-30",
                    "games": [
                        {
                            "gamePk": 1,
                            "officialDate": "2023-03-30",
                            "gameType": "R",
                            "teams": {
                                "away": {"team": {"id": 10}},
                                "home": {"team": {"id": 20}},
                            },
                        }
                    ],
                }
            ]
        },
        sort_keys=True,
    ).encode("utf-8")


def feed_body() -> bytes:
    teams: dict[str, object] = {}
    for side, base, team_id in (("away", 100, 10), ("home", 200, 20)):
        teams[side] = {
            "team": {"id": team_id},
            "players": {
                f"ID{base + slot}": {
                    "person": {"id": base + slot},
                    "battingOrder": f"{slot}00",
                    "stats": {
                        "batting": {
                            "plateAppearances": 5 if slot <= 4 else 4
                        }
                    },
                }
                for slot in range(1, 10)
            },
        }
    return json.dumps(
        {
            "gamePk": 1,
            "gameData": {
                "datetime": {"officialDate": "2023-03-30"},
                "game": {"type": "R"},
                "status": {
                    "codedGameState": "F",
                    "abstractGameState": "Final",
                },
                "teams": {"away": {"id": 10}, "home": {"id": 20}},
            },
            "liveData": {"boxscore": {"teams": teams}},
        },
        sort_keys=True,
    ).encode("utf-8")


def make_inputs(tmp_path: Path, monkeypatch) -> dict[str, Path | str]:
    monkeypatch.setattr(capture, "EXPECTED_GAMES", 1)
    policy_path, authorization_path, verified_access = source_access(tmp_path)
    runtime_authorization = runtime(digest(policy_path))
    fixed_clock = lambda: datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    schedule_root = tmp_path / "schedule"
    schedule_manifest = capture.capture_schedule(
        output_dir=schedule_root,
        runtime=runtime_authorization,
        source_access=verified_access,
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport({capture.SCHEDULE_FULL_URL: schedule_body()}),
        clock=fixed_clock,
    )
    plan = capture.build_feed_plan(
        schedule_capture_dir=schedule_root,
        expected_schedule_capture_digest=schedule_manifest[
            "observed_capture_digest"
        ],
    )
    feed_root = tmp_path / "feeds"
    feed_manifest = capture.capture_feeds(
        plan=plan,
        output_dir=feed_root,
        work_dir=tmp_path / "work",
        runtime=runtime_authorization,
        source_access=verified_access,
        source_bundle_sha256=capture.capture_source_bundle_sha256(),
        transport=FakeTransport({plan["requests"][0]["full_url"]: feed_body()}),
        clock=fixed_clock,
    )
    lock = tmp_path / "requirements.lock"
    lock.write_bytes(
        (
            Path(__file__).resolve().parents[1]
            / "requirements-direct-batter-pa-source-authority.lock"
        ).read_bytes()
    )
    source_root = tmp_path / "source-release"
    manifest = source_release.build_source_release(
        schedule_capture_dir=schedule_root,
        expected_schedule_capture_digest=schedule_manifest[
            "observed_capture_digest"
        ],
        feed_capture_dir=feed_root,
        expected_feed_capture_digest=feed_manifest["observed_capture_digest"],
        dependency_lock_path=lock,
        output_dir=source_root,
    )
    verification = {
        "schema_version": source_release.EXTERNAL_VERIFICATION_SCHEMA,
        "status": "INDEPENDENT_SOURCE_RELEASE_VERIFIED",
        "source_access_authorization_id": manifest[
            "source_access_authorization_id"
        ],
        "source_access_authorization_sha256": manifest[
            "source_access_authorization_sha256"
        ],
        "source_manifest_sha256": digest(source_root / "source_manifest.json"),
        "projection_sha256": digest(source_root / "projection.json"),
        "schedule_capture_observed_digest": manifest[
            "schedule_capture_observed_digest"
        ],
        "feed_capture_observed_digest": manifest[
            "feed_capture_observed_digest"
        ],
        "dependency_lock_sha256": digest(lock),
        "reviewed_source_bundle_sha256": manifest[
            "reviewed_source_bundle_sha256"
        ],
        "protected_data": capture.PROTECTED,
    }
    verification_path = tmp_path / "source-external-verification.json"
    write_json(verification_path, verification)
    builder = (
        Path(__file__).resolve().parents[1]
        / "src/evaluation/pa_volume_source_truth_v2.py"
    )
    return {
        "source_release_dir": source_root,
        "schedule_capture_dir": schedule_root,
        "feed_capture_dir": feed_root,
        "source_release_external_verification_path": verification_path,
        "expected_source_release_external_verification_sha256": digest(
            verification_path
        ),
        "dependency_lock_path": lock,
        "source_access_authorization_path": authorization_path,
        "expected_source_access_authorization_sha256": digest(
            authorization_path
        ),
        "runtime_policy_path": policy_path,
        "expected_runtime_policy_sha256": digest(policy_path),
        "expected_builder_source_sha256": digest(builder),
    }


def build_qualified(tmp_path: Path, monkeypatch) -> tuple[Path, dict[str, object]]:
    inputs = make_inputs(tmp_path, monkeypatch)
    authority_root = tmp_path / "qualified"
    result = qualify.qualify_source_release(
        **inputs,
        qualified_at_utc=QUALIFIED_TIME,
        output_dir=authority_root,
    )
    return authority_root, result


def attestation(authority_root: Path, *, observed: str = OBSERVED_TIME) -> dict:
    authority_path = authority_root / qualify.AUTHORITY_MANIFEST_RELATIVE
    authority = json.loads(authority_path.read_bytes())
    return {
        "schema_version": qualify.EXTERNAL_ATTESTATION_SCHEMA,
        "authority_id": authority["authority_id"],
        "authority_manifest_path": qualify.AUTHORITY_MANIFEST_RELATIVE,
        "authority_manifest_sha256": digest(authority_path),
        "source_release_manifest_sha256": authority[
            "source_release_manifest"
        ]["sha256"],
        "source_release_external_verification_sha256": authority[
            "source_release_external_verification"
        ]["sha256"],
        "rebuild_manifest_sha256": authority["rebuild_manifest"]["sha256"],
        "pa_volume_artifact_sha256": authority["pa_volume_artifact"]["sha256"],
        "dependency_lock_sha256": authority["exact_dependency_lock"]["sha256"],
        "source_access_authorization_sha256": digest(
            authority_root / "source_access/authorization.json"
        ),
        "runtime_policy_sha256": digest(
            authority_root / "source_access/runtime_policy.json"
        ),
        "capture_receipt_evidence_sha256": digest(
            authority_root / qualify.CAPTURE_EVIDENCE_MANIFEST_RELATIVE
        ),
        "first_source_request_at_utc": authority["source_capture_window"][
            "first_request_at_utc"
        ],
        "latest_source_observation_at_utc": authority[
            "source_capture_window"
        ]["latest_observation_at_utc"],
        "observed_at_utc": observed,
        "decision_time_utc": DECISION_TIME,
    }


def test_qualification_and_external_receipt_complete_downstream_authority(
    tmp_path: Path, monkeypatch
) -> None:
    authority_root, result = build_qualified(tmp_path, monkeypatch)
    authority_path = authority_root / qualify.AUTHORITY_MANIFEST_RELATIVE
    attestation_path = tmp_path / "external-attestation.json"
    write_json(attestation_path, attestation(authority_root))
    receipt = qualify.emit_external_runtime_receipt(
        authority_root=authority_root,
        authority_manifest_relative=qualify.AUTHORITY_MANIFEST_RELATIVE,
        expected_authority_manifest_sha256=digest(authority_path),
        external_attestation_path=attestation_path,
        expected_external_attestation_sha256=digest(attestation_path),
        decision_time_utc=DECISION_TIME,
    )
    receipt_path = authority_root / qualify.DEFAULT_RECEIPT_RELATIVE
    artifact_path = authority_root / "artifacts/pa_volume.json"
    verified = verify_pa_volume_source_authority(
        authority_root=authority_root,
        authority_manifest_relative=qualify.AUTHORITY_MANIFEST_RELATIVE,
        external_receipt_relative=qualify.DEFAULT_RECEIPT_RELATIVE,
        expected_external_receipt_sha256=digest(receipt_path),
        expected_pa_volume_artifact_sha256=digest(artifact_path),
        decision_time_utc=DECISION_TIME,
    )
    assert result["pa_volume_artifact_sha256"] == digest(artifact_path)
    assert receipt["authority_manifest_sha256"] == digest(authority_path)
    assert receipt["source_access_authorization_sha256"] == digest(
        authority_root / "source_access/authorization.json"
    )
    assert receipt["runtime_policy_sha256"] == digest(
        authority_root / "source_access/runtime_policy.json"
    )
    assert receipt["capture_receipt_evidence_sha256"] == digest(
        authority_root / qualify.CAPTURE_EVIDENCE_MANIFEST_RELATIVE
    )
    assert verified.pa_volume_artifact_sha256 == digest(artifact_path)
    artifact = json.loads(artifact_path.read_bytes())
    assert artifact["source"]["fit_rows"] == 18
    assert artifact["source"]["bindings"]["builder_source_sha256"] == digest(
        Path(__file__).resolve().parents[1]
        / "src/evaluation/pa_volume_source_truth_v2.py"
    )


def test_missing_release_file_fails_before_output(tmp_path: Path, monkeypatch) -> None:
    inputs = make_inputs(tmp_path, monkeypatch)
    Path(inputs["source_release_dir"] / "schedule_index.json").unlink()
    output = tmp_path / "qualified"
    with pytest.raises(qualify.PAVolumeQualificationError, match="exact file set"):
        qualify.qualify_source_release(
            **inputs,
            qualified_at_utc=QUALIFIED_TIME,
            output_dir=output,
        )
    assert not output.exists()


def test_rehashed_projection_cannot_replace_official_receipt_replay(
    tmp_path: Path, monkeypatch
) -> None:
    inputs = make_inputs(tmp_path, monkeypatch)
    source_root = Path(inputs["source_release_dir"])
    projection_path = source_root / "projection.json"
    projection = json.loads(projection_path.read_bytes())
    projection["projection"]["rows"][0]["out_pa"] = 99
    write_json(projection_path, projection)
    manifest_path = source_root / "source_manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["projection_sha256"] = digest(projection_path)
    write_json(manifest_path, manifest)
    verification_path = Path(inputs["source_release_external_verification_path"])
    verification = json.loads(verification_path.read_bytes())
    verification["source_manifest_sha256"] = digest(manifest_path)
    verification["projection_sha256"] = digest(projection_path)
    write_json(verification_path, verification)
    inputs["expected_source_release_external_verification_sha256"] = digest(
        verification_path
    )
    with pytest.raises(
        qualify.PAVolumeQualificationError,
        match="independently replayed official receipts",
    ):
        qualify.qualify_source_release(
            **inputs,
            qualified_at_utc=QUALIFIED_TIME,
            output_dir=tmp_path / "qualified",
        )


def test_contradictory_external_source_verification_fails(
    tmp_path: Path, monkeypatch
) -> None:
    inputs = make_inputs(tmp_path, monkeypatch)
    verification_path = Path(inputs["source_release_external_verification_path"])
    verification = json.loads(verification_path.read_bytes())
    verification["feed_capture_observed_digest"] = "f" * 64
    write_json(verification_path, verification)
    inputs["expected_source_release_external_verification_sha256"] = digest(
        verification_path
    )
    with pytest.raises(qualify.PAVolumeQualificationError, match="contradictory"):
        qualify.qualify_source_release(
            **inputs,
            qualified_at_utc=QUALIFIED_TIME,
            output_dir=tmp_path / "qualified",
        )


def test_builder_digest_mismatch_fails_without_output(
    tmp_path: Path, monkeypatch
) -> None:
    inputs = make_inputs(tmp_path, monkeypatch)
    inputs["expected_builder_source_sha256"] = "0" * 64
    output = tmp_path / "qualified"
    with pytest.raises(qualify.PAVolumeQualificationError, match="builder differs"):
        qualify.qualify_source_release(
            **inputs,
            qualified_at_utc=QUALIFIED_TIME,
            output_dir=output,
        )
    assert not output.exists()


def test_consistently_rehashed_fabricated_source_access_authorization_fails(
    tmp_path: Path, monkeypatch
) -> None:
    inputs = make_inputs(tmp_path, monkeypatch)
    authorization_path = Path(inputs["source_access_authorization_path"])
    authorization = json.loads(authorization_path.read_bytes())
    authorization["authorized_actions"]["feature_construction"] = True
    authorization["authorization_sha256"] = (
        historical_access.authorization_semantic_sha256(authorization)
    )
    write_json(authorization_path, authorization)
    inputs["expected_source_access_authorization_sha256"] = digest(
        authorization_path
    )
    with pytest.raises(
        qualify.PAVolumeQualificationError,
        match="source-access authorization verification failed",
    ):
        qualify.qualify_source_release(
            **inputs,
            qualified_at_utc=QUALIFIED_TIME,
            output_dir=tmp_path / "qualified",
        )


def test_source_access_must_be_active_by_first_retained_request(
    tmp_path: Path, monkeypatch
) -> None:
    inputs = make_inputs(tmp_path, monkeypatch)
    authorization_path = Path(inputs["source_access_authorization_path"])
    authorization = json.loads(authorization_path.read_bytes())
    authorization["valid_from_utc"] = "2026-07-29T12:00:00.000001Z"
    authorization["authorization_sha256"] = (
        historical_access.authorization_semantic_sha256(authorization)
    )
    write_json(authorization_path, authorization)
    inputs["expected_source_access_authorization_sha256"] = digest(
        authorization_path
    )
    with pytest.raises(
        qualify.PAVolumeQualificationError,
        match="source-access authorization verification failed",
    ):
        qualify.qualify_source_release(
            **inputs,
            qualified_at_utc=QUALIFIED_TIME,
            output_dir=tmp_path / "qualified",
        )


def test_claimed_capture_window_is_recomputed_from_retained_receipts(
    tmp_path: Path, monkeypatch
) -> None:
    authority_root, _ = build_qualified(tmp_path, monkeypatch)
    authority_path = authority_root / qualify.AUTHORITY_MANIFEST_RELATIVE
    authority = json.loads(authority_path.read_bytes())
    authority["source_capture_window"] = {
        "first_request_at_utc": "2026-07-29T11:00:00.000000Z",
        "latest_observation_at_utc": "2026-07-29T12:30:00.000000Z",
    }
    write_json(authority_path, authority)
    with pytest.raises(
        qualify.PAVolumeQualificationError,
        match="differs from retained receipt bytes",
    ):
        qualify.verify_qualified_bundle(
            authority_root=authority_root,
            authority_manifest_relative=qualify.AUTHORITY_MANIFEST_RELATIVE,
        )


def test_rehashed_copied_retry_metadata_cannot_pass_qualification(
    tmp_path: Path, monkeypatch
) -> None:
    authority_root, _ = build_qualified(tmp_path, monkeypatch)
    evidence_path = authority_root / qualify.CAPTURE_EVIDENCE_MANIFEST_RELATIVE
    evidence = json.loads(evidence_path.read_bytes())
    entry = next(
        row for row in evidence["entries"]
        if row["role"] == "schedule_support"
        and row["path"].endswith("result-0001.json")
    )
    result_path = authority_root / entry["path"]
    result = json.loads(result_path.read_bytes())
    result["attempt"]["backoff_seconds"] = 1.0
    write_json(result_path, result)
    entry["sha256"] = digest(result_path)
    write_json(evidence_path, evidence)
    authority_path = authority_root / qualify.AUTHORITY_MANIFEST_RELATIVE
    authority = json.loads(authority_path.read_bytes())
    authority["source_capture_receipt_evidence"]["sha256"] = digest(evidence_path)
    write_json(authority_path, authority)
    with pytest.raises(
        qualify.PAVolumeQualificationError,
        match="deterministic copied evidence differs",
    ):
        qualify.verify_qualified_bundle(
            authority_root=authority_root,
            authority_manifest_relative=qualify.AUTHORITY_MANIFEST_RELATIVE,
        )


def test_late_external_observation_and_contradictory_attestation_fail(
    tmp_path: Path, monkeypatch
) -> None:
    authority_root, _ = build_qualified(tmp_path, monkeypatch)
    authority_path = authority_root / qualify.AUTHORITY_MANIFEST_RELATIVE
    late_path = tmp_path / "late-attestation.json"
    write_json(late_path, attestation(authority_root, observed="2026-07-29T13:03:00Z"))
    with pytest.raises(qualify.PAVolumeQualificationError, match="late"):
        qualify.emit_external_runtime_receipt(
            authority_root=authority_root,
            authority_manifest_relative=qualify.AUTHORITY_MANIFEST_RELATIVE,
            expected_authority_manifest_sha256=digest(authority_path),
            external_attestation_path=late_path,
            expected_external_attestation_sha256=digest(late_path),
            decision_time_utc=DECISION_TIME,
        )
    contradictory_path = tmp_path / "contradictory-attestation.json"
    contradictory = attestation(authority_root)
    contradictory["pa_volume_artifact_sha256"] = "0" * 64
    write_json(contradictory_path, contradictory)
    with pytest.raises(qualify.PAVolumeQualificationError, match="contradictory"):
        qualify.emit_external_runtime_receipt(
            authority_root=authority_root,
            authority_manifest_relative=qualify.AUTHORITY_MANIFEST_RELATIVE,
            expected_authority_manifest_sha256=digest(authority_path),
            external_attestation_path=contradictory_path,
            expected_external_attestation_sha256=digest(contradictory_path),
            decision_time_utc=DECISION_TIME,
        )


def test_outputs_never_overwrite(tmp_path: Path, monkeypatch) -> None:
    inputs = make_inputs(tmp_path, monkeypatch)
    output = tmp_path / "qualified"
    output.mkdir()
    with pytest.raises(qualify.PAVolumeQualificationError, match="already exists"):
        qualify.qualify_source_release(
            **inputs,
            qualified_at_utc=QUALIFIED_TIME,
            output_dir=output,
        )

    output.rmdir()
    authority_root, _ = build_qualified(tmp_path / "second", monkeypatch)
    receipt_path = authority_root / qualify.DEFAULT_RECEIPT_RELATIVE
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("existing\n", encoding="utf-8")
    attestation_path = tmp_path / "attestation.json"
    write_json(attestation_path, attestation(authority_root))
    with pytest.raises(qualify.PAVolumeQualificationError, match="overwrite"):
        qualify.emit_external_runtime_receipt(
            authority_root=authority_root,
            authority_manifest_relative=qualify.AUTHORITY_MANIFEST_RELATIVE,
            expected_authority_manifest_sha256=digest(
                authority_root / qualify.AUTHORITY_MANIFEST_RELATIVE
            ),
            external_attestation_path=attestation_path,
            expected_external_attestation_sha256=digest(attestation_path),
            decision_time_utc=DECISION_TIME,
        )
