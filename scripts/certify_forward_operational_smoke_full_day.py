#!/usr/bin/env python3
"""Certify an operational smoke only after its entire planned day is complete.

This wrapper makes no provider requests.  It independently rebuilds the
lifecycle verification, requires every planned target (not merely every target
due so far), and then delegates certificate creation to the locked schema-v2
artifact-chain certifier.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_shadow_lifecycle_tree import verify_lifecycle_tree  # noqa: E402
from src.evaluation.forward_evidence_era import (  # noqa: E402
    certify_operational_smoke,
    validate_evidence_scope,
)
from src.evaluation.shadow_capture_plan import load_capture_plan  # noqa: E402
from src.utils.provenance import sha256_file  # noqa: E402


class FullDaySmokeError(ValueError):
    """Raised when a lifecycle is incomplete for the whole planned day."""


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise FullDaySmokeError("timestamps must be timezone-aware UTC values")
    return parsed.astimezone(timezone.utc)


def require_full_day_completion(plan: Any, report: dict[str, Any]) -> None:
    """Reject a currently-due subset masquerading as a completed smoke day."""

    total = len(plan.targets)
    if total <= 0:
        raise FullDaySmokeError("operational smoke plan has no targets")
    if report.get("official_game_date") != plan.official_game_date:
        raise FullDaySmokeError("lifecycle report is bound to a different official date")

    coverage = report.get("entry_capture", {}).get("coverage")
    if not isinstance(coverage, dict):
        raise FullDaySmokeError("lifecycle report lacks entry-capture coverage")
    if int(coverage.get("due_targets", -1)) != total or int(coverage.get("future_targets", -1)) != 0:
        raise FullDaySmokeError("the entire planned day is not yet due")
    if int(report.get("entry_capture", {}).get("terminal_receipts_loaded", -1)) != total:
        raise FullDaySmokeError("not every planned target has a terminal receipt")
    if (
        coverage.get("complete") is not True
        or coverage.get("missing_target_ids") != []
        or int(coverage.get("source_error_targets", -1)) != 0
        or int(coverage.get("observed_targets", -1)) != total
    ):
        raise FullDaySmokeError("entry capture is missing, failed, or count-incomplete")

    assessed_at = _utc(str(report.get("assessed_at_utc", "")))
    latest_start = max(_utc(target.official_start_time_utc) for target in plan.targets)
    if assessed_at < latest_start:
        raise FullDaySmokeError("lifecycle was assessed before the final planned game start")

    counts = report.get("lifecycle_counts")
    if not isinstance(counts, dict):
        raise FullDaySmokeError("lifecycle counts are missing")
    if int(counts.get("selection_committed_targets", 0)) != total:
        raise FullDaySmokeError("not every planned target has a committed selection funnel")
    prestart_accounted = int(counts.get("prestart_verified_targets", 0)) + int(
        counts.get("prestart_not_applicable_targets", 0)
    )
    if prestart_accounted != total or int(counts.get("prestart_future_targets", 0)) != 0:
        raise FullDaySmokeError("prestart evidence is not complete for every planned target")

    selected = int(counts.get("selected_entries", 0))
    if selected <= 0:
        raise FullDaySmokeError("the smoke selected no entries and did not exercise settlement")
    if (
        report.get("complete_due_entry_and_prestart_phases") is not True
        or report.get("settlement_complete") is not True
        or int(report.get("unresolved_entries", -1)) != 0
        or int(report.get("verified_resolution_artifacts", -1)) != selected
    ):
        raise FullDaySmokeError("official settlement is incomplete or does not reconcile")
    if report.get("replacement_odds_fetched") is not False or report.get("betting_authorized") is not False:
        raise FullDaySmokeError("smoke scope changed into replacement or betting behavior")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--live-root", required=True)
    parser.add_argument("--lifecycle-root", required=True)
    parser.add_argument("--close-root", required=True)
    parser.add_argument("--official-root", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--evidence-scope", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--verification-out", required=True)
    parser.add_argument("--certificate-out", required=True)
    parser.add_argument("--as-of", help="UTC timestamp; defaults to now")
    args = parser.parse_args(argv)

    plan_path = Path(args.plan).resolve()
    scope_path = Path(args.evidence_scope).resolve()
    verification_path = Path(args.verification_out).resolve()
    certificate_path = Path(args.certificate_out).resolve()
    scope_root = scope_path.parent
    if not verification_path.is_relative_to(scope_root) or not certificate_path.is_relative_to(scope_root):
        raise FullDaySmokeError("verification and certificate must stay inside the smoke root")

    as_of = args.as_of or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    plan = load_capture_plan(plan_path)
    if plan.official_game_date != args.date:
        raise FullDaySmokeError("requested date differs from the immutable plan")
    report = verify_lifecycle_tree(
        plan_path=plan_path,
        live_root=Path(args.live_root).resolve(),
        lifecycle_root=Path(args.lifecycle_root).resolve(),
        close_root=Path(args.close_root).resolve(),
        official_root=Path(args.official_root).resolve(),
        ledger_path=Path(args.ledger).resolve(),
        assessed_at_utc=as_of,
    )
    scope = validate_evidence_scope(scope_path, root=ROOT, require_current_runtime=False)
    report["evidence_scope_sha256"] = sha256_file(scope_path)
    report["evidence_scope_mode"] = scope["mode"]
    report["economic_evidence_eligible"] = scope["economic_evidence_eligible"]
    require_full_day_completion(plan, report)
    _atomic_json(verification_path, report)

    certificate = certify_operational_smoke(
        evidence_scope_path=scope_path,
        lifecycle_verification_path=verification_path,
        official_game_date=args.date,
        certificate_path=certificate_path,
        root=ROOT,
    )
    _atomic_json(certificate_path, certificate)
    print("FULL-DAY OPERATIONAL SMOKE CERTIFIED")
    print(f"  targets: {len(plan.targets)}; unresolved entries: 0")
    print("  economic evidence eligible: FALSE; betting authorized: FALSE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
