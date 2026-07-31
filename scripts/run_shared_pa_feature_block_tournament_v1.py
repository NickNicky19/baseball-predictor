"""Run or verify the locked finite C0 feature-block tournament."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.evaluation.shared_pa_feature_block_tournament_v1 import (
    run_feature_block_tournament,
    verify_feature_block_release,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label-release", required=True, type=Path)
    parser.add_argument("--parent-release", required=True, type=Path)
    parser.add_argument("--c0-protocol", required=True, type=Path)
    parser.add_argument("--feature-protocol", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if bool(args.output) == bool(args.verify):
        parser.error("provide exactly one of --output or --verify")
    if args.verify:
        print(verify_feature_block_release(args.verify))
    else:
        print(run_feature_block_tournament(
            label_release=args.label_release,
            parent_release=args.parent_release,
            c0_protocol_path=args.c0_protocol,
            feature_protocol_path=args.feature_protocol,
            output_dir=args.output,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
