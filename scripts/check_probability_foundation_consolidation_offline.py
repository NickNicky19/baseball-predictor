#!/usr/bin/env python3
"""Mutation tests for the probability-foundation consolidation validator."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from validate_probability_foundation_consolidation import ROOT, validate


def _fails(protocol: Path) -> None:
    try:
        validate(protocol, root=ROOT)
    except ValueError:
        return
    raise AssertionError("mutation unexpectedly validated")


def main() -> None:
    source = ROOT / "config" / "probability_foundation_consolidation_v1.json"
    with tempfile.TemporaryDirectory(prefix="probability_foundation_consolidation_") as temp:
        work = Path(temp) / "protocol.json"
        shutil.copyfile(source, work)
        assert validate(work, root=ROOT)["record_count"] == 10
        print("[OK] exact locked evidence validates")

        data = json.loads(work.read_text(encoding="utf-8"))
        data["scope"]["betting_authorized"] = True
        work.write_text(json.dumps(data), encoding="utf-8")
        _fails(work)
        print("[OK] MUTATION authorization weakening fails")

        shutil.copyfile(source, work)
        data = json.loads(work.read_text(encoding="utf-8"))
        data["records"][0]["sha256"] = "0" * 64
        work.write_text(json.dumps(data), encoding="utf-8")
        _fails(work)
        print("[OK] MUTATION evidence hash drift fails")

        shutil.copyfile(source, work)
        data = json.loads(work.read_text(encoding="utf-8"))
        data["records"][1]["id"] = data["records"][0]["id"]
        work.write_text(json.dumps(data), encoding="utf-8")
        _fails(work)
        print("[OK] MUTATION duplicate record id fails")

        shutil.copyfile(source, work)
        data = json.loads(work.read_text(encoding="utf-8"))
        data["records"] = data["records"][:-1]
        work.write_text(json.dumps(data), encoding="utf-8")
        _fails(work)
        print("[OK] MUTATION omitted evidence fails")


if __name__ == "__main__":
    main()
