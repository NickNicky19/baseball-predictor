#!/usr/bin/env python3
"""Build the versioned 2023-only PA-volume integrity artifact."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.pa_volume_chronology import (  # noqa: E402
    fit_2023_pa_volume,
    read_seasons_without_later_outcomes,
    sha256_file,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    if not source.is_file():
        raise SystemExit(f"source does not exist: {source}")
    if output.exists():
        raise SystemExit(f"refusing to overwrite versioned artifact: {output}")
    frame = read_seasons_without_later_outcomes(source, seasons=(2023,))
    artifact = fit_2023_pa_volume(
        frame,
        source_path=str(source),
        source_sha256=sha256_file(source),
    )
    payload = (json.dumps(artifact.to_dict(), indent=2, sort_keys=True) + "\n").encode("utf-8")
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
        "fit_rows": artifact.fit_rows,
        "fit_games": artifact.fit_games,
        "date_min": artifact.date_min,
        "date_max": artifact.date_max,
        "source_projection_sha256": artifact.source_projection_sha256,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
