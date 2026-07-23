"""Append-only, one-terminal-state-per-game-side shared PA evidence ledger."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.shared_pa_forward_collector import (
    RawPregameResponse,
    SharedPAForwardCollectorError,
    pa_counts_from_hitting_stats,
    projected_lineups_from_schedule,
    validate_schedule_input_surface,
    validate_stats_input_surface,
)
from src.evaluation.shared_pa_forward_evidence import (
    SharedPAForwardEvidenceError,
    canonical_bytes,
    sha256_value,
    validate_player_snapshot,
)


class SharedPAForwardLedgerError(ValueError):
    """The immutable side ledger is incomplete, conflicting, or altered."""


COMPLETE = "captured_complete"
EXCLUSIONS = {
    "lineup_unavailable", "lineup_malformed", "source_error",
    "missed_before_horizon", "game_identity_ambiguous", "raw_schema_changed",
}


def side_target_id(*, plan: ShadowCapturePlan, target: CaptureTarget, side: str) -> str:
    if side not in {"home", "away"}:
        raise SharedPAForwardLedgerError("side must be home or away")
    if target.target_id not in {item.target_id for item in plan.targets}:
        raise SharedPAForwardLedgerError("target is not in the immutable plan")
    return sha256_value({
        "schema_version": "shared-pa-forward-side-target-v1",
        "plan_sha256": plan.plan_sha256,
        "target_id": target.target_id,
        "side": side,
    })


def _atomic_publish_once(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise SharedPAForwardLedgerError(f"immutable artifact already differs: {path.name}")
        return False
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        # NamedTemporaryFile is 0600 regardless of the service UMask.  The
        # independent verifier is deliberately a different, non-writing user
        # in the baseball-shadow group, so immutable evidence must be group
        # readable before the hard link publishes it.  0640 grants no group
        # write and leaves other users with no access.
        os.fchmod(handle.fileno(), 0o640)
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise SharedPAForwardLedgerError(f"concurrent immutable artifact differs: {path.name}")
            return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _json(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SharedPAForwardLedgerError(f"cannot read {label}") from exc
    if not isinstance(value, Mapping):
        raise SharedPAForwardLedgerError(f"{label} must be an object")
    return value


class SharedPAForwardLedger:
    def __init__(
        self,
        root: str | Path,
        *,
        plan: ShadowCapturePlan,
        contract_sha256: str,
        runtime_manifest_sha256: str,
        collector_code_sha256: str,
    ) -> None:
        if plan.official_game_date.startswith("2026-05-"):
            raise SharedPAForwardLedgerError("May 2026 is sealed before ledger creation")
        if plan.entry_hours != 4:
            raise SharedPAForwardLedgerError("shared PA ledger requires the locked T-minus-4 plan")
        self.root = Path(root).resolve()
        self.plan = plan
        self.contract_sha256 = str(contract_sha256)
        self.runtime_manifest_sha256 = str(runtime_manifest_sha256)
        self.collector_code_sha256 = str(collector_code_sha256)
        for label, value in (
            ("contract_sha256", self.contract_sha256),
            ("runtime_manifest_sha256", self.runtime_manifest_sha256),
            ("collector_code_sha256", self.collector_code_sha256),
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise SharedPAForwardLedgerError(f"{label} must be a lowercase SHA-256 digest")
        manifest = {
            "schema_version": "shared-pa-forward-ledger-v1",
            "plan_sha256": plan.plan_sha256,
            "official_game_date": plan.official_game_date,
            "entry_hours": plan.entry_hours,
            "contract_sha256": self.contract_sha256,
            "runtime_manifest_sha256": self.runtime_manifest_sha256,
            "collector_code_sha256": self.collector_code_sha256,
            "research_only": True,
            "betting_authorized": False,
            "production_changed": False,
            "expected_side_target_ids": sorted(
                side_target_id(plan=plan, target=target, side=side)
                for target in plan.targets for side in ("away", "home")
            ),
        }
        _atomic_publish_once(self.root / "ledger_manifest.json", canonical_bytes(manifest) + b"\n")

    def terminal_side_ids(self) -> set[str]:
        directory = self.root / "terminal"
        if not directory.exists():
            return set()
        return {path.stem for path in directory.glob("*.json")}

    def _publish_raw(self, payload: bytes) -> dict[str, str]:
        if not isinstance(payload, bytes) or not payload:
            raise SharedPAForwardLedgerError("raw payload must be non-empty bytes")
        digest = hashlib.sha256(payload).hexdigest()
        path = self.root / "raw" / f"{digest}.json"
        _atomic_publish_once(path, payload)
        return {"path": path.relative_to(self.root).as_posix(), "sha256": digest}

    def _validate_complete_source_chain(
        self,
        *,
        target: CaptureTarget,
        side: str,
        players: list[Mapping[str, Any]],
        lineup_raw: bytes,
        stats_raw_by_sha256: Mapping[str, bytes],
    ) -> None:
        ordered = sorted(players, key=lambda value: int(value["source_lineup_slot"]))
        lineup_receipts = {str(player["lineup_receipt_utc"]) for player in ordered}
        if len(lineup_receipts) != 1:
            raise SharedPAForwardLedgerError("complete side has inconsistent lineup receipt times")
        try:
            lineup_response = RawPregameResponse(
                body=lineup_raw, received_at_utc=next(iter(lineup_receipts))
            )
            validate_schedule_input_surface(lineup_response)
            parsed = projected_lineups_from_schedule(
                response=lineup_response, plan=self.plan, target=target
            )
            if parsed.get(side) != [int(player["player_id"]) for player in ordered]:
                raise SharedPAForwardLedgerError("retained lineup order differs from player snapshots")
            if any(
                int(player["home_team_id"]) != int(parsed["home_team_id"])
                or int(player["away_team_id"]) != int(parsed["away_team_id"])
                for player in ordered
            ):
                raise SharedPAForwardLedgerError("retained team identity differs from player snapshots")
            player_by_stats_hash = {
                str(player["raw_stats_payload_sha256"]): player for player in ordered
            }
            if set(player_by_stats_hash) != set(stats_raw_by_sha256):
                raise SharedPAForwardLedgerError("retained stats payload set differs from player snapshots")
            for digest, payload in stats_raw_by_sha256.items():
                player = player_by_stats_hash[digest]
                stats_response = RawPregameResponse(
                    body=payload, received_at_utc=str(player["stats_receipt_utc"])
                )
                validate_stats_input_surface(stats_response)
                counts = pa_counts_from_hitting_stats(
                    response=stats_response, player_id=int(player["player_id"])
                )
                if counts != player["stats_counts"]:
                    raise SharedPAForwardLedgerError("retained stats counts differ from consumed features")
        except SharedPAForwardCollectorError as exc:
            raise SharedPAForwardLedgerError("retained complete raw payload is outside the safe input surface") from exc

    def append_complete(
        self,
        *,
        target: CaptureTarget,
        side: str,
        players: list[Mapping[str, Any]],
        lineup_raw: bytes,
        stats_raw_by_sha256: Mapping[str, bytes],
        committed_utc: str,
    ) -> bool:
        identifier = side_target_id(plan=self.plan, target=target, side=side)
        if len(players) != 9:
            raise SharedPAForwardLedgerError("complete side must contain exactly nine player snapshots")
        hard_keys: set[str] = set()
        stats_hashes: set[str] = set()
        lineup_hash = hashlib.sha256(lineup_raw).hexdigest()
        normalized_players: list[dict[str, Any]] = []
        for raw in players:
            record = dict(raw)
            try:
                validate_player_snapshot(record)
            except SharedPAForwardEvidenceError as exc:
                raise SharedPAForwardLedgerError(str(exc)) from exc
            if record["target_id"] != target.target_id or record["plan_sha256"] != self.plan.plan_sha256 or record["side"] != side:
                raise SharedPAForwardLedgerError("player snapshot belongs to another target or side")
            if (
                record["runtime_manifest_sha256"] != self.runtime_manifest_sha256
                or record["collector_code_sha256"] != self.collector_code_sha256
            ):
                raise SharedPAForwardLedgerError("player snapshot runtime or code hash differs from the ledger")
            if record["raw_lineup_payload_sha256"] != lineup_hash:
                raise SharedPAForwardLedgerError("player snapshot lineup hash differs from retained raw payload")
            hard_key = str(record["hard_player_key"])
            if hard_key in hard_keys:
                raise SharedPAForwardLedgerError("complete side contains a duplicate player target")
            hard_keys.add(hard_key)
            stats_hashes.add(str(record["raw_stats_payload_sha256"]))
            normalized_players.append(record)
        if set(stats_raw_by_sha256) != stats_hashes:
            raise SharedPAForwardLedgerError("stats raw payload set does not match player snapshots")
        self._validate_complete_source_chain(
            target=target,
            side=side,
            players=normalized_players,
            lineup_raw=lineup_raw,
            stats_raw_by_sha256=stats_raw_by_sha256,
        )
        lineup_reference = self._publish_raw(lineup_raw)
        stats_references = []
        for expected_sha in sorted(stats_raw_by_sha256):
            payload = stats_raw_by_sha256[expected_sha]
            if hashlib.sha256(payload).hexdigest() != expected_sha:
                raise SharedPAForwardLedgerError("stats raw payload hash differs from its key")
            stats_references.append(self._publish_raw(payload))
        entry = {
            "schema_version": "shared-pa-forward-side-terminal-v1",
            "side_target_id": identifier,
            "plan_sha256": self.plan.plan_sha256,
            "target_id": target.target_id,
            "mlb_game_pk": target.mlb_game_pk,
            "official_game_date": target.official_game_date,
            "side": side,
            "terminal_state": COMPLETE,
            "committed_utc": committed_utc,
            "lineup_raw": lineup_reference,
            "stats_raw": stats_references,
            "players": sorted(normalized_players, key=lambda value: int(value["source_lineup_slot"])),
            "detail": "",
            "research_only": True,
            "betting_authorized": False,
        }
        entry["entry_sha256"] = sha256_value(entry)
        return _atomic_publish_once(
            self.root / "terminal" / f"{identifier}.json", canonical_bytes(entry) + b"\n"
        )

    def append_exclusion(
        self,
        *,
        target: CaptureTarget,
        side: str,
        state: str,
        observed_at_utc: str,
        detail: str,
        raw_payload: bytes | None = None,
        additional_raw_payloads: list[bytes] | None = None,
    ) -> bool:
        if state not in EXCLUSIONS:
            raise SharedPAForwardLedgerError("unknown shared PA exclusion state")
        identifier = side_target_id(plan=self.plan, target=target, side=side)
        if not str(detail).strip():
            raise SharedPAForwardLedgerError("exclusion requires a non-empty detail")
        try:
            if raw_payload is not None:
                validate_schedule_input_surface(RawPregameResponse(
                    body=raw_payload, received_at_utc=observed_at_utc
                ))
            for payload in additional_raw_payloads or []:
                validate_stats_input_surface(RawPregameResponse(
                    body=payload, received_at_utc=observed_at_utc
                ))
        except SharedPAForwardCollectorError as exc:
            raise SharedPAForwardLedgerError("excluded raw payload is outside the safe input surface") from exc
        raw_reference = self._publish_raw(raw_payload) if raw_payload is not None else None
        additional_references = [
            self._publish_raw(payload) for payload in (additional_raw_payloads or [])
        ]
        entry = {
            "schema_version": "shared-pa-forward-side-terminal-v1",
            "side_target_id": identifier,
            "plan_sha256": self.plan.plan_sha256,
            "target_id": target.target_id,
            "mlb_game_pk": target.mlb_game_pk,
            "official_game_date": target.official_game_date,
            "side": side,
            "terminal_state": state,
            "committed_utc": observed_at_utc,
            "lineup_raw": raw_reference,
            "stats_raw": additional_references,
            "players": [],
            "detail": str(detail).strip(),
            "research_only": True,
            "betting_authorized": False,
        }
        entry["entry_sha256"] = sha256_value(entry)
        return _atomic_publish_once(
            self.root / "terminal" / f"{identifier}.json", canonical_bytes(entry) + b"\n"
        )

    def verify(self, *, require_complete_coverage: bool = False) -> dict[str, int]:
        manifest = _json(self.root / "ledger_manifest.json", "ledger manifest")
        expected_manifest = {
            "plan_sha256": self.plan.plan_sha256,
            "contract_sha256": self.contract_sha256,
            "runtime_manifest_sha256": self.runtime_manifest_sha256,
            "collector_code_sha256": self.collector_code_sha256,
            "research_only": True,
            "betting_authorized": False,
            "production_changed": False,
        }
        for key, expected in expected_manifest.items():
            if manifest.get(key) != expected:
                raise SharedPAForwardLedgerError(f"ledger manifest {key} differs")
        expected = set(manifest.get("expected_side_target_ids", []))
        target_by_id = {target.target_id: target for target in self.plan.targets}
        canonical_expected = {
            side_target_id(plan=self.plan, target=target, side=side)
            for target in self.plan.targets for side in ("away", "home")
        }
        if expected != canonical_expected:
            raise SharedPAForwardLedgerError("ledger expected side target population differs")
        terminal_dir = self.root / "terminal"
        paths = sorted(terminal_dir.glob("*.json")) if terminal_dir.exists() else []
        if any(path.stem not in expected for path in paths):
            raise SharedPAForwardLedgerError("ledger contains an unplanned side target")
        if require_complete_coverage and {path.stem for path in paths} != expected:
            raise SharedPAForwardLedgerError("ledger is missing terminal side records")
        referenced_raw: set[Path] = set()
        counts = {COMPLETE: 0, **{state: 0 for state in EXCLUSIONS}}
        for path in paths:
            entry = _json(path, f"terminal entry {path.name}")
            unsigned = dict(entry)
            digest = unsigned.pop("entry_sha256", None)
            if digest != sha256_value(unsigned) or digest is None:
                raise SharedPAForwardLedgerError("terminal entry hash differs")
            if entry.get("side_target_id") != path.stem:
                raise SharedPAForwardLedgerError("terminal filename differs from target identity")
            target_id = entry.get("target_id")
            side = entry.get("side")
            target = target_by_id.get(str(target_id))
            if target is None or side not in {"home", "away"}:
                raise SharedPAForwardLedgerError("terminal entry target or side is unknown")
            if (
                side_target_id(plan=self.plan, target=target, side=str(side)) != path.stem
                or entry.get("plan_sha256") != self.plan.plan_sha256
                or entry.get("mlb_game_pk") != target.mlb_game_pk
                or entry.get("official_game_date") != target.official_game_date
            ):
                raise SharedPAForwardLedgerError("terminal entry identity differs from the immutable plan")
            state = entry.get("terminal_state")
            if state not in counts:
                raise SharedPAForwardLedgerError("terminal entry state is unknown")
            counts[state] += 1
            players = entry.get("players")
            if not isinstance(players, list):
                raise SharedPAForwardLedgerError("terminal players must be a list")
            if state == COMPLETE:
                if len(players) != 9:
                    raise SharedPAForwardLedgerError("complete terminal entry does not contain nine players")
                for player in players:
                    if not isinstance(player, Mapping):
                        raise SharedPAForwardLedgerError("terminal player snapshot is malformed")
                    try:
                        validate_player_snapshot(player)
                    except SharedPAForwardEvidenceError as exc:
                        raise SharedPAForwardLedgerError(str(exc)) from exc
                    if (
                        player.get("target_id") != target.target_id
                        or player.get("plan_sha256") != self.plan.plan_sha256
                        or player.get("side") != side
                        or player.get("runtime_manifest_sha256") != self.runtime_manifest_sha256
                        or player.get("collector_code_sha256") != self.collector_code_sha256
                    ):
                        raise SharedPAForwardLedgerError("player snapshot differs from terminal side identity")
            elif players:
                raise SharedPAForwardLedgerError("excluded terminal entry contains player predictions")
            references = [entry.get("lineup_raw"), *(entry.get("stats_raw") or [])]
            for reference in references:
                if reference is None:
                    continue
                if not isinstance(reference, Mapping) or set(reference) != {"path", "sha256"}:
                    raise SharedPAForwardLedgerError("raw reference is malformed")
                raw_path = (self.root / str(reference["path"])).resolve()
                try:
                    raw_path.relative_to(self.root)
                except ValueError as exc:
                    raise SharedPAForwardLedgerError("raw reference escapes the ledger root") from exc
                if not raw_path.is_file() or hashlib.sha256(raw_path.read_bytes()).hexdigest() != reference["sha256"]:
                    raise SharedPAForwardLedgerError("raw reference hash differs")
                if raw_path.name != f"{reference['sha256']}.json":
                    raise SharedPAForwardLedgerError("raw reference filename differs from its content hash")
                referenced_raw.add(raw_path)
            if state == COMPLETE:
                lineup_reference = entry.get("lineup_raw")
                stats_references = entry.get("stats_raw")
                if not isinstance(lineup_reference, Mapping) or not isinstance(stats_references, list):
                    raise SharedPAForwardLedgerError("complete terminal raw references are incomplete")
                expected_lineup_hashes = {str(player["raw_lineup_payload_sha256"]) for player in players}
                expected_stats_hashes = {str(player["raw_stats_payload_sha256"]) for player in players}
                actual_stats_hashes = {
                    str(reference.get("sha256")) for reference in stats_references
                    if isinstance(reference, Mapping)
                }
                if expected_lineup_hashes != {str(lineup_reference.get("sha256"))} or expected_stats_hashes != actual_stats_hashes:
                    raise SharedPAForwardLedgerError("complete terminal raw lineage differs from player snapshots")
                lineup_path = (self.root / str(lineup_reference["path"])).resolve()
                stats_payloads = {
                    str(reference["sha256"]): (self.root / str(reference["path"])).resolve().read_bytes()
                    for reference in stats_references
                }
                self._validate_complete_source_chain(
                    target=target,
                    side=str(side),
                    players=[dict(player) for player in players],
                    lineup_raw=lineup_path.read_bytes(),
                    stats_raw_by_sha256=stats_payloads,
                )
            elif entry.get("stats_raw") and state not in {"source_error", "lineup_malformed", "raw_schema_changed"}:
                raise SharedPAForwardLedgerError("excluded terminal state cannot contain partial source payloads")
        raw_dir = self.root / "raw"
        actual_raw = {path.resolve() for path in raw_dir.glob("*.json")} if raw_dir.exists() else set()
        if actual_raw != referenced_raw:
            raise SharedPAForwardLedgerError("ledger has orphaned or missing raw payloads")
        counts["expected"] = len(expected)
        counts["terminal"] = len(paths)
        counts["missing"] = len(expected) - len(paths)
        return counts
