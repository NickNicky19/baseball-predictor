#!/usr/bin/env python3
"""Validate corrected pre-2026 shared-PA inputs without scoring a model."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import sha256  # noqa: E402
from src.evaluation.shared_pa_benchmark_protocol import load_protocol  # noqa: E402
from src.evaluation.shared_pa_training_data import validate_hitter_frame  # noqa: E402


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    protocol_path = ROOT / "config/shared_pa_benchmark_protocol.json"
    protocol = load_protocol(protocol_path, evidence_root=args.evidence_root)
    foundation = json.loads((ROOT / "config/multi_market_probability_foundation_protocol.json").read_text(encoding="utf-8"))
    rel = foundation["training_inputs"]["corrected_hitters"]["path"]
    expected = foundation["training_inputs"]["corrected_hitters"]["sha256"]
    source = args.evidence_root / rel
    if sha256(source) != expected:
        raise ValueError("corrected hitter input hash changed before audit")
    frame = pd.read_csv(source, low_memory=False)
    report = validate_hitter_frame(frame, protocol)
    report.update({
        "schema_version": "shared-pa-training-input-audit-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {"path": rel, "sha256": expected},
        "protocol": {
            "path": "config/shared_pa_benchmark_protocol.json",
            "sha256": sha256(protocol_path),
        },
    })
    atomic_json(args.out, report)
    print(json.dumps({key: report[key] for key in ("status", "rows", "games", "rows_by_season")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
