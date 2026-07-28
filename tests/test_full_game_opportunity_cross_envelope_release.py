from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.evaluation import full_game_opportunity_cross_envelope_v1 as cross

from src.evaluation.full_game_opportunity_cross_envelope_release import (
    CrossEnvelopeReleaseError,
    MANIFEST_PATH,
    REQUIRED_FILES,
    verify_manifest_payload,
    verify_release,
)


ROOT = Path(__file__).resolve().parents[1]


def test_exact_release_manifest_replays_every_file() -> None:
    payload = verify_release(ROOT)
    assert {row["path"] for row in payload["files"]} == REQUIRED_FILES


def test_manifest_hash_mutation_fails() -> None:
    payload = json.loads((ROOT / MANIFEST_PATH).read_text(encoding="utf-8"))
    changed = copy.deepcopy(payload)
    changed["files"][0]["sha256"] = "f" * 64
    with pytest.raises(CrossEnvelopeReleaseError, match="bytes changed"):
        verify_manifest_payload(ROOT, changed)


def test_manifest_cannot_hide_or_duplicate_a_file() -> None:
    payload = json.loads((ROOT / MANIFEST_PATH).read_text(encoding="utf-8"))
    missing = copy.deepcopy(payload)
    missing["files"].pop()
    duplicate = copy.deepcopy(payload)
    duplicate["files"].append(copy.deepcopy(duplicate["files"][0]))
    for changed in (missing, duplicate):
        with pytest.raises(CrossEnvelopeReleaseError, match="file set|duplicate"):
            verify_manifest_payload(ROOT, changed)


def test_release_root_ancestor_reparse_is_rejected(tmp_path: Path) -> None:
    linked_root = tmp_path / "linked-release"
    try:
        linked_root.symlink_to(ROOT, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable on this host")
    with pytest.raises(CrossEnvelopeReleaseError, match="redirected"):
        verify_release(linked_root)


def test_release_root_ancestor_reparse_mutation_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poisoned_ancestor = ROOT.absolute().parent
    original = cross._is_link_or_reparse
    monkeypatch.setattr(
        cross,
        "_is_link_or_reparse",
        lambda path: path == poisoned_ancestor or original(path),
    )
    with pytest.raises(CrossEnvelopeReleaseError, match="redirected"):
        verify_release(ROOT)
