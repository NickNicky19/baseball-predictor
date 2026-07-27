"""Immutable ledger for prospective batter opportunity input snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.evaluation.projected_lineup_contract import canonical_bytes, sha256_value
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.prospective_batter_opportunity import (
    ProspectiveBatterOpportunityError,
    build_pregame_opportunity_snapshot,
    validate_snapshot,
)


class ProspectiveBatterOpportunityLedgerError(ValueError):
    """The prospective evidence ledger is incomplete, inconsistent, or altered."""


CAPTURED_STATES = {"captured_complete", "captured_with_terminal_history_missingness"}
EXCLUSION_STATES = {
    "source_error",
    "roster_unavailable",
    "history_source_error",
    "missed_before_horizon",
    "identity_ambiguous",
}


def opportunity_side_target_id(*, plan: ShadowCapturePlan, target: CaptureTarget, side: str) -> str:
    if side not in {"home", "away"} or target.target_id not in {item.target_id for item in plan.targets}:
        raise ProspectiveBatterOpportunityLedgerError("invalid planned opportunity side target")
    return sha256_value(
        {
            "schema_version": "prospective-batter-opportunity-side-target-v1",
            "plan_sha256": plan.plan_sha256,
            "target_id": target.target_id,
            "side": side,
        }
    )


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ProspectiveBatterOpportunityLedgerError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProspectiveBatterOpportunityLedgerError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProspectiveBatterOpportunityLedgerError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveBatterOpportunityLedgerError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _publish_once(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ProspectiveBatterOpportunityLedgerError(f"immutable artifact already differs: {path.name}")
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
                raise ProspectiveBatterOpportunityLedgerError(f"concurrent immutable artifact differs: {path.name}")
            return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


class ProspectiveBatterOpportunityLedger:
    """Publish-once, replayable T-4 input evidence for each planned game side."""

    def __init__(
        self,
        root: str | Path,
        *,
        plan: ShadowCapturePlan,
        contract_sha256: str,
        collector_code_sha256: str,
    ) -> None:
        if plan.entry_hours != 4 or plan.official_game_date.startswith("2026-05-"):
            raise ProspectiveBatterOpportunityLedgerError("opportunity ledger requires a non-May T-4 plan")
        self.root = Path(root).resolve()
        self.plan = plan
        self.contract_sha256 = _digest(contract_sha256, "contract_sha256")
        self.collector_code_sha256 = _digest(collector_code_sha256, "collector_code_sha256")
        manifest = {
            "schema_version": "prospective-batter-opportunity-ledger-v1",
            "plan_sha256": plan.plan_sha256,
            "official_game_date": plan.official_game_date,
            "entry_hours": 4,
            "contract_sha256": self.contract_sha256,
            "collector_code_sha256": self.collector_code_sha256,
            "expected_side_target_ids": sorted(
                opportunity_side_target_id(plan=plan, target=target, side=side)
                for target in plan.targets
                for side in ("away", "home")
            ),
            "research_only": True,
            "betting_authorized": False,
            "production_probability_consumption_authorized": False,
        }
        _publish_once(self.root / "ledger_manifest.json", canonical_bytes(manifest) + b"\n")

    def terminal_side_ids(self) -> set[str]:
        directory = self.root / "terminal"
        return {path.stem for path in directory.glob("*.json")} if directory.is_dir() else set()

    def _raw(self, payload: bytes) -> dict[str, str]:
        if not isinstance(payload, bytes) or not payload:
            raise ProspectiveBatterOpportunityLedgerError("raw evidence bytes are missing")
        digest = hashlib.sha256(payload).hexdigest()
        path = self.root / "raw" / f"{digest}.json"
        _publish_once(path, payload)
        return {"path": path.relative_to(self.root).as_posix(), "sha256": digest}

    def append_snapshot(
        self,
        *,
        target: CaptureTarget,
        side: str,
        team_id: int,
        snapshot: Mapping[str, Any],
        active_roster_receipt: Mapping[str, Any],
        active_roster_raw: bytes,
        history_records: Sequence[Mapping[str, Any]],
        history_raw_by_sha256: Mapping[str, bytes],
        schedule_raw_by_sha256: Mapping[str, bytes],
    ) -> bool:
        try:
            validate_snapshot(snapshot)
        except ProspectiveBatterOpportunityError as exc:
            raise ProspectiveBatterOpportunityLedgerError("opportunity snapshot is invalid") from exc
        if (
            snapshot.get("mlb_game_pk") != target.mlb_game_pk
            or snapshot.get("official_game_date") != target.official_game_date
            or _utc(snapshot.get("target_horizon_utc"), "snapshot target_horizon_utc")
            != _utc(target.entry_target_at_utc, "target entry_target_at_utc")
            or snapshot.get("side") != side
            or snapshot.get("team_id") != team_id
        ):
            raise ProspectiveBatterOpportunityLedgerError("opportunity snapshot differs from planned identity")
        identifier = opportunity_side_target_id(plan=self.plan, target=target, side=side)
        if not isinstance(active_roster_raw, bytes) or not active_roster_raw:
            raise ProspectiveBatterOpportunityLedgerError("active roster raw bytes are missing")
        for digest, payload in history_raw_by_sha256.items():
            _digest(digest, "history raw key")
            if not isinstance(payload, bytes) or hashlib.sha256(payload).hexdigest() != digest:
                raise ProspectiveBatterOpportunityLedgerError("history raw key differs from payload hash")
        for digest, payload in schedule_raw_by_sha256.items():
            _digest(digest, "schedule raw key")
            if not isinstance(payload, bytes) or hashlib.sha256(payload).hexdigest() != digest:
                raise ProspectiveBatterOpportunityLedgerError("schedule raw key differs from payload hash")
        try:
            rebuilt = build_pregame_opportunity_snapshot(
                official_game_date=target.official_game_date,
                mlb_game_pk=target.mlb_game_pk,
                side=side,
                team_id=team_id,
                target_horizon_utc=target.entry_target_at_utc,
                assembled_at_utc=str(snapshot["assembled_at_utc"]),
                active_roster_receipt=active_roster_receipt,
                active_roster_raw=active_roster_raw,
                history_records=history_records,
                history_raw_by_sha256=history_raw_by_sha256,
                history_coverage=snapshot["history_coverage"],
                schedule_raw_by_sha256=schedule_raw_by_sha256,
            )
        except (ProspectiveBatterOpportunityError, KeyError, TypeError, ValueError) as exc:
            raise ProspectiveBatterOpportunityLedgerError(
                "opportunity snapshot cannot be replayed before publication"
            ) from exc
        if rebuilt != dict(snapshot):
            raise ProspectiveBatterOpportunityLedgerError(
                "opportunity snapshot differs from prepublication replay"
            )
        roster_ref = self._raw(active_roster_raw)
        history_refs = {digest: self._raw(payload) for digest, payload in sorted(history_raw_by_sha256.items())}
        schedule_refs = {digest: self._raw(payload) for digest, payload in sorted(schedule_raw_by_sha256.items())}
        entry = {
            "schema_version": "prospective-batter-opportunity-side-terminal-v1",
            "side_target_id": identifier,
            "plan_sha256": self.plan.plan_sha256,
            "target_id": target.target_id,
            "mlb_game_pk": target.mlb_game_pk,
            "official_game_date": target.official_game_date,
            "side": side,
            "team_id": team_id,
            "terminal_state": snapshot["terminal_state"],
            "snapshot": dict(snapshot),
            "active_roster_receipt": dict(active_roster_receipt),
            "active_roster_raw": roster_ref,
            "history_records": [dict(record) for record in history_records],
            "history_raw": history_refs,
            "schedule_raw": schedule_refs,
            "detail": "",
            "research_only": True,
            "betting_authorized": False,
            "production_probability_consumption_authorized": False,
        }
        entry["entry_sha256"] = sha256_value(entry)
        return _publish_once(self.root / "terminal" / f"{identifier}.json", canonical_bytes(entry) + b"\n")

    def append_exclusion(
        self,
        *,
        target: CaptureTarget,
        side: str,
        state: str,
        observed_at_utc: str,
        detail: str,
    ) -> bool:
        if state not in EXCLUSION_STATES or not isinstance(detail, str) or not detail.strip():
            raise ProspectiveBatterOpportunityLedgerError("invalid terminal opportunity exclusion")
        _utc(observed_at_utc, "observed_at_utc")
        identifier = opportunity_side_target_id(plan=self.plan, target=target, side=side)
        entry = {
            "schema_version": "prospective-batter-opportunity-side-terminal-v1",
            "side_target_id": identifier,
            "plan_sha256": self.plan.plan_sha256,
            "target_id": target.target_id,
            "mlb_game_pk": target.mlb_game_pk,
            "official_game_date": target.official_game_date,
            "side": side,
            "team_id": None,
            "terminal_state": state,
            "observed_at_utc": observed_at_utc,
            "snapshot": None,
            "active_roster_receipt": None,
            "active_roster_raw": None,
            "history_records": [],
            "history_raw": {},
            "schedule_raw": {},
            "detail": detail.strip(),
            "research_only": True,
            "betting_authorized": False,
            "production_probability_consumption_authorized": False,
        }
        entry["entry_sha256"] = sha256_value(entry)
        return _publish_once(self.root / "terminal" / f"{identifier}.json", canonical_bytes(entry) + b"\n")

    def verify(self, *, require_complete_coverage: bool = False) -> dict[str, int]:
        try:
            manifest = json.loads((self.root / "ledger_manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProspectiveBatterOpportunityLedgerError("opportunity ledger manifest is unreadable") from exc
        expected = {
            opportunity_side_target_id(plan=self.plan, target=target, side=side)
            for target in self.plan.targets
            for side in ("away", "home")
        }
        if (
            manifest.get("plan_sha256") != self.plan.plan_sha256
            or set(manifest.get("expected_side_target_ids", [])) != expected
            or manifest.get("contract_sha256") != self.contract_sha256
            or manifest.get("collector_code_sha256") != self.collector_code_sha256
            or manifest.get("research_only") is not True
            or manifest.get("betting_authorized") is not False
            or manifest.get("production_probability_consumption_authorized") is not False
        ):
            raise ProspectiveBatterOpportunityLedgerError("opportunity ledger manifest identity differs")
        paths = sorted((self.root / "terminal").glob("*.json")) if (self.root / "terminal").is_dir() else []
        terminal_ids = {path.stem for path in paths}
        if not terminal_ids <= expected or (require_complete_coverage and terminal_ids != expected):
            raise ProspectiveBatterOpportunityLedgerError("opportunity ledger terminal coverage differs")
        target_by_id = {target.target_id: target for target in self.plan.targets}
        counts = {state: 0 for state in CAPTURED_STATES | EXCLUSION_STATES}
        raw_seen: set[Path] = set()
        for path in paths:
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ProspectiveBatterOpportunityLedgerError("opportunity terminal entry is unreadable") from exc
            unsigned = dict(entry)
            digest = unsigned.pop("entry_sha256", None)
            state = entry.get("terminal_state")
            if digest != sha256_value(unsigned) or entry.get("side_target_id") != path.stem or state not in counts:
                raise ProspectiveBatterOpportunityLedgerError("opportunity terminal identity or hash differs")
            target = target_by_id.get(str(entry.get("target_id")))
            if (
                target is None
                or entry.get("plan_sha256") != self.plan.plan_sha256
                or entry.get("mlb_game_pk") != target.mlb_game_pk
                or entry.get("official_game_date") != target.official_game_date
                or entry.get("side_target_id")
                != opportunity_side_target_id(plan=self.plan, target=target, side=str(entry.get("side")))
            ):
                raise ProspectiveBatterOpportunityLedgerError("opportunity terminal differs from planned identity")
            counts[state] += 1
            if state not in CAPTURED_STATES:
                continue
            roster_ref = entry.get("active_roster_raw")
            history_refs = entry.get("history_raw")
            schedule_refs = entry.get("schedule_raw")
            if not isinstance(roster_ref, Mapping) or not isinstance(history_refs, Mapping) or not isinstance(schedule_refs, Mapping):
                raise ProspectiveBatterOpportunityLedgerError("captured opportunity raw references are missing")
            roster_raw = self._read_raw_reference(roster_ref, raw_seen)
            history_raw = {
                digest: self._read_raw_reference(reference, raw_seen)
                for digest, reference in history_refs.items()
            }
            schedule_raw = {
                digest: self._read_raw_reference(reference, raw_seen)
                for digest, reference in schedule_refs.items()
            }
            if any(hashlib.sha256(raw).hexdigest() != digest for digest, raw in history_raw.items()):
                raise ProspectiveBatterOpportunityLedgerError("history raw map identity differs")
            if any(hashlib.sha256(raw).hexdigest() != digest for digest, raw in schedule_raw.items()):
                raise ProspectiveBatterOpportunityLedgerError("schedule raw map identity differs")
            snapshot = entry.get("snapshot")
            if not isinstance(snapshot, Mapping) or not isinstance(entry.get("active_roster_receipt"), Mapping):
                raise ProspectiveBatterOpportunityLedgerError("captured opportunity snapshot inputs are missing")
            try:
                rebuilt = build_pregame_opportunity_snapshot(
                    official_game_date=target.official_game_date,
                    mlb_game_pk=target.mlb_game_pk,
                    side=str(entry["side"]),
                    team_id=int(entry["team_id"]),
                    target_horizon_utc=target.entry_target_at_utc,
                    assembled_at_utc=str(snapshot["assembled_at_utc"]),
                    active_roster_receipt=entry["active_roster_receipt"],
                    active_roster_raw=roster_raw,
                    history_records=entry.get("history_records", []),
                    history_raw_by_sha256=history_raw,
                    history_coverage=snapshot["history_coverage"],
                    schedule_raw_by_sha256=schedule_raw,
                )
            except (ProspectiveBatterOpportunityError, KeyError, TypeError, ValueError) as exc:
                raise ProspectiveBatterOpportunityLedgerError("captured opportunity snapshot cannot be replayed") from exc
            if rebuilt != dict(snapshot):
                raise ProspectiveBatterOpportunityLedgerError("captured opportunity snapshot differs from replay")
        raw_dir = self.root / "raw"
        actual_raw = {path.resolve() for path in raw_dir.glob("*.json")} if raw_dir.is_dir() else set()
        if actual_raw != raw_seen:
            raise ProspectiveBatterOpportunityLedgerError("opportunity ledger has orphaned or missing raw receipts")
        counts.update({"expected": len(expected), "terminal": len(paths), "missing": len(expected) - len(paths)})
        return counts

    def _read_raw_reference(self, reference: Mapping[str, Any], raw_seen: set[Path]) -> bytes:
        if set(reference) != {"path", "sha256"}:
            raise ProspectiveBatterOpportunityLedgerError("opportunity raw reference is malformed")
        expected = _digest(reference["sha256"], "raw reference sha256")
        path = (self.root / str(reference["path"])).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ProspectiveBatterOpportunityLedgerError("opportunity raw reference escapes ledger root") from exc
        if not path.is_file():
            raise ProspectiveBatterOpportunityLedgerError("opportunity raw reference is missing")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise ProspectiveBatterOpportunityLedgerError("opportunity raw reference hash differs")
        raw_seen.add(path)
        return payload
