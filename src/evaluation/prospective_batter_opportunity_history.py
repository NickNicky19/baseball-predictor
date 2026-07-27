"""Future-only collection ledger and one-tick runner for PA-opportunity history.

The ledger accepts only capture plans that were bound to an existing T-4
active-roster receipt and published before first pitch.  A final response must
arrive inside the predeclared window.  Missed windows are terminal; this module
has no historical backfill entry point.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.evaluation.projected_lineup_contract import canonical_bytes, sha256_value
from src.evaluation.projected_lineup_official_roster import (
    OfficialRosterReceiptError,
    RawOfficialRosterResponse,
    parse_active_roster_receipt,
)
from src.evaluation.projected_lineup_roster_ledger import (
    CAPTURED as ROSTER_CAPTURED,
    ProjectedLineupRosterLedger,
    ProjectedLineupRosterLedgerError,
    roster_side_target_id,
)
from src.evaluation.shadow_capture_plan import ShadowCapturePlan, ShadowCapturePlanError
from src.evaluation.prospective_batter_opportunity import (
    ProspectiveBatterOpportunityError,
    RawOpportunityResponse,
    parse_opportunity_history,
    sanitize_opportunity_transport,
    validate_history_capture_plan,
)


class ProspectiveOpportunityHistoryError(ValueError):
    """Future opportunity history is incomplete, inconsistent, or altered."""


CAPTURED = "captured"
PLANNING_MISSED = "missed_before_plan"
EXCLUSION_STATES = {"deadline_missing", "source_invalid"}


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ProspectiveOpportunityHistoryError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProspectiveOpportunityHistoryError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProspectiveOpportunityHistoryError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveOpportunityHistoryError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ProspectiveOpportunityHistoryError(f"{label} must be a canonical ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ProspectiveOpportunityHistoryError(f"{label} must be a canonical ISO date") from exc
    if parsed.isoformat() != value:
        raise ProspectiveOpportunityHistoryError(f"{label} must be a canonical ISO date")
    if parsed.year == 2026 and parsed.month == 5:
        raise ProspectiveOpportunityHistoryError("May 2026 is sealed from opportunity history")
    return parsed


def _publish_once(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ProspectiveOpportunityHistoryError(f"immutable artifact already differs: {path.name}")
        return False
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
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
                raise ProspectiveOpportunityHistoryError(f"concurrent immutable artifact differs: {path.name}")
            return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


class ProspectiveOpportunityHistoryLedger:
    """Publish-once plan/raw/terminal store for future opportunity history."""

    def __init__(
        self,
        root: str | Path,
        *,
        collection_epoch_date: str,
        contract_sha256: str,
        collector_code_sha256: str,
        evidence_scope_sha256: str,
    ) -> None:
        self.root = Path(root).resolve()
        self.collection_epoch_date = _canonical_date(collection_epoch_date, "collection_epoch_date")
        self.contract_sha256 = _sha(contract_sha256, "contract_sha256")
        self.collector_code_sha256 = _sha(collector_code_sha256, "collector_code_sha256")
        self.evidence_scope_sha256 = _sha(evidence_scope_sha256, "evidence_scope_sha256")
        manifest = {
            "schema_version": "prospective-batter-opportunity-history-ledger-v1",
            "collection_epoch_date": self.collection_epoch_date.isoformat(),
            "contract_sha256": self.contract_sha256,
            "collector_code_sha256": self.collector_code_sha256,
            "evidence_scope_sha256": self.evidence_scope_sha256,
            "research_only": True,
            "historical_backfill_authorized": False,
            "production_probability_consumption_authorized": False,
            "betting_authorized": False,
        }
        _publish_once(self.root / "ledger_manifest.json", canonical_bytes(manifest) + b"\n")

    def append_plan(
        self,
        plan: Mapping[str, Any],
        *,
        published_at_utc: str,
        source_roster_ledger: ProjectedLineupRosterLedger,
    ) -> bool:
        try:
            validated = validate_history_capture_plan(plan)
        except ProspectiveBatterOpportunityError as exc:
            raise ProspectiveOpportunityHistoryError("history capture plan is invalid") from exc
        published = _utc(published_at_utc, "published_at_utc")
        if _stamp(published) != validated["created_at_utc"]:
            raise ProspectiveOpportunityHistoryError("plan publication time must equal its immutable creation time")
        if published >= _utc(validated["official_start_time_utc"], "official_start_time_utc"):
            raise ProspectiveOpportunityHistoryError("history plan cannot be published at or after first pitch")
        if _canonical_date(validated["official_game_date"], "official_game_date") < self.collection_epoch_date:
            raise ProspectiveOpportunityHistoryError("history plan predates the prospective collection epoch")
        bundle = self._verified_plan_bundle(
            validated,
            source_roster_ledger=source_roster_ledger,
        )
        digest = validated["capture_plan_sha256"]
        return _publish_once(self.root / "plans" / f"{digest}.json", canonical_bytes(bundle) + b"\n")

    def plans(self) -> list[dict[str, Any]]:
        paths = sorted((self.root / "plans").glob("*.json")) if (self.root / "plans").is_dir() else []
        return [self._read_plan(path) for path in paths]

    def terminal_plan_ids(self) -> set[str]:
        directory = self.root / "terminal"
        return {path.stem for path in directory.glob("*.json")} if directory.is_dir() else set()

    def planning_exclusion_ids(self) -> set[str]:
        directory = self.root / "planning_terminal"
        return {path.stem for path in directory.glob("*.json")} if directory.is_dir() else set()

    def append_planning_exclusion(
        self,
        *,
        source_roster_ledger: ProjectedLineupRosterLedger,
        target_id: str,
        side: str,
        observed_at_utc: str,
        detail: str,
    ) -> bool:
        """Record that a receipt-proven side was discovered too late to plan.

        This is deliberately separate from a capture plan: manufacturing a plan
        after first pitch would backdate authorization.  The terminal record
        carries a self-contained replayable copy of the pregame roster proof.
        """

        if not isinstance(source_roster_ledger, ProjectedLineupRosterLedger):
            raise ProspectiveOpportunityHistoryError("source roster ledger is required")
        try:
            source_roster_ledger.verify()
        except ProjectedLineupRosterLedgerError as exc:
            raise ProspectiveOpportunityHistoryError("source roster ledger failed replay") from exc
        source_plan = source_roster_ledger.plan
        target = next((item for item in source_plan.targets if item.target_id == target_id), None)
        if target is None:
            raise ProspectiveOpportunityHistoryError("planning exclusion target is absent from the T-minus-4 plan")
        if side not in {"away", "home"}:
            raise ProspectiveOpportunityHistoryError("planning exclusion side is invalid")
        game_date = _canonical_date(target.official_game_date, "official_game_date")
        if game_date < self.collection_epoch_date:
            raise ProspectiveOpportunityHistoryError("planning exclusion predates collection epoch")
        observed = _utc(observed_at_utc, "observed_at_utc")
        if observed < _utc(target.official_start_time_utc, "official_start_time_utc"):
            raise ProspectiveOpportunityHistoryError("planning exclusion cannot be published before first pitch")
        if not isinstance(detail, str) or not detail.strip():
            raise ProspectiveOpportunityHistoryError("planning exclusion detail is required")
        side_id = roster_side_target_id(plan=source_plan, target=target, side=side)
        terminal_path = source_roster_ledger.root / "terminal" / f"{side_id}.json"
        try:
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProspectiveOpportunityHistoryError("captured source roster terminal is missing") from exc
        roster_reference = terminal.get("roster_raw") if isinstance(terminal, Mapping) else None
        if not isinstance(roster_reference, Mapping) or set(roster_reference) != {"path", "sha256"}:
            raise ProspectiveOpportunityHistoryError("captured source roster raw reference is invalid")
        roster_path = (source_roster_ledger.root / str(roster_reference["path"])).resolve()
        try:
            roster_path.relative_to(source_roster_ledger.root)
            roster_raw = roster_path.read_bytes()
        except (ValueError, OSError) as exc:
            raise ProspectiveOpportunityHistoryError("captured source roster raw is unavailable") from exc
        entry = {
            "schema_version": "prospective-batter-opportunity-planning-terminal-v1",
            "planning_exclusion_id": side_id,
            "terminal_state": PLANNING_MISSED,
            "observed_at_utc": _stamp(observed),
            "detail": detail.strip(),
            "source_t4_plan": source_plan.to_dict(),
            "active_roster_terminal": terminal,
            "active_roster_raw_base64": base64.b64encode(roster_raw).decode("ascii"),
            "research_only": True,
            "historical_backfill_authorized": False,
            "production_probability_consumption_authorized": False,
            "betting_authorized": False,
        }
        self._verify_planning_exclusion(entry)
        entry["entry_sha256"] = sha256_value(entry)
        return _publish_once(
            self.root / "planning_terminal" / f"{side_id}.json",
            canonical_bytes(entry) + b"\n",
        )

    def planning_exclusions(self) -> list[dict[str, Any]]:
        directory = self.root / "planning_terminal"
        paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
        rows: list[dict[str, Any]] = []
        for path in paths:
            entry = self._json(path, "history planning terminal")
            unsigned = dict(entry)
            supplied = unsigned.pop("entry_sha256", None)
            if supplied != sha256_value(unsigned) or path.stem != entry.get("planning_exclusion_id"):
                raise ProspectiveOpportunityHistoryError("history planning terminal identity or hash differs")
            self._verify_planning_exclusion(unsigned)
            rows.append(entry)
        return rows

    def append_capture(self, *, plan: Mapping[str, Any], response: RawOpportunityResponse) -> bool:
        validated = self._require_published_plan(plan)
        try:
            record = parse_opportunity_history(
                response=response,
                expected_game_pk=validated["mlb_game_pk"],
                expected_official_date=validated["official_game_date"],
                side=validated["side"],
                expected_team_id=validated["team_id"],
                capture_plan=validated,
            )
        except ProspectiveBatterOpportunityError as exc:
            raise ProspectiveOpportunityHistoryError("history response cannot be captured") from exc
        raw_ref = self._raw(response.body)
        entry = {
            "schema_version": "prospective-batter-opportunity-history-terminal-v1",
            "capture_plan_sha256": validated["capture_plan_sha256"],
            "terminal_state": CAPTURED,
            "history_record": record,
            "raw": raw_ref,
            "detail": "",
            "research_only": True,
            "historical_backfill_authorized": False,
            "production_probability_consumption_authorized": False,
            "betting_authorized": False,
        }
        entry["entry_sha256"] = sha256_value(entry)
        return _publish_once(
            self.root / "terminal" / f"{validated['capture_plan_sha256']}.json",
            canonical_bytes(entry) + b"\n",
        )

    def append_exclusion(
        self,
        *,
        plan: Mapping[str, Any],
        state: str,
        observed_at_utc: str,
        detail: str,
        raw_payload: bytes | None = None,
        transport_payload_sha256: str | None = None,
        transport_payload_size: int | None = None,
    ) -> bool:
        validated = self._require_published_plan(plan)
        if state not in EXCLUSION_STATES or not isinstance(detail, str) or not detail.strip():
            raise ProspectiveOpportunityHistoryError("invalid terminal history exclusion")
        observed = _utc(observed_at_utc, "observed_at_utc")
        deadline = _utc(validated["capture_deadline_utc"], "capture_deadline_utc")
        if state == "deadline_missing" and observed < deadline:
            raise ProspectiveOpportunityHistoryError("deadline missing cannot be published before its deadline")
        if (transport_payload_sha256 is None) != (transport_payload_size is None):
            raise ProspectiveOpportunityHistoryError("transport hash and size must be supplied together")
        if transport_payload_sha256 is not None:
            _sha(transport_payload_sha256, "transport_payload_sha256")
            if isinstance(transport_payload_size, bool) or not isinstance(transport_payload_size, int) or transport_payload_size <= 0:
                raise ProspectiveOpportunityHistoryError("transport_payload_size must be a positive integer")
        raw_ref = self._raw(raw_payload) if raw_payload is not None else None
        entry = {
            "schema_version": "prospective-batter-opportunity-history-terminal-v1",
            "capture_plan_sha256": validated["capture_plan_sha256"],
            "terminal_state": state,
            "observed_at_utc": _stamp(observed),
            "history_record": None,
            "raw": raw_ref,
            "transport_payload_sha256": transport_payload_sha256,
            "transport_payload_size": transport_payload_size,
            "detail": detail.strip(),
            "research_only": True,
            "historical_backfill_authorized": False,
            "production_probability_consumption_authorized": False,
            "betting_authorized": False,
        }
        entry["entry_sha256"] = sha256_value(entry)
        return _publish_once(
            self.root / "terminal" / f"{validated['capture_plan_sha256']}.json",
            canonical_bytes(entry) + b"\n",
        )

    def verify(self, *, require_all_terminal: bool = False) -> dict[str, int]:
        manifest = self._json(self.root / "ledger_manifest.json", "history ledger manifest")
        if (
            manifest.get("collection_epoch_date") != self.collection_epoch_date.isoformat()
            or manifest.get("contract_sha256") != self.contract_sha256
            or manifest.get("collector_code_sha256") != self.collector_code_sha256
            or manifest.get("evidence_scope_sha256") != self.evidence_scope_sha256
            or manifest.get("historical_backfill_authorized") is not False
            or manifest.get("production_probability_consumption_authorized") is not False
            or manifest.get("betting_authorized") is not False
        ):
            raise ProspectiveOpportunityHistoryError("history ledger manifest identity differs")
        plans = {plan["capture_plan_sha256"]: plan for plan in self.plans()}
        planning_exclusions = self.planning_exclusions()
        planned_side_ids = {plan["roster_side_target_id"] for plan in plans.values()}
        excluded_side_ids = {entry["planning_exclusion_id"] for entry in planning_exclusions}
        if planned_side_ids & excluded_side_ids:
            raise ProspectiveOpportunityHistoryError("a roster side is both planned and terminally missed")
        terminal_paths = sorted((self.root / "terminal").glob("*.json")) if (self.root / "terminal").is_dir() else []
        terminal_ids = {path.stem for path in terminal_paths}
        if not terminal_ids <= set(plans) or (require_all_terminal and terminal_ids != set(plans)):
            raise ProspectiveOpportunityHistoryError("history terminal coverage differs from planned coverage")
        raw_seen: set[Path] = set()
        counts = {CAPTURED: 0, "deadline_missing": 0, "source_invalid": 0}
        for path in terminal_paths:
            entry = self._json(path, "history terminal entry")
            unsigned = dict(entry)
            supplied = unsigned.pop("entry_sha256", None)
            if supplied != sha256_value(unsigned) or entry.get("capture_plan_sha256") != path.stem:
                raise ProspectiveOpportunityHistoryError("history terminal identity or hash differs")
            state = entry.get("terminal_state")
            if state not in counts:
                raise ProspectiveOpportunityHistoryError("history terminal state is unknown")
            counts[state] += 1
            raw_ref = entry.get("raw")
            if state == CAPTURED:
                if not isinstance(raw_ref, Mapping) or not isinstance(entry.get("history_record"), Mapping):
                    raise ProspectiveOpportunityHistoryError("captured history raw or record is missing")
                raw = self._read_raw(raw_ref, raw_seen)
                record = entry["history_record"]
                replayed = parse_opportunity_history(
                    response=RawOpportunityResponse(
                        raw,
                        str(record["source_received_at_utc"]),
                        str(record["source_request_url"]),
                        record["source_http_status"],
                        str(record["source_content_type"]),
                        str(record["transport_payload_sha256"]),
                        int(record["transport_payload_size"]),
                    ),
                    expected_game_pk=plans[path.stem]["mlb_game_pk"],
                    expected_official_date=plans[path.stem]["official_game_date"],
                    side=plans[path.stem]["side"],
                    expected_team_id=plans[path.stem]["team_id"],
                    capture_plan=plans[path.stem],
                )
                if replayed != record:
                    raise ProspectiveOpportunityHistoryError("captured history differs from raw replay")
            elif raw_ref is not None:
                if not isinstance(raw_ref, Mapping):
                    raise ProspectiveOpportunityHistoryError("history exclusion raw reference is malformed")
                self._read_raw(raw_ref, raw_seen)
            if state != CAPTURED:
                transport_sha = entry.get("transport_payload_sha256")
                transport_size = entry.get("transport_payload_size")
                if (transport_sha is None) != (transport_size is None):
                    raise ProspectiveOpportunityHistoryError("history exclusion transport identity is incomplete")
                if transport_sha is not None:
                    _sha(transport_sha, "history exclusion transport sha256")
                    if isinstance(transport_size, bool) or not isinstance(transport_size, int) or transport_size <= 0:
                        raise ProspectiveOpportunityHistoryError("history exclusion transport size is invalid")
        raw_dir = self.root / "raw"
        actual_raw = {path.resolve() for path in raw_dir.glob("*.json")} if raw_dir.is_dir() else set()
        if raw_seen != actual_raw:
            raise ProspectiveOpportunityHistoryError("history ledger has orphaned or missing raw receipts")
        counts.update(
            {
                "planned": len(plans),
                "terminal": len(terminal_paths),
                "missing": len(plans) - len(terminal_paths),
                PLANNING_MISSED: len(planning_exclusions),
            }
        )
        return counts

    def captured_materials(self) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
        self.verify()
        records: list[dict[str, Any]] = []
        raw_by_hash: dict[str, bytes] = {}
        terminal_dir = self.root / "terminal"
        for path in sorted(terminal_dir.glob("*.json")) if terminal_dir.is_dir() else []:
            entry = self._json(path, "history terminal entry")
            if entry.get("terminal_state") != CAPTURED:
                continue
            record = dict(entry["history_record"])
            raw_path = (self.root / entry["raw"]["path"]).resolve()
            records.append(record)
            raw_by_hash[record["source_payload_sha256"]] = raw_path.read_bytes()
        return records, raw_by_hash

    def _require_published_plan(self, value: Mapping[str, Any]) -> dict[str, Any]:
        try:
            plan = validate_history_capture_plan(value)
        except ProspectiveBatterOpportunityError as exc:
            raise ProspectiveOpportunityHistoryError("history capture plan is invalid") from exc
        path = self.root / "plans" / f"{plan['capture_plan_sha256']}.json"
        if self._read_plan(path) != plan:
            raise ProspectiveOpportunityHistoryError("history capture plan was not published exactly")
        return plan

    def _read_plan(self, path: Path) -> dict[str, Any]:
        bundle = self._json(path, "history capture plan bundle")
        if set(bundle) != {
            "schema_version",
            "plan",
            "source_t4_plan",
            "active_roster_terminal",
            "active_roster_raw_base64",
        } or bundle.get("schema_version") != "prospective-batter-opportunity-history-plan-proof-v1":
            raise ProspectiveOpportunityHistoryError("stored history plan proof schema changed")
        plan = bundle.get("plan")
        try:
            validated = validate_history_capture_plan(plan)
        except ProspectiveBatterOpportunityError as exc:
            raise ProspectiveOpportunityHistoryError("stored history capture plan is invalid") from exc
        if path.stem != validated["capture_plan_sha256"]:
            raise ProspectiveOpportunityHistoryError("stored history capture plan filename differs")
        self._verify_plan_proof(
            validated,
            source_t4_plan=bundle["source_t4_plan"],
            active_roster_terminal=bundle["active_roster_terminal"],
            active_roster_raw_base64=bundle["active_roster_raw_base64"],
        )
        return validated

    def _verified_plan_bundle(
        self,
        plan: Mapping[str, Any],
        *,
        source_roster_ledger: ProjectedLineupRosterLedger,
    ) -> dict[str, Any]:
        if not isinstance(source_roster_ledger, ProjectedLineupRosterLedger):
            raise ProspectiveOpportunityHistoryError("source roster ledger is required")
        try:
            source_roster_ledger.verify()
        except ProjectedLineupRosterLedgerError as exc:
            raise ProspectiveOpportunityHistoryError("source roster ledger failed replay") from exc
        source_plan = source_roster_ledger.plan
        target = next(
            (
                item
                for item in source_plan.targets
                if item.mlb_game_pk == plan["mlb_game_pk"]
                and item.official_game_date == plan["official_game_date"]
                and _utc(item.official_start_time_utc, "source target start")
                == _utc(plan["official_start_time_utc"], "history plan start")
            ),
            None,
        )
        if target is None:
            raise ProspectiveOpportunityHistoryError("history plan target is absent from the T-minus-4 plan")
        side_id = roster_side_target_id(plan=source_plan, target=target, side=str(plan["side"]))
        terminal_path = source_roster_ledger.root / "terminal" / f"{side_id}.json"
        try:
            terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProspectiveOpportunityHistoryError("captured source roster terminal is missing") from exc
        roster_reference = terminal.get("roster_raw") if isinstance(terminal, Mapping) else None
        if not isinstance(roster_reference, Mapping) or set(roster_reference) != {"path", "sha256"}:
            raise ProspectiveOpportunityHistoryError("captured source roster raw reference is invalid")
        roster_path = (source_roster_ledger.root / str(roster_reference["path"])).resolve()
        try:
            roster_path.relative_to(source_roster_ledger.root)
            roster_raw = roster_path.read_bytes()
        except (ValueError, OSError) as exc:
            raise ProspectiveOpportunityHistoryError("captured source roster raw is unavailable") from exc
        bundle = {
            "schema_version": "prospective-batter-opportunity-history-plan-proof-v1",
            "plan": dict(plan),
            "source_t4_plan": source_plan.to_dict(),
            "active_roster_terminal": terminal,
            "active_roster_raw_base64": base64.b64encode(roster_raw).decode("ascii"),
        }
        self._verify_plan_proof(
            plan,
            source_t4_plan=bundle["source_t4_plan"],
            active_roster_terminal=terminal,
            active_roster_raw_base64=bundle["active_roster_raw_base64"],
        )
        return bundle

    def _verify_plan_proof(
        self,
        plan: Mapping[str, Any],
        *,
        source_t4_plan: Any,
        active_roster_terminal: Any,
        active_roster_raw_base64: Any,
    ) -> None:
        try:
            source_plan = ShadowCapturePlan.from_mapping(source_t4_plan)
        except (ShadowCapturePlanError, TypeError, ValueError) as exc:
            raise ProspectiveOpportunityHistoryError("source T-minus-4 plan proof is invalid") from exc
        if source_plan.plan_sha256 != plan["source_t4_plan_sha256"] or source_plan.entry_hours != 4:
            raise ProspectiveOpportunityHistoryError("source T-minus-4 plan identity differs")
        target = next(
            (
                item
                for item in source_plan.targets
                if item.mlb_game_pk == plan["mlb_game_pk"]
                and item.official_game_date == plan["official_game_date"]
                and _utc(item.official_start_time_utc, "source target start")
                == _utc(plan["official_start_time_utc"], "history plan start")
            ),
            None,
        )
        if target is None:
            raise ProspectiveOpportunityHistoryError("source T-minus-4 target identity differs")
        expected_side_id = roster_side_target_id(
            plan=source_plan,
            target=target,
            side=str(plan["side"]),
        )
        if not isinstance(active_roster_terminal, Mapping):
            raise ProspectiveOpportunityHistoryError("active-roster terminal proof is missing")
        unsigned = dict(active_roster_terminal)
        entry_sha = unsigned.pop("entry_sha256", None)
        if entry_sha != sha256_value(unsigned):
            raise ProspectiveOpportunityHistoryError("active-roster terminal proof hash differs")
        if (
            active_roster_terminal.get("terminal_state") != ROSTER_CAPTURED
            or active_roster_terminal.get("side_target_id") != expected_side_id
            or active_roster_terminal.get("plan_sha256") != source_plan.plan_sha256
            or active_roster_terminal.get("target_id") != target.target_id
            or active_roster_terminal.get("mlb_game_pk") != target.mlb_game_pk
            or active_roster_terminal.get("official_game_date") != target.official_game_date
            or active_roster_terminal.get("side") != plan["side"]
            or active_roster_terminal.get("team_id") != plan["team_id"]
            or plan["roster_side_target_id"] != expected_side_id
        ):
            raise ProspectiveOpportunityHistoryError("active-roster terminal semantic identity differs")
        if _utc(plan["created_at_utc"], "created_at_utc") < _utc(
            active_roster_terminal.get("committed_utc"), "active roster committed_utc"
        ):
            raise ProspectiveOpportunityHistoryError("history plan predates its active-roster receipt")
        if not isinstance(active_roster_raw_base64, str):
            raise ProspectiveOpportunityHistoryError("active-roster raw proof is missing")
        try:
            roster_raw = base64.b64decode(active_roster_raw_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise ProspectiveOpportunityHistoryError("active-roster raw proof is not canonical base64") from exc
        roster_reference = active_roster_terminal.get("roster_raw")
        roster_record = active_roster_terminal.get("roster")
        if (
            not roster_raw
            or not isinstance(roster_reference, Mapping)
            or set(roster_reference) != {"path", "sha256"}
            or hashlib.sha256(roster_raw).hexdigest() != roster_reference.get("sha256")
            or not isinstance(roster_record, Mapping)
            or sha256_value(roster_record) != plan["active_roster_receipt_sha256"]
        ):
            raise ProspectiveOpportunityHistoryError("active-roster receipt or raw proof differs")
        try:
            replayed = parse_active_roster_receipt(
                response=RawOfficialRosterResponse(
                    roster_raw,
                    str(active_roster_terminal.get("committed_utc")),
                ),
                requested_date=target.official_game_date,
                team_id=int(plan["team_id"]),
                target_horizon_utc=target.entry_target_at_utc,
            )
        except (OfficialRosterReceiptError, TypeError, ValueError) as exc:
            raise ProspectiveOpportunityHistoryError("active-roster raw proof cannot be replayed") from exc
        if replayed != dict(roster_record):
            raise ProspectiveOpportunityHistoryError("active-roster receipt differs from raw replay")

    def _verify_planning_exclusion(self, value: Mapping[str, Any]) -> None:
        required = {
            "schema_version",
            "planning_exclusion_id",
            "terminal_state",
            "observed_at_utc",
            "detail",
            "source_t4_plan",
            "active_roster_terminal",
            "active_roster_raw_base64",
            "research_only",
            "historical_backfill_authorized",
            "production_probability_consumption_authorized",
            "betting_authorized",
        }
        if set(value) != required:
            raise ProspectiveOpportunityHistoryError("planning exclusion schema changed")
        if (
            value.get("schema_version") != "prospective-batter-opportunity-planning-terminal-v1"
            or value.get("terminal_state") != PLANNING_MISSED
            or value.get("research_only") is not True
            or value.get("historical_backfill_authorized") is not False
            or value.get("production_probability_consumption_authorized") is not False
            or value.get("betting_authorized") is not False
            or not isinstance(value.get("detail"), str)
            or not value["detail"].strip()
        ):
            raise ProspectiveOpportunityHistoryError("planning exclusion safety identity differs")
        try:
            source_plan = ShadowCapturePlan.from_mapping(value["source_t4_plan"])
        except (ShadowCapturePlanError, TypeError, ValueError) as exc:
            raise ProspectiveOpportunityHistoryError("planning exclusion T-minus-4 plan is invalid") from exc
        terminal = value.get("active_roster_terminal")
        if not isinstance(terminal, Mapping):
            raise ProspectiveOpportunityHistoryError("planning exclusion roster terminal is missing")
        target = next((item for item in source_plan.targets if item.target_id == terminal.get("target_id")), None)
        side = terminal.get("side")
        if target is None or side not in {"away", "home"}:
            raise ProspectiveOpportunityHistoryError("planning exclusion target or side differs")
        expected_side_id = roster_side_target_id(plan=source_plan, target=target, side=str(side))
        unsigned_terminal = dict(terminal)
        terminal_sha = unsigned_terminal.pop("entry_sha256", None)
        if terminal_sha != sha256_value(unsigned_terminal):
            raise ProspectiveOpportunityHistoryError("planning exclusion roster terminal hash differs")
        if (
            terminal.get("terminal_state") != ROSTER_CAPTURED
            or terminal.get("side_target_id") != expected_side_id
            or terminal.get("plan_sha256") != source_plan.plan_sha256
            or terminal.get("mlb_game_pk") != target.mlb_game_pk
            or terminal.get("official_game_date") != target.official_game_date
            or value.get("planning_exclusion_id") != expected_side_id
        ):
            raise ProspectiveOpportunityHistoryError("planning exclusion roster semantic identity differs")
        game_date = _canonical_date(target.official_game_date, "official_game_date")
        if game_date < self.collection_epoch_date:
            raise ProspectiveOpportunityHistoryError("planning exclusion predates collection epoch")
        if _utc(value["observed_at_utc"], "observed_at_utc") < _utc(
            target.official_start_time_utc, "official_start_time_utc"
        ):
            raise ProspectiveOpportunityHistoryError("planning exclusion predates first pitch")
        roster_reference = terminal.get("roster_raw")
        roster_record = terminal.get("roster")
        try:
            roster_raw = base64.b64decode(value["active_roster_raw_base64"], validate=True)
        except (ValueError, TypeError) as exc:
            raise ProspectiveOpportunityHistoryError("planning exclusion roster raw is not canonical base64") from exc
        if (
            not roster_raw
            or not isinstance(roster_reference, Mapping)
            or set(roster_reference) != {"path", "sha256"}
            or hashlib.sha256(roster_raw).hexdigest() != roster_reference.get("sha256")
            or not isinstance(roster_record, Mapping)
        ):
            raise ProspectiveOpportunityHistoryError("planning exclusion roster proof differs")
        try:
            replayed = parse_active_roster_receipt(
                response=RawOfficialRosterResponse(
                    roster_raw,
                    str(terminal.get("committed_utc")),
                ),
                requested_date=target.official_game_date,
                team_id=int(terminal["team_id"]),
                target_horizon_utc=target.entry_target_at_utc,
            )
        except (OfficialRosterReceiptError, TypeError, ValueError) as exc:
            raise ProspectiveOpportunityHistoryError("planning exclusion roster proof cannot be replayed") from exc
        if replayed != dict(roster_record):
            raise ProspectiveOpportunityHistoryError("planning exclusion roster differs from raw replay")

    def _raw(self, payload: bytes) -> dict[str, str]:
        if not isinstance(payload, bytes) or not payload:
            raise ProspectiveOpportunityHistoryError("history raw bytes are missing")
        digest = hashlib.sha256(payload).hexdigest()
        path = self.root / "raw" / f"{digest}.json"
        _publish_once(path, payload)
        return {"path": path.relative_to(self.root).as_posix(), "sha256": digest}

    def _read_raw(self, reference: Mapping[str, Any], seen: set[Path]) -> bytes:
        if set(reference) != {"path", "sha256"}:
            raise ProspectiveOpportunityHistoryError("history raw reference is malformed")
        digest = _sha(reference["sha256"], "history raw sha256")
        path = (self.root / str(reference["path"])).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ProspectiveOpportunityHistoryError("history raw reference escapes ledger root") from exc
        if not path.is_file():
            raise ProspectiveOpportunityHistoryError("history raw reference is missing")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ProspectiveOpportunityHistoryError("history raw reference hash differs")
        seen.add(path)
        return payload

    @staticmethod
    def _json(path: Path, label: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProspectiveOpportunityHistoryError(f"{label} is unreadable") from exc
        if not isinstance(value, dict):
            raise ProspectiveOpportunityHistoryError(f"{label} must be an object")
        return value


def run_history_tick(
    *,
    ledger: ProspectiveOpportunityHistoryLedger,
    fetch_final: Callable[[Mapping[str, Any]], RawOpportunityResponse],
    now: datetime,
) -> dict[str, int]:
    """Attempt due future plans once; nonfinal/network responses remain retryable."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ProspectiveOpportunityHistoryError("history collector clock must include a timezone")
    current = now.astimezone(timezone.utc)
    terminal = ledger.terminal_plan_ids()
    counts = {CAPTURED: 0, "deadline_missing": 0, "source_invalid": 0, "pending": 0, "future": 0}
    for plan in ledger.plans():
        identifier = plan["capture_plan_sha256"]
        if identifier in terminal:
            continue
        starts = _utc(plan["official_start_time_utc"], "official_start_time_utc")
        deadline = _utc(plan["capture_deadline_utc"], "capture_deadline_utc")
        if current < starts:
            counts["future"] += 1
            continue
        if current >= deadline:
            ledger.append_exclusion(
                plan=plan,
                state="deadline_missing",
                observed_at_utc=_stamp(current),
                detail="predeclared final-receipt deadline elapsed; no backfill attempted",
            )
            counts["deadline_missing"] += 1
            continue
        try:
            response = fetch_final(plan)
        except Exception:
            counts["pending"] += 1
            continue
        try:
            sanitized = sanitize_opportunity_transport(
                response=response,
                expected_game_pk=plan["mlb_game_pk"],
            )
            ledger.append_capture(plan=plan, response=sanitized)
        except (ProspectiveOpportunityHistoryError, ProspectiveBatterOpportunityError) as exc:
            if "not coded final" in str(exc.__cause__ or exc):
                counts["pending"] += 1
                continue
            ledger.append_exclusion(
                plan=plan,
                state="source_invalid",
                observed_at_utc=response.received_at_utc,
                detail=f"fields-limited final source failed closed ({type(exc.__cause__ or exc).__name__})",
                transport_payload_sha256=hashlib.sha256(response.body).hexdigest(),
                transport_payload_size=len(response.body),
            )
            counts["source_invalid"] += 1
            continue
        counts[CAPTURED] += 1
    return counts
