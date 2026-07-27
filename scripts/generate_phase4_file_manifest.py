#!/usr/bin/env python3
"""Generate the deterministic Phase 4 file-change manifest from Git state."""

from __future__ import annotations

import argparse
import csv
import subprocess
from pathlib import Path


def git_lines(root: Path, *arguments: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line for line in completed.stdout.splitlines() if line]


def collect(root: Path, baseline: str, output: Path) -> dict[str, str]:
    states: dict[str, str] = {}
    for line in git_lines(root, "diff", "--name-status", baseline):
        fields = line.split("\t")
        code = fields[0]
        if code.startswith("R"):
            states[fields[1]] = f"RELOCATED_FROM->{fields[2]}"
            states[fields[2]] = f"RELOCATED_TO<-{fields[1]}"
        else:
            states[fields[1]] = {
                "A": "ADDED",
                "D": "REMOVED",
                "M": "MODIFIED",
            }.get(code, code)
    for path in git_lines(root, "ls-files", "--others", "--exclude-standard"):
        states[path] = "ADDED"
    relative_output = output.resolve().relative_to(root.resolve()).as_posix()
    states[relative_output] = "ADDED"
    return states


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.repository_root.resolve(strict=True)
    output = args.output.resolve()
    states = collect(root, args.baseline, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "relative_path",
                "change_type",
                "status",
                "evidence_path",
                "test_reference",
            ]
        )
        for path, change_type in sorted(states.items()):
            writer.writerow(
                [
                    path,
                    change_type,
                    "UNCOMMITTED_LOCAL_HOLD",
                    "docs/omega_stage2/phase4/PHASE4_FILES_CREATED_OR_CHANGED.csv",
                    "git diff --name-status 1ff7a2b and git ls-files --others",
                ]
            )
    print(f"wrote {len(states)} Phase 4 file rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
