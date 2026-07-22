#!/usr/bin/env python3
"""Finalize one already-fetched, hard-resolved T-horizon shadow capture."""

from __future__ import annotations

import argparse
import sys

from src.evaluation.shadow_capture_plan import load_capture_plan
from src.evaluation.shadow_target_capture import (
    ShadowTargetCaptureError,
    finalize_target_capture,
    publish_bundle,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--target-id", required=True)
    ap.add_argument("--started-at-utc", required=True)
    ap.add_argument("--completed-at-utc", required=True)
    ap.add_argument("--source-name", required=True)
    ap.add_argument("--expected-config-sha256", required=True)
    ap.add_argument("--prediction-archive", required=True)
    ap.add_argument("--raw-provider-artifact", required=True)
    ap.add_argument("--resolved-quote-artifact", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        bundle = finalize_target_capture(
            plan=load_capture_plan(args.plan),
            target_id=args.target_id,
            started_at_utc=args.started_at_utc,
            completed_at_utc=args.completed_at_utc,
            source_name=args.source_name,
            expected_config_sha256=args.expected_config_sha256,
            prediction_archive=args.prediction_archive,
            raw_provider_artifact=args.raw_provider_artifact,
            resolved_quote_artifact=args.resolved_quote_artifact,
        )
        created = publish_bundle(bundle, args.out)
    except (OSError, ValueError, ShadowTargetCaptureError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2
    print(f"target capture {'published' if created else 'idempotent'}: {args.out}")
    print(f"bundle_sha256={bundle.bundle_sha256}")
    print("ledger appended: FALSE; betting authorized: FALSE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
