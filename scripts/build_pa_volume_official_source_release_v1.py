"""Build a deterministic PA-volume source release from retained captures.

The source phase writes only identity/projection evidence.  The PA phase is
separate and requires an independently supplied, digest-anchored verification
of that source release before it may create the empirical PA artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import capture_pa_volume_official_source_v1 as capture
from src.evaluation.pa_volume_official_feed_projection_v1 import (
    build_official_feed_projection,
    canonical_json_bytes,
)
from src.evaluation.pa_volume_source_truth_v2 import build_pa_volume_artifact


SOURCE_SCHEMA = "pa-volume-official-source-release-v1"
EXTERNAL_VERIFICATION_SCHEMA = "pa-volume-official-source-external-verification-v1"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class PAVolumeSourceReleaseError(ValueError):
    """The retained captures cannot support a qualified source release."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise PAVolumeSourceReleaseError(f"{label} must be a lowercase SHA-256")
    return value


def _safe_file(path: Path, label: str) -> Path:
    value = Path(os.path.abspath(os.fspath(path)))
    if ".." in path.parts or not value.is_file() or value.is_symlink():
        raise PAVolumeSourceReleaseError(f"{label} is missing or unsafe")
    return value


def _safe_output(path: Path) -> Path:
    value = Path(os.path.abspath(os.fspath(path)))
    if ".." in path.parts or value.parent == value or value.exists():
        raise PAVolumeSourceReleaseError("source-release output is unsafe or exists")
    cursor = value.parent
    while not cursor.exists():
        cursor = cursor.parent
    while True:
        if cursor.is_symlink():
            raise PAVolumeSourceReleaseError("source-release output traverses a symlink")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return value


def _safe_output_file(path: Path) -> Path:
    value = _safe_output(path)
    if value.is_symlink():
        raise PAVolumeSourceReleaseError("PA artifact output is a symlink")
    return value


def _feed_documents(feed_root: Path, requests: list[dict[str, Any]]) -> dict[int, bytes]:
    result: dict[int, bytes] = {}
    for request in requests:
        game_pk = request["expected"]["game_pk"]
        path = feed_root / "feeds" / request["request_id"] / "response.json"
        if not path.is_file() or path.is_symlink():
            raise PAVolumeSourceReleaseError("retained official feed is missing")
        result[game_pk] = path.read_bytes()
    return result


def _final_schedule_index(
    requests: list[dict[str, Any]], feeds: Mapping[int, bytes]
) -> dict[str, Any]:
    games = []
    for request in requests:
        expected = request["expected"]
        game_pk = expected["game_pk"]
        try:
            feed = json.loads(feeds[game_pk])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PAVolumeSourceReleaseError("retained official feed is invalid JSON") from exc
        game_data = feed.get("gameData") if isinstance(feed, Mapping) else None
        if not isinstance(game_data, Mapping):
            raise PAVolumeSourceReleaseError("retained official feed lacks gameData")
        official_date = ((game_data.get("datetime") or {}).get("officialDate"))
        if official_date != expected["schedule_official_date"]:
            raise PAVolumeSourceReleaseError(
                "retained official feed date differs from schedule officialDate"
            )
        # The strict projection parser performs the canonical-date and all
        # remaining semantic validation.  This index is an identity projection,
        # not a permissive fallback to the schedule listing date.
        games.append({
            "game_pk": game_pk,
            "official_date": official_date,
            "game_type": "R",
            "away_team_id": expected["away_team_id"],
            "home_team_id": expected["home_team_id"],
        })
    games.sort(key=lambda row: (str(row["official_date"]), row["game_pk"]))
    return {
        "schema_version": "pa-volume-2023-schedule-index-v1",
        "season": 2023,
        "fields": [
            "game_pk", "official_date", "game_type", "away_team_id", "home_team_id",
        ],
        "games": games,
    }


def _reviewed_source_files() -> list[dict[str, str]]:
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "scripts/capture_direct_batter_pa_source_transport_v2.py",
        root / "scripts/capture_pa_volume_official_source_v1.py",
        Path(__file__).resolve(),
        root / "src/evaluation/pa_volume_official_feed_projection_v1.py",
        root / "src/evaluation/pa_volume_source_truth_v2.py",
    ]
    return [
        {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}
        for path in paths
    ]


