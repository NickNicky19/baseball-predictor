"""Append-only terminal ledger for prospective pitcher-context observations.

The ledger is deliberately separate from market prices and model predictions.
It proves whether every due official-game target produced a timely schedule
fact, a source failure, or a permanently missed observation.  It cannot
backfill a missed T-horizon fact and it never authorizes betting.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from src.evaluation.forward_pitcher_context import (
    ForwardPitcherContext,
    ForwardPitcherContextError,
    load_context,
    publish_context,
)
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan


LedgerState = Literal["captured", "source_error", "missed"]
_EMPTY_CHAIN = "0" * 64


class ForwardPitcherContextLedgerError(ValueError):
    """The prospective pitcher-context evidence chain is incomplete or altered."""


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc(value: object, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ForwardPitcherContextLedgerError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ForwardPitcherContextLedgerError(f"{label} must include timezone")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardPitcherContextLedgerError(f"cannot read {label}") from exc
    if not isinstance(raw, dict):
        raise ForwardPitcherContextLedgerError(f"{label} must be an object")
    return raw


class ForwardPitcherContextLedger:
    """One immutable terminal context state per plan target."""

    def __init__(self, root: str | Path, plan: ShadowCapturePlan) -> None:
        self.root = Path(root).resolve()
        self.plan = plan
        self.targets = {target.target_id: target for target in plan.targets}
        if not self.targets:
            raise ForwardPitcherContextLedgerError("pitcher-context plan has no targets")

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def index_path(self) -> Path:
        return self.root / "terminal_index.json"

    def initialize(self) -> None:
        expected = {
            "schema_version": "forward-pitcher-context-ledger-v1",
            "plan_sha256": self.plan.plan_sha256,
            "target_ids": sorted(self.targets),
            "records": 0,
            "last_chain_sha256": _EMPTY_CHAIN,
            "entry_chain_order": [],
            "research_only": True,
            "betting_authorized": False,
        }
        if self.manifest_path.exists():
            actual = _load_object(self.manifest_path, "pitcher context manifest")
            for key, value in expected.items():
                if key in {"records", "last_chain_sha256", "entry_chain_order"}:
                    continue
                if actual.get(key) != value:
                    raise ForwardPitcherContextLedgerError("existing pitcher context ledger has a different contract")
            if not isinstance(actual.get("records"), int) or actual["records"] < 0:
                raise ForwardPitcherContextLedgerError("ledger record count is invalid")
            if not isinstance(actual.get("last_chain_sha256"), str) or len(actual["last_chain_sha256"]) != 64:
                raise ForwardPitcherContextLedgerError("ledger chain tip is invalid")
            if not isinstance(actual.get("entry_chain_order"), list):
                raise ForwardPitcherContextLedgerError("ledger chain order is invalid")
            return
        _atomic_write(self.manifest_path, _canonical_bytes(expected))
        _atomic_write(self.index_path, _canonical_bytes({"targets": {}}))

    def _append(
        self,
        *,
        target: CaptureTarget,
        state: LedgerState,
        observed_at_utc: str,
        detail: str,
        context: ForwardPitcherContext | None = None,
        raw_payload: bytes | None = None,
    ) -> str:
        self.initialize()
        if target.target_id not in self.targets:
            raise ForwardPitcherContextLedgerError("target is not in ledger plan")
        if state not in {"captured", "source_error", "missed"}:
            raise ForwardPitcherContextLedgerError("unknown terminal pitcher-context state")
        observed = _utc(observed_at_utc, "observed_at_utc")
        due = target.entry_target_at_utc
        if state in {"captured", "source_error"} and _dt(observed) > _dt(due):
            raise ForwardPitcherContextLedgerError("timely context state cannot be observed after T-horizon")
        if state == "missed" and _dt(observed) <= _dt(due):
            raise ForwardPitcherContextLedgerError("missed state cannot be declared before the T-horizon")
        clean_detail = str(detail).strip()
        if state != "captured" and not clean_detail:
            raise ForwardPitcherContextLedgerError("excluded terminal state requires a reason")
        if state == "captured":
            if context is None or raw_payload is None or clean_detail:
                raise ForwardPitcherContextLedgerError("captured state requires exactly context and raw payload")
            try:
                context.bind_target(target)
            except ForwardPitcherContextError as exc:
                raise ForwardPitcherContextLedgerError(str(exc)) from exc
            if context.plan_sha256 != self.plan.plan_sha256:
                raise ForwardPitcherContextLedgerError("context plan hash differs from ledger plan")
            if context.source_payload_sha256 != _sha(raw_payload):
                raise ForwardPitcherContextLedgerError("context raw payload hash does not match retained bytes")
        elif context is not None or raw_payload is not None:
            raise ForwardPitcherContextLedgerError("excluded state cannot claim a complete context or raw payload")

        index = _load_object(self.index_path, "pitcher context index")
        targets = index.get("targets")
        if not isinstance(targets, Mapping):
            raise ForwardPitcherContextLedgerError("pitcher context index is invalid")
        if target.target_id in targets:
            raise ForwardPitcherContextLedgerError("target already has a permanent terminal state")
        manifest = _load_object(self.manifest_path, "pitcher context manifest")
        previous = str(manifest.get("last_chain_sha256", ""))
        if len(previous) != 64:
            raise ForwardPitcherContextLedgerError("ledger chain tip is invalid")

        raw_sha: str | None = None
        raw_path: str | None = None
        context_sha: str | None = None
        context_path: str | None = None
        if state == "captured":
            assert context is not None and raw_payload is not None
            raw_sha = _sha(raw_payload)
            raw_file = self.root / "raw" / f"{raw_sha}.json"
            if raw_file.exists() and raw_file.read_bytes() != raw_payload:
                raise ForwardPitcherContextLedgerError("raw hash path has conflicting bytes")
            if not raw_file.exists():
                _atomic_write(raw_file, raw_payload)
            context_file = self.root / "contexts" / f"{target.target_id}.json"
            publish_context(context, self.plan, target, context_file)
            context_sha = context.context_sha256
            raw_path = str(raw_file.relative_to(self.root).as_posix())
            context_path = str(context_file.relative_to(self.root).as_posix())

        record = {
            "schema_version": "forward-pitcher-context-ledger-entry-v1",
            "target_id": target.target_id,
            "plan_sha256": self.plan.plan_sha256,
            "terminal_state": state,
            "observed_at_utc": observed,
            "detail": clean_detail,
            "raw_payload_sha256": raw_sha,
            "raw_payload_path": raw_path,
            "context_sha256": context_sha,
            "context_path": context_path,
            "previous_chain_sha256": previous,
        }
        chain = _sha(_canonical_bytes(record))
        entry = dict(record) | {"chain_sha256": chain}
        record_path = self.root / "records" / f"{chain}.json"
        if record_path.exists():
            raise ForwardPitcherContextLedgerError("record hash collision or prior reuse")
        _atomic_write(record_path, _canonical_bytes(entry))
        next_targets = dict(targets)
        next_targets[target.target_id] = {
            "terminal_state": state,
            "record_path": str(record_path.relative_to(self.root).as_posix()),
            "chain_sha256": chain,
        }
        next_index = {"targets": next_targets}
        next_manifest = dict(manifest)
        next_manifest["records"] = int(manifest["records"]) + 1
        next_manifest["last_chain_sha256"] = chain
        chain_order = manifest.get("entry_chain_order")
        if not isinstance(chain_order, list) or any(not isinstance(item, str) or len(item) != 64 for item in chain_order):
            raise ForwardPitcherContextLedgerError("ledger chain order is invalid")
        next_manifest["entry_chain_order"] = [*chain_order, chain]
        _atomic_write(self.index_path, _canonical_bytes(next_index))
        _atomic_write(self.manifest_path, _canonical_bytes(next_manifest))
        return chain

    def append_captured(
        self, *, target: CaptureTarget, context: ForwardPitcherContext, raw_payload: bytes
    ) -> str:
        return self._append(target=target, state="captured", observed_at_utc=context.captured_at_utc, detail="", context=context, raw_payload=raw_payload)

    def append_exclusion(
        self, *, target: CaptureTarget, state: Literal["source_error", "missed"], observed_at_utc: str, detail: str
    ) -> str:
        return self._append(target=target, state=state, observed_at_utc=observed_at_utc, detail=detail)

    def verify(self, *, assessed_at_utc: str) -> dict[str, Any]:
        self.initialize()
        assessed = _utc(assessed_at_utc, "assessed_at_utc")
        manifest = _load_object(self.manifest_path, "pitcher context manifest")
        index = _load_object(self.index_path, "pitcher context index")
        targets = index.get("targets")
        if not isinstance(targets, Mapping):
            raise ForwardPitcherContextLedgerError("pitcher context index is invalid")
        if manifest.get("plan_sha256") != self.plan.plan_sha256 or manifest.get("target_ids") != sorted(self.targets):
            raise ForwardPitcherContextLedgerError("ledger manifest plan binding drifted")
        if manifest.get("research_only") is not True or manifest.get("betting_authorized") is not False:
            raise ForwardPitcherContextLedgerError("ledger scope changed")
        if len(targets) != int(manifest.get("records", -1)):
            raise ForwardPitcherContextLedgerError("ledger index and record count differ")
        chain_order = manifest.get("entry_chain_order")
        if (
            not isinstance(chain_order, list)
            or len(chain_order) != len(targets)
            or len(set(chain_order)) != len(chain_order)
            or any(not isinstance(chain, str) or len(chain) != 64 for chain in chain_order)
        ):
            raise ForwardPitcherContextLedgerError("ledger chain order is invalid")
        previous = _EMPTY_CHAIN
        seen_chain: set[str] = set()
        actual_records = set((self.root / "records").glob("*.json")) if (self.root / "records").exists() else set()
        expected_records: set[Path] = set()
        for chain_expected in chain_order:
            record_path = self.root / "records" / f"{chain_expected}.json"
            expected_records.add(record_path)
            record = _load_object(record_path, "pitcher context record")
            chain = str(record.get("chain_sha256", ""))
            unsigned = dict(record); unsigned.pop("chain_sha256", None)
            if chain in seen_chain or _sha(_canonical_bytes(unsigned)) != chain or record.get("previous_chain_sha256") != previous:
                raise ForwardPitcherContextLedgerError("ledger hash chain is broken")
            target_id = str(record.get("target_id", ""))
            row = targets.get(target_id)
            if target_id not in self.targets or not isinstance(row, Mapping) or row.get("chain_sha256") != chain or record.get("plan_sha256") != self.plan.plan_sha256:
                raise ForwardPitcherContextLedgerError("ledger record target binding drifted")
            state = record.get("terminal_state")
            target = self.targets[target_id]
            observed = _utc(record.get("observed_at_utc"), "record observed_at_utc")
            if state in {"captured", "source_error"} and _dt(observed) > _dt(target.entry_target_at_utc):
                raise ForwardPitcherContextLedgerError("timely record is late")
            if state == "missed" and _dt(observed) <= _dt(target.entry_target_at_utc):
                raise ForwardPitcherContextLedgerError("missed record is early")
            if state == "captured":
                raw_path = self.root / str(record.get("raw_payload_path", ""))
                raw = raw_path.read_bytes()
                if _sha(raw) != record.get("raw_payload_sha256"):
                    raise ForwardPitcherContextLedgerError("captured raw bytes drifted")
                context = load_context(self.root / str(record.get("context_path", "")), self.plan, target)
                if context.context_sha256 != record.get("context_sha256") or context.source_payload_sha256 != record.get("raw_payload_sha256"):
                    raise ForwardPitcherContextLedgerError("captured context binding drifted")
            elif state not in {"source_error", "missed"} or not str(record.get("detail", "")).strip():
                raise ForwardPitcherContextLedgerError("invalid excluded terminal record")
            previous = chain
            seen_chain.add(chain)
        if actual_records != expected_records or manifest.get("last_chain_sha256") != previous:
            raise ForwardPitcherContextLedgerError("ledger record inventory or chain tip drifted")
        due = [target.target_id for target in self.targets.values() if _dt(target.entry_target_at_utc) <= _dt(assessed)]
        missing = sorted(set(due) - set(targets))
        counts = {state: sum(1 for row in targets.values() if row.get("terminal_state") == state) for state in ("captured", "source_error", "missed")}
        return {
            "plan_sha256": self.plan.plan_sha256,
            "assessed_at_utc": assessed,
            "due_targets": len(due),
            "terminal_targets": len(targets),
            "missing_due_target_ids": missing,
            "complete_due_targets": not missing,
            "state_counts": counts,
            "research_only": True,
            "betting_authorized": False,
        }
