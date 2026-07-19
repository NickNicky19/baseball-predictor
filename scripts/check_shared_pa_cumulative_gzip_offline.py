#!/usr/bin/env python3
"""Prove cumulative feature gzip bytes are independent of temp filenames."""
from __future__ import annotations

import gzip
import hashlib
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_shared_pa_cumulative_history_features import atomic_csv_gz  # noqa: E402


def main() -> int:
    frame = pd.DataFrame({"game_pk": [2, 1], "probability": [0.25, 0.75]})
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        first = root / "first-name.csv.gz"
        second = root / "different-name.csv.gz"
        atomic_csv_gz(first, frame)
        atomic_csv_gz(second, frame)
        first_bytes = first.read_bytes()
        second_bytes = second.read_bytes()
        checks = [
            ("byte-identical across filenames", first_bytes == second_bytes),
            ("gzip magic", first_bytes[:3] == b"\x1f\x8b\x08"),
            ("no optional filename header", first_bytes[3] == 0),
            ("zero mtime", first_bytes[4:8] == b"\x00\x00\x00\x00"),
            ("content round-trip", gzip.decompress(first_bytes).decode("utf-8") == "game_pk,probability\n2,0.25\n1,0.75\n"),
            ("stable digest", hashlib.sha256(first_bytes).hexdigest() == hashlib.sha256(second_bytes).hexdigest()),
        ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"cumulative gzip checks failed: {failed}")
    print(f"CUMULATIVE GZIP VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
