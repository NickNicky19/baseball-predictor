#!/usr/bin/env python3
"""Fail without echoing values when tracked source contains likely credentials."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

RULES = {
    "odds_api_environment_assignment": re.compile(
        rb"(?:ODDS_API_KEY|THE_ODDS_API_KEY)\s*[:=]\s*[\"']?[A-Za-z0-9_-]{24,}"
    ),
    "odds_api_query_parameter": re.compile(rb"apiKey=[A-Za-z0-9_-]{24,}"),
    "github_personal_access_token": re.compile(rb"(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "aws_access_key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "private_key_block": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return [ROOT / value.decode("utf-8") for value in result.stdout.split(b"\0") if value]


def scan(paths: list[Path]) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in paths:
        if path.resolve() == SELF or not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        for rule_name, pattern in RULES.items():
            for match in pattern.finditer(data):
                line = data.count(b"\n", 0, match.start()) + 1
                relative = path.relative_to(ROOT).as_posix()
                findings.append((relative, line, rule_name))
    return findings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths",
        nargs="*",
        help="Optional repository-relative paths; default scans Git-tracked files",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = [ROOT / value for value in args.paths] if args.paths else tracked_files()
    findings = scan(paths)
    if findings:
        print("[FAIL] likely credential material found; values are intentionally suppressed")
        for path, line, rule in findings:
            print(f"  {path}:{line}  rule={rule}")
        return 2
    print(f"[OK] no likely credentials found in {len(paths)} inspected tracked files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
