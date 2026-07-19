#!/usr/bin/env python3
"""Verify the immutable runtime contract for the future pitcher collector."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_forward_pitcher_context_collector import load_runtime  # noqa: E402


def fails(path: Path) -> bool:
    try:
        load_runtime(path)
    except ValueError:
        return True
    return False


def main() -> int:
    source = ROOT / "config/forward_pitcher_context_runtime_v1.json"
    payload, observed = load_runtime(source)
    assert observed == hashlib.sha256(source.read_bytes()).hexdigest()
    print("[OK] locked public-source, scheduler, and research-only runtime validates")
    with tempfile.TemporaryDirectory(prefix="pitcher_runtime_") as temporary:
        path = Path(temporary) / "runtime.json"
        for label, mutate in (
            ("source mutation", lambda p: p["source"].update(base_url="https://example.invalid")),
            ("unsafe scope", lambda p: p.update(scope="economic_execution")),
            ("negative tolerance", lambda p: p["scheduler"].update(max_early_seconds=-1)),
            ("backfill mutation", lambda p: p["invariants"].update(backfill_forbidden=False)),
            ("authorization mutation", lambda p: p["invariants"].update(betting_authorized=True)),
        ):
            changed = copy.deepcopy(payload)
            mutate(changed)
            path.write_text(json.dumps(changed), encoding="utf-8")
            assert fails(path), label
            print(f"[OK] MUTATION {label} fails")
    print("6/6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
