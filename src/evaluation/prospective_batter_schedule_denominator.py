"""Publish genuine T-4 prior-schedule denominator surfaces 10-12.

The producer is deliberately isolated from probability code. Network transport
is injected, while captured and missing history states are derived only from a
replayed immutable history ledger. The module owns only immutable raw schedule
bytes, their replayed semantic receipts, exact denominator coverage, and
explicit terminal missingness.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from src.evaluation.projected_lineup_contract import canonical_bytes, sha256_value
from src.evaluation.projected_lineup_roster_ledger import (
    CAPTURED as ROSTER_CAPTURED,
    ProjectedLineupRosterLedger,
    roster_side_target_id,
)
from src.evaluation.prospective_batter_opportunity import (
    ProspectiveBatterOpportunityError,
    RawOpportunityResponse,
    build_history_coverage,
    parse_prior_schedule_denominator as _parse_prior_schedule_denominator_v1,
)
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.prospective_batter_opportunity_history import (
    ProspectiveOpportunityHistoryLedger,
)


class ScheduleDenominatorError(ValueError):
    """The prior-schedule denominator cannot be proved truthfully."""


LEDGER_SCHEMA = "prospective-batter-schedule-denominator-ledger-v1"
TARGET_SCHEMA = "prospective-batter-schedule-denominator-target-v1"
TERMINAL_SCHEMA = "prospective-batter-schedule-denominator-terminal-v1"
CAPTURED = "captured_schedule_denominator"
SOURCE_ERROR = "source_error_before_horizon"
MISSED = "missed_after_horizon"
EXCLUSIONS = {SOURCE_ERROR, MISSED}
MAX_EARLY_SECONDS = 120
MODULE_PATH = Path(os.path.abspath(__file__))
ROOT = MODULE_PATH.resolve().parents[2]
CONTRACT_PATH = ROOT / "config/prospective_batter_opportunity_contract_v1.json"
RUNTIME_PATH = ROOT / "config/prospective_batter_opportunity_runtime_v1.json"
HISTORY_CODE_PATHS = (
    ROOT / "scripts/run_prospective_batter_opportunity_tick.py",
    ROOT / "src/evaluation/prospective_batter_opportunity.py",
    ROOT / "src/evaluation/prospective_batter_opportunity_history.py",
    ROOT / "src/evaluation/prospective_batter_opportunity_ledger.py",
    ROOT / "src/evaluation/projected_lineup_official_roster.py",
    ROOT / "src/evaluation/projected_lineup_roster_ledger.py",
    ROOT / "src/evaluation/shadow_capture_plan.py",
)
CODE_PATHS = (
    ROOT / "src/evaluation/prospective_batter_schedule_denominator.py",
    ROOT / "src/evaluation/prospective_batter_opportunity.py",
    ROOT / "src/evaluation/projected_lineup_contract.py",
    ROOT / "src/evaluation/projected_lineup_official_roster.py",
    ROOT / "src/evaluation/projected_lineup_roster_ledger.py",
    ROOT / "src/evaluation/prospective_batter_opportunity_history.py",
    ROOT / "src/evaluation/prospective_batter_opportunity_ledger.py",
    ROOT / "src/evaluation/shared_pa_forward_collector.py",
    ROOT / "src/evaluation/shared_pa_forward_evidence.py",
    ROOT / "src/evaluation/shadow_capture_plan.py",
    ROOT / "scripts/run_prospective_batter_opportunity_tick.py",
)


def _sha(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ScheduleDenominatorError(f"{label} must be a lowercase SHA-256")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ScheduleDenominatorError(f"{label} must be timezone-aware ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScheduleDenominatorError(
            f"{label} must be timezone-aware ISO-8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ScheduleDenominatorError(f"{label} must be timezone-aware ISO-8601")
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _canonical_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ScheduleDenominatorError(f"{label} must be a canonical ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ScheduleDenominatorError(f"{label} must be a canonical ISO date") from exc
    if parsed.isoformat() != value:
        raise ScheduleDenominatorError(f"{label} must be a canonical ISO date")
    if parsed.year == 2026 and parsed.month == 5:
        raise ScheduleDenominatorError("May 2026 is sealed before source construction")
    return parsed


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ScheduleDenominatorError(f"{label} must be a positive integer")
    return value


def _is_reparse(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _assert_unlinked(path: Path, label: str, *, require_file: bool = False) -> None:
    absolute = Path(os.path.abspath(path))
    cursor = absolute
    while True:
        if cursor.exists() and _is_reparse(cursor):
            raise ScheduleDenominatorError(f"{label} contains a linked or reparse path")
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    if require_file:
        try:
            mode = absolute.lstat().st_mode
        except OSError as exc:
            raise ScheduleDenominatorError(f"{label} is unavailable") from exc
        if not stat.S_ISREG(mode):
            raise ScheduleDenominatorError(f"{label} must be an unlinked regular file")


def release_identities() -> tuple[str, str]:
    """Recompute the fixed contract and code identities; callers cannot supply them."""

    _assert_unlinked(MODULE_PATH, "collector module path", require_file=True)
    _assert_unlinked(CONTRACT_PATH, "authoritative contract", require_file=True)
    contract_sha256 = hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
    manifest: dict[str, str] = {}
    for path in CODE_PATHS:
        _assert_unlinked(path, "authoritative code path", require_file=True)
        manifest[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    collector_code_sha256 = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return contract_sha256, collector_code_sha256


def _snapshot_ledger(root: Path, label: str) -> list[dict[str, Any]]:
    _assert_unlinked(root, label)
    if not root.is_dir():
        raise ScheduleDenominatorError(f"{label} is unavailable")
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        _assert_unlinked(path, label)
        if path.is_dir():
            continue
        _assert_unlinked(path, label, require_file=True)
        raw = path.read_bytes()
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "raw": raw,
            }
        )
    if not rows or rows[0]["path"] != "ledger_manifest.json":
        raise ScheduleDenominatorError(f"{label} manifest is missing")
    return rows


def _snapshot_identity(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    files = [
        {"path": row["path"], "sha256": row["sha256"], "size": row["size"]}
        for row in rows
    ]
    unsigned = {"files": files}
    return {**unsigned, "snapshot_sha256": sha256_value(unsigned)}


def _roster_authority(
    ledger: ProjectedLineupRosterLedger,
    *,
    plan: ShadowCapturePlan,
    target: CaptureTarget,
    side: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ledger.verify()
    rows = _snapshot_ledger(ledger.root, "roster authority")
    side_id = roster_side_target_id(plan=plan, target=target, side=side)
    terminal = _json(
        ledger.root / "terminal" / f"{side_id}.json", "captured roster authority"
    )
    authority = {
        "schema_version": "prospective-batter-schedule-roster-authority-v1",
        "side_target_id": side_id,
        "terminal_entry_sha256": terminal.get("entry_sha256"),
        "schedule_raw_sha256": terminal.get("schedule_raw", {}).get("sha256")
        if isinstance(terminal.get("schedule_raw"), Mapping)
        else None,
        "roster_raw_sha256": terminal.get("roster_raw", {}).get("sha256")
        if isinstance(terminal.get("roster_raw"), Mapping)
        else None,
        "ledger_snapshot": _snapshot_identity(rows),
    }
    authority["authority_sha256"] = sha256_value(authority)
    return authority, terminal


def _history_authority(
    ledger: ProspectiveOpportunityHistoryLedger,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ledger.verify(require_all_terminal=True)
    rows = _snapshot_ledger(ledger.root, "history authority")
    authority = {
        "schema_version": "prospective-batter-schedule-history-authority-v1",
        "collection_epoch_date": ledger.collection_epoch_date.isoformat(),
        "contract_sha256": ledger.contract_sha256,
        "collector_code_sha256": ledger.collector_code_sha256,
        "evidence_scope_sha256": ledger.evidence_scope_sha256,
        "ledger_snapshot": _snapshot_identity(rows),
    }
    authority["authority_sha256"] = sha256_value(authority)
    return authority, rows


def _history_collector_identity() -> str:
    manifest: dict[str, str] = {}
    for path in HISTORY_CODE_PATHS:
        _assert_unlinked(path, "history collector dependency", require_file=True)
        manifest[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_evidence_scope(
    value: Mapping[str, Any],
    *,
    roster_ledger: ProjectedLineupRosterLedger,
    history_ledger: ProspectiveOpportunityHistoryLedger,
) -> dict[str, Any]:
    expected_keys = {
        "schema_version", "created_at_utc", "collection_epoch_date",
        "runtime_manifest_sha256", "contract_sha256", "collector_code_sha256",
        "source_roster_contract_sha256", "source_roster_collector_code_sha256",
        "research_only", "economic_evidence_eligible",
        "historical_backfill_authorized",
        "production_probability_consumption_authorized", "betting_authorized",
        "evidence_scope_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise ScheduleDenominatorError("history evidence scope schema differs")
    _assert_unlinked(RUNTIME_PATH, "history runtime manifest", require_file=True)
    runtime_raw = RUNTIME_PATH.read_bytes()
    runtime = json.loads(runtime_raw, object_pairs_hook=_reject_duplicate_pairs)
    unsigned = dict(value)
    supplied = unsigned.pop("evidence_scope_sha256", None)
    source = runtime.get("source_roster_evidence", {})
    contract = runtime.get("contract", {})
    if (
        supplied != sha256_value(unsigned)
        or value.get("schema_version")
        != "prospective-batter-opportunity-evidence-scope-v1"
        or value.get("runtime_manifest_sha256")
        != hashlib.sha256(runtime_raw).hexdigest()
        or value.get("contract_sha256") != contract.get("sha256")
        or value.get("collector_code_sha256") != _history_collector_identity()
        or value.get("source_roster_contract_sha256")
        != source.get("contract_sha256")
        or value.get("source_roster_collector_code_sha256")
        != source.get("collector_code_sha256")
        or history_ledger.collection_epoch_date.isoformat()
        != value.get("collection_epoch_date")
        or history_ledger.contract_sha256 != value.get("contract_sha256")
        or history_ledger.collector_code_sha256 != value.get("collector_code_sha256")
        or history_ledger.evidence_scope_sha256 != supplied
        or roster_ledger.contract_sha256
        != value.get("source_roster_contract_sha256")
        or roster_ledger.collector_code_sha256
        != value.get("source_roster_collector_code_sha256")
        or value.get("research_only") is not True
        or value.get("economic_evidence_eligible") is not False
        or value.get("historical_backfill_authorized") is not False
        or value.get("production_probability_consumption_authorized") is not False
        or value.get("betting_authorized") is not False
    ):
        raise ScheduleDenominatorError("history evidence scope authority differs")
    _utc(value.get("created_at_utc"), "evidence scope created_at_utc")
    _canonical_date(value.get("collection_epoch_date"), "collection_epoch_date")
    return dict(value)


def _publish_once(path: Path, payload: bytes) -> bool:
    _assert_unlinked(path, "immutable artifact")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _assert_unlinked(path, "immutable artifact", require_file=True)
        if path.read_bytes() != payload:
            raise ScheduleDenominatorError(f"immutable artifact differs: {path.name}")
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
            if path.read_bytes() != payload:
                raise ScheduleDenominatorError(
                    f"concurrent immutable artifact differs: {path.name}"
                )
            return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _publish_target_directory(
    root: Path, identifier: str, files: Mapping[str, bytes]
) -> bool:
    """Stage one complete target and expose it with one atomic directory rename."""

    target_parent = root / "targets"
    final = target_parent / identifier
    _assert_unlinked(target_parent, "target authority root")
    target_parent.mkdir(parents=True, exist_ok=True)
    expected = {str(Path(name).as_posix()): bytes(payload) for name, payload in files.items()}
    if final.exists():
        _verify_target_files(final, expected)
        return False
    staging_parent = root / ".staging"
    _assert_unlinked(staging_parent, "non-authoritative staging root")
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = staging_parent / f"{identifier}-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        for relative, payload in expected.items():
            relative_path = Path(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ScheduleDenominatorError("staged target path escapes target directory")
            destination = staging / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            os.rename(staging, final)
        except FileExistsError:
            _verify_target_files(final, expected)
            return False
        _verify_target_files(final, expected)
        return True
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _verify_target_files(root: Path, expected: Mapping[str, bytes]) -> None:
    _assert_unlinked(root, "published target directory")
    if not root.is_dir():
        raise ScheduleDenominatorError("published target directory is unavailable")
    actual: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            _assert_unlinked(path, "published target directory")
            continue
        _assert_unlinked(path, "published target file", require_file=True)
        relative = path.relative_to(root).as_posix()
        actual[relative] = path.read_bytes()
    if actual != dict(expected):
        raise ScheduleDenominatorError("concurrent or existing target bytes differ")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ScheduleDenominatorError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _json(path: Path, label: str) -> dict[str, Any]:

    try:
        _assert_unlinked(path, label, require_file=True)
        value = json.loads(path.read_bytes(), object_pairs_hook=_reject_duplicate_pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScheduleDenominatorError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise ScheduleDenominatorError(f"{label} must be an object")
    return value


def permitted_prior_segments(
    *, collection_epoch_date: str, target_official_game_date: str
) -> tuple[tuple[str, str], ...]:
    """Return complete non-May date segments strictly before the target."""

    epoch = _canonical_date(collection_epoch_date, "collection_epoch_date")
    target = _canonical_date(target_official_game_date, "target_official_game_date")
    if epoch >= target:
        raise ScheduleDenominatorError("collection epoch must precede target date")
    segments: list[tuple[str, str]] = []
    cursor = epoch
    segment_start: date | None = None
    while cursor < target:
        sealed = cursor.year == 2026 and cursor.month == 5
        if sealed and segment_start is not None:
            segments.append((segment_start.isoformat(), (cursor - timedelta(days=1)).isoformat()))
            segment_start = None
        elif not sealed and segment_start is None:
            segment_start = cursor
        cursor += timedelta(days=1)
    if segment_start is not None:
        segments.append((segment_start.isoformat(), (target - timedelta(days=1)).isoformat()))
    if not segments:
        raise ScheduleDenominatorError("no permitted prior dates exist")
    return tuple(segments)


def parse_prior_schedule_denominator(
    *,
    response: RawOpportunityResponse,
    requested_start_date: str,
    requested_end_date: str,
    target_official_game_date: str,
    target_horizon_utc: str,
    team_id: int,
) -> dict[str, Any]:
    """V2 source boundary requiring an explicit row for every requested date.

    The v1 parser remains byte-preserved for its historical release manifest.
    Every new schedule-denominator producer calls this repaired boundary.
    """

    start = _canonical_date(requested_start_date, "requested_start_date")
    end = _canonical_date(requested_end_date, "requested_end_date")
    target = _canonical_date(target_official_game_date, "target_official_game_date")
    if start > end or end >= target:
        raise ScheduleDenominatorError(
            "prior schedule range must end before the target date"
        )
    cursor = start
    while cursor <= end:
        if cursor.year == 2026 and cursor.month == 5:
            raise ScheduleDenominatorError(
                "May 2026 is sealed before schedule response access"
            )
        cursor += timedelta(days=1)
    try:
        payload = json.loads(response.body, object_pairs_hook=_reject_duplicate_pairs)
        rows = payload["dates"]
        returned = {
            _canonical_date(row["date"], "schedule returned date").isoformat()
            for row in rows
        }
    except (TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScheduleDenominatorError(
            "schedule response cannot prove exact returned-date coverage"
        ) from exc
    expected: set[str] = set()
    cursor = start
    while cursor <= end:
        expected.add(cursor.isoformat())
        cursor += timedelta(days=1)
    if len(rows) != len(expected) or returned != expected:
        raise ScheduleDenominatorError(
            "schedule response does not return exactly every requested date"
        )
    return _parse_prior_schedule_denominator_v1(
        response=response,
        requested_start_date=requested_start_date,
        requested_end_date=requested_end_date,
        target_official_game_date=target_official_game_date,
        target_horizon_utc=target_horizon_utc,
        team_id=team_id,
    )


def build_denominator_target(
    *,
    plan: ShadowCapturePlan,
    target: CaptureTarget,
    side: str,
    source_roster_ledger: ProjectedLineupRosterLedger,
    source_history_ledger: ProspectiveOpportunityHistoryLedger,
    source_evidence_scope: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind one team-side denominator to an immutable T-4 target."""

    if side not in {"away", "home"}:
        raise ScheduleDenominatorError("side must be away or home")
    if plan.entry_hours != 4 or target.target_id not in {
        item.target_id for item in plan.targets
    }:
        raise ScheduleDenominatorError("target is not in an immutable T-4 plan")
    target_date = _canonical_date(target.official_game_date, "official_game_date")
    if plan.official_game_date != target_date.isoformat():
        raise ScheduleDenominatorError("plan and target date differ")
    if not isinstance(source_roster_ledger, ProjectedLineupRosterLedger):
        raise ScheduleDenominatorError("verified roster-ledger authority is required")
    if not isinstance(source_history_ledger, ProspectiveOpportunityHistoryLedger):
        raise ScheduleDenominatorError("verified history-ledger authority is required")
    if source_roster_ledger.plan.plan_sha256 != plan.plan_sha256:
        raise ScheduleDenominatorError("roster authority plan differs")
    expected_roster_root = (
        source_roster_ledger.root.parent.name == target_date.isoformat()
        and source_roster_ledger.root.name == plan.plan_sha256
    )
    expected_history_root = (
        source_history_ledger.root.parent.name == target_date.isoformat()
        and source_history_ledger.root.name == plan.plan_sha256
    )
    if not expected_roster_root or not expected_history_root:
        raise ScheduleDenominatorError("source ledger release path differs from target")
    roster_authority, terminal = _roster_authority(
        source_roster_ledger, plan=plan, target=target, side=side
    )
    history_authority, _ = _history_authority(source_history_ledger)
    evidence_scope = _validate_evidence_scope(
        source_evidence_scope,
        roster_ledger=source_roster_ledger,
        history_ledger=source_history_ledger,
    )
    side_id = roster_side_target_id(plan=plan, target=target, side=side)
    expected_terminal_keys = {
        "schema_version", "side_target_id", "plan_sha256", "target_id",
        "mlb_game_pk", "official_game_date", "side", "team_id",
        "terminal_state", "committed_utc", "schedule_received_at_utc",
        "schedule_raw", "roster_raw", "roster", "detail", "research_only",
        "betting_authorized", "entry_sha256",
    }
    unsigned_terminal = dict(terminal)
    terminal_sha = unsigned_terminal.pop("entry_sha256", None)
    if (
        set(terminal) != expected_terminal_keys
        or terminal_sha != sha256_value(unsigned_terminal)
        or terminal.get("schema_version") != "projected-lineup-roster-side-terminal-v1"
        or terminal.get("side_target_id") != side_id
        or terminal.get("plan_sha256") != plan.plan_sha256
        or terminal.get("target_id") != target.target_id
        or terminal.get("mlb_game_pk") != target.mlb_game_pk
        or terminal.get("official_game_date") != target.official_game_date
        or terminal.get("side") != side
        or terminal.get("terminal_state") != ROSTER_CAPTURED
        or terminal.get("research_only") is not True
        or terminal.get("betting_authorized") is not False
    ):
        raise ScheduleDenominatorError("captured roster authority differs")
    team_id = _positive_int(terminal.get("team_id"), "authority team_id")
    epoch = _canonical_date(
        source_history_ledger.collection_epoch_date.isoformat(),
        "collection_epoch_date",
    )
    segments = permitted_prior_segments(
        collection_epoch_date=epoch.isoformat(),
        target_official_game_date=target_date.isoformat(),
    )
    unsigned = {
        "schema_version": TARGET_SCHEMA,
        "plan_sha256": plan.plan_sha256,
        "target_id": target.target_id,
        "mlb_game_pk": target.mlb_game_pk,
        "official_game_date": target.official_game_date,
        "official_start_utc": target.official_start_time_utc,
        "target_horizon_utc": target.entry_target_at_utc,
        "side": side,
        "team_id": team_id,
        "collection_epoch_date": epoch.isoformat(),
        "roster_authority": roster_authority,
        "history_authority": history_authority,
        "evidence_scope": evidence_scope,
        "requested_segments": [
            {"start_date": start, "end_date": end} for start, end in segments
        ],
        "research_only": True,
        "backfill_authorized": False,
        "probability_consumption_authorized": False,
        "betting_authorized": False,
    }
    return {**unsigned, "denominator_target_id": sha256_value(unsigned)}