def build_source_release(
    *, schedule_capture_dir: Path, expected_schedule_capture_digest: str,
    feed_capture_dir: Path, expected_feed_capture_digest: str,
    dependency_lock_path: Path, output_dir: Path,
) -> dict[str, Any]:
    """Build source/projection evidence but no PA artifact or prediction."""
    schedule_manifest = capture.verify_schedule_capture(
        schedule_capture_dir, expected_schedule_capture_digest
    )
    feed_manifest = capture.verify_feed_capture(
        feed_capture_dir, expected_feed_capture_digest
    )
    if feed_manifest["schedule_capture_digest"] != schedule_manifest["observed_capture_digest"]:
        raise PAVolumeSourceReleaseError("feed capture binds a different schedule")
    if (
        feed_manifest["source_access_authorization_id"]
        != schedule_manifest["source_access_authorization_id"]
        or feed_manifest["source_access_authorization_sha256"]
        != schedule_manifest["source_access_authorization_sha256"]
    ):
        raise PAVolumeSourceReleaseError(
            "schedule and feed captures bind different source access"
        )
    expected_capture_source = capture.capture_source_bundle_sha256()
    if (
        schedule_manifest["source_bundle_sha256"] != expected_capture_source
        or feed_manifest["source_bundle_sha256"] != expected_capture_source
    ):
        raise PAVolumeSourceReleaseError(
            "capture manifests do not bind the exact reviewed capture source bytes"
        )
    lock = _safe_file(dependency_lock_path, "dependency lock")
    feed_root = Path(os.path.abspath(os.fspath(feed_capture_dir)))
    plan = json.loads((feed_root / "plan.json").read_bytes())
    requests = capture._plan(plan)
    feeds = _feed_documents(feed_root, requests)
    schedule_index = _final_schedule_index(requests, feeds)
    parser_path = Path(__file__).resolve().parents[1] / "src/evaluation/pa_volume_official_feed_projection_v1.py"
    parser_sha = sha256_file(parser_path)
    projection = build_official_feed_projection(
        schedule_index=schedule_index,
        feeds_by_game_pk=feeds,
        schedule_capture_manifest_sha256=sha256_file(Path(schedule_capture_dir) / "manifest.json"),
        feed_capture_manifest_sha256=sha256_file(Path(feed_capture_dir) / "manifest.json"),
        parser_source_sha256=parser_sha,
    )
    output = _safe_output(output_dir)
    staging = output.with_name(output.name + ".staging")
    if staging.exists():
        raise PAVolumeSourceReleaseError("stale source-release staging exists")
    staging.mkdir(parents=True)
    try:
        (staging / "schedule_index.json").write_bytes(canonical_json_bytes(schedule_index))
        (staging / "projection.json").write_bytes(canonical_json_bytes(projection))
        source_manifest = {
            "schema_version": SOURCE_SCHEMA,
            "status": "AWAITING_INDEPENDENT_EXTERNAL_VERIFICATION",
            "season": 2023,
            "research_only": True,
            "betting_authorized": False,
            "model_fitting_performed": False,
            "probabilities_generated": False,
            "protected_data": capture.PROTECTED,
            "source_access_authorization_id": schedule_manifest[
                "source_access_authorization_id"
            ],
            "source_access_authorization_sha256": schedule_manifest[
                "source_access_authorization_sha256"
            ],
            "schedule_capture_observed_digest": schedule_manifest["observed_capture_digest"],
            "schedule_capture_manifest_sha256": sha256_file(Path(schedule_capture_dir) / "manifest.json"),
            "feed_capture_observed_digest": feed_manifest["observed_capture_digest"],
            "feed_capture_manifest_sha256": sha256_file(Path(feed_capture_dir) / "manifest.json"),
            "schedule_index_sha256": sha256_file(staging / "schedule_index.json"),
            "projection_sha256": sha256_file(staging / "projection.json"),
            "parser_source_sha256": parser_sha,
            "reviewed_source_files": _reviewed_source_files(),
            "reviewed_source_bundle_sha256": sha256_bytes(
                canonical_json_bytes(_reviewed_source_files())
            ),
            "dependency_lock_sha256": sha256_file(lock),
            "game_count": projection["game_count"],
            "row_count": projection["row_count"],
        }
        (staging / "source_manifest.json").write_bytes(canonical_json_bytes(source_manifest))
        os.replace(staging, output)
        return source_manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def build_pa_artifact_after_external_verification(
    *, source_release_dir: Path, external_verification_path: Path,
    expected_external_verification_sha256: str, dependency_lock_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Build PA artifact only after independent source-release verification."""
    root = Path(os.path.abspath(os.fspath(source_release_dir)))
    manifest_path = _safe_file(root / "source_manifest.json", "source manifest")
    projection_path = _safe_file(root / "projection.json", "projection")
    verification_path = _safe_file(external_verification_path, "external verification")
    expected_verification = _sha(
        expected_external_verification_sha256, "external verification digest"
    )
    if sha256_file(verification_path) != expected_verification:
        raise PAVolumeSourceReleaseError("external verification differs from expected digest")
    verification = json.loads(verification_path.read_bytes())
    if not isinstance(verification, Mapping) or set(verification) != {
        "schema_version", "status", "source_manifest_sha256", "projection_sha256",
        "schedule_capture_observed_digest", "feed_capture_observed_digest",
        "source_access_authorization_id", "source_access_authorization_sha256",
        "dependency_lock_sha256", "reviewed_source_bundle_sha256", "protected_data",
    }:
        raise PAVolumeSourceReleaseError("external verification positive schema differs")
    manifest = json.loads(manifest_path.read_bytes())
    lock = _safe_file(dependency_lock_path, "dependency lock")
    expected = {
        "schema_version": EXTERNAL_VERIFICATION_SCHEMA,
        "status": "INDEPENDENT_SOURCE_RELEASE_VERIFIED",
        "source_manifest_sha256": sha256_file(manifest_path),
        "projection_sha256": sha256_file(projection_path),
        "schedule_capture_observed_digest": manifest["schedule_capture_observed_digest"],
        "feed_capture_observed_digest": manifest["feed_capture_observed_digest"],
        "source_access_authorization_id": manifest[
            "source_access_authorization_id"
        ],
        "source_access_authorization_sha256": manifest[
            "source_access_authorization_sha256"
        ],
        "dependency_lock_sha256": sha256_file(lock),
        "reviewed_source_bundle_sha256": manifest["reviewed_source_bundle_sha256"],
        "protected_data": capture.PROTECTED,
    }
    if verification != expected:
        raise PAVolumeSourceReleaseError("external verification does not bind exact source release")
    projection = json.loads(projection_path.read_bytes())
    rows = ((projection.get("projection") or {}).get("rows"))
    if not isinstance(rows, list):
        raise PAVolumeSourceReleaseError("qualified projection rows are missing")
    builder = Path(__file__).resolve().parents[1] / "src/evaluation/pa_volume_source_truth_v2.py"
    artifact = build_pa_volume_artifact(
        rows=rows,
        source_release_manifest_sha256=sha256_file(manifest_path),
        source_release_external_verification_sha256=expected_verification,
        dependency_lock_sha256=sha256_file(lock),
        builder_source_sha256=sha256_file(builder),
    )
    output = _safe_output_file(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json_bytes(artifact))
    return artifact


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    source = sub.add_parser("build-source-release")
    source.add_argument("--schedule-capture-dir", required=True, type=Path)
    source.add_argument("--expected-schedule-capture-digest", required=True)
    source.add_argument("--feed-capture-dir", required=True, type=Path)
    source.add_argument("--expected-feed-capture-digest", required=True)
    source.add_argument("--dependency-lock", required=True, type=Path)
    source.add_argument("--output-dir", required=True, type=Path)

    artifact = sub.add_parser("build-pa-artifact")
    artifact.add_argument("--source-release-dir", required=True, type=Path)
    artifact.add_argument("--external-verification", required=True, type=Path)
    artifact.add_argument("--expected-external-verification-sha256", required=True)
    artifact.add_argument("--dependency-lock", required=True, type=Path)
    artifact.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "build-source-release":
        result = build_source_release(
            schedule_capture_dir=args.schedule_capture_dir,
            expected_schedule_capture_digest=args.expected_schedule_capture_digest,
            feed_capture_dir=args.feed_capture_dir,
            expected_feed_capture_digest=args.expected_feed_capture_digest,
            dependency_lock_path=args.dependency_lock,
            output_dir=args.output_dir,
        )
    elif args.command == "build-pa-artifact":
        result = build_pa_artifact_after_external_verification(
            source_release_dir=args.source_release_dir,
            external_verification_path=args.external_verification,
            expected_external_verification_sha256=(
                args.expected_external_verification_sha256
            ),
            dependency_lock_path=args.dependency_lock,
            output_path=args.output,
        )
    else:  # pragma: no cover - argparse enforces the command set.
        raise PAVolumeSourceReleaseError("unsupported command")
    print(json.dumps({
        "schema_version": result.get("schema_version"),
        "status": result.get("status", "PA_ARTIFACT_BUILT"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
