"""Offline CLI for the deterministic shared-PA 2023 outcome-label release."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_outcome_label_release_v1 import (
    EXPECTED_CAPTURE_DIGEST,
    build_release,
    representative_preflight,
    verify_release,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-capture-digest", default=EXPECTED_CAPTURE_DIGEST)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    preflight = representative_preflight(
        args.capture_root, expected_capture_digest=args.expected_capture_digest
    )
    if args.preflight_only:
        print(json.dumps(preflight, sort_keys=True))
        return 0
    if args.output is None:
        parser.error("--output is required unless --preflight-only is used")
    release = build_release(
        capture_root=args.capture_root,
        output_dir=args.output,
        expected_capture_digest=args.expected_capture_digest,
    )
    result = {"preflight": preflight, "release": release}
    if args.verify:
        result["verification"] = verify_release(
            args.output, expected_release_sha256=release["release_sha256"]
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
