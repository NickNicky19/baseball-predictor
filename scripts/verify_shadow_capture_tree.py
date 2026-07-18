#!/usr/bin/env python3
"""Independently verify due shadow targets from a synchronized artifact tree.

This verifier never fetches replacement odds.  It is suitable for GitHub or a
second machine after the primary collector synchronizes its immutable files.
Missing, late, source-error, or tampered evidence makes the verification fail.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shadow_capture_plan import (  # noqa: E402
    CaptureAttempt,
    ShadowCapturePlanError,
    assess_capture_attempts,
    load_capture_plan,
)
from src.evaluation.shadow_target_capture import (  # noqa: E402
    ShadowTargetCaptureError,
    load_resolved_quotes,
    load_target_capture_bundle,
    relocate_bundle_artifact_path,
)
from src.utils.provenance import sha256_file  # noqa: E402


class ShadowCaptureTreeError(ValueError):
    """Raised when synchronized primary evidence is missing or inconsistent."""


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ShadowCaptureTreeError(f"{label} is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ShadowCaptureTreeError(f"{label} is malformed: {path}") from exc
    if not isinstance(payload, dict):
        raise ShadowCaptureTreeError(f"{label} root must be an object: {path}")
    return payload


def verify_capture_tree(
    *,
    plan_path: Path,
    artifact_root: Path,
    assessed_at_utc: str,
) -> dict[str, Any]:
    drift_path = plan_path.parent / "schedule_drift.json"
    if drift_path.exists():
        raise ShadowCaptureTreeError(
            "primary collector published unresolved official schedule drift"
        )
    plan = load_capture_plan(plan_path)
    attempts: list[CaptureAttempt] = []
    verified_bundles = 0
    verified_source_errors = 0

    for target in plan.targets:
        target_root = artifact_root / target.official_game_date / target.target_id
        terminal_path = target_root / "terminal_attempt.json"
        if not terminal_path.is_file():
            continue
        attempt = CaptureAttempt.from_mapping(_json_object(terminal_path, "terminal attempt"))
        if attempt.target_id != target.target_id or attempt.plan_sha256 != plan.plan_sha256:
            raise ShadowCaptureTreeError("terminal attempt is bound to a different target or plan")

        if attempt.outcome in {"captured", "no_eligible_market"}:
            bundle_path = target_root / "target_bundle.json"
            bundle = load_target_capture_bundle(bundle_path, relocation_root=target_root)
            bundle_attempt = CaptureAttempt.from_mapping(bundle.attempt)
            if bundle.plan_sha256 != plan.plan_sha256:
                raise ShadowCaptureTreeError("target bundle is bound to a different plan")
            if bundle.target.get("target_id") != target.target_id:
                raise ShadowCaptureTreeError("target bundle is bound to a different target")
            if bundle_attempt != attempt:
                raise ShadowCaptureTreeError("terminal attempt differs from its target bundle")
            relocated_quotes = relocate_bundle_artifact_path(
                bundle.resolved_quote_artifact_path,
                target_id=target.target_id,
                relocation_root=target_root,
            )
            quotes = load_resolved_quotes(relocated_quotes)
            if tuple(quote.quote_sha256 for quote in quotes) != bundle.quote_sha256:
                raise ShadowCaptureTreeError("relocated resolved quotes differ from bundle quote hashes")
            verified_bundles += 1
        else:
            matches = [
                path for path in (target_root / "attempts").glob("*/source_error.json")
                if path.is_file() and sha256_file(path) == attempt.source_payload_sha256
            ]
            if len(matches) != 1:
                raise ShadowCaptureTreeError(
                    "source-error terminal receipt lacks exactly one matching retained error artifact"
                )
            error = _json_object(matches[0], "source error")
            if error.get("credential_value_recorded") is not False:
                raise ShadowCaptureTreeError("source-error artifact does not prove credential redaction")
            verified_source_errors += 1
        attempts.append(attempt)

    coverage = assess_capture_attempts(
        plan,
        attempts,
        assessed_at_utc=assessed_at_utc,
    )
    return {
        "schema_version": "shadow-capture-tree-verification-v1",
        "assessed_at_utc": coverage.assessed_at_utc,
        "plan_path": str(plan_path),
        "plan_sha256": plan.plan_sha256,
        "artifact_root": str(artifact_root),
        "coverage": coverage.to_dict(),
        "terminal_receipts_loaded": len(attempts),
        "verified_target_bundles": verified_bundles,
        "verified_source_error_artifacts": verified_source_errors,
        "complete": coverage.complete,
        "betting_authorized": False,
        "verification_role": "independent_artifact_verifier_not_primary_collector",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--as-of", help="UTC timestamp; default is now")
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    as_of = args.as_of or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = verify_capture_tree(
        plan_path=Path(args.plan).resolve(),
        artifact_root=Path(args.artifact_root).resolve(),
        assessed_at_utc=as_of,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    coverage = report["coverage"]
    print("INDEPENDENT SHADOW CAPTURE VERIFICATION")
    print(f"  due: {coverage['due_targets']}  future: {coverage['future_targets']}")
    print(f"  captured: {coverage['captured_targets']}")
    print(f"  no eligible market: {coverage['no_eligible_market_targets']}")
    print(f"  source errors: {coverage['source_error_targets']}")
    print(f"  missing: {len(coverage['missing_target_ids'])}")
    print("  primary collector: FALSE; betting authorized: FALSE")
    if not report["complete"]:
        print("[FAIL] due capture evidence is incomplete or contains a source error")
        return 2
    print("[OK] every due target has independently verified terminal evidence")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FileNotFoundError,
        ShadowCapturePlanError,
        ShadowTargetCaptureError,
        ShadowCaptureTreeError,
    ) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(2)
