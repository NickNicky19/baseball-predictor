"""Run or independently verify the locked 2023 shared-PA C0 tournament."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_c0_tournament_v1 import (
    run_tournament,
    verify_tournament_release,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label-release", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=ROOT / "config" / "shared_pa_c0_protocol_v1.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_only:
        if args.output is None:
            parser.error("--output is required with --verify-only")
        print(json.dumps(verify_tournament_release(args.output), sort_keys=True))
        return 0
    if args.output is None:
        parser.error("--output is required")
    release = run_tournament(label_release=args.label_release, protocol_path=args.protocol, output_dir=args.output)
    verification = verify_tournament_release(args.output, expected_release_sha256=release["release_sha256"])
    print(json.dumps({"release": release, "verification": verification}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
