#!/usr/bin/env python3
"""Run the single locked 2024 PA-volume repair adjudication."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.pa_volume_chronology import sha256_file  # noqa: E402
from src.evaluation.pa_volume_market_adjudication import (  # noqa: E402
    adjudicate,
    read_2023_2024_without_2025_outcomes,
)
from src.features.pa_volume_gate import load_pa_volume_artifact  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    source = Path(args.source).resolve()
    artifact_path = Path(args.artifact).resolve()
    protocol_path = Path(args.protocol).resolve()
    output = Path(args.output).resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite single-selection report: {output}")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    artifact_sha256 = sha256_file(artifact_path)
    artifact = load_pa_volume_artifact(artifact_path, artifact_sha256)
    frame = read_2023_2024_without_2025_outcomes(source)
    report = adjudicate(
        frame=frame,
        artifact=artifact,
        protocol=protocol,
        source_path=str(source),
        source_sha256=sha256_file(source),
        artifact_sha256=artifact_sha256,
        protocol_sha256=sha256_file(protocol_path),
    )
    payload = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({
        "output": str(output),
        "sha256": sha256_file(output),
        "status": report["status"],
        "promotion_decision": report["promotion_decision"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
