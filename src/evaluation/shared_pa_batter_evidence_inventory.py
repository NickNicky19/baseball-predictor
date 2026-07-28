"""Publish-once T-4 batter evidence inventory.

This module is deliberately an observer.  It opens existing plan, roster, and
opportunity-history ledgers without invoking their publishing constructors,
replays their semantic validators, and copies exact source bytes into a
content-addressed inventory.  It never fetches, backfills, predicts, scores, or
changes an upstream ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from src.evaluation.projected_lineup_contract import canonical_bytes, sha256_value
from src.evaluation.projected_lineup_roster_ledger import ProjectedLineupRosterLedger
from src.evaluation.prospective_batter_opportunity_history import (
    ProspectiveOpportunityHistoryLedger,
)
from src.evaluation.shadow_capture_plan import ShadowCapturePlan


class BatterEvidenceInventoryError(ValueError):
    """The prospective source evidence cannot be inventoried truthfully."""


SCHEMA_VERSION = "shared-pa-batter-evidence-inventory-v1"
TERMINAL_SCHEMA_VERSION = "shared-pa-batter-evidence-terminal-missing-v1"
PLAN_ATTEMPT_SCHEMA_VERSION = "shared-pa-batter-plan-missing-attempt-v1"
PLAN_ABSENCE_SCHEMA_VERSION = "shared-pa-batter-plan-date-terminal-absence-v1"
SURFACES: tuple[tuple[int, str], ...] = (
    (3, "pr32_runtime_release_receipt"),
    (4, "shared_t4_plan"),
    (5, "pr32_side_bundle"),
    (6, "pr32_candidate_records"),
    (7, "pr32_projected_lineup"),
    (8, "batter_active_roster_receipt"),
    (9, "batter_active_roster_raw"),
    (10, "batter_history_coverage"),
    (11, "batter_prior_schedule_receipt"),
    (12, "batter_prior_schedule_raw"),
    (13, "batter_prior_game_receipts"),
    (14, "batter_prior_game_raw"),
    (15, "batter_stats_transport_receipts"),
    (16, "batter_stats_raw"),
    (17, "batter_stats_terminal_attempts"),
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BatterEvidenceInventoryError(f"{label} must be a lowercase SHA-256")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise BatterEvidenceInventoryError(f"{label} must be timezone-aware ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BatterEvidenceInventoryError(
            f"{label} must be timezone-aware ISO-8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BatterEvidenceInventoryError(f"{label} must be timezone-aware ISO-8601")
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _canonical_date(value: Any) -> date:
    if not isinstance(value, str):
        raise BatterEvidenceInventoryError("official date must be canonical ISO")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise BatterEvidenceInventoryError("official date must be canonical ISO") from exc
    if parsed.isoformat() != value:
        raise BatterEvidenceInventoryError("official date must be canonical ISO")
    if parsed.year == 2026 and parsed.month == 5:
        raise BatterEvidenceInventoryError("sealed May 2026 is rejected before source paths")
    return parsed


def _json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise BatterEvidenceInventoryError(
                    f"{label} contains duplicate JSON key: {key}"
                )
            value[key] = item
        return value

    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BatterEvidenceInventoryError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise BatterEvidenceInventoryError(f"{label} must be an object")
    return value


def parse_json_object_bytes(raw: bytes, label: str) -> dict[str, Any]:
    """Parse one JSON object while rejecting duplicate keys at every depth."""

    return _json_bytes(raw, label)


def unlinked_regular_file_exists(path: Path, label: str) -> bool:
    """Check existence only after rejecting linked/reparse path components."""

    _assert_no_linked_ancestor(path, label)
    return path.is_file()


def stable_read_bytes(path: Path, label: str) -> bytes:
    """Public read-only stable-file primitive for the CLI snapshot boundary."""

    return _stable_read(path, label)


def _assert_no_linked_ancestor(path: Path, label: str) -> Path:
    """Reject symlinks and Windows reparse points without resolving through them."""

    absolute = Path(os.path.abspath(path))
    chain = list(reversed(absolute.parents)) + [absolute]
    for candidate in chain:
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        attributes = int(getattr(metadata, "st_file_attributes", 0))
        reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if stat.S_ISLNK(metadata.st_mode) or attributes & reparse:
            raise BatterEvidenceInventoryError(f"{label} contains a linked ancestor")
    return absolute


def _stable_read(path: Path, label: str) -> bytes:
    absolute = _assert_no_linked_ancestor(path, label)
    before = absolute.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise BatterEvidenceInventoryError(f"{label} is not a regular file")
    raw = absolute.read_bytes()
    after = absolute.stat(follow_symlinks=False)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(raw) != after.st_size:
        raise BatterEvidenceInventoryError(f"{label} changed while being read")
    return raw


def _publish_once(path: Path, payload: bytes) -> bool:
    """Publish bytes once without replacing an existing name."""

    path = _assert_no_linked_ancestor(path, "immutable output path")
    _assert_no_linked_ancestor(path.parent, "immutable output parent")
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_linked_ancestor(path.parent, "immutable output parent")
    if path.exists():
        if _stable_read(path, "immutable output") != payload:
            raise BatterEvidenceInventoryError(f"immutable output differs: {path}")
        return False
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        if hasattr(os, "fchmod"):
            os.fchmod(handle.fileno(), 0o640)
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _stable_read(path, "concurrent immutable output") != payload:
                raise BatterEvidenceInventoryError(
                    f"concurrent immutable output differs: {path}"
                )
            return False
        _assert_no_linked_ancestor(path, "published immutable output")
        if _stable_read(path, "published immutable output") != payload:
            raise BatterEvidenceInventoryError("published immutable output differs")
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _read_only_roster_ledger(
    root: Path, *, plan: ShadowCapturePlan, manifest: Mapping[str, Any]
) -> ProjectedLineupRosterLedger:
    """Open an existing ledger while bypassing its publishing constructor."""

    ledger = object.__new__(ProjectedLineupRosterLedger)
    ledger.root = root.resolve()
    ledger.plan = plan
    ledger.contract_sha256 = _digest(manifest.get("contract_sha256"), "roster contract")
    ledger.collector_code_sha256 = _digest(
        manifest.get("collector_code_sha256"), "roster collector"
    )
    return ledger


def _read_only_history_ledger(
    root: Path, *, manifest: Mapping[str, Any]
) -> ProspectiveOpportunityHistoryLedger:
    ledger = object.__new__(ProspectiveOpportunityHistoryLedger)
    ledger.root = root.resolve()
    ledger.collection_epoch_date = _canonical_date(manifest.get("collection_epoch_date"))
    ledger.contract_sha256 = _digest(manifest.get("contract_sha256"), "history contract")
    ledger.collector_code_sha256 = _digest(
        manifest.get("collector_code_sha256"), "history collector"
    )
    ledger.evidence_scope_sha256 = _digest(
        manifest.get("evidence_scope_sha256"), "history evidence scope"
    )
    return ledger


def _source_snapshot(root: Path) -> list[dict[str, Any]]:
    root = _assert_no_linked_ancestor(root, "source ledger root")
    if not root.is_dir():
        raise BatterEvidenceInventoryError("source ledger root is missing or linked")
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        _assert_no_linked_ancestor(path, "source ledger path")
        if path.is_file():
            raw = _stable_read(path, "source ledger file")
            if path.suffix.lower() == ".json":
                _json_bytes(raw, f"source ledger JSON {path.name}")
            files.append(
                {
                    "relative_path": path.relative_to(root).as_posix(),
                    "raw": raw,
                    "size": len(raw),
                    "sha256": _sha256(raw),
                }
            )
    return files


def _snapshot_identity(snapshot: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
    return [
        (str(row["relative_path"]), int(row["size"]), str(row["sha256"]))
        for row in snapshot
    ]


def _assert_snapshot_chronology(
    *, roster_snapshot: list[dict[str, Any]], history_snapshot: list[dict[str, Any]], observed: datetime
) -> None:
    """Reject ledger bytes whose own observation/publication time is after the tick."""

    for row in roster_snapshot:
        relative = str(row["relative_path"])
        if not relative.startswith("terminal/"):
            continue
        value = _json_bytes(bytes(row["raw"]), f"roster chronology {relative}")
        for field in ("committed_utc", "schedule_received_at_utc"):
            timestamp = value.get(field)
            if timestamp is not None and _utc(timestamp, f"roster {field}") > observed:
                raise BatterEvidenceInventoryError(
                    f"roster {field} is later than inventory observation"
                )
    for row in history_snapshot:
        relative = str(row["relative_path"])
        value = _json_bytes(bytes(row["raw"]), f"history chronology {relative}")
        timestamp: Any = None
        label = ""
        if relative.startswith("plans/"):
            plan = value.get("plan")
            if isinstance(plan, Mapping):
                timestamp = plan.get("created_at_utc")
                label = "history plan created_at_utc"
        elif relative.startswith("planning_terminal/"):
            timestamp = value.get("observed_at_utc")
            label = "history planning terminal observed_at_utc"
        elif relative.startswith("terminal/"):
            if value.get("terminal_state") == "captured":
                record = value.get("history_record")
                if isinstance(record, Mapping):
                    timestamp = record.get("source_received_at_utc")
                    label = "history source_received_at_utc"
            else:
                timestamp = value.get("observed_at_utc")
                label = "history terminal observed_at_utc"
        if timestamp is not None and _utc(timestamp, label) > observed:
            raise BatterEvidenceInventoryError(f"{label} is later than inventory observation")


def _verify_snapshot_ledgers(
    *, roster_snapshot: list[dict[str, Any]], history_snapshot: list[dict[str, Any]],
    plan: ShadowCapturePlan, roster_manifest: Mapping[str, Any],
    history_manifest: Mapping[str, Any]
) -> tuple[dict[str, int], dict[str, int]]:
    """Replay only isolated snapshot bytes; never let validators reopen live roots."""

    with tempfile.TemporaryDirectory(prefix="batter-evidence-verify-") as directory:
        root = Path(directory)
        roster_root = root / "roster"
        history_root = root / "history"
        for destination_root, snapshot in (
            (roster_root, roster_snapshot),
            (history_root, history_snapshot),
        ):
            for row in snapshot:
                relative = Path(str(row["relative_path"]))
                if relative.is_absolute() or ".." in relative.parts:
                    raise BatterEvidenceInventoryError("snapshot relative path escapes root")
                destination = destination_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(bytes(row["raw"]))
        roster = _read_only_roster_ledger(roster_root, plan=plan, manifest=roster_manifest)
        history = _read_only_history_ledger(history_root, manifest=history_manifest)
        return roster.verify(), history.verify()


def _copy_exact(
    *, relative_path: str, raw: bytes, output_root: Path, role: str, authority: str
) -> dict[str, Any]:
    digest = _sha256(raw)
    object_path = output_root / "objects" / digest[:2] / digest
    _publish_once(object_path, raw)
    if _sha256(object_path.read_bytes()) != digest:
        raise BatterEvidenceInventoryError("published object bytes differ")
    return {
        "role": role,
        "source_authority": authority,
        "source_relative_path": relative_path,
        "size": len(raw),
        "sha256": digest,
        "object_path": object_path.relative_to(output_root).as_posix(),
    }


def _surface_rows(*, roster_terminal: Mapping[str, Any]) -> list[dict[str, Any]]:
    available = {
        4: True,
        8: roster_terminal.get("terminal_state") == "captured_active_roster",
        9: roster_terminal.get("roster_raw") is not None,
    }
    reasons = {
        3: "external clean-runtime observer is not produced by the existing history collector",
        5: "PR32 side-bundle producer is not present in the existing history collector",
        6: "PR32 candidate-record producer is not present in the existing history collector",
        7: "PR32 projected-lineup producer is not present in the existing history collector",
        10: "prior-schedule denominator coverage is not produced by the existing history collector",
        11: "prior-schedule denominator receipt is not produced by the existing history collector",
        12: "prior-schedule denominator raw is not produced by the existing history collector",
        13: "current-game history cannot qualify as prior-game evidence until surfaces 10-12 prove the denominator",
        14: "current-game raw cannot qualify as prior-game evidence until surfaces 10-12 prove the denominator",
        15: "date-bounded stats transport receipts are not produced by the existing history collector",
        16: "date-bounded stats raw is not produced by the existing history collector",
        17: "date-bounded stats failed-attempt receipts are not produced by the existing history collector",
    }
    rows = []
    for number, name in SURFACES:
        present = available.get(number, False)
        rows.append(
            {
                "surface_number": number,
                "surface": name,
                "state": "captured_exact_bytes" if present else "terminal_missing",
                "detail": "" if present else reasons.get(number, "required source receipt is absent"),
                "fallback_used": False,
            }
        )
    return rows


def record_missing_plan_attempt(
    *, official_game_date: str, observed_at_utc: str, output_root: Path
) -> dict[str, Any]:
    """Durably record a missing date plan without fabricating games or sides."""

    target_date = _canonical_date(official_game_date)
    observed = _utc(observed_at_utc, "observed_at_utc")
    _assert_no_linked_ancestor(output_root, "inventory output root")
    date_root = output_root / target_date.isoformat() / "plan_missing"
    terminal_path = date_root / "terminal_plan_absent.json"
    if terminal_path.is_file():
        terminal = _json_bytes(
            _stable_read(terminal_path, "existing plan-absence terminal"),
            "existing plan-absence terminal",
        )
        expected_keys = {
            "schema_version", "official_game_date", "terminal_state",
            "finalized_at_utc", "finality_rule", "game_identity_available",
            "side_identity_available", "coverage_denominator_available",
            "historical_backfill_authorized", "research_only",
            "betting_authorized", "terminal_sha256",
        }
        unsigned_terminal = dict(terminal)
        supplied = unsigned_terminal.pop("terminal_sha256", None)
        if (
            set(terminal) != expected_keys
            or terminal.get("schema_version") != PLAN_ABSENCE_SCHEMA_VERSION
            or terminal.get("official_game_date") != target_date.isoformat()
            or terminal.get("terminal_state") != "date_ended_without_immutable_plan"
            or terminal.get("finality_rule") != "UTC date reached official_game_date plus two days"
            or terminal.get("game_identity_available") is not False
            or terminal.get("side_identity_available") is not False
            or terminal.get("coverage_denominator_available") is not False
            or terminal.get("historical_backfill_authorized") is not False
            or terminal.get("research_only") is not True
            or terminal.get("betting_authorized") is not False
            or _utc(terminal.get("finalized_at_utc"), "finalized_at_utc").date()
            < target_date + timedelta(days=2)
            or supplied != sha256_value(unsigned_terminal)
        ):
            raise BatterEvidenceInventoryError("existing plan-absence terminal differs")
        return {"attempt": None, "terminal": terminal, "attempt_path": None}
    bucket = observed.replace(minute=0, second=0, microsecond=0)
    attempt_id = sha256_value(
        {
            "schema_version": "shared-pa-batter-plan-missing-attempt-id-v1",
            "official_game_date": target_date.isoformat(),
            "observation_bucket_utc": _stamp(bucket),
        }
    )
    attempt_path = date_root / "attempts" / f"{attempt_id}.json"
    if attempt_path.is_file():
        attempt = _json_bytes(
            _stable_read(attempt_path, "existing missing-plan attempt"),
            "existing missing-plan attempt",
        )
        unsigned_existing = dict(attempt)
        supplied = unsigned_existing.pop("attempt_sha256", None)
        signed_observed = _utc(attempt.get("observed_at_utc"), "attempt observed_at_utc")
        signed_bucket = _utc(
            attempt.get("observation_bucket_utc"), "attempt observation_bucket_utc"
        )
        if (
            set(attempt) != {
                "schema_version", "attempt_id", "official_game_date",
                "observed_at_utc", "observation_bucket_utc", "plan_filename",
                "state", "game_identity_available", "side_identity_available",
                "terminal", "historical_backfill_authorized", "research_only",
                "betting_authorized", "attempt_sha256",
            }
            or attempt.get("schema_version") != PLAN_ATTEMPT_SCHEMA_VERSION
            or attempt.get("attempt_id") != attempt_id
            or attempt.get("official_game_date") != target_date.isoformat()
            or attempt.get("observation_bucket_utc") != _stamp(bucket)
            or signed_observed < signed_bucket
            or signed_observed >= signed_bucket + timedelta(hours=1)
            or attempt.get("plan_filename") != f"{target_date.isoformat()}.plan.json"
            or attempt.get("state") != "plan_absent_at_observation"
            or attempt.get("game_identity_available") is not False
            or attempt.get("side_identity_available") is not False
            or attempt.get("terminal") is not False
            or attempt.get("historical_backfill_authorized") is not False
            or attempt.get("research_only") is not True
            or attempt.get("betting_authorized") is not False
            or supplied != sha256_value(unsigned_existing)
        ):
            raise BatterEvidenceInventoryError("existing missing-plan attempt differs")
    else:
        unsigned_attempt = {
            "schema_version": PLAN_ATTEMPT_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "official_game_date": target_date.isoformat(),
            "observed_at_utc": _stamp(observed),
            "observation_bucket_utc": _stamp(bucket),
            "plan_filename": f"{target_date.isoformat()}.plan.json",
            "state": "plan_absent_at_observation",
            "game_identity_available": False,
            "side_identity_available": False,
            "terminal": False,
            "historical_backfill_authorized": False,
            "research_only": True,
            "betting_authorized": False,
        }
        attempt = {
            **unsigned_attempt,
            "attempt_sha256": sha256_value(unsigned_attempt),
        }
        _publish_once(attempt_path, canonical_bytes(attempt) + b"\n")
    # A two-UTC-date boundary is deliberately conservative and independent of
    # host timezone data: by then every North American official-date clock has
    # ended. Earlier absence remains a nonterminal attempt.
    finality_date_utc = target_date + timedelta(days=2)
    terminal: dict[str, Any] | None = None
    if observed.date() >= finality_date_utc:
        unsigned_terminal = {
            "schema_version": PLAN_ABSENCE_SCHEMA_VERSION,
            "official_game_date": target_date.isoformat(),
            "terminal_state": "date_ended_without_immutable_plan",
            "finalized_at_utc": _stamp(observed),
            "finality_rule": "UTC date reached official_game_date plus two days",
            "game_identity_available": False,
            "side_identity_available": False,
            "coverage_denominator_available": False,
            "historical_backfill_authorized": False,
            "research_only": True,
            "betting_authorized": False,
        }
        terminal = {
            **unsigned_terminal,
            "terminal_sha256": sha256_value(unsigned_terminal),
        }
        _publish_once(terminal_path, canonical_bytes(terminal) + b"\n")
    return {
        "attempt": attempt,
        "terminal": terminal,
        "attempt_path": attempt_path.relative_to(output_root).as_posix(),
    }


def _validate_existing(
    *, path: Path, target_date: date, plan_sha256: str, target_id: str, side: str,
    mlb_game_pk: int, target_horizon_utc: str
) -> dict[str, Any]:
    value = _json_bytes(
        _stable_read(path, "existing immutable output"), "existing immutable output"
    )
    supplied = value.get("inventory_sha256") or value.get("terminal_sha256")
    unsigned = dict(value)
    unsigned.pop("inventory_sha256", None)
    unsigned.pop("terminal_sha256", None)
    if supplied != sha256_value(unsigned):
        raise BatterEvidenceInventoryError("existing immutable output hash differs")
    if (
        value.get("official_game_date") != target_date.isoformat()
        or value.get("plan_sha256") != plan_sha256
        or value.get("target_id") != target_id
        or value.get("side") != side
        or value.get("mlb_game_pk") != mlb_game_pk
        or value.get("target_horizon_utc") != target_horizon_utc
    ):
        raise BatterEvidenceInventoryError("existing immutable output identity differs")
    if value.get("schema_version") == SCHEMA_VERSION:
        if (
            set(value) != {
                "schema_version", "terminal_state", "official_game_date",
                "plan_sha256", "target_id", "mlb_game_pk", "side",
                "target_horizon_utc", "observed_at_utc", "roster_protocol",
                "history_protocol", "verified_counts", "surfaces", "files",
                "source_ledgers_modified", "historical_backfill_authorized",
                "production_probability_consumption_authorized", "research_only",
                "betting_authorized", "inventory_sha256",
            }
            or value.get("terminal_state") != "t4_inventory_published"
            or value.get("source_ledgers_modified") is not False
            or value.get("historical_backfill_authorized") is not False
            or value.get("production_probability_consumption_authorized") is not False
            or value.get("research_only") is not True
            or value.get("betting_authorized") is not False
            or _utc(value.get("observed_at_utc"), "inventory observed_at_utc")
            > _utc(target_horizon_utc, "target_horizon_utc")
        ):
            raise BatterEvidenceInventoryError("existing inventory schema or safety fields differ")
        surfaces = value.get("surfaces")
        if not isinstance(surfaces, list) or len(surfaces) != len(SURFACES):
            raise BatterEvidenceInventoryError("existing inventory surfaces differ")
        by_number = {
            row.get("surface_number"): row for row in surfaces if isinstance(row, Mapping)
        }
        if set(by_number) != {number for number, _ in SURFACES} or len(by_number) != len(surfaces):
            raise BatterEvidenceInventoryError("existing inventory surface coverage differs")
        for number, name in SURFACES:
            row = by_number[number]
            if (
                set(row) != {"surface_number", "surface", "state", "detail", "fallback_used"}
                or row.get("surface") != name
                or row.get("state") not in {"captured_exact_bytes", "terminal_missing"}
                or not isinstance(row.get("detail"), str)
                or row.get("fallback_used") is not False
                or (number in {3, 5, 6, 7, 10, 11, 12, 13, 14, 15, 16, 17}
                    and row.get("state") != "terminal_missing")
            ):
                raise BatterEvidenceInventoryError("existing inventory surface semantics differ")
        roster_protocol = value.get("roster_protocol")
        history_protocol = value.get("history_protocol")
        counts = value.get("verified_counts")
        if (
            not isinstance(roster_protocol, Mapping)
            or set(roster_protocol) != {"contract_sha256", "collector_code_sha256"}
            or any(
                _digest(roster_protocol.get(field), f"roster protocol {field}")
                != roster_protocol.get(field)
                for field in roster_protocol
            )
            or not isinstance(history_protocol, Mapping)
            or set(history_protocol) != {
                "collection_epoch_date", "contract_sha256", "collector_code_sha256",
                "evidence_scope_sha256",
            }
            or _canonical_date(history_protocol.get("collection_epoch_date"))
            != date.fromisoformat(str(history_protocol.get("collection_epoch_date")))
            or any(
                _digest(history_protocol.get(field), f"history protocol {field}")
                != history_protocol.get(field)
                for field in ("contract_sha256", "collector_code_sha256", "evidence_scope_sha256")
            )
            or not isinstance(counts, Mapping)
            or set(counts) != {"roster", "history"}
        ):
            raise BatterEvidenceInventoryError("existing inventory protocol semantics differ")
        roster_counts = counts.get("roster")
        history_counts = counts.get("history")
        roster_count_keys = {
            "captured_active_roster", "source_error", "roster_malformed",
            "missed_before_horizon", "game_identity_ambiguous", "expected",
            "terminal", "missing",
        }
        history_count_keys = {
            "captured", "deadline_missing", "source_invalid", "planned", "terminal",
            "missing", "missed_before_plan",
        }
        if (
            not isinstance(roster_counts, Mapping)
            or set(roster_counts) != roster_count_keys
            or not isinstance(history_counts, Mapping)
            or set(history_counts) != history_count_keys
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in roster_counts.values())
            or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in history_counts.values())
            or roster_counts["terminal"] + roster_counts["missing"] != roster_counts["expected"]
            or sum(roster_counts[key] for key in roster_count_keys - {"expected", "terminal", "missing"})
            != roster_counts["terminal"]
            or history_counts["terminal"] + history_counts["missing"] != history_counts["planned"]
            or sum(history_counts[key] for key in ("captured", "deadline_missing", "source_invalid"))
            != history_counts["terminal"]
        ):
            raise BatterEvidenceInventoryError("existing inventory verified counts differ")
        files = value.get("files")
        if not isinstance(files, list):
            raise BatterEvidenceInventoryError("existing inventory file manifest is missing")
        payloads: dict[tuple[str, str], bytes] = {}
        object_paths: set[str] = set()
        authority_by_role = {
            "immutable_t4_plan": "immutable_shared_t4_plan",
            "roster_ledger_byte": "immutable_projected_lineup_roster_ledger",
            "history_ledger_byte": "immutable_prospective_batter_opportunity_history_ledger",
        }
        for row in files:
            if not isinstance(row, Mapping):
                raise BatterEvidenceInventoryError("existing inventory file row is malformed")
            if (
                set(row) != {
                    "role", "source_authority", "source_relative_path", "size",
                    "sha256", "object_path",
                }
                or row.get("role") not in {
                    "immutable_t4_plan", "roster_ledger_byte", "history_ledger_byte"
                }
                or row.get("source_authority") != authority_by_role.get(row.get("role"))
                or not isinstance(row.get("source_relative_path"), str)
                or PurePosixPath(row["source_relative_path"]).is_absolute()
                or ".." in PurePosixPath(row["source_relative_path"]).parts
                or "\\" in row["source_relative_path"]
                or ":" in row["source_relative_path"]
                or isinstance(row.get("size"), bool)
                or not isinstance(row.get("size"), int)
                or row["size"] < 0
                or _digest(row.get("sha256"), "existing object") != row.get("sha256")
                or not isinstance(row.get("object_path"), str)
            ):
                raise BatterEvidenceInventoryError("existing inventory file semantics differ")
            candidate = path.parent / str(row.get("object_path"))
            _assert_no_linked_ancestor(candidate, "existing inventory object")
            object_path = candidate.resolve()
            try:
                object_path.relative_to(path.parent.resolve())
            except ValueError as exc:
                raise BatterEvidenceInventoryError("existing inventory object escapes root") from exc
            if not object_path.is_file():
                raise BatterEvidenceInventoryError("existing inventory object is missing or linked")
            raw = _stable_read(object_path, "existing inventory object")
            if len(raw) != row.get("size") or _sha256(raw) != row.get("sha256"):
                raise BatterEvidenceInventoryError("existing inventory object bytes differ")
            identity = (str(row["role"]), str(row["source_relative_path"]))
            if identity in payloads or row["object_path"] in object_paths:
                raise BatterEvidenceInventoryError("existing inventory file identity is duplicated")
            payloads[identity] = raw
            object_paths.add(str(row["object_path"]))
        plan_rows = [key for key in payloads if key[0] == "immutable_t4_plan"]
        if len(plan_rows) != 1 or plan_rows[0][1] != f"{target_date.isoformat()}.plan.json":
            raise BatterEvidenceInventoryError("existing inventory plan file binding differs")
        try:
            copied_plan = ShadowCapturePlan.from_mapping(
                _json_bytes(payloads[plan_rows[0]], "copied immutable T-4 plan")
            )
        except (ValueError, TypeError) as exc:
            raise BatterEvidenceInventoryError("existing inventory copied plan is invalid") from exc
        copied_target = next(
            (item for item in copied_plan.targets if item.target_id == target_id), None
        )
        if (
            copied_plan.official_game_date != target_date.isoformat()
            or copied_plan.entry_hours != 4
            or copied_plan.plan_sha256 != plan_sha256
            or copied_target is None
            or copied_target.mlb_game_pk != mlb_game_pk
            or copied_target.entry_target_at_utc != target_horizon_utc
        ):
            raise BatterEvidenceInventoryError("existing inventory copied plan identity differs")
        roster_manifest_raw = payloads.get(("roster_ledger_byte", "ledger_manifest.json"))
        history_manifest_raw = payloads.get(("history_ledger_byte", "ledger_manifest.json"))
        if roster_manifest_raw is None or history_manifest_raw is None:
            raise BatterEvidenceInventoryError("existing inventory ledger manifests are missing")
        roster_manifest = _json_bytes(roster_manifest_raw, "copied roster manifest")
        history_manifest = _json_bytes(history_manifest_raw, "copied history manifest")
        if (
            roster_manifest.get("contract_sha256") != roster_protocol["contract_sha256"]
            or roster_manifest.get("collector_code_sha256") != roster_protocol["collector_code_sha256"]
            or history_manifest.get("collection_epoch_date") != history_protocol["collection_epoch_date"]
            or history_manifest.get("contract_sha256") != history_protocol["contract_sha256"]
            or history_manifest.get("collector_code_sha256") != history_protocol["collector_code_sha256"]
            or history_manifest.get("evidence_scope_sha256") != history_protocol["evidence_scope_sha256"]
        ):
            raise BatterEvidenceInventoryError("existing inventory protocol-to-manifest binding differs")
        relevant_terminals = []
        for (role, relative), raw in payloads.items():
            if role == "roster_ledger_byte" and relative.startswith("terminal/"):
                candidate = _json_bytes(raw, "copied roster terminal")
                if candidate.get("target_id") == target_id and candidate.get("side") == side:
                    relevant_terminals.append(candidate)
        if len(relevant_terminals) != 1:
            raise BatterEvidenceInventoryError("existing inventory target-side roster terminal differs")
        roster_terminal = relevant_terminals[0]
        captured = roster_terminal.get("terminal_state") == "captured_active_roster"
        roster_raw = roster_terminal.get("roster_raw")
        raw_bound = False
        if isinstance(roster_raw, Mapping):
            raw_payload = payloads.get(("roster_ledger_byte", str(roster_raw.get("path"))))
            raw_bound = raw_payload is not None and _sha256(raw_payload) == roster_raw.get("sha256")
        if (
            (by_number[4]["state"] == "captured_exact_bytes") is not True
            or (by_number[8]["state"] == "captured_exact_bytes") != captured
            or (by_number[9]["state"] == "captured_exact_bytes") != raw_bound
        ):
            raise BatterEvidenceInventoryError("existing inventory roster surface binding differs")
    elif value.get("schema_version") == TERMINAL_SCHEMA_VERSION:
        if (
            set(value) != {
                "schema_version", "terminal_state", "official_game_date",
                "plan_sha256", "target_id", "mlb_game_pk", "side",
                "target_horizon_utc", "observed_at_utc", "missing_surface_numbers",
                "historical_backfill_authorized", "research_only",
                "betting_authorized", "terminal_sha256",
            }
            or value.get("terminal_state") != "missed_before_t4_inventory"
            or value.get("missing_surface_numbers") != [number for number, _ in SURFACES]
            or value.get("historical_backfill_authorized") is not False
            or value.get("research_only") is not True
            or value.get("betting_authorized") is not False
            or _utc(value.get("observed_at_utc"), "terminal observed_at_utc")
            <= _utc(target_horizon_utc, "target_horizon_utc")
        ):
            raise BatterEvidenceInventoryError("existing missing terminal schema differs")
    else:
        raise BatterEvidenceInventoryError("existing immutable output schema differs")
    return value


def assemble_side_inventory(
    *,
    official_game_date: str,
    plan_path: Path,
    roster_ledger_root: Path,
    history_ledger_root: Path,
    output_root: Path,
    target_id: str,
    side: str,
    observed_at_utc: str,
    max_early_seconds: int = 120,
) -> dict[str, Any]:
    """Publish one T-4 inventory or a permanent no-backfill terminal miss."""

    target_date = _canonical_date(official_game_date)  # seal before path access
    if side not in {"away", "home"}:
        raise BatterEvidenceInventoryError("side must be away or home")
    observed = _utc(observed_at_utc, "observed_at_utc")
    if isinstance(max_early_seconds, bool) or not isinstance(max_early_seconds, int) or max_early_seconds < 0:
        raise BatterEvidenceInventoryError("max_early_seconds must be nonnegative")
    _assert_no_linked_ancestor(plan_path, "T-4 plan path")
    _assert_no_linked_ancestor(roster_ledger_root, "roster authority root")
    _assert_no_linked_ancestor(history_ledger_root, "history authority root")
    _assert_no_linked_ancestor(output_root, "inventory output root")
    expected_plan_path = plan_path.parent / f"{target_date.isoformat()}.plan.json"
    if Path(os.path.abspath(plan_path)) != Path(os.path.abspath(expected_plan_path)):
        raise BatterEvidenceInventoryError("plan path does not match the canonical official date")
    plan_raw = _stable_read(plan_path, "T-4 plan")
    plan = ShadowCapturePlan.from_mapping(_json_bytes(plan_raw, "T-4 plan"))
    if plan.official_game_date != target_date.isoformat() or plan.entry_hours != 4:
        raise BatterEvidenceInventoryError("plan date or T-4 horizon differs")
    target = next((item for item in plan.targets if item.target_id == target_id), None)
    if target is None:
        raise BatterEvidenceInventoryError("target is absent from the immutable plan")
    horizon = _utc(target.entry_target_at_utc, "target horizon")
    side_root = output_root / target_date.isoformat() / plan.plan_sha256 / target_id / side
    claim_path = side_root / "terminal.json"
    _assert_no_linked_ancestor(side_root, "inventory side root")
    if claim_path.is_file():
        return _validate_existing(
            path=claim_path,
            target_date=target_date,
            plan_sha256=plan.plan_sha256,
            target_id=target_id,
            side=side,
            mlb_game_pk=target.mlb_game_pk,
            target_horizon_utc=target.entry_target_at_utc,
        )
    if observed < horizon - timedelta(seconds=max_early_seconds):
        return {
            "schema_version": "shared-pa-batter-evidence-pending-v1",
            "state": "not_due",
            "target_id": target_id,
            "side": side,
            "research_only": True,
            "betting_authorized": False,
        }
    if observed > horizon:
        unsigned = {
            "schema_version": TERMINAL_SCHEMA_VERSION,
            "terminal_state": "missed_before_t4_inventory",
            "official_game_date": target_date.isoformat(),
            "plan_sha256": plan.plan_sha256,
            "target_id": target_id,
            "mlb_game_pk": target.mlb_game_pk,
            "side": side,
            "target_horizon_utc": target.entry_target_at_utc,
            "observed_at_utc": _stamp(observed),
            "missing_surface_numbers": [number for number, _ in SURFACES],
            "historical_backfill_authorized": False,
            "research_only": True,
            "betting_authorized": False,
        }
        value = {**unsigned, "terminal_sha256": sha256_value(unsigned)}
        _publish_once(claim_path, canonical_bytes(value) + b"\n")
        return value

    roster_root = roster_ledger_root / target_date.isoformat() / plan.plan_sha256
    history_root = history_ledger_root / target_date.isoformat() / plan.plan_sha256
    roster_manifest_path = roster_root / "ledger_manifest.json"
    history_manifest_path = history_root / "ledger_manifest.json"
    if not roster_manifest_path.is_file() or not history_manifest_path.is_file():
        return {
            "schema_version": "shared-pa-batter-evidence-pending-v1",
            "state": "awaiting_upstream_ledger",
            "target_id": target_id,
            "side": side,
            "research_only": True,
            "betting_authorized": False,
        }
    roster_manifest = _json_bytes(
        _stable_read(roster_manifest_path, "roster manifest"), "roster manifest"
    )
    history_manifest = _json_bytes(
        _stable_read(history_manifest_path, "history manifest"), "history manifest"
    )
    epoch = _canonical_date(history_manifest.get("collection_epoch_date"))
    if target_date > date(2026, 5, 31) and epoch <= date(2026, 5, 31):
        raise BatterEvidenceInventoryError(
            "history epoch could include sealed May 2026; rejected before ledger replay"
        )
    roster_before = _source_snapshot(roster_root)
    history_before = _source_snapshot(history_root)
    roster_files = _source_snapshot(roster_root)
    history_files = _source_snapshot(history_root)
    if _snapshot_identity(roster_before) != _snapshot_identity(roster_files):
        raise BatterEvidenceInventoryError("roster ledger changed during verification")
    if _snapshot_identity(history_before) != _snapshot_identity(history_files):
        raise BatterEvidenceInventoryError("history ledger changed during verification")
    _assert_snapshot_chronology(
        roster_snapshot=roster_files,
        history_snapshot=history_files,
        observed=observed,
    )
    roster_counts, history_counts = _verify_snapshot_ledgers(
        roster_snapshot=roster_files,
        history_snapshot=history_files,
        plan=plan,
        roster_manifest=roster_manifest,
        history_manifest=history_manifest,
    )
    roster_ledger = _read_only_roster_ledger(roster_root, plan=plan, manifest=roster_manifest)
    history_ledger = _read_only_history_ledger(history_root, manifest=history_manifest)
    terminal_path = roster_root / "terminal" / (
        sha256_value(
            {
                "schema_version": "projected-lineup-roster-side-target-v1",
                "plan_sha256": plan.plan_sha256,
                "target_id": target.target_id,
                "side": side,
            }
        )
        + ".json"
    )
    if not terminal_path.is_file():
        return {
            "schema_version": "shared-pa-batter-evidence-pending-v1",
            "state": "awaiting_roster_terminal",
            "target_id": target_id,
            "side": side,
            "research_only": True,
            "betting_authorized": False,
        }
    roster_terminal = _json_bytes(
        _stable_read(terminal_path, "roster terminal"), "roster terminal"
    )
    source_files = [
        _copy_exact(
            relative_path=plan_path.name,
            raw=plan_raw,
            output_root=side_root,
            role="immutable_t4_plan",
            authority="immutable_shared_t4_plan",
        ),
        *(
            _copy_exact(
                relative_path=str(row["relative_path"]),
                raw=bytes(row["raw"]),
                output_root=side_root,
                role="roster_ledger_byte",
                authority="immutable_projected_lineup_roster_ledger",
            )
            for row in roster_files
        ),
        *(
            _copy_exact(
                relative_path=str(row["relative_path"]),
                raw=bytes(row["raw"]),
                output_root=side_root,
                role="history_ledger_byte",
                authority="immutable_prospective_batter_opportunity_history_ledger",
            )
            for row in history_files
        ),
    ]
    if _stable_read(plan_path, "T-4 plan") != plan_raw:
        raise BatterEvidenceInventoryError("T-4 plan changed during inventory")
    if _snapshot_identity(roster_files) != _snapshot_identity(_source_snapshot(roster_root)):
        raise BatterEvidenceInventoryError("roster ledger changed during inventory")
    if _snapshot_identity(history_files) != _snapshot_identity(_source_snapshot(history_root)):
        raise BatterEvidenceInventoryError("history ledger changed during inventory")
    source_files = sorted(source_files, key=lambda row: (row["role"], row["source_relative_path"], row["sha256"]))
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "terminal_state": "t4_inventory_published",
        "official_game_date": target_date.isoformat(),
        "plan_sha256": plan.plan_sha256,
        "target_id": target.target_id,
        "mlb_game_pk": target.mlb_game_pk,
        "side": side,
        "target_horizon_utc": target.entry_target_at_utc,
        "observed_at_utc": _stamp(observed),
        "roster_protocol": {
            "contract_sha256": roster_ledger.contract_sha256,
            "collector_code_sha256": roster_ledger.collector_code_sha256,
        },
        "history_protocol": {
            "collection_epoch_date": history_ledger.collection_epoch_date.isoformat(),
            "contract_sha256": history_ledger.contract_sha256,
            "collector_code_sha256": history_ledger.collector_code_sha256,
            "evidence_scope_sha256": history_ledger.evidence_scope_sha256,
        },
        "verified_counts": {"roster": roster_counts, "history": history_counts},
        "surfaces": _surface_rows(roster_terminal=roster_terminal),
        "files": source_files,
        "source_ledgers_modified": False,
        "historical_backfill_authorized": False,
        "production_probability_consumption_authorized": False,
        "research_only": True,
        "betting_authorized": False,
    }
    value = {**unsigned, "inventory_sha256": sha256_value(unsigned)}
    _publish_once(claim_path, canonical_bytes(value) + b"\n")
    return value
