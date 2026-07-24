"""Immutable postgame lineup labels paired to prior T-minus-4 roster receipts.

This ledger is research-only evidence. It binds an already-captured active
roster receipt to MLB's official completed-game original batting order after
the target game finishes. It does not produce a projected lineup, alter live
probabilities, or authorize betting.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.projected_lineup_contract import canonical_bytes, sha256_value
from src.evaluation.projected_lineup_roster_ledger import (
    CAPTURED as ROSTER_CAPTURED,
    EXCLUSIONS as ROSTER_EXCLUSIONS,
    ProjectedLineupRosterLedger,
    ProjectedLineupRosterLedgerError,
    roster_side_target_id,
)
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan


class ProjectedLineupLabelLedgerError(ValueError):
    """The immutable projected-lineup label chain is incomplete or altered."""


CAPTURED = "captured_final_lineup_label"
EXCLUSIONS = {
    "roster_receipt_missing",
    "official_lineup_malformed",
    "roster_label_mismatch",
}


def _publish_once(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ProjectedLineupLabelLedgerError(
                f"immutable artifact already differs: {path.name}"
            )
        return False
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        if hasattr(os, "fchmod"):
            os.fchmod(handle.fileno(), 0o640)
        else:
            os.chmod(temporary, 0o640)
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ProjectedLineupLabelLedgerError(
                    f"concurrent immutable artifact differs: {path.name}"
                )
            return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectedLineupLabelLedgerError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise ProjectedLineupLabelLedgerError(f"{label} must be a JSON object")
    return value


class ProjectedLineupLabelLedger:
    def __init__(
        self,
        root: str | Path,
        *,
        plan: ShadowCapturePlan,
        contract_sha256: str,
        collector_code_sha256: str,
        roster_manifest_sha256: str,
    ) -> None:
        if plan.official_game_date.startswith("2026-05-") or plan.entry_hours != 4:
            raise ProjectedLineupLabelLedgerError(
                "label ledger requires a non-May T-minus-4 plan"
            )
        for label, digest in (
            ("contract_sha256", contract_sha256),
            ("collector_code_sha256", collector_code_sha256),
            ("roster_manifest_sha256", roster_manifest_sha256),
        ):
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(value not in "0123456789abcdef" for value in digest)
            ):
                raise ProjectedLineupLabelLedgerError(
                    f"{label} must be a lowercase SHA-256 digest"
                )
        self.root = Path(root).resolve()
        self.plan = plan
        self.contract_sha256 = contract_sha256
        self.collector_code_sha256 = collector_code_sha256
        self.roster_manifest_sha256 = roster_manifest_sha256
        manifest = {
            "schema_version": "projected-lineup-label-ledger-v1",
            "plan_sha256": plan.plan_sha256,
            "official_game_date": plan.official_game_date,
            "entry_hours": 4,
            "contract_sha256": contract_sha256,
            "collector_code_sha256": collector_code_sha256,
            "roster_manifest_sha256": roster_manifest_sha256,
            "expected_side_target_ids": sorted(
                roster_side_target_id(plan=plan, target=target, side=side)
                for target in plan.targets
                for side in ("away", "home")
            ),
            "research_only": True,
            "betting_authorized": False,
            "production_changed": False,
        }
        _publish_once(
            self.root / "ledger_manifest.json", canonical_bytes(manifest) + b"\n"
        )

    def terminal_side_ids(self) -> set[str]:
        directory = self.root / "terminal"
        return {path.stem for path in directory.glob("*.json")} if directory.is_dir() else set()

    def _raw(self, payload: bytes) -> dict[str, str]:
        digest = hashlib.sha256(payload).hexdigest()
        path = self.root / "raw" / f"{digest}.json"
        _publish_once(path, payload)
        return {"path": path.relative_to(self.root).as_posix(), "sha256": digest}

    def append_capture(
        self,
        *,
        target: CaptureTarget,
        side: str,
        team_id: int,
        roster_terminal_ref: Mapping[str, str],
        lineup_player_ids: list[int],
        final_feed_raw: bytes,
        committed_utc: str,
    ) -> bool:
        identifier = roster_side_target_id(plan=self.plan, target=target, side=side)
        if (
            not isinstance(roster_terminal_ref, Mapping)
            or set(roster_terminal_ref) != {"path", "sha256"}
            or not isinstance(roster_terminal_ref["path"], str)
            or not isinstance(roster_terminal_ref["sha256"], str)
        ):
            raise ProjectedLineupLabelLedgerError("roster terminal reference is malformed")
        if (
            not isinstance(lineup_player_ids, list)
            or len(lineup_player_ids) != 9
            or len(set(lineup_player_ids)) != 9
            or any(not isinstance(value, int) or value <= 0 for value in lineup_player_ids)
        ):
            raise ProjectedLineupLabelLedgerError("final lineup label must contain nine unique player IDs")
        entry = {
            "schema_version": "projected-lineup-label-side-terminal-v1",
            "side_target_id": identifier,
            "plan_sha256": self.plan.plan_sha256,
            "target_id": target.target_id,
            "mlb_game_pk": target.mlb_game_pk,
            "official_game_date": target.official_game_date,
            "side": side,
            "team_id": team_id,
            "terminal_state": CAPTURED,
            "committed_utc": committed_utc,
            "roster_terminal": dict(roster_terminal_ref),
            "final_feed_raw": self._raw(final_feed_raw),
            "official_original_batting_order": lineup_player_ids,
            "detail": "",
            "research_only": True,
            "betting_authorized": False,
        }
        entry["entry_sha256"] = sha256_value(entry)
        return _publish_once(
            self.root / "terminal" / f"{identifier}.json",
            canonical_bytes(entry) + b"\n",
        )

    def append_exclusion(
        self,
        *,
        target: CaptureTarget,
        side: str,
        state: str,
        observed_at_utc: str,
        detail: str,
        roster_terminal_ref: Mapping[str, str] | None = None,
        final_feed_raw: bytes | None = None,
    ) -> bool:
        if state not in EXCLUSIONS or not str(detail).strip():
            raise ProjectedLineupLabelLedgerError("invalid terminal label exclusion")
        identifier = roster_side_target_id(plan=self.plan, target=target, side=side)
        entry = {
            "schema_version": "projected-lineup-label-side-terminal-v1",
            "side_target_id": identifier,
            "plan_sha256": self.plan.plan_sha256,
            "target_id": target.target_id,
            "mlb_game_pk": target.mlb_game_pk,
            "official_game_date": target.official_game_date,
            "side": side,
            "team_id": None,
            "terminal_state": state,
            "committed_utc": observed_at_utc,
            "roster_terminal": dict(roster_terminal_ref) if roster_terminal_ref else None,
            "final_feed_raw": self._raw(final_feed_raw) if final_feed_raw else None,
            "official_original_batting_order": None,
            "detail": str(detail).strip(),
            "research_only": True,
            "betting_authorized": False,
        }
        entry["entry_sha256"] = sha256_value(entry)
        return _publish_once(
            self.root / "terminal" / f"{identifier}.json",
            canonical_bytes(entry) + b"\n",
        )

    def verify(
        self,
        *,
        roster_ledger: ProjectedLineupRosterLedger,
        require_complete_coverage: bool = False,
    ) -> dict[str, int]:
        roster_counts = roster_ledger.verify(require_complete_coverage=False)
        roster_manifest_path = roster_ledger.root / "ledger_manifest.json"
        roster_manifest_sha = hashlib.sha256(roster_manifest_path.read_bytes()).hexdigest()
        if roster_manifest_sha != self.roster_manifest_sha256:
            raise ProjectedLineupLabelLedgerError("bound roster manifest differs")
        manifest = _load_json(self.root / "ledger_manifest.json", "label ledger manifest")
        expected = {
            roster_side_target_id(plan=self.plan, target=target, side=side)
            for target in self.plan.targets
            for side in ("away", "home")
        }
        if (
            manifest.get("plan_sha256") != self.plan.plan_sha256
            or set(manifest.get("expected_side_target_ids", [])) != expected
            or manifest.get("research_only") is not True
            or manifest.get("betting_authorized") is not False
            or manifest.get("roster_manifest_sha256") != self.roster_manifest_sha256
        ):
            raise ProjectedLineupLabelLedgerError("label ledger manifest differs from immutable plan bindings")
        target_by_id = {target.target_id: target for target in self.plan.targets}
        roster_entries = roster_ledger.terminal_entries()
        paths = sorted((self.root / "terminal").glob("*.json")) if (self.root / "terminal").is_dir() else []
        if any(path.stem not in expected for path in paths) or (
            require_complete_coverage and {path.stem for path in paths} != expected
        ):
            raise ProjectedLineupLabelLedgerError("label terminal coverage differs from immutable plan")
        raw_seen: set[Path] = set()
        counts = {CAPTURED: 0, **{state: 0 for state in EXCLUSIONS}}
        for path in paths:
            entry = _load_json(path, "label terminal entry")
            unsigned = dict(entry)
            digest = unsigned.pop("entry_sha256", None)
            if (
                digest != sha256_value(unsigned)
                or entry.get("side_target_id") != path.stem
                or entry.get("terminal_state") not in counts
            ):
                raise ProjectedLineupLabelLedgerError("label terminal entry identity or hash differs")
            target = target_by_id.get(str(entry.get("target_id")))
            if (
                target is None
                or entry.get("plan_sha256") != self.plan.plan_sha256
                or entry.get("mlb_game_pk") != target.mlb_game_pk
                or entry.get("official_game_date") != target.official_game_date
            ):
                raise ProjectedLineupLabelLedgerError("label terminal entry differs from immutable plan")
            roster_ref = entry.get("roster_terminal")
            if not isinstance(roster_ref, Mapping) or set(roster_ref) != {"path", "sha256"}:
                raise ProjectedLineupLabelLedgerError("label entry lacks a bound roster terminal")
            roster_path = (roster_ledger.root / str(roster_ref["path"])).resolve()
            if (
                not roster_path.is_file()
                or hashlib.sha256(roster_path.read_bytes()).hexdigest() != roster_ref["sha256"]
            ):
                raise ProjectedLineupLabelLedgerError("bound roster terminal reference differs")
            roster_terminal = roster_entries.get(path.stem)
            if roster_terminal is None:
                raise ProjectedLineupLabelLedgerError("bound roster terminal is missing")
            counts[entry["terminal_state"]] += 1
            feed_ref = entry.get("final_feed_raw")
            if feed_ref is not None:
                if not isinstance(feed_ref, Mapping) or set(feed_ref) != {"path", "sha256"}:
                    raise ProjectedLineupLabelLedgerError("final feed reference is malformed")
                raw_path = (self.root / str(feed_ref["path"])).resolve()
                if (
                    not raw_path.is_file()
                    or hashlib.sha256(raw_path.read_bytes()).hexdigest() != feed_ref["sha256"]
                ):
                    raise ProjectedLineupLabelLedgerError("final feed raw reference hash differs")
                raw_seen.add(raw_path)
            state = str(entry["terminal_state"])
            if state == CAPTURED:
                if roster_terminal.get("terminal_state") != ROSTER_CAPTURED:
                    raise ProjectedLineupLabelLedgerError("captured label cannot bind a non-captured roster receipt")
                lineup = entry.get("official_original_batting_order")
                team_id = entry.get("team_id")
                roster = roster_terminal.get("roster")
                if (
                    not isinstance(lineup, list)
                    or len(lineup) != 9
                    or len(set(lineup)) != 9
                    or not isinstance(team_id, int)
                    or not isinstance(roster, Mapping)
                ):
                    raise ProjectedLineupLabelLedgerError("captured label entry is incomplete")
                roster_ids = {
                    int(item["player_id"])
                    for item in roster.get("players", [])
                    if isinstance(item, Mapping) and isinstance(item.get("player_id"), int)
                }
                if set(lineup) - roster_ids:
                    raise ProjectedLineupLabelLedgerError("captured lineup contains a player outside the receipted active roster")
            else:
                if state == "roster_receipt_missing" and roster_terminal.get("terminal_state") == ROSTER_CAPTURED:
                    raise ProjectedLineupLabelLedgerError("missing-roster exclusion cannot bind a captured roster")
                if state in {"official_lineup_malformed", "roster_label_mismatch"} and roster_terminal.get("terminal_state") != ROSTER_CAPTURED:
                    raise ProjectedLineupLabelLedgerError("lineup exclusion requiring final feed must bind a captured roster")
        actual = {path.resolve() for path in (self.root / "raw").glob("*.json")} if (self.root / "raw").is_dir() else set()
        if raw_seen != actual:
            raise ProjectedLineupLabelLedgerError("label ledger has orphaned or missing raw feeds")
        counts.update(
            {
                "expected": len(expected),
                "terminal": len(paths),
                "missing": len(expected) - len(paths),
                "roster_captured": roster_counts.get(ROSTER_CAPTURED, 0),
                "roster_excluded": sum(roster_counts.get(state, 0) for state in ROSTER_EXCLUSIONS),
            }
        )
        return counts


def roster_manifest_sha256(root: str | Path) -> str:
    """Hash the immutable roster-ledger manifest exactly as retained on disk."""
    path = Path(root) / "ledger_manifest.json"
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ProjectedLineupLabelLedgerError("roster ledger manifest is unreadable") from exc
    return hashlib.sha256(payload).hexdigest()
