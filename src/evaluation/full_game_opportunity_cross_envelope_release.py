"""Exact-byte manifest verifier for the inert cross-envelope release."""

from __future__ import annotations

import json
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from src.evaluation.full_game_opportunity_cross_envelope_v1 import (
    FullGameOpportunityEnvelopeError,
    _safe_regular_file,
    sha256_file,
)


MANIFEST_PATH = "reports/full_game_opportunity_cross_envelope_v1_manifest.json"
SCHEMA_VERSION = "full-game-opportunity-cross-envelope-release-v1"
REQUIRED_FILES = {
    "config/full_game_opportunity_cross_envelope_v1_authority.json",
    "config/schemas/full_game_opportunity_cross_envelope_v1.schema.json",
    "reports/full_game_opportunity_cross_envelope_v1.md",
    "src/evaluation/full_game_opportunity_cross_envelope_release.py",
    "src/evaluation/full_game_opportunity_cross_envelope_v1.py",
    "tests/test_full_game_opportunity_cross_envelope_release.py",
    "tests/test_full_game_opportunity_cross_envelope_v1.py",
}


class CrossEnvelopeReleaseError(ValueError):
    pass


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    attrs = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(attrs & reparse)


def verify_manifest_payload(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version", "status", "base_commits", "files", "limitations",
    }:
        raise CrossEnvelopeReleaseError("manifest surface changed")
    if payload["schema_version"] != SCHEMA_VERSION or payload["status"] != "INERT_UNBOUND_NO_PROBABILITY_CONSUMPTION":
        raise CrossEnvelopeReleaseError("manifest authority state changed")
    if payload["base_commits"] != {
        "projected_lineup_pr32": "1ebeb255bf3ecf17adb82f644f9dc2ff11c7491c",
        "pitcher_source_truth_pr33": "506ebb40203582d25ff01c0a8c2d1dec4c44ed7d",
        "pitcher_joint_pr35": "0e6a40d5cd1b00d3b1f1128f97a12e7b897aaf55",
    }:
        raise CrossEnvelopeReleaseError("manifest base identity changed")
    rows = payload["files"]
    if not isinstance(rows, list) or {row.get("path") for row in rows if isinstance(row, Mapping)} != REQUIRED_FILES:
        raise CrossEnvelopeReleaseError("manifest file set changed")
    if len(rows) != len(REQUIRED_FILES):
        raise CrossEnvelopeReleaseError("manifest has duplicate file identity")
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"path", "size_bytes", "sha256"}:
            raise CrossEnvelopeReleaseError("manifest row surface changed")
        relative = row["path"]
        if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise CrossEnvelopeReleaseError("manifest path is unsafe")
        try:
            path = _safe_regular_file(
                root, PurePosixPath(relative).as_posix(), "release manifest file"
            )
        except FullGameOpportunityEnvelopeError as exc:
            raise CrossEnvelopeReleaseError(
                "manifest file is missing, redirected, or outside release root"
            ) from exc
        if path.stat().st_size != row["size_bytes"] or sha256_file(path) != row["sha256"]:
            raise CrossEnvelopeReleaseError(f"manifest bytes changed: {relative}")
    limitations = payload["limitations"]
    if limitations != [
        "external authority is unbound",
        "public probability consumption abstains",
        "batter and starter PMFs remain separate",
        "complete PR32 retained-evidence replay types are not delivered in this source tree",
        "bullpen quality and reliever identity are not modeled",
        "no fitting, scoring, promotion, deployment, or betting authorization",
    ]:
        raise CrossEnvelopeReleaseError("release limitations changed")
    return dict(payload)


def verify_release(root: Path) -> dict[str, Any]:
    try:
        path = _safe_regular_file(root, MANIFEST_PATH, "release manifest")
    except FullGameOpportunityEnvelopeError as exc:
        raise CrossEnvelopeReleaseError(
            "release manifest is missing, redirected, or outside release root"
        ) from exc
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CrossEnvelopeReleaseError("release manifest is unreadable") from exc
    return verify_manifest_payload(root, payload)
