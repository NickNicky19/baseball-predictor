#!/usr/bin/env python3
"""Mutation tests for the hash-bound clean-release preflight."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.release_artifact_preflight import (  # noqa: E402
    ReleaseArtifactPreflightError,
    validate_hash_bound_runtime_artifacts,
)


def must_fail(root: Path, config: Path, label: str) -> None:
    try:
        validate_hash_bound_runtime_artifacts(repo_root=root, config_path=config)
    except ReleaseArtifactPreflightError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "release"
        (root / "config").mkdir(parents=True)
        artifact = root / "data" / "learning" / "k.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"verified kbb artifact")
        import hashlib
        config = {
            "pa_simulator": {
                "use_fitted_kbb": True,
                "kbb_artifact_path": "data/learning/k.json",
                "kbb_artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
        }
        config_path = root / "config" / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        validate_hash_bound_runtime_artifacts(repo_root=root, config_path=config_path)
        print("[OK] complete hash-bound release validates")

        artifact.unlink()
        must_fail(root, config_path, "missing artifact")
        artifact.write_bytes(b"different bytes")
        must_fail(root, config_path, "artifact hash drift")
        config["pa_simulator"]["kbb_artifact_path"] = "../outside.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        must_fail(root, config_path, "artifact path escapes release")
    print("3/3 mutations rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
