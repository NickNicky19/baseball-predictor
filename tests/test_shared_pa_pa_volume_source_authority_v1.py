"""Focused source-authority mutations for a future projected-opportunity v3."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.evaluation.pa_volume_source_truth_v2 import (
    build_pa_volume_artifact,
    canonical_json_bytes,
)
from src.evaluation.shared_pa_pa_volume_source_authority_v1 import (
    AUTHORITY_SCHEMA,
    COMPLETE_DECISION,
    PAVolumeSourceAuthorityError,
    REBUILD_COMPLETE_DECISION,
    REBUILD_SCHEMA,
    RECEIPT_SCHEMA,
    VerifiedPAVolumeSourceAuthority,
    invoke_after_pa_volume_source_authority,
)


PROTECTED = {
    "may_2026_accessed": False,
    "selection_2024_accessed": False,
    "spent_hr_confirmation_2025_accessed": False,
    "prices_accessed": False,
    "prospective_evidence_accessed": False,
    "prospective_backfill_performed": False,
}
REQUIRED = {
    "raw_transport_request_and_response_receipts": True,
    "independent_official_source_receipts": True,
    "exact_reproducible_dependency_lock": True,
    "external_expected_release_digest": True,
}


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def pa_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for side, team_id, player_base in (("away", 10, 1000), ("home", 20, 2000)):
        for slot in range(1, 10):
            rows.append(
                {
                    "game_pk": 1,
                    "official_date": "2023-03-30",
                    "side": side,
                    "team_id": team_id,
                    "player_id": player_base + slot,
                    "lineup_slot": slot,
                    "out_pa": 5 if slot <= 4 else 4,
                }
            )
    return rows


def fixture(root: Path, *, blocked: bool = False) -> dict[str, object]:
    source_root = root / "source_release"
    source = source_root / "source_manifest.json"
    schedule = source_root / "schedule_index.json"
    projection_path = source_root / "projection.json"
    source_verification = root / "source_release_external_verification.json"
    lock = root / "locks" / "requirements.lock"
    pa = root / "artifacts" / "pa_volume.json"
    source.parent.mkdir(parents=True)
    lock.parent.mkdir(parents=True)
    pa.parent.mkdir(parents=True)
    repo = Path(__file__).resolve().parents[1]
    lock.write_bytes(
        (repo / "requirements-direct-batter-pa-source-authority.lock").read_bytes()
    )
    rows = pa_rows()
    schedule_value = {
        "schema_version": "pa-volume-2023-schedule-index-v1",
        "season": 2023,
        "fields": [
            "game_pk", "official_date", "game_type", "away_team_id", "home_team_id",
        ],
        "games": [{
            "game_pk": 1,
            "official_date": "2023-03-30",
            "game_type": "R",
            "away_team_id": 10,
            "home_team_id": 20,
        }],
    }
    write_json(schedule, schedule_value)
    reviewed_paths = [
        "scripts/capture_direct_batter_pa_source_transport_v2.py",
        "scripts/capture_pa_volume_official_source_v1.py",
        "scripts/build_pa_volume_official_source_release_v1.py",
        "src/evaluation/pa_volume_official_feed_projection_v1.py",
        "src/evaluation/pa_volume_source_truth_v2.py",
    ]
    reviewed = [
        {"path": relative, "sha256": digest(repo / relative)}
        for relative in reviewed_paths
    ]
    projection_rows = {
        "schema_version": "pa-volume-official-starter-projection-v2",
        "season": 2023,
        "fields": [
            "game_pk", "official_date", "side", "team_id", "player_id",
            "lineup_slot", "out_pa",
        ],
        "rows": rows,
    }
    projection = {
        "schema_version": "pa-volume-official-feed-projection-v1",
        "status": "COMPLETE_OFFICIAL_2023_FINAL_FEED_PROJECTION",
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "prospective_evidence_claimed": False,
        "protected_data": PROTECTED,
        "bindings": {
            "schedule_capture_manifest_sha256": "1" * 64,
            "feed_capture_manifest_sha256": "2" * 64,
            "parser_source_sha256": reviewed[3]["sha256"],
        },
        "game_count": 1,
        "row_count": 18,
        "feed_hashes": [{"game_pk": 1, "sha256": "3" * 64}],
        "projection_sha256": hashlib.sha256(
            canonical_json_bytes(projection_rows)
        ).hexdigest(),
        "projection": projection_rows,
    }
    write_json(projection_path, projection)
    source_value = {
        "schema_version": "pa-volume-official-source-release-v1",
        "status": "AWAITING_INDEPENDENT_EXTERNAL_VERIFICATION",
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "model_fitting_performed": False,
        "probabilities_generated": False,
        "protected_data": PROTECTED,
        "source_access_authorization_id": "official-2023-source-access-v1",
        "source_access_authorization_sha256": "4" * 64,
        "schedule_capture_observed_digest": "5" * 64,
        "schedule_capture_manifest_sha256": "1" * 64,
        "feed_capture_observed_digest": "6" * 64,
        "feed_capture_manifest_sha256": "2" * 64,
        "schedule_index_sha256": digest(schedule),
        "projection_sha256": digest(projection_path),
        "parser_source_sha256": reviewed[3]["sha256"],
        "reviewed_source_files": reviewed,
        "reviewed_source_bundle_sha256": hashlib.sha256(
            canonical_json_bytes(reviewed)
        ).hexdigest(),
        "dependency_lock_sha256": digest(lock),
        "game_count": 1,
        "row_count": 18,
    }
    write_json(source, source_value)
    verification = {
        "schema_version": "pa-volume-official-source-external-verification-v1",
        "status": "INDEPENDENT_SOURCE_RELEASE_VERIFIED",
        "source_access_authorization_id": source_value["source_access_authorization_id"],
        "source_access_authorization_sha256": source_value["source_access_authorization_sha256"],
        "source_manifest_sha256": digest(source),
        "projection_sha256": digest(projection_path),
        "schedule_capture_observed_digest": source_value["schedule_capture_observed_digest"],
        "feed_capture_observed_digest": source_value["feed_capture_observed_digest"],
        "dependency_lock_sha256": digest(lock),
        "reviewed_source_bundle_sha256": source_value["reviewed_source_bundle_sha256"],
        "protected_data": PROTECTED,
    }
    write_json(source_verification, verification)
    builder_source = (
        repo / "src/evaluation/pa_volume_source_truth_v2.py"
    )
    pa.write_bytes(
        canonical_json_bytes(
            build_pa_volume_artifact(
                rows=rows,
                source_release_manifest_sha256=digest(source),
                source_release_external_verification_sha256=digest(source_verification),
                dependency_lock_sha256=digest(lock),
                builder_source_sha256=digest(builder_source),
            )
        )
    )

    rebuild = {
        "schema_version": REBUILD_SCHEMA,
        "decision": REBUILD_COMPLETE_DECISION,
        "source_season": 2023,
        "fit_seasons": [2023],
        "source_release_manifest_sha256": digest(source),
        "source_release_external_verification_sha256": digest(source_verification),
        "pa_volume_artifact_path": "artifacts/pa_volume.json",
        "pa_volume_artifact_sha256": digest(pa),
        "dependency_lock_sha256": digest(lock),
        "manual_coefficients": False,
        "protected_data": PROTECTED,
    }
    rebuild_path = root / "rebuild" / "manifest.json"
    write_json(rebuild_path, rebuild)
    authority = {
        "schema_version": AUTHORITY_SCHEMA,
        "authority_id": "synthetic-receipt-complete-2023-v1",
        "decision": (
            "BLOCKED_INCOMPLETE_SOURCE_AND_DEPENDENCY_AUTHORITY"
            if blocked
            else COMPLETE_DECISION
        ),
        "research_only": True,
        "betting_authorized": False,
        "eligible_for_model_consumption": not blocked,
        "blockers": ["RAW_TRANSPORT_RECEIPTS_MISSING"] if blocked else [],
        "source_season": 2023,
        "qualified_at_utc": "2026-07-29T10:00:00Z",
        "required_authority": REQUIRED,
        "observed_authority": (
            {**REQUIRED, "raw_transport_request_and_response_receipts": False}
            if blocked
            else REQUIRED
        ),
        "protected_data": PROTECTED,
        "source_release_manifest": {
            "path": "source_release/source_manifest.json",
            "sha256": digest(source),
        },
        "source_release_external_verification": {
            "path": "source_release_external_verification.json",
            "sha256": digest(source_verification),
        },
        "rebuild_manifest": {
            "path": "rebuild/manifest.json",
            "sha256": digest(rebuild_path),
        },
        "pa_volume_artifact": {
            "path": "artifacts/pa_volume.json",
            "sha256": digest(pa),
        },
        "exact_dependency_lock": {
            "path": "locks/requirements.lock",
            "sha256": digest(lock),
        },
    }
    authority_path = root / "authority" / "manifest.json"
    write_json(authority_path, authority)
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "authority_id": authority["authority_id"],
        "authority_manifest_path": "authority/manifest.json",
        "authority_manifest_sha256": digest(authority_path),
        "source_release_manifest_sha256": digest(source),
        "source_release_external_verification_sha256": digest(source_verification),
        "rebuild_manifest_sha256": digest(rebuild_path),
        "pa_volume_artifact_sha256": digest(pa),
        "dependency_lock_sha256": digest(lock),
        "observed_at_utc": "2026-07-29T10:01:00Z",
    }
    receipt["receipt_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
    receipt_path = root / "external" / "receipt.json"
    write_json(receipt_path, receipt)
    return {
        "authority_root": root,
        "authority_manifest_relative": "authority/manifest.json",
        "external_receipt_relative": "external/receipt.json",
        "expected_external_receipt_sha256": digest(receipt_path),
        "expected_pa_volume_artifact_sha256": digest(pa),
        "decision_time_utc": "2026-07-29T10:02:00Z",
        "paths": {
            "authority": authority_path,
            "receipt": receipt_path,
            "rebuild": rebuild_path,
            "pa": pa,
            "source": source,
            "source_verification": source_verification,
            "schedule": schedule,
            "projection": projection_path,
            "lock": lock,
        },
    }


def guarded_call(arguments: dict[str, object]) -> tuple[list[object], object]:
    called: list[object] = []

    def parent(authority: VerifiedPAVolumeSourceAuthority) -> dict[str, object]:
        called.append(authority)
        return authority.binding()

    return called, lambda: invoke_after_pa_volume_source_authority(parent, **{
        key: value for key, value in arguments.items() if key != "paths"
    })


def reanchor_all_outer_hashes(arguments: dict[str, object]) -> None:
    """Model a malicious but consistently rehashed outer authority envelope."""
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    authority_path = paths["authority"]
    receipt_path = paths["receipt"]
    rebuild_path = paths["rebuild"]
    pa_path = paths["pa"]
    source_path = paths["source"]
    verification_path = paths["source_verification"]
    lock_path = paths["lock"]
    assert all(
        isinstance(path, Path)
        for path in (
            authority_path, receipt_path, rebuild_path, pa_path, source_path,
            verification_path, lock_path,
        )
    )
    rebuild = json.loads(rebuild_path.read_bytes())
    rebuild["source_release_manifest_sha256"] = digest(source_path)
    rebuild["source_release_external_verification_sha256"] = digest(
        verification_path
    )
    rebuild["pa_volume_artifact_sha256"] = digest(pa_path)
    rebuild["dependency_lock_sha256"] = digest(lock_path)
    write_json(rebuild_path, rebuild)
    authority = json.loads(authority_path.read_bytes())
    authority["source_release_manifest"]["sha256"] = digest(source_path)
    authority["source_release_external_verification"]["sha256"] = digest(
        verification_path
    )
    authority["rebuild_manifest"]["sha256"] = digest(rebuild_path)
    authority["pa_volume_artifact"]["sha256"] = digest(pa_path)
    authority["exact_dependency_lock"]["sha256"] = digest(lock_path)
    write_json(authority_path, authority)
    receipt = json.loads(receipt_path.read_bytes())
    receipt["authority_manifest_sha256"] = digest(authority_path)
    receipt["source_release_manifest_sha256"] = digest(source_path)
    receipt["source_release_external_verification_sha256"] = digest(
        verification_path
    )
    receipt["rebuild_manifest_sha256"] = digest(rebuild_path)
    receipt["pa_volume_artifact_sha256"] = digest(pa_path)
    receipt["dependency_lock_sha256"] = digest(lock_path)
    receipt.pop("receipt_sha256", None)
    receipt["receipt_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
    write_json(receipt_path, receipt)
    arguments["expected_external_receipt_sha256"] = digest(receipt_path)
    arguments["expected_pa_volume_artifact_sha256"] = digest(pa_path)


def test_matching_pa_hash_but_blocked_authority_prevents_parent_probability(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path, blocked=True)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="blocked or ineligible"):
        run()
    assert called == []


@pytest.mark.parametrize("missing", ["authority", "receipt"])
def test_missing_authority_evidence_prevents_parent_probability(
    tmp_path: Path, missing: str
) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    path = paths[missing]
    assert isinstance(path, Path)
    path.unlink()
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="missing"):
        run()
    assert called == []


def test_unanchored_rehashed_authority_prevents_parent_probability(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    authority_path = paths["authority"]
    assert isinstance(authority_path, Path)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority["authority_id"] = "locally-rehashed-without-external-anchor"
    write_json(authority_path, authority)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="authority manifest differs"):
        run()
    assert called == []


def test_wrong_external_expected_digest_prevents_parent_probability(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    arguments["expected_external_receipt_sha256"] = "0" * 64
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="independent expected digest"):
        run()
    assert called == []


def test_rebuild_or_artifact_drift_prevents_parent_probability(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    pa_path = paths["pa"]
    assert isinstance(pa_path, Path)
    pa_path.write_bytes(pa_path.read_bytes() + b"mutation")
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="PA-volume artifact bytes differ"):
        run()
    assert called == []


def test_late_authority_observation_prevents_parent_probability(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    arguments["decision_time_utc"] = "2026-07-29T10:00:30Z"
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="chronology is invalid"):
        run()
    assert called == []


def test_protected_boundary_mutation_prevents_parent_probability(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    authority_path = paths["authority"]
    receipt_path = paths["receipt"]
    assert isinstance(authority_path, Path) and isinstance(receipt_path, Path)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority["protected_data"]["may_2026_accessed"] = True
    write_json(authority_path, authority)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["authority_manifest_sha256"] = digest(authority_path)
    receipt.pop("receipt_sha256")
    receipt["receipt_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
    write_json(receipt_path, receipt)
    arguments["expected_external_receipt_sha256"] = digest(receipt_path)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="protected-data boundary"):
        run()
    assert called == []


def test_well_hashed_arbitrary_source_manifest_cannot_pass(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict) and isinstance(paths["source"], Path)
    write_json(paths["source"], {"schema_version": "arbitrary-source-v1"})
    reanchor_all_outer_hashes(arguments)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="source manifest schema"):
        run()
    assert called == []


def test_well_hashed_arbitrary_external_verification_cannot_pass(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict) and isinstance(
        paths["source_verification"], Path
    )
    write_json(
        paths["source_verification"],
        {"schema_version": "arbitrary-external-verification-v1"},
    )
    reanchor_all_outer_hashes(arguments)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="not semantically bound"):
        run()
    assert called == []


def test_well_hashed_arbitrary_dependency_lock_cannot_pass(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict) and isinstance(paths["lock"], Path)
    paths["lock"].write_bytes(
        b"arbitrary==1.0 --hash=sha256:" + b"a" * 64 + b"\n"
    )
    reanchor_all_outer_hashes(arguments)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="exact audited lock"):
        run()
    assert called == []


def test_well_hashed_arbitrary_builder_binding_cannot_pass(tmp_path: Path) -> None:
    arguments = fixture(tmp_path)
    paths = arguments["paths"]
    assert isinstance(paths, dict) and isinstance(paths["pa"], Path)
    artifact = json.loads(paths["pa"].read_bytes())
    artifact["source"]["bindings"]["builder_source_sha256"] = "e" * 64
    write_json(paths["pa"], artifact)
    reanchor_all_outer_hashes(arguments)
    called, run = guarded_call(arguments)
    with pytest.raises(PAVolumeSourceAuthorityError, match="deterministic source rebuild"):
        run()
    assert called == []


def test_valid_authority_calls_parent_once_and_binds_exact_artifact_and_rebuild(
    tmp_path: Path,
) -> None:
    arguments = fixture(tmp_path)
    called, run = guarded_call(arguments)
    binding = run()
    assert len(called) == 1
    authority = called[0]
    assert isinstance(authority, VerifiedPAVolumeSourceAuthority)
    paths = arguments["paths"]
    assert isinstance(paths, dict)
    assert binding["pa_volume_artifact_sha256"] == digest(paths["pa"])
    assert binding["pa_volume_rebuild_manifest_sha256"] == digest(paths["rebuild"])
    assert binding["pa_volume_source_release_manifest_sha256"] == digest(paths["source"])
    assert binding["pa_volume_source_release_external_verification_sha256"] == digest(paths["source_verification"])
    assert binding["pa_volume_dependency_lock_sha256"] == digest(paths["lock"])
