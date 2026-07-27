"""Create a new exact build context from the single authoritative sources."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

SOURCE_DIRECTORIES = (Path("dashboard"), Path("src/omega_contracts"))
BUILD_METADATA = Path("packaging/omega_scaffold/pyproject.toml")


def prepare_context(repository_root: Path, destination: Path) -> list[str]:
    root = repository_root.resolve(strict=True)
    target = destination.resolve(strict=False)
    if target == root or root in target.parents:
        raise ValueError("build context must be outside the repository checkout")
    if target.exists():
        raise ValueError("build context destination must not already exist")
    target.mkdir(parents=True, mode=0o700)
    copied: list[str] = []
    metadata = root / BUILD_METADATA
    if not metadata.is_file() or metadata.is_symlink():
        raise ValueError("exact scaffold build metadata is unavailable")
    shutil.copyfile(metadata, target / "pyproject.toml", follow_symlinks=False)
    copied.append("pyproject.toml")
    for relative in SOURCE_DIRECTORIES:
        source = root / relative
        if not source.is_dir() or source.is_symlink():
            raise ValueError(
                f"authoritative source directory is invalid: {relative.as_posix()}"
            )
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ValueError(
                    f"source symlink is prohibited: {path.relative_to(root).as_posix()}"
                )
            if not path.is_file() or path.suffix not in {".py", ".html", ".css"}:
                continue
            output = target / path.relative_to(root)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output, follow_symlinks=False)
            copied.append(path.relative_to(root).as_posix())
    required = {
        "dashboard/app.py",
        "dashboard/contracts.py",
        "dashboard/snapshot_store.py",
        "dashboard/templates/index.html",
        "dashboard/static/style.css",
        "src/omega_contracts/chronology.py",
    }
    if not required.issubset(copied):
        raise ValueError(
            "standalone build context is missing required authoritative sources"
        )
    if any("phase3" in path.casefold() for path in copied):
        raise ValueError("Phase 3 audit material entered the build context")
    return copied


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    copied = prepare_context(args.repository_root, args.destination)
    print(f"prepared exact standalone scaffold context: {len(copied)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
