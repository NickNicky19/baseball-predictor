#!/usr/bin/env python3
"""Independently certify the bound open-2026 benchmark sources."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.open_2026_benchmark_sources import sha256, validate_source_manifest  # noqa: E402


def atomic_json(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        handle.write((json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    validate_source_manifest(payload, evidence_root=args.evidence_root)
    certificate = {
        "schema_version": "open-2026-probability-benchmark-source-certificate-v1",
        "status": "OPEN_2026_BENCHMARK_SOURCES_CERTIFIED",
        "betting_authorized": False,
        "may_2026_opened": False,
        "production_unchanged": True,
        "source_manifest": {
            "path": args.manifest.resolve().relative_to(args.evidence_root.resolve()).as_posix(),
            "sha256": sha256(args.manifest),
        },
        "dates": payload["dates"],
        "official_feed_count": payload["official_feed_count"],
        "official_outcome_rows": payload["official_outcome_rows"],
        "lineup_snapshot_rows": payload["lineup_snapshot_rows"],
        "crosscheck": payload["crosscheck"],
        "total_bases_production_comparator_available": False,
    }
    atomic_json(certificate, args.out)
    print("OPEN_2026_BENCHMARK_SOURCES_CERTIFIED")
    print(f"certificate_sha256={sha256(args.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
