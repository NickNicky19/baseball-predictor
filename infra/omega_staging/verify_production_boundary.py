"""Fail closed if protected production automation differs from its frozen manifest."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


LINE = re.compile(r"^(?P<digest>[0-9a-f]{64}) {2}(?P<path>[^\r\n]+)$")


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    manifest = Path(__file__).with_name("production-boundary.sha256")
    failures: list[str] = []
    checked = 0
    for number, raw_line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line:
            continue
        match = LINE.fullmatch(raw_line)
        if match is None:
            failures.append(f"line {number}: malformed manifest entry")
            continue
        relative = Path(match.group("path"))
        target = (root / relative).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            failures.append(f"line {number}: path escapes repository: {relative}")
            continue
        if not target.is_file():
            failures.append(f"missing protected file: {relative.as_posix()}")
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != match.group("digest"):
            failures.append(
                f"protected file changed: {relative.as_posix()} "
                f"expected={match.group('digest')} actual={actual}"
            )
        checked += 1
    if checked == 0:
        failures.append("manifest contains no protected files")
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    print(f"production boundary verified: {checked} protected files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