def _validate_target(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "plan_sha256", "target_id", "mlb_game_pk",
        "official_game_date", "official_start_utc", "target_horizon_utc",
        "side", "team_id", "collection_epoch_date", "requested_segments",
        "roster_authority", "history_authority", "evidence_scope",
        "research_only", "backfill_authorized",
        "probability_consumption_authorized", "betting_authorized",
        "denominator_target_id",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ScheduleDenominatorError("denominator target schema changed")
    if (
        value["schema_version"] != TARGET_SCHEMA
        or value["side"] not in {"away", "home"}
        or value["research_only"] is not True
        or value["backfill_authorized"] is not False
        or value["probability_consumption_authorized"] is not False
        or value["betting_authorized"] is not False
    ):
        raise ScheduleDenominatorError("denominator target authority changed")
    _sha(value["plan_sha256"], "plan_sha256")
    _sha(value["target_id"], "target_id")
    _positive_int(value["mlb_game_pk"], "mlb_game_pk")
    _positive_int(value["team_id"], "team_id")
    _canonical_date(value["official_game_date"], "official_game_date")
    _utc(value["official_start_utc"], "official_start_utc")
    _utc(value["target_horizon_utc"], "target_horizon_utc")
    for label, authority, fields in (
        (
            "roster authority", value["roster_authority"],
            {"schema_version", "side_target_id", "terminal_entry_sha256",
             "schedule_raw_sha256", "roster_raw_sha256", "ledger_snapshot",
             "authority_sha256"},
        ),
        (
            "history authority", value["history_authority"],
            {"schema_version", "collection_epoch_date", "contract_sha256",
             "collector_code_sha256", "evidence_scope_sha256",
             "ledger_snapshot", "authority_sha256"},
        ),
    ):
        if not isinstance(authority, Mapping) or set(authority) != fields:
            raise ScheduleDenominatorError(f"{label} schema differs")
        unsigned_authority = dict(authority)
        authority_sha = unsigned_authority.pop("authority_sha256", None)
        if authority_sha != sha256_value(unsigned_authority):
            raise ScheduleDenominatorError(f"{label} hash differs")
        snapshot = authority.get("ledger_snapshot")
        if not isinstance(snapshot, Mapping) or set(snapshot) != {"files", "snapshot_sha256"}:
            raise ScheduleDenominatorError(f"{label} snapshot differs")
        unsigned_snapshot = {"files": snapshot.get("files")}
        if snapshot.get("snapshot_sha256") != sha256_value(unsigned_snapshot):
            raise ScheduleDenominatorError(f"{label} snapshot hash differs")
    scope = value["evidence_scope"]
    if not isinstance(scope, Mapping):
        raise ScheduleDenominatorError("evidence scope is missing")
    unsigned_scope = dict(scope)
    scope_sha = unsigned_scope.pop("evidence_scope_sha256", None)
    if (
        scope_sha != sha256_value(unsigned_scope)
        or scope_sha != value["history_authority"].get("evidence_scope_sha256")
        or scope.get("collection_epoch_date") != value["collection_epoch_date"]
    ):
        raise ScheduleDenominatorError("evidence scope binding differs")
    expected_segments = [
        {"start_date": start, "end_date": end}
        for start, end in permitted_prior_segments(
            collection_epoch_date=str(value["collection_epoch_date"]),
            target_official_game_date=str(value["official_game_date"]),
        )
    ]
    if value["requested_segments"] != expected_segments:
        raise ScheduleDenominatorError("denominator target segments differ")
    unsigned = dict(value)
    supplied = unsigned.pop("denominator_target_id", None)
    if supplied != sha256_value(unsigned):
        raise ScheduleDenominatorError("denominator target identity differs")
    return dict(value)


def _replay_target_authorities(
    target: Mapping[str, Any],
    *,
    roster_ledger: ProjectedLineupRosterLedger,
    history_ledger: ProspectiveOpportunityHistoryLedger,
    evidence_scope: Mapping[str, Any],
) -> list[dict[str, Any]]:
    plan = roster_ledger.plan
    planned_target = next(
        (item for item in plan.targets if item.target_id == target["target_id"]), None
    )
    if planned_target is None or plan.plan_sha256 != target["plan_sha256"]:
        raise ScheduleDenominatorError("target is absent from roster authority plan")
    if (
        planned_target.mlb_game_pk != target["mlb_game_pk"]
        or planned_target.official_game_date != target["official_game_date"]
        or planned_target.official_start_time_utc != target["official_start_utc"]
        or planned_target.entry_target_at_utc != target["target_horizon_utc"]
    ):
        raise ScheduleDenominatorError("target game chronology differs from roster authority")
    roster_authority, terminal = _roster_authority(
        roster_ledger,
        plan=plan,
        target=planned_target,
        side=str(target["side"]),
    )
    history_authority, history_rows = _history_authority(history_ledger)
    replayed_scope = _validate_evidence_scope(
        evidence_scope, roster_ledger=roster_ledger, history_ledger=history_ledger
    )
    if (
        roster_authority != target["roster_authority"]
        or history_authority != target["history_authority"]
        or replayed_scope != target["evidence_scope"]
        or terminal.get("team_id") != target["team_id"]
    ):
        raise ScheduleDenominatorError("target authority replay differs")
    return history_rows


def _derive_history_partition(
    ledger: ProspectiveOpportunityHistoryLedger,
    *,
    expected_game_pks: Sequence[int],
    team_id: int,
    collection_epoch_date: str,
    target_official_game_date: str,
) -> dict[str, Any]:
    ledger.verify(require_all_terminal=True)
    expected = {_positive_int(value, "expected gamePk") for value in expected_game_pks}
    if len(expected) != len(expected_game_pks):
        raise ScheduleDenominatorError("schedule denominator game identities duplicate")
    epoch = _canonical_date(collection_epoch_date, "collection_epoch_date")
    target_date = _canonical_date(target_official_game_date, "target date")
    plans = {plan["capture_plan_sha256"]: plan for plan in ledger.plans()}
    proofs: list[dict[str, Any]] = []
    seen: set[int] = set()
    terminal_dir = ledger.root / "terminal"
    for path in sorted(terminal_dir.glob("*.json")) if terminal_dir.is_dir() else []:
        plan = plans[path.stem]
        game_date = _canonical_date(plan["official_game_date"], "history game date")
        if plan["team_id"] != team_id or not epoch <= game_date < target_date:
            continue
        game_pk = _positive_int(plan["mlb_game_pk"], "history gamePk")
        if game_pk not in expected or game_pk in seen:
            raise ScheduleDenominatorError("history authority game differs from schedule denominator")
        entry = _json(path, "history partition terminal")
        state = entry.get("terminal_state")
        raw = entry.get("raw")
        proof = {
            "mlb_game_pk": game_pk,
            "partition_state": "captured" if state == "captured" else "terminal_missing",
            "authority_path": path.relative_to(ledger.root).as_posix(),
            "terminal_entry_sha256": entry.get("entry_sha256"),
            "raw_path": raw.get("path") if isinstance(raw, Mapping) else None,
            "raw_sha256": raw.get("sha256") if isinstance(raw, Mapping) else None,
        }
        proofs.append(proof)
        seen.add(game_pk)
    for entry in ledger.planning_exclusions():
        terminal = entry.get("active_roster_terminal", {})
        game_date = _canonical_date(terminal.get("official_game_date"), "history game date")
        if terminal.get("team_id") != team_id or not epoch <= game_date < target_date:
            continue
        game_pk = _positive_int(terminal.get("mlb_game_pk"), "history gamePk")
        if game_pk not in expected or game_pk in seen:
            raise ScheduleDenominatorError("planning-miss authority differs from denominator")
        proofs.append(
            {
                "mlb_game_pk": game_pk,
                "partition_state": "terminal_missing",
                "authority_path": f"planning_terminal/{entry['planning_exclusion_id']}.json",
                "terminal_entry_sha256": entry.get("entry_sha256"),
                "raw_path": None,
                "raw_sha256": None,
            }
        )
        seen.add(game_pk)
    if seen != expected:
        raise ScheduleDenominatorError("verified history terminals do not cover denominator")
    proofs = sorted(proofs, key=lambda row: row["mlb_game_pk"])
    unsigned = {
        "captured_game_pks": [
            row["mlb_game_pk"] for row in proofs if row["partition_state"] == "captured"
        ],
        "terminal_missing_game_pks": [
            row["mlb_game_pk"]
            for row in proofs
            if row["partition_state"] == "terminal_missing"
        ],
        "terminal_authorities": proofs,
    }
    return {**unsigned, "partition_sha256": sha256_value(unsigned)}


def _replay_copied_history_authority(
    *,
    ledger_root: Path,
    target: Mapping[str, Any],
    references: Any,
    expected_game_pks: Sequence[int],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    if not isinstance(references, list):
        raise ScheduleDenominatorError("history authority file manifest is missing")
    identifier = target["denominator_target_id"]
    payloads: dict[str, bytes] = {}
    canonical_rows: list[dict[str, Any]] = []
    for reference in references:
        if not isinstance(reference, Mapping) or set(reference) != {
            "path", "sha256", "size", "source_relative_path"
        }:
            raise ScheduleDenominatorError("history authority reference schema differs")
        source_relative = str(reference["source_relative_path"])
        canonical_relative = PurePosixPath(source_relative)
        if (
            not source_relative
            or "\\" in source_relative
            or canonical_relative.is_absolute()
            or ".." in canonical_relative.parts
            or source_relative != canonical_relative.as_posix()
        ):
            raise ScheduleDenominatorError(
                "history authority source path is not canonical"
            )
        expected_path = f"targets/{identifier}/history_authority/{source_relative}"
        if reference != {
            "path": expected_path,
            "sha256": reference["sha256"],
            "size": reference["size"],
            "source_relative_path": source_relative,
        }:
            raise ScheduleDenominatorError("history authority reference differs")
        candidate = ledger_root / expected_path
        _assert_unlinked(candidate, "copied history authority", require_file=True)
        raw = candidate.read_bytes()
        if (
            len(raw) != reference["size"]
            or hashlib.sha256(raw).hexdigest() != reference["sha256"]
            or source_relative in payloads
        ):
            raise ScheduleDenominatorError("copied history authority bytes differ")
        payloads[source_relative] = raw
        canonical_rows.append(
            {
                "path": source_relative,
                "sha256": reference["sha256"],
                "size": reference["size"],
            }
        )
    canonical_rows.sort(key=lambda row: row["path"])
    if _snapshot_identity(canonical_rows) != target["history_authority"]["ledger_snapshot"]:
        raise ScheduleDenominatorError("copied history snapshot identity differs")
    with tempfile.TemporaryDirectory() as temporary:
        replay_root = Path(temporary) / "history"
        for relative, raw in payloads.items():
            destination = replay_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        authority = target["history_authority"]
        replayed = ProspectiveOpportunityHistoryLedger(
            replay_root,
            collection_epoch_date=authority["collection_epoch_date"],
            contract_sha256=authority["contract_sha256"],
            collector_code_sha256=authority["collector_code_sha256"],
            evidence_scope_sha256=authority["evidence_scope_sha256"],
        )
        partition = _derive_history_partition(
            replayed,
            expected_game_pks=expected_game_pks,
            team_id=target["team_id"],
            collection_epoch_date=target["collection_epoch_date"],
            target_official_game_date=target["official_game_date"],
        )
    return partition, payloads


class ScheduleDenominatorLedger:
    """Publish-once raw/receipt/coverage ledger for surfaces 10-12."""

    def __init__(
        self,
        root: str | Path,
    ) -> None:
        _assert_unlinked(Path(root), "denominator ledger root")
        self.root = Path(os.path.abspath(root))
        self.contract_sha256, self.collector_code_sha256 = release_identities()
        manifest = {
            "schema_version": LEDGER_SCHEMA,
            "contract_sha256": self.contract_sha256,
            "collector_code_sha256": self.collector_code_sha256,
            "surface_numbers": [10, 11, 12],
            "research_only": True,
            "backfill_authorized": False,
            "probability_consumption_authorized": False,
            "betting_authorized": False,
        }
        _publish_once(self.root / "ledger_manifest.json", canonical_bytes(manifest) + b"\n")

    def terminal_target_ids(self) -> set[str]:
        directory = self.root / "targets"
        if not directory.is_dir():
            return set()
        _assert_unlinked(directory, "denominator targets directory")
        identifiers: set[str] = set()
        for path in directory.iterdir():
            if path.name.startswith("."):
                raise ScheduleDenominatorError("unfinished staged target exists")
            _assert_unlinked(path, "denominator target directory")
            if not path.is_dir() or len(path.name) != 64:
                raise ScheduleDenominatorError("denominator target directory differs")
            identifiers.add(path.name)
        return identifiers

    @staticmethod
    def _receipts(
        target: Mapping[str, Any], responses: Sequence[RawOpportunityResponse]
    ) -> list[dict[str, Any]]:
        segments = target["requested_segments"]
        if not isinstance(responses, Sequence) or isinstance(responses, (bytes, str)):
            raise ScheduleDenominatorError("schedule responses must be a sequence")
        if len(responses) != len(segments):
            raise ScheduleDenominatorError("schedule response segment coverage differs")
        receipts: list[dict[str, Any]] = []
        for segment, response in zip(segments, responses, strict=True):
            if not isinstance(response, RawOpportunityResponse):
                raise ScheduleDenominatorError("schedule response type differs")
            try:
                receipt = parse_prior_schedule_denominator(
                    response=response,
                    requested_start_date=segment["start_date"],
                    requested_end_date=segment["end_date"],
                    target_official_game_date=target["official_game_date"],
                    target_horizon_utc=target["target_horizon_utc"],
                    team_id=target["team_id"],
                )
            except ProspectiveBatterOpportunityError as exc:
                raise ScheduleDenominatorError(
                    "schedule response cannot prove its denominator segment"
                ) from exc
            receipts.append(receipt)
        return receipts

    def _append_capture(
        self,
        *,
        target: Mapping[str, Any],
        responses: Sequence[RawOpportunityResponse],
        source_roster_ledger: ProjectedLineupRosterLedger,
        source_history_ledger: ProspectiveOpportunityHistoryLedger,
        source_evidence_scope: Mapping[str, Any],
        committed_at_utc: str,
    ) -> bool:
        validated = _validate_target(target)
        history_rows = _replay_target_authorities(
            validated,
            roster_ledger=source_roster_ledger,
            history_ledger=source_history_ledger,
            evidence_scope=source_evidence_scope,
        )
        committed = _utc(committed_at_utc, "committed_at_utc")
        horizon = _utc(validated["target_horizon_utc"], "target_horizon_utc")
        if committed > horizon:
            raise ScheduleDenominatorError("schedule denominator committed after T-4")
        receipts = self._receipts(validated, responses)
        if any(
            _utc(receipt["source_received_at_utc"], "source_received_at_utc")
            > committed
            for receipt in receipts
        ):
            raise ScheduleDenominatorError("schedule response arrived after commit time")
        try:
            expected_game_pks = sorted(
                game["mlb_game_pk"]
                for receipt in receipts
                for game in receipt["games"]
                if game["coded_final"] is True
            )
            partition = _derive_history_partition(
                source_history_ledger,
                expected_game_pks=expected_game_pks,
                team_id=validated["team_id"],
                collection_epoch_date=validated["collection_epoch_date"],
                target_official_game_date=validated["official_game_date"],
            )
            coverage = build_history_coverage(
                collection_epoch_date=validated["collection_epoch_date"],
                target_official_game_date=validated["official_game_date"],
                team_id=validated["team_id"],
                schedule_receipts=receipts,
                captured_prior_game_pks=partition["captured_game_pks"],
                terminal_missing_prior_game_pks=partition[
                    "terminal_missing_game_pks"
                ],
            )
        except ProspectiveBatterOpportunityError as exc:
            raise ScheduleDenominatorError(
                "history terminals do not partition the schedule denominator"
            ) from exc
        identifier = validated["denominator_target_id"]
        raw_references = []
        history_references = []
        staged_files: dict[str, bytes] = {}
        for response in responses:
            digest = hashlib.sha256(response.body).hexdigest()
            relative = f"raw/{digest}.json"
            if relative in staged_files and staged_files[relative] != response.body:
                raise ScheduleDenominatorError("schedule raw hash collision")
            staged_files[relative] = response.body
            raw_references.append(
                {
                    "path": f"targets/{identifier}/{relative}",
                    "sha256": digest,
                    "size": len(response.body),
                }
            )
        for row in history_rows:
            relative = f"history_authority/{row['path']}"
            staged_files[relative] = bytes(row["raw"])
            history_references.append(
                {
                    "path": f"targets/{identifier}/{relative}",
                    "sha256": row["sha256"],
                    "size": row["size"],
                    "source_relative_path": row["path"],
                }
            )
        unsigned = {
            "schema_version": TERMINAL_SCHEMA,
            "denominator_target": validated,
            "terminal_state": CAPTURED,
            "committed_at_utc": _stamp(committed),
            "surface_10_history_coverage": coverage,
            "surface_11_prior_schedule_receipts": receipts,
            "surface_12_prior_schedule_raw": raw_references,
            "history_partition_authority": partition,
            "history_authority_files": history_references,
            "detail": "",
            "research_only": True,
            "backfill_authorized": False,
            "probability_consumption_authorized": False,
            "betting_authorized": False,
        }
        entry = {**unsigned, "terminal_sha256": sha256_value(unsigned)}
        staged_files["terminal.json"] = canonical_bytes(entry) + b"\n"
        return _publish_target_directory(self.root, identifier, staged_files)

    def _append_exclusion(
        self,
        *,
        target: Mapping[str, Any],
        source_roster_ledger: ProjectedLineupRosterLedger,
        source_history_ledger: ProspectiveOpportunityHistoryLedger,
        source_evidence_scope: Mapping[str, Any],
        state: str,
        observed_at_utc: str,
        detail: str,
    ) -> bool:
        validated = _validate_target(target)
        _replay_target_authorities(
            validated,
            roster_ledger=source_roster_ledger,
            history_ledger=source_history_ledger,
            evidence_scope=source_evidence_scope,
        )
        if state not in EXCLUSIONS or not isinstance(detail, str) or not detail.strip():
            raise ScheduleDenominatorError("invalid schedule denominator exclusion")
        observed = _utc(observed_at_utc, "observed_at_utc")
        horizon = _utc(validated["target_horizon_utc"], "target_horizon_utc")
        if state == SOURCE_ERROR and observed > horizon:
            raise ScheduleDenominatorError("source error was observed after T-4")
        if state == MISSED and observed <= horizon:
            raise ScheduleDenominatorError("missed target was observed before T-4")
        unsigned = {
            "schema_version": TERMINAL_SCHEMA,
            "denominator_target": validated,
            "terminal_state": state,
            "committed_at_utc": _stamp(observed),
            "surface_10_history_coverage": None,
            "surface_11_prior_schedule_receipts": [],
            "surface_12_prior_schedule_raw": [],
            "history_partition_authority": None,
            "history_authority_files": [],
            "detail": detail.strip(),
            "research_only": True,
            "backfill_authorized": False,
            "probability_consumption_authorized": False,
            "betting_authorized": False,
        }
        entry = {**unsigned, "terminal_sha256": sha256_value(unsigned)}
        identifier = validated["denominator_target_id"]
        return _publish_target_directory(
            self.root,
            identifier,
            {"terminal.json": canonical_bytes(entry) + b"\n"},
        )

    def verify(self) -> dict[str, int]:
        manifest = _json(self.root / "ledger_manifest.json", "denominator ledger manifest")
        if manifest != {
            "schema_version": LEDGER_SCHEMA,
            "contract_sha256": self.contract_sha256,
            "collector_code_sha256": self.collector_code_sha256,
            "surface_numbers": [10, 11, 12],
            "research_only": True,
            "backfill_authorized": False,
            "probability_consumption_authorized": False,
            "betting_authorized": False,
        }:
            raise ScheduleDenominatorError("denominator ledger manifest differs")
        counts = {CAPTURED: 0, SOURCE_ERROR: 0, MISSED: 0}
        target_ids = self.terminal_target_ids()
        paths = [self.root / "targets" / identifier / "terminal.json" for identifier in sorted(target_ids)]
        for path in paths:
            entry = _json(path, "denominator terminal")
            unsigned = dict(entry)
            supplied = unsigned.pop("terminal_sha256", None)
            if supplied != sha256_value(unsigned) or set(unsigned) != {
                "schema_version", "denominator_target", "terminal_state",
                "committed_at_utc", "surface_10_history_coverage",
                "surface_11_prior_schedule_receipts",
                "surface_12_prior_schedule_raw", "history_partition_authority",
                "history_authority_files", "detail", "research_only",
                "backfill_authorized", "probability_consumption_authorized",
                "betting_authorized",
            }:
                raise ScheduleDenominatorError("denominator terminal hash or schema differs")
            target = _validate_target(entry["denominator_target"])
            if path.parent.name != target["denominator_target_id"]:
                raise ScheduleDenominatorError("denominator terminal filename differs")
            state = entry["terminal_state"]
            if (
                state not in counts
                or entry.get("research_only") is not True
                or entry.get("backfill_authorized") is not False
                or entry.get("probability_consumption_authorized") is not False
                or entry.get("betting_authorized") is not False
            ):
                raise ScheduleDenominatorError("denominator terminal state differs")
            committed = _utc(entry["committed_at_utc"], "committed_at_utc")
            horizon = _utc(target["target_horizon_utc"], "target_horizon_utc")
            if state == CAPTURED:
                references = entry["surface_12_prior_schedule_raw"]
                receipts = entry["surface_11_prior_schedule_receipts"]
                coverage = entry["surface_10_history_coverage"]
                partition = entry["history_partition_authority"]
                history_references = entry["history_authority_files"]
                if not isinstance(references, list) or not isinstance(receipts, list):
                    raise ScheduleDenominatorError("captured surfaces are malformed")
                if len(references) != len(receipts) or committed > horizon:
                    raise ScheduleDenominatorError("captured surface chronology differs")
                responses: list[RawOpportunityResponse] = []
                for reference, receipt in zip(references, receipts, strict=True):
                    if not isinstance(reference, Mapping) or set(reference) != {
                        "path", "sha256", "size"
                    }:
                        raise ScheduleDenominatorError("raw schedule reference differs")
                    expected_reference = {
                        "path": (
                            f"targets/{target['denominator_target_id']}/raw/"
                            f"{reference['sha256']}.json"
                        ),
                        "sha256": reference["sha256"],
                        "size": reference["size"],
                    }
                    if dict(reference) != expected_reference:
                        raise ScheduleDenominatorError("raw schedule path binding differs")
                    raw_candidate = self.root / str(reference["path"])
                    _assert_unlinked(raw_candidate, "raw schedule bytes", require_file=True)
                    raw_path = raw_candidate.resolve()
                    try:
                        raw_path.relative_to(self.root)
                    except ValueError as exc:
                        raise ScheduleDenominatorError("raw schedule path escapes ledger") from exc
                    if not raw_path.is_file():
                        raise ScheduleDenominatorError("raw schedule bytes are missing")
                    raw = raw_path.read_bytes()
                    if (
                        len(raw) != reference["size"]
                        or hashlib.sha256(raw).hexdigest() != reference["sha256"]
                        or receipt.get("source_payload_sha256") != reference["sha256"]
                    ):
                        raise ScheduleDenominatorError("raw schedule bytes differ")
                    responses.append(
                        RawOpportunityResponse(
                            raw,
                            str(receipt.get("source_received_at_utc")),
                            str(receipt.get("source_request_url")),
                            int(receipt.get("source_http_status")),
                            str(receipt.get("source_content_type")),
                        )
                    )
                replayed = self._receipts(target, responses)
                if replayed != receipts or not isinstance(coverage, Mapping):
                    raise ScheduleDenominatorError("schedule receipts do not replay")
                expected_game_pks = sorted(
                    game["mlb_game_pk"]
                    for receipt in replayed
                    for game in receipt["games"]
                    if game["coded_final"] is True
                )
                replayed_partition, history_payloads = _replay_copied_history_authority(
                    ledger_root=self.root,
                    target=target,
                    references=history_references,
                    expected_game_pks=expected_game_pks,
                )
                if replayed_partition != partition:
                    raise ScheduleDenominatorError("history partition authority differs")
                try:
                    rebuilt = build_history_coverage(
                        collection_epoch_date=target["collection_epoch_date"],
                        target_official_game_date=target["official_game_date"],
                        team_id=target["team_id"],
                        schedule_receipts=replayed,
                        captured_prior_game_pks=coverage.get(
                            "captured_prior_game_pks", []
                        ),
                        terminal_missing_prior_game_pks=coverage.get(
                            "terminal_missing_prior_game_pks", []
                        ),
                    )
                except ProspectiveBatterOpportunityError as exc:
                    raise ScheduleDenominatorError("history coverage does not replay") from exc
                if (
                    rebuilt != dict(coverage)
                    or coverage.get("captured_prior_game_pks")
                    != replayed_partition["captured_game_pks"]
                    or coverage.get("terminal_missing_prior_game_pks")
                    != replayed_partition["terminal_missing_game_pks"]
                ):
                    raise ScheduleDenominatorError("history coverage differs from replay")
                expected_files = {
                    "terminal.json": path.read_bytes(),
                    **{
                        str(reference["path"]).split(
                            f"targets/{target['denominator_target_id']}/", 1
                        )[1]: (self.root / str(reference["path"])).read_bytes()
                        for reference in references
                    },
                    **{
                        f"history_authority/{relative}": raw
                        for relative, raw in history_payloads.items()
                    },
                }
                _verify_target_files(path.parent, expected_files)
            else:
                if (
                    entry["surface_10_history_coverage"] is not None
                    or entry["surface_11_prior_schedule_receipts"] != []
                    or entry["surface_12_prior_schedule_raw"] != []
                    or entry["history_partition_authority"] is not None
                    or entry["history_authority_files"] != []
                    or not str(entry["detail"]).strip()
                ):
                    raise ScheduleDenominatorError("terminal missingness claims evidence")
                if (state == SOURCE_ERROR and committed > horizon) or (
                    state == MISSED and committed <= horizon
                ):
                    raise ScheduleDenominatorError("terminal missing chronology differs")
                _verify_target_files(path.parent, {"terminal.json": path.read_bytes()})
            counts[state] += 1
        return counts


ScheduleFetcher = Callable[[str, str, int], RawOpportunityResponse]
TrustedClock = Callable[[], datetime]


def _trusted_now(clock: TrustedClock) -> datetime:
    if not callable(clock):
        raise ScheduleDenominatorError("an injected trusted clock is required")
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleDenominatorError("trusted clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def run_target_tick(
    *,
    ledger: ScheduleDenominatorLedger,
    target: Mapping[str, Any],
    fetch_schedule: ScheduleFetcher,
    source_roster_ledger: ProjectedLineupRosterLedger,
    source_history_ledger: ProspectiveOpportunityHistoryLedger,
    source_evidence_scope: Mapping[str, Any],
    trusted_clock: TrustedClock,
    max_early_seconds: int = MAX_EARLY_SECONDS,
) -> str:
    """Run one immutable target once, with no late fetch or retry path."""

    validated = _validate_target(target)
    _replay_target_authorities(
        validated,
        roster_ledger=source_roster_ledger,
        history_ledger=source_history_ledger,
        evidence_scope=source_evidence_scope,
    )
    if max_early_seconds != MAX_EARLY_SECONDS:
        raise ScheduleDenominatorError("T-4 early window is immutable")
    current = _trusted_now(trusted_clock)
    identifier = validated["denominator_target_id"]
    if identifier in ledger.terminal_target_ids():
        ledger.verify()
        return "terminal_existing"
    horizon = _utc(validated["target_horizon_utc"], "target_horizon_utc")
    if current < horizon - timedelta(seconds=MAX_EARLY_SECONDS):
        return "future"
    if current > horizon:
        try:
            published = ledger._append_exclusion(
                target=validated,
                source_roster_ledger=source_roster_ledger,
                source_history_ledger=source_history_ledger,
                source_evidence_scope=source_evidence_scope,
                state=MISSED,
                observed_at_utc=_stamp(current),
                detail="collector tick occurred after T-4; no fetch or backfill attempted",
            )
            if not published:
                ledger.verify()
                return "terminal_existing"
        except Exception:
            if identifier in ledger.terminal_target_ids():
                ledger.verify()
                return "terminal_existing"
            raise
        return MISSED
    responses: list[RawOpportunityResponse] = []
    try:
        for segment in validated["requested_segments"]:
            responses.append(
                fetch_schedule(
                    str(segment["start_date"]),
                    str(segment["end_date"]),
                    int(validated["team_id"]),
                )
            )
        receipts = ledger._receipts(validated, responses)
        committed = _trusted_now(trusted_clock)
        if committed < current:
            raise ScheduleDenominatorError("trusted clock moved backwards")
        if committed > horizon:
            ledger._append_exclusion(
                target=validated,
                source_roster_ledger=source_roster_ledger,
                source_history_ledger=source_history_ledger,
                source_evidence_scope=source_evidence_scope,
                state=MISSED,
                observed_at_utc=_stamp(committed),
                detail="schedule collection crossed T-4; no evidence was committed or backfilled",
            )
            return MISSED
        published = ledger._append_capture(
            target=validated,
            responses=responses,
            source_roster_ledger=source_roster_ledger,
            source_history_ledger=source_history_ledger,
            source_evidence_scope=source_evidence_scope,
            committed_at_utc=_stamp(committed),
        )
        if not published:
            ledger.verify()
            return "terminal_existing"
    except Exception as exc:
        if identifier in ledger.terminal_target_ids():
            ledger.verify()
            return "terminal_existing"
        observed = max(
            [
                current,
                _trusted_now(trusted_clock),
                *[
                    _utc(response.received_at_utc, "response received_at_utc")
                    for response in responses
                ],
            ]
        )
        state = SOURCE_ERROR if observed <= horizon else MISSED
        ledger._append_exclusion(
            target=validated,
            source_roster_ledger=source_roster_ledger,
            source_history_ledger=source_history_ledger,
            source_evidence_scope=source_evidence_scope,
            state=state,
            observed_at_utc=_stamp(observed),
            detail=f"schedule denominator failed closed ({type(exc).__name__})",
        )
        return state
    return CAPTURED
