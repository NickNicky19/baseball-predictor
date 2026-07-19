"""Append-only local ledger for the separate future full-game HR evidence release.

It is intentionally source-agnostic and makes no network request.  A future
collector must supply the raw payload it observed; this ledger binds that
payload to a complete pregame record, or permanently records one exclusion.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.hr_full_game_forward_contract import (
    HRFullGameForwardContractError,
    validate_completed_t4_record,
    validate_contract,
)


class HRFullGameForwardLedgerError(ValueError):
    """Raised when append-only or evidence-chain invariants are violated."""


_EMPTY_CHAIN = "0" * 64


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HRFullGameForwardLedgerError(f"cannot read {label}: {exc}") from exc
    if not isinstance(loaded, Mapping):
        raise HRFullGameForwardLedgerError(f"{label} must be a JSON object")
    return loaded


def _hard_target(record: Mapping[str, Any]) -> str:
    try:
        game_pk = int(record["mlb_game_pk"])
        player_id = int(record["player_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HRFullGameForwardLedgerError("record needs hard integer MLB game and player IDs") from exc
    expected = f"{game_pk}:{player_id}:home_runs:0.5:over"
    if record.get("hard_model_key") != expected:
        raise HRFullGameForwardLedgerError("hard_model_key does not match the fixed HR-over-0.5 target")
    return expected


def initialize_ledger(*, root: str | Path, contract_path: str | Path) -> Path:
    """Create an empty release root exactly once and bind it to a contract hash."""
    destination = Path(root).resolve()
    contract_file = Path(contract_path).resolve()
    contract = _load_json(contract_file, "HR forward contract")
    try:
        validate_contract(contract)
    except HRFullGameForwardContractError as exc:
        raise HRFullGameForwardLedgerError(str(exc)) from exc
    manifest = destination / "ledger_manifest.json"
    expected = {
        "schema_version": "hr-full-game-forward-ledger-v1",
        "contract_path": str(contract_file),
        "contract_sha256": _sha256_file(contract_file),
        "economic_evidence_eligible": False,
        "betting_authorized": False,
        "records": 0,
        "last_chain_sha256": _EMPTY_CHAIN,
        "entry_chain_order": [],
    }
    if manifest.exists():
        actual = _load_json(manifest, "ledger manifest")
        for key in ("schema_version", "contract_path", "contract_sha256", "economic_evidence_eligible", "betting_authorized"):
            if actual.get(key) != expected[key]:
                raise HRFullGameForwardLedgerError("existing ledger has a different contract or safety state")
        if not isinstance(actual.get("records"), int) or int(actual["records"]) < 0:
            raise HRFullGameForwardLedgerError("existing ledger record count is invalid")
        if not isinstance(actual.get("last_chain_sha256"), str) or len(str(actual["last_chain_sha256"])) != 64:
            raise HRFullGameForwardLedgerError("existing ledger chain tip is invalid")
        if not isinstance(actual.get("entry_chain_order"), list):
            raise HRFullGameForwardLedgerError("existing ledger entry order is invalid")
        return destination
    _atomic_write(manifest, _canonical_bytes(expected))
    _atomic_write(destination / "terminal_index.json", _canonical_bytes({"targets": {}}))
    return destination


def append_terminal_record(
    *,
    root: str | Path,
    contract_path: str | Path,
    record: Mapping[str, Any],
    raw_payload: bytes | None,
) -> str:
    """Atomically append one terminal target record and return its chain hash.

    `captured_complete` requires the full T-4 record plus exact raw payload
    bytes.  Other terminal states remain permanent exclusions and must not be
    backfilled later.
    """
    destination = initialize_ledger(root=root, contract_path=contract_path)
    target = _hard_target(record)
    terminal = record.get("terminal_state")
    contract = _load_json(Path(contract_path).resolve(), "HR forward contract")
    allowed = set(contract.get("terminal_states", []))
    if terminal not in allowed:
        raise HRFullGameForwardLedgerError("record terminal_state is not contract-approved")
    if terminal == "captured_complete":
        try:
            validate_completed_t4_record(record)
        except HRFullGameForwardContractError as exc:
            raise HRFullGameForwardLedgerError(str(exc)) from exc
        if raw_payload is None:
            raise HRFullGameForwardLedgerError("captured_complete requires the raw observed payload")
        if _sha256_bytes(raw_payload) != record.get("raw_source_payload_sha256"):
            raise HRFullGameForwardLedgerError("raw payload hash does not match the pregame record")
    elif raw_payload is not None:
        raise HRFullGameForwardLedgerError("excluded targets cannot claim a raw payload as complete evidence")

    index_path = destination / "terminal_index.json"
    manifest_path = destination / "ledger_manifest.json"
    index = _load_json(index_path, "terminal index")
    manifest = _load_json(manifest_path, "ledger manifest")
    targets = index.get("targets")
    if not isinstance(targets, Mapping):
        raise HRFullGameForwardLedgerError("terminal index is malformed")
    if target in targets:
        raise HRFullGameForwardLedgerError("target already has a permanent terminal state")
    previous = str(manifest.get("last_chain_sha256", ""))
    if len(previous) != 64:
        raise HRFullGameForwardLedgerError("ledger manifest chain hash is invalid")

    raw_sha = _sha256_bytes(raw_payload) if raw_payload is not None else None
    raw_path: str | None = None
    if raw_payload is not None:
        raw_file = destination / "raw" / f"{raw_sha}.json"
        if raw_file.exists() and _sha256_file(raw_file) != raw_sha:
            raise HRFullGameForwardLedgerError("existing raw payload path is hash-inconsistent")
        if not raw_file.exists():
            _atomic_write(raw_file, raw_payload)
        raw_path = str(raw_file.relative_to(destination).as_posix())

    entry = {
        "schema_version": "hr-full-game-forward-ledger-entry-v1",
        "target": target,
        "terminal_state": terminal,
        "record": dict(record),
        "raw_payload_sha256": raw_sha,
        "raw_payload_path": raw_path,
        "previous_chain_sha256": previous,
        "committed_utc": datetime.now(timezone.utc).isoformat(),
    }
    chain = _sha256_bytes(_canonical_bytes(entry))
    entry["chain_sha256"] = chain
    record_file = destination / "records" / f"{chain}.json"
    if record_file.exists():
        raise HRFullGameForwardLedgerError("hash collision or prior record reuse")
    _atomic_write(record_file, _canonical_bytes(entry))

    next_targets = dict(targets)
    next_targets[target] = {"terminal_state": terminal, "record_path": str(record_file.relative_to(destination).as_posix()), "chain_sha256": chain}
    next_index = {"targets": next_targets}
    next_manifest = dict(manifest)
    next_manifest["records"] = int(manifest.get("records", 0)) + 1
    next_manifest["last_chain_sha256"] = chain
    order = manifest.get("entry_chain_order")
    if not isinstance(order, list) or any(not isinstance(value, str) or len(value) != 64 for value in order):
        raise HRFullGameForwardLedgerError("ledger manifest entry order is invalid")
    next_manifest["entry_chain_order"] = [*order, chain]
    _atomic_write(index_path, _canonical_bytes(next_index))
    _atomic_write(manifest_path, _canonical_bytes(next_manifest))
    return chain


def verify_ledger(*, root: str | Path, contract_path: str | Path) -> int:
    """Fail closed if any ledger entry, raw payload, index, or hash chain drifts."""
    destination = initialize_ledger(root=root, contract_path=contract_path)
    manifest = _load_json(destination / "ledger_manifest.json", "ledger manifest")
    index = _load_json(destination / "terminal_index.json", "terminal index")
    indexed = index.get("targets")
    if not isinstance(indexed, Mapping):
        raise HRFullGameForwardLedgerError("terminal index is malformed")
    order = manifest.get("entry_chain_order")
    if not isinstance(order, list) or len(order) != len(set(order)) or any(not isinstance(value, str) or len(value) != 64 for value in order):
        raise HRFullGameForwardLedgerError("ledger manifest entry order is invalid")
    records_dir = destination / "records"
    paths = [records_dir / f"{chain}.json" for chain in order]
    if len(paths) != int(manifest.get("records", -1)) or len(indexed) != len(paths) or any(not path.is_file() for path in paths):
        raise HRFullGameForwardLedgerError("record, manifest, and index counts differ")
    actual_record_paths = set(records_dir.glob("*.json")) if records_dir.exists() else set()
    if actual_record_paths != set(paths):
        raise HRFullGameForwardLedgerError("orphaned or unindexed ledger record exists")
    previous = _EMPTY_CHAIN
    seen: set[str] = set()
    referenced_raw_paths: set[Path] = set()
    for path in paths:
        entry = _load_json(path, f"ledger entry {path.name}")
        chain = str(entry.get("chain_sha256", ""))
        unchained = dict(entry)
        unchained.pop("chain_sha256", None)
        if _sha256_bytes(_canonical_bytes(unchained)) != chain or entry.get("previous_chain_sha256") != previous:
            raise HRFullGameForwardLedgerError("ledger hash chain is broken")
        target = str(entry.get("target", ""))
        if target in seen or target not in indexed:
            raise HRFullGameForwardLedgerError("ledger target duplication or index drift")
        record = entry.get("record")
        if not isinstance(record, Mapping) or _hard_target(record) != target:
            raise HRFullGameForwardLedgerError("ledger target does not bind to its record")
        if record.get("terminal_state") == "captured_complete":
            try:
                validate_completed_t4_record(record)
            except HRFullGameForwardContractError as exc:
                raise HRFullGameForwardLedgerError(str(exc)) from exc
            raw_path = entry.get("raw_payload_path")
            raw_sha = entry.get("raw_payload_sha256")
            if not isinstance(raw_path, str) or not isinstance(raw_sha, str):
                raise HRFullGameForwardLedgerError("complete record lacks raw payload binding")
            raw_file = destination / raw_path
            if not raw_file.is_file() or _sha256_file(raw_file) != raw_sha or raw_sha != record.get("raw_source_payload_sha256"):
                raise HRFullGameForwardLedgerError("complete record raw payload binding is invalid")
            referenced_raw_paths.add(raw_file.resolve())
        elif entry.get("raw_payload_path") is not None or entry.get("raw_payload_sha256") is not None:
            raise HRFullGameForwardLedgerError("exclusion record contains raw-complete evidence")
        if indexed[target].get("chain_sha256") != chain or indexed[target].get("terminal_state") != record.get("terminal_state"):
            raise HRFullGameForwardLedgerError("terminal index does not bind to entry")
        seen.add(target)
        previous = chain
    if previous != manifest.get("last_chain_sha256"):
        raise HRFullGameForwardLedgerError("ledger manifest does not end at chain tip")
    raw_dir = destination / "raw"
    actual_raw_paths = {path.resolve() for path in raw_dir.glob("*.json")} if raw_dir.exists() else set()
    if actual_raw_paths != referenced_raw_paths:
        raise HRFullGameForwardLedgerError("orphaned or unreferenced raw payload exists")
    return len(paths)
