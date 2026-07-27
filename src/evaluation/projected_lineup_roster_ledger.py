"""Immutable T-minus-4 official active-roster evidence ledger.

This is intentionally independent from shared-PA probability records.  A roster
receipt establishes only the eligible-player universe for a future lineup model.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.projected_lineup_contract import canonical_bytes, sha256_value
from src.evaluation.projected_lineup_official_roster import RawOfficialRosterResponse, OfficialRosterReceiptError, parse_active_roster_receipt
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.shared_pa_forward_collector import (
    RawPregameResponse,
    SharedPAForwardCollectorError,
    projected_lineups_from_schedule,
)


class ProjectedLineupRosterLedgerError(ValueError):
    """The immutable roster evidence chain is incomplete or altered."""


CAPTURED = "captured_active_roster"
EXCLUSIONS = {"source_error", "roster_malformed", "missed_before_horizon", "game_identity_ambiguous"}


def roster_side_target_id(*, plan: ShadowCapturePlan, target: CaptureTarget, side: str) -> str:
    if side not in {"home", "away"} or target.target_id not in {item.target_id for item in plan.targets}:
        raise ProjectedLineupRosterLedgerError("invalid planned roster side target")
    return sha256_value({"schema_version": "projected-lineup-roster-side-target-v1", "plan_sha256": plan.plan_sha256, "target_id": target.target_id, "side": side})


def _publish_once(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ProjectedLineupRosterLedgerError(f"immutable artifact already differs: {path.name}")
        return False
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fchmod(handle.fileno(), 0o640)
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ProjectedLineupRosterLedgerError(f"concurrent immutable artifact differs: {path.name}")
            return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


class ProjectedLineupRosterLedger:
    def __init__(self, root: str | Path, *, plan: ShadowCapturePlan, contract_sha256: str, collector_code_sha256: str) -> None:
        if plan.official_game_date.startswith("2026-05-") or plan.entry_hours != 4:
            raise ProjectedLineupRosterLedgerError("roster ledger requires a non-May T-minus-4 plan")
        for label, digest in (("contract_sha256", contract_sha256), ("collector_code_sha256", collector_code_sha256)):
            if not isinstance(digest, str) or len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
                raise ProjectedLineupRosterLedgerError(f"{label} must be a lowercase SHA-256 digest")
        self.root, self.plan = Path(root).resolve(), plan
        self.contract_sha256, self.collector_code_sha256 = contract_sha256, collector_code_sha256
        manifest = {
            "schema_version": "projected-lineup-roster-ledger-v1", "plan_sha256": plan.plan_sha256,
            "official_game_date": plan.official_game_date, "entry_hours": 4,
            "contract_sha256": contract_sha256, "collector_code_sha256": collector_code_sha256,
            "expected_side_target_ids": sorted(roster_side_target_id(plan=plan, target=target, side=side) for target in plan.targets for side in ("away", "home")),
            "research_only": True, "betting_authorized": False, "production_changed": False,
        }
        _publish_once(self.root / "ledger_manifest.json", canonical_bytes(manifest) + b"\n")

    def terminal_side_ids(self) -> set[str]:
        directory = self.root / "terminal"
        return {path.stem for path in directory.glob("*.json")} if directory.is_dir() else set()

    def _raw(self, payload: bytes) -> dict[str, str]:
        digest = hashlib.sha256(payload).hexdigest()
        path = self.root / "raw" / f"{digest}.json"
        _publish_once(path, payload)
        return {"path": path.relative_to(self.root).as_posix(), "sha256": digest}

    def append_capture(self, *, target: CaptureTarget, side: str, team_id: int, roster_record: Mapping[str, Any], schedule_raw: bytes, schedule_received_at_utc: str, roster_raw: bytes, committed_utc: str) -> bool:
        identifier = roster_side_target_id(plan=self.plan, target=target, side=side)
        try:
            schedule_response = RawPregameResponse(schedule_raw, schedule_received_at_utc)
            schedule_game = projected_lineups_from_schedule(
                response=schedule_response,
                plan=self.plan,
                target=target,
            )
        except SharedPAForwardCollectorError as exc:
            raise ProjectedLineupRosterLedgerError("schedule raw cannot prove the captured game identity") from exc
        if schedule_game.get(f"{side}_team_id") != team_id:
            raise ProjectedLineupRosterLedgerError("schedule raw team identity differs from captured roster")
        try:
            replayed_roster = parse_active_roster_receipt(
                response=RawOfficialRosterResponse(roster_raw, committed_utc),
                requested_date=target.official_game_date,
                team_id=team_id,
                target_horizon_utc=target.entry_target_at_utc,
            )
        except OfficialRosterReceiptError as exc:
            raise ProjectedLineupRosterLedgerError("roster raw cannot prove the captured receipt") from exc
        if dict(roster_record) != replayed_roster:
            raise ProjectedLineupRosterLedgerError("roster record differs from raw or commit time replay")
        players = roster_record.get("players")
        if not isinstance(players, list) or len(players) < 9 or len({item.get("player_id") for item in players if isinstance(item, Mapping)}) != len(players):
            raise ProjectedLineupRosterLedgerError("roster record has invalid player identities")
        entry = {
            "schema_version": "projected-lineup-roster-side-terminal-v1", "side_target_id": identifier,
            "plan_sha256": self.plan.plan_sha256, "target_id": target.target_id, "mlb_game_pk": target.mlb_game_pk,
            "official_game_date": target.official_game_date, "side": side, "team_id": team_id,
            "terminal_state": CAPTURED, "committed_utc": committed_utc,
            "schedule_received_at_utc": schedule_response.received_at_utc,
            "schedule_raw": self._raw(schedule_raw), "roster_raw": self._raw(roster_raw),
            "roster": dict(roster_record), "detail": "", "research_only": True, "betting_authorized": False,
        }
        entry["entry_sha256"] = sha256_value(entry)
        return _publish_once(self.root / "terminal" / f"{identifier}.json", canonical_bytes(entry) + b"\n")

    def append_exclusion(self, *, target: CaptureTarget, side: str, state: str, observed_at_utc: str, detail: str, schedule_raw: bytes | None = None, roster_raw: bytes | None = None) -> bool:
        if state not in EXCLUSIONS or not str(detail).strip():
            raise ProjectedLineupRosterLedgerError("invalid terminal roster exclusion")
        identifier = roster_side_target_id(plan=self.plan, target=target, side=side)
        entry = {
            "schema_version": "projected-lineup-roster-side-terminal-v1", "side_target_id": identifier,
            "plan_sha256": self.plan.plan_sha256, "target_id": target.target_id, "mlb_game_pk": target.mlb_game_pk,
            "official_game_date": target.official_game_date, "side": side, "team_id": None,
            "terminal_state": state, "committed_utc": observed_at_utc,
            "schedule_raw": self._raw(schedule_raw) if schedule_raw else None,
            "roster_raw": self._raw(roster_raw) if roster_raw else None,
            "roster": None, "detail": str(detail).strip(), "research_only": True, "betting_authorized": False,
        }
        entry["entry_sha256"] = sha256_value(entry)
        return _publish_once(self.root / "terminal" / f"{identifier}.json", canonical_bytes(entry) + b"\n")

    def verify(self, *, require_complete_coverage: bool = False) -> dict[str, int]:
        manifest_path = self.root / "ledger_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectedLineupRosterLedgerError("ledger manifest is unreadable") from exc
        expected = {roster_side_target_id(plan=self.plan, target=target, side=side) for target in self.plan.targets for side in ("away", "home")}
        if manifest.get("plan_sha256") != self.plan.plan_sha256 or set(manifest.get("expected_side_target_ids", [])) != expected or manifest.get("contract_sha256") != self.contract_sha256 or manifest.get("collector_code_sha256") != self.collector_code_sha256 or manifest.get("research_only") is not True or manifest.get("betting_authorized") is not False:
            raise ProjectedLineupRosterLedgerError("ledger manifest differs from the immutable research plan")
        paths = sorted((self.root / "terminal").glob("*.json")) if (self.root / "terminal").is_dir() else []
        if any(path.stem not in expected for path in paths) or (require_complete_coverage and {path.stem for path in paths} != expected):
            raise ProjectedLineupRosterLedgerError("ledger terminal coverage differs from the immutable plan")
        target_by_id = {target.target_id: target for target in self.plan.targets}
        raw_seen: set[Path] = set(); counts = {CAPTURED: 0, **{state: 0 for state in EXCLUSIONS}}
        for path in paths:
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ProjectedLineupRosterLedgerError("terminal entry is unreadable") from exc
            unsigned = dict(entry); digest = unsigned.pop("entry_sha256", None)
            if digest != sha256_value(unsigned) or entry.get("side_target_id") != path.stem or entry.get("terminal_state") not in counts:
                raise ProjectedLineupRosterLedgerError("terminal entry identity or hash differs")
            target = target_by_id.get(str(entry.get("target_id")))
            if target is None or entry.get("plan_sha256") != self.plan.plan_sha256 or entry.get("mlb_game_pk") != target.mlb_game_pk or entry.get("official_game_date") != target.official_game_date:
                raise ProjectedLineupRosterLedgerError("terminal entry differs from the immutable plan")
            counts[entry["terminal_state"]] += 1
            for reference in (entry.get("schedule_raw"), entry.get("roster_raw")):
                if reference is None:
                    continue
                if not isinstance(reference, Mapping) or set(reference) != {"path", "sha256"}:
                    raise ProjectedLineupRosterLedgerError("raw reference is malformed")
                raw_path = (self.root / str(reference["path"])).resolve()
                if not raw_path.is_file() or hashlib.sha256(raw_path.read_bytes()).hexdigest() != reference["sha256"]:
                    raise ProjectedLineupRosterLedgerError("raw reference hash differs")
                raw_seen.add(raw_path)
            if entry.get("terminal_state") == CAPTURED:
                roster = entry.get("roster"); roster_ref = entry.get("roster_raw")
                schedule_ref = entry.get("schedule_raw")
                schedule_received = entry.get("schedule_received_at_utc")
                if not isinstance(roster, Mapping) or not isinstance(roster_ref, Mapping) or not isinstance(schedule_ref, Mapping) or not isinstance(schedule_received, str) or not isinstance(entry.get("team_id"), int):
                    raise ProjectedLineupRosterLedgerError("captured roster terminal is incomplete")
                roster_path = (self.root / str(roster_ref["path"])).resolve()
                try:
                    schedule_path = (self.root / str(schedule_ref["path"])).resolve()
                    schedule_game = projected_lineups_from_schedule(
                        response=RawPregameResponse(schedule_path.read_bytes(), schedule_received),
                        plan=self.plan,
                        target=target,
                    )
                    if schedule_game.get(f"{entry['side']}_team_id") != entry["team_id"]:
                        raise ProjectedLineupRosterLedgerError(
                            "retained schedule raw team identity differs from terminal"
                        )
                    reproduced = parse_active_roster_receipt(
                        response=RawOfficialRosterResponse(roster_path.read_bytes(), str(entry.get("committed_utc"))),
                        requested_date=target.official_game_date,
                        team_id=int(entry["team_id"]),
                        target_horizon_utc=target.entry_target_at_utc,
                    )
                except (OfficialRosterReceiptError, SharedPAForwardCollectorError, OSError) as exc:
                    raise ProjectedLineupRosterLedgerError("retained schedule or roster raw is outside the safe input surface") from exc
                if dict(roster) != reproduced:
                    raise ProjectedLineupRosterLedgerError("retained roster raw differs from consumed identity record")
        raw_dir = self.root / "raw"; actual = {path.resolve() for path in raw_dir.glob("*.json")} if raw_dir.is_dir() else set()
        if raw_seen != actual:
            raise ProjectedLineupRosterLedgerError("ledger has orphaned or missing raw receipts")
        counts.update({"expected": len(expected), "terminal": len(paths), "missing": len(expected) - len(paths)})
        return counts
