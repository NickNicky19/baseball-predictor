#!/usr/bin/env python3
"""Independently verify synchronized entry, selection, close, and settlement evidence."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_shadow_capture_tree import verify_capture_tree  # noqa: E402
from src.evaluation.shadow_capture_plan import CaptureAttempt, load_capture_plan  # noqa: E402
from src.evaluation.shadow_ledger import ForwardShadowLedger  # noqa: E402
from src.evaluation.shadow_live_provider import validate_resolved_hits_payload  # noqa: E402
from src.evaluation.shadow_target_capture import (  # noqa: E402
    load_target_capture_bundle,
    relocate_bundle_artifact_path,
)
from src.evaluation.forward_evidence_era import validate_evidence_scope  # noqa: E402
from src.utils.provenance import sha256_file  # noqa: E402


class ShadowLifecycleTreeError(ValueError):
    """Raised when synchronized lifecycle evidence is missing or tampered."""


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ShadowLifecycleTreeError("lifecycle timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ShadowLifecycleTreeError(f"{label} is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ShadowLifecycleTreeError(f"{label} is malformed: {path}") from exc
    if not isinstance(payload, dict):
        raise ShadowLifecycleTreeError(f"{label} root must be an object")
    return payload


def verify_lifecycle_tree(
    *,
    plan_path: Path,
    live_root: Path,
    lifecycle_root: Path,
    close_root: Path,
    official_root: Path,
    ledger_path: Path,
    assessed_at_utc: str,
) -> dict[str, Any]:
    capture = verify_capture_tree(
        plan_path=plan_path,
        artifact_root=live_root,
        assessed_at_utc=assessed_at_utc,
    )
    plan = load_capture_plan(plan_path)
    ledger = ForwardShadowLedger(ledger_path)
    ledger_report = ledger.verify()
    records = ledger.verified_records()
    ledger_entry_ids = {
        str(record["entry_id"]) for record in records if record["record_type"] == "entry"
    }
    entry_by_id = {
        str(record["entry_id"]): record for record in records if record["record_type"] == "entry"
    }
    counts: Counter[str] = Counter()
    as_of = _utc(assessed_at_utc)

    for target in plan.targets:
        live_target = live_root / target.official_game_date / target.target_id
        terminal_path = live_target / "terminal_attempt.json"
        if not terminal_path.is_file():
            continue
        attempt = CaptureAttempt.from_mapping(_json(terminal_path, "terminal attempt"))
        if attempt.outcome == "source_error":
            counts["entry_source_error"] += 1
            continue
        bundle = load_target_capture_bundle(
            live_target / "target_bundle.json",
            relocation_root=live_target,
        )
        lifecycle_target = lifecycle_root / target.official_game_date / target.target_id
        funnel_path = lifecycle_target / "selection_funnel.json"
        commit_path = lifecycle_target / "entry_commit.json"
        funnel = _json(funnel_path, "selection funnel")
        commit = _json(commit_path, "entry commit")
        if (
            funnel.get("schema_version") != "shadow-research-selection-funnel-v1"
            or funnel.get("target_id") != target.target_id
            or funnel.get("bundle_sha256") != bundle.bundle_sha256
            or funnel.get("selection_policy_sha256") != plan.policy_sha256
            or funnel.get("betting_authorized") is not False
        ):
            raise ShadowLifecycleTreeError("selection funnel differs from target, bundle, or policy")
        rows = funnel.get("rows")
        if not isinstance(rows, list) or len(rows) != len(bundle.quote_sha256):
            raise ShadowLifecycleTreeError("selection funnel does not account for every quote")
        status_counts = dict(sorted(Counter(str(row.get("status")) for row in rows).items()))
        if status_counts != funnel.get("status_counts"):
            raise ShadowLifecycleTreeError("selection funnel status counts do not reconcile")
        selected = sorted(str(row["entry_id"]) for row in rows if row.get("entry_id"))
        if (
            commit.get("schema_version") != "shadow-research-entry-commit-v1"
            or commit.get("target_id") != target.target_id
            or commit.get("selection_funnel_sha256") != sha256_file(funnel_path)
            or commit.get("entry_ids") != selected
            or commit.get("betting_authorized") is not False
        ):
            raise ShadowLifecycleTreeError("entry commit differs from the selection funnel")
        if not set(selected).issubset(ledger_entry_ids):
            raise ShadowLifecycleTreeError("entry commit names an entry absent from the verified ledger")
        counts["selection_committed_targets"] += 1
        counts["selected_entries"] += len(selected)

        if attempt.outcome == "no_eligible_market":
            counts["prestart_not_applicable_targets"] += 1
            continue
        close_target = close_root / target.official_game_date / target.target_id
        close_bundle_path = close_target / "prestart_reference_bundle.json"
        close_error_path = close_target / "prestart_terminal_error.json"
        if close_error_path.exists():
            raise ShadowLifecycleTreeError("prestart reference contains a terminal source error")
        if as_of < _utc(target.official_start_time_utc):
            counts["prestart_future_targets"] += 1
            continue
        close_bundle = _json(close_bundle_path, "prestart reference bundle")
        if (
            close_bundle.get("schema_version") != "shadow-prestart-reference-bundle-v1"
            or close_bundle.get("target_id") != target.target_id
            or close_bundle.get("entry_bundle_sha256") != bundle.bundle_sha256
            or close_bundle.get("actual_fill") is not False
            or close_bundle.get("betting_authorized") is not False
        ):
            raise ShadowLifecycleTreeError("prestart reference bundle changed scope or identity")
        raw = relocate_bundle_artifact_path(
            close_bundle["raw_provider_artifact_path"],
            target_id=target.target_id,
            relocation_root=close_target,
        )
        resolved = relocate_bundle_artifact_path(
            close_bundle["resolved_quote_artifact_path"],
            target_id=target.target_id,
            relocation_root=close_target,
        )
        if (
            sha256_file(raw) != close_bundle.get("raw_provider_artifact_sha256")
            or sha256_file(resolved) != close_bundle.get("resolved_quote_artifact_sha256")
        ):
            raise ShadowLifecycleTreeError("prestart reference bound artifact is missing or tampered")
        validate_resolved_hits_payload(_json(resolved, "prestart resolved quotes"))
        coverage_rows = close_bundle.get("coverage_rows")
        if not isinstance(coverage_rows, list) or len(coverage_rows) != len(bundle.quote_sha256):
            raise ShadowLifecycleTreeError("prestart reference does not account for every entry market")
        counts["prestart_verified_targets"] += 1

    verified_resolution_artifacts = 0
    for record in records:
        if record["record_type"] != "resolution":
            continue
        entry = entry_by_id[str(record["entry_id"])]
        if str(entry["game_date"]) != plan.official_game_date:
            continue
        artifact = (
            official_root
            / str(entry["game_date"])
            / str(entry["mlb_game_pk"])
            / "entries"
            / f"{entry['entry_id']}.json"
        )
        expected = (
            record.get("official_outcome_artifact_sha256")
            if record["settlement_status"] == "graded"
            else record.get("settlement_evidence_artifact_sha256")
        )
        if not artifact.is_file() or sha256_file(artifact) != expected:
            raise ShadowLifecycleTreeError("ledger resolution lacks its exact official disposition artifact")
        verified_resolution_artifacts += 1

    unresolved = [entry for entry in ledger.unresolved_entries() if entry.game_date == plan.official_game_date]
    complete_due_phases = bool(capture["complete"])
    return {
        "schema_version": "shadow-lifecycle-tree-verification-v1",
        "assessed_at_utc": assessed_at_utc,
        "plan_sha256": plan.plan_sha256,
        "entry_capture": capture,
        "lifecycle_counts": dict(sorted(counts.items())),
        "ledger_records": ledger_report.total_records,
        "ledger_head_hash": ledger_report.head_hash,
        "verified_resolution_artifacts": verified_resolution_artifacts,
        "unresolved_entries": len(unresolved),
        "complete_due_entry_and_prestart_phases": complete_due_phases,
        "settlement_complete": len(unresolved) == 0,
        "primary_collector": False,
        "replacement_odds_fetched": False,
        "betting_authorized": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--live-root", required=True)
    parser.add_argument("--lifecycle-root", required=True)
    parser.add_argument("--close-root", required=True)
    parser.add_argument("--official-root", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--evidence-scope", required=True)
    parser.add_argument("--as-of", help="UTC timestamp; default now")
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    as_of = args.as_of or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = verify_lifecycle_tree(
        plan_path=Path(args.plan).resolve(),
        live_root=Path(args.live_root).resolve(),
        lifecycle_root=Path(args.lifecycle_root).resolve(),
        close_root=Path(args.close_root).resolve(),
        official_root=Path(args.official_root).resolve(),
        ledger_path=Path(args.ledger).resolve(),
        assessed_at_utc=as_of,
    )
    scope_path = Path(args.evidence_scope).resolve()
    scope = validate_evidence_scope(scope_path, root=ROOT)
    report["evidence_scope_sha256"] = sha256_file(scope_path)
    report["evidence_scope_mode"] = scope["mode"]
    report["economic_evidence_eligible"] = scope["economic_evidence_eligible"]
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("INDEPENDENT SHADOW LIFECYCLE VERIFICATION")
    print(f"  ledger records: {report['ledger_records']}")
    print(f"  unresolved entries: {report['unresolved_entries']}")
    print("  replacement odds fetched: FALSE; betting authorized: FALSE")
    if not report["complete_due_entry_and_prestart_phases"]:
        print("[FAIL] due entry or prestart evidence is incomplete")
        return 2
    print("[OK] all currently due entry and prestart phases are independently verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(2)
