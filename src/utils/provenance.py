"""Small, explicit provenance helpers for persisted research artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def sha256_file(path: str | Path) -> str:
    """Return the content SHA-256 of one file without loading it all at once."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    """Hash a JSON-compatible value with a stable canonical encoding."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def code_provenance(repo_root: str | Path) -> dict[str, Any]:
    """Describe the repository state without pretending a clean commit is enough.

    The returned ``snapshot_sha256`` incorporates HEAD, all tracked changes
    relative to HEAD, and the contents of every non-ignored untracked file. If
    Git is unavailable, the failure is explicit; callers must not substitute a
    made-up code hash.
    """

    root = Path(repo_root).resolve()
    git = shutil.which("git")
    if git is None:
        return {"status": "unavailable", "reason": "git executable not found"}

    def run(*args: str) -> bytes:
        result = subprocess.run(
            [git, *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(message or f"git {' '.join(args)} failed")
        return result.stdout

    try:
        head = run("rev-parse", "HEAD").decode("ascii").strip()
        # ``git diff HEAD`` includes staged and unstaged changes relative to the
        # committed source tree. Binary mode prevents a binary change being
        # silently omitted from the evidence.
        tracked_patch = run("diff", "--binary", "HEAD")
        untracked_paths = [
            Path(line.decode("utf-8", errors="surrogateescape"))
            for line in run("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
            if line
        ]
        untracked = []
        for relative in sorted(untracked_paths, key=lambda p: str(p).replace("\\", "/")):
            candidate = root / relative
            if candidate.is_file():
                untracked.append({
                    "path": str(relative).replace("\\", "/"),
                    "sha256": sha256_file(candidate),
                })

        state = {
            "head": head,
            "tracked_patch_sha256": hashlib.sha256(tracked_patch).hexdigest(),
            "untracked": untracked,
        }
        return {
            "status": "available",
            **state,
            "snapshot_sha256": sha256_json(state),
        }
    except (OSError, RuntimeError) as exc:
        return {"status": "unavailable", "reason": str(exc)}
