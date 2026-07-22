"""Crash-safe orchestration for immutable forward-shadow lifecycle evidence.

This module contains no network access and no betting authorization.  It
commits research selections from an already verified T-4h capture to the
append-only ledger while preserving a complete selected/no-selection funnel.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.evaluation.shadow_ledger import ForwardShadowLedger, ShadowEntry
from src.evaluation.shadow_live_provider import artifact_bytes
from src.evaluation.shadow_provider_adapter import build_shadow_entry_decision
from src.evaluation.shadow_target_capture import load_target_capture_bundle
from src.utils.provenance import sha256_file


SELECTION_SCHEMA = "shadow-research-selection-funnel-v1"
COMMIT_SCHEMA = "shadow-research-entry-commit-v1"


class ShadowLifecycleError(ValueError):
    """Raised when a lifecycle transition would weaken forward evidence."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _publish_once(path: Path, payload: object) -> bool:
    data = artifact_bytes(payload)
    if path.exists():
        if path.read_bytes() != data:
            raise ShadowLifecycleError(f"immutable lifecycle artifact conflict: {path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
    return True


def _decisions(
    *,
    bundle_path: Path,
    selection_policy_path: Path,
) -> tuple[list[ShadowEntry], list[dict[str, Any]]]:
    bundle = load_target_capture_bundle(bundle_path)
    entries: list[ShadowEntry] = []
    rows: list[dict[str, Any]] = []
    for quote_sha in sorted(bundle.quote_sha256):
        entry, status = build_shadow_entry_decision(
            bundle_path=bundle_path,
            selection_policy_path=selection_policy_path,
            quote_sha256=quote_sha,
        )
        row: dict[str, Any] = {
            "quote_sha256": quote_sha,
            "status": status,
            "entry_id": None,
            "selection_side": None,
        }
        if entry is not None:
            entries.append(entry)
            row["entry_id"] = entry.entry_id
            row["selection_side"] = entry.selection_side
        rows.append(row)
    if len(rows) != len(bundle.quote_sha256):
        raise ShadowLifecycleError("selection funnel does not account for every resolved quote")
    if len({row["quote_sha256"] for row in rows}) != len(rows):
        raise ShadowLifecycleError("selection funnel repeats a quote hash")
    if len({entry.entry_id for entry in entries}) != len(entries):
        raise ShadowLifecycleError("selection funnel creates duplicate immutable entries")
    return entries, rows


def commit_target_entries(
    *,
    bundle_path: str | Path,
    selection_policy_path: str | Path,
    ledger_path: str | Path,
    artifact_root: str | Path,
    clock: Callable[[], str] = _now,
    ledger_clock: Callable[[], datetime] | None = None,
) -> Path:
    """Commit every selected research entry and name every non-selection.

    Publishing the deterministic funnel before the ledger append makes a crash
    recoverable.  Retrying the same transition is idempotent; a changed bundle,
    policy, selection, or ledger record is a hard failure.
    """

    bundle_source = Path(bundle_path).resolve()
    policy_source = Path(selection_policy_path).resolve()
    bundle = load_target_capture_bundle(bundle_source)
    target_id = str(bundle.target["target_id"])
    target_root = Path(artifact_root).resolve() / str(bundle.target["official_game_date"]) / target_id
    funnel_path = target_root / "selection_funnel.json"
    commit_path = target_root / "entry_commit.json"

    entries, rows = _decisions(
        bundle_path=bundle_source,
        selection_policy_path=policy_source,
    )
    counts = dict(sorted(Counter(row["status"] for row in rows).items()))
    funnel = {
        "schema_version": SELECTION_SCHEMA,
        "target_id": target_id,
        "plan_sha256": bundle.plan_sha256,
        "bundle_sha256": bundle.bundle_sha256,
        "selection_policy_sha256": sha256_file(policy_source),
        "resolved_quote_count": len(bundle.quote_sha256),
        "selected_entry_count": len(entries),
        "status_counts": counts,
        "rows": rows,
        "all_quotes_accounted_for": len(rows) == len(bundle.quote_sha256),
        "research_only": True,
        "betting_authorized": False,
    }
    _publish_once(funnel_path, funnel)

    ledger = ForwardShadowLedger(ledger_path, clock=ledger_clock)
    expected_ids = sorted(entry.entry_id for entry in entries)
    if commit_path.exists():
        try:
            prior = json.loads(commit_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ShadowLifecycleError("entry commit artifact is malformed") from exc
        if (
            prior.get("schema_version") != COMMIT_SCHEMA
            or prior.get("target_id") != target_id
            or prior.get("selection_funnel_sha256") != sha256_file(funnel_path)
            or prior.get("entry_ids") != expected_ids
        ):
            raise ShadowLifecycleError("entry commit artifact differs from deterministic selection evidence")
        missing = sorted(set(expected_ids) - ledger.recorded_entry_ids())
        if missing:
            raise ShadowLifecycleError("entry commit claims entries absent from the verified ledger")
        return commit_path

    report = ledger.append_entries(entries)
    missing = sorted(set(expected_ids) - ledger.recorded_entry_ids())
    if missing:
        raise ShadowLifecycleError("verified ledger does not contain every selected entry")
    commit = {
        "schema_version": COMMIT_SCHEMA,
        "target_id": target_id,
        "selection_funnel_sha256": sha256_file(funnel_path),
        "entry_ids": expected_ids,
        "selected_entry_count": len(expected_ids),
        "ledger_append_added": report.added,
        "ledger_append_idempotent": report.idempotent,
        "committed_at_utc": clock(),
        "research_only": True,
        "betting_authorized": False,
    }
    _publish_once(commit_path, commit)
    return commit_path
