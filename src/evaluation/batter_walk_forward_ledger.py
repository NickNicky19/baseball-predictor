"""Append-only walk-control ledger bound to a replayed shared-PA ledger."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.evaluation.batter_walk_forward_control import (
    BatterWalkForwardControlError,
    LoadedBatterWalkContract,
    build_side_terminal,
    canonical_bytes,
    replay_contract,
    sha256_value,
    validate_player_output_against_source,
    validate_side_terminal,
)
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.shared_pa_forward_ledger import (
    SharedPAForwardLedger,
    SharedPAForwardLedgerError,
    side_target_id,
)
from src.evaluation.shared_pa_forward_collector import (
    RawPregameResponse,
    SharedPAForwardCollectorError,
    projected_lineups_from_schedule,
    validate_schedule_input_surface,
)


class BatterWalkForwardLedgerError(ValueError):
    """The append-only walk ledger is incomplete, mutable, or contradictory."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = {
    "schema_version", "contract_sha256", "settlement_contract_sha256",
    "adapter_code_sha256", "ledger_code_sha256", "test_suite_sha256",
    "plan", "source_ledger_manifest_sha256", "source_contract_sha256",
    "source_runtime_manifest_sha256", "source_collector_code_sha256",
    "expected_side_keys", "expected_source_side_target_ids",
    "expected_side_file_ids", "append_only", "terminal_last", "research_only",
    "promotion_eligible", "betting_authorized", "activated",
}


def _is_linklike(path: Path) -> bool:
    """Detect POSIX symlinks and every Windows reparse-point form."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attributes & reparse)
    except OSError:
        return False


def _assert_no_linklike_components(path: Path, label: str) -> None:
    absolute = path if path.is_absolute() else Path.cwd() / path
    parts = absolute.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        if _is_linklike(current):
            raise BatterWalkForwardLedgerError(f"{label} crosses a symlink, junction, or reparse point")


def _safe_root(path: str | Path, label: str) -> Path:
    raw = Path(path)
    _assert_no_linklike_components(raw, label)
    resolved = raw.resolve()
    if resolved.parent == resolved:
        raise BatterWalkForwardLedgerError(f"{label} cannot be a filesystem root")
    return resolved


def _safe_child(root: Path, relative: str | Path, label: str) -> Path:
    if _is_linklike(root):
        raise BatterWalkForwardLedgerError(f"{label} root is a symlink, junction, or reparse point")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise BatterWalkForwardLedgerError(f"{label} path escapes the ledger")
    candidate = root.joinpath(relative_path)
    current = root
    for part in relative_path.parts:
        current = current / part
        if _is_linklike(current):
            raise BatterWalkForwardLedgerError(f"{label} path crosses a symlink, junction, or reparse point")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BatterWalkForwardLedgerError(f"{label} path escapes the ledger") from exc
    return candidate


def _publish_once(root: Path, relative: str | Path, payload: bytes) -> bool:
    """Durably publish immutable bytes; identical concurrent retries are idempotent."""
    path = _safe_child(root, relative, "publication")
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    _safe_child(root, parent.relative_to(root), "publication directory")
    if path.exists():
        if _is_linklike(path) or path.read_bytes() != payload:
            raise BatterWalkForwardLedgerError(f"immutable artifact differs: {path.name}")
        return False
    temporary = parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if _is_linklike(path) or path.read_bytes() != payload:
                raise BatterWalkForwardLedgerError(f"concurrent immutable artifact differs: {path.name}")
            return False
        try:
            directory_fd = os.open(parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return canonical_bytes(value) + b"\n"


def _read_json(root: Path, relative: str | Path, label: str) -> dict[str, Any]:
    path = _safe_child(root, relative, label)
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise BatterWalkForwardLedgerError(f"cannot read {label}") from exc
    if not isinstance(value, dict) or raw != _json_bytes(value):
        raise BatterWalkForwardLedgerError(f"{label} must be a canonical object")
    return value


def side_file_id(market_hard_side_key: str) -> str:
    if not isinstance(market_hard_side_key, str) or not market_hard_side_key:
        raise BatterWalkForwardLedgerError("market side key is blank")
    return sha256_value({"market_hard_side_key": market_hard_side_key})


class BatterWalkForwardLedger:
    """Content-addressed output store whose source is an exact shared-PA ledger."""

    def __init__(
        self, root: str | Path, *, contract: LoadedBatterWalkContract,
        plan: ShadowCapturePlan, source_ledger_root: str | Path,
    ) -> None:
        self.contract = replay_contract(contract)
        if not isinstance(plan, ShadowCapturePlan) or plan.entry_hours != 4 or plan.official_game_date.startswith("2026-05-") or not plan.targets:
            raise BatterWalkForwardLedgerError("walk ledger requires a non-May immutable T-4 plan")
        self.plan = ShadowCapturePlan.from_mapping(plan.to_dict())
        self.root = _safe_root(root, "walk ledger root")
        self.source_ledger_root = _safe_root(source_ledger_root, "shared-PA ledger root")
        try:
            source = SharedPAForwardLedger.open_existing(self.source_ledger_root, plan=self.plan)
            source.verify(require_complete_coverage=True)
        except SharedPAForwardLedgerError as exc:
            raise BatterWalkForwardLedgerError("shared-PA source ledger does not replay") from exc
        expected_source_hash = self.contract.payload["source_shared_pa"]["contract"]["sha256"]
        if source.contract_sha256 != expected_source_hash:
            raise BatterWalkForwardLedgerError("shared-PA source ledger contract differs")
        self.source_ledger = source
        self.expected_side_keys = sorted(
            f"{target.mlb_game_pk}:{side}:batter_walks"
            for target in self.plan.targets for side in ("home", "away")
        )
        self._target_by_game = {target.mlb_game_pk: target for target in self.plan.targets}
        source_manifest_bytes = _safe_child(
            self.source_ledger_root, "ledger_manifest.json", "shared-PA manifest"
        ).read_bytes()
        source_manifest = json.loads(source_manifest_bytes)
        if source_manifest_bytes != _json_bytes(source_manifest):
            raise BatterWalkForwardLedgerError("shared-PA manifest is not canonical")
        _publish_once(self.root, "upstream/ledger_manifest.json", source_manifest_bytes)
        implementation = self.contract.payload["implementation"]
        manifest = {
            "schema_version": "batter-walk-forward-ledger-v1",
            "contract_sha256": self.contract.sha256,
            "settlement_contract_sha256": self.contract.settlement_sha256,
            "adapter_code_sha256": implementation["adapter"]["sha256"],
            "ledger_code_sha256": implementation["ledger"]["sha256"],
            "test_suite_sha256": implementation["regression_and_mutation_tests"]["sha256"],
            "plan": self.plan.to_dict(),
            "source_ledger_manifest_sha256": hashlib.sha256(source_manifest_bytes).hexdigest(),
            "source_contract_sha256": source.contract_sha256,
            "source_runtime_manifest_sha256": source.runtime_manifest_sha256,
            "source_collector_code_sha256": source.collector_code_sha256,
            "expected_side_keys": self.expected_side_keys,
            "expected_source_side_target_ids": sorted(
                side_target_id(plan=self.plan, target=target, side=side)
                for target in self.plan.targets for side in ("away", "home")
            ),
            "expected_side_file_ids": {key: side_file_id(key) for key in self.expected_side_keys},
            "append_only": True, "terminal_last": True, "research_only": True,
            "promotion_eligible": False, "betting_authorized": False, "activated": False,
        }
        _publish_once(self.root, "ledger_manifest.json", _json_bytes(manifest))

    def _target_side(self, target: CaptureTarget, side: str) -> tuple[str, str]:
        if not isinstance(target, CaptureTarget) or target.target_id not in {item.target_id for item in self.plan.targets} or side not in {"home", "away"}:
            raise BatterWalkForwardLedgerError("walk side is absent from the immutable plan")
        key = f"{target.mlb_game_pk}:{side}:batter_walks"
        return key, side_target_id(plan=self.plan, target=target, side=side)

    def _source_terminal(self, target: CaptureTarget, side: str) -> tuple[dict[str, Any], bytes]:
        _, identifier = self._target_side(target, side)
        try:
            self.source_ledger.verify(require_complete_coverage=True)
        except SharedPAForwardLedgerError as exc:
            raise BatterWalkForwardLedgerError("shared-PA source ledger changed") from exc
        relative = Path("terminal") / f"{identifier}.json"
        path = _safe_child(self.source_ledger_root, relative, "shared-PA terminal")
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise BatterWalkForwardLedgerError("shared-PA source terminal is unreadable") from exc
        if not isinstance(value, dict) or raw != _json_bytes(value):
            raise BatterWalkForwardLedgerError("shared-PA source terminal is not canonical")
        required = {
            "schema_version", "side_target_id", "plan_sha256", "target_id",
            "mlb_game_pk", "official_game_date", "side", "terminal_state",
            "committed_utc", "lineup_raw", "stats_raw", "players", "detail",
            "research_only", "betting_authorized", "entry_sha256",
        }
        if set(value) != required:
            raise BatterWalkForwardLedgerError("shared-PA source terminal schema changed")
        state = value.get("terminal_state")
        if state != "captured_complete":
            self._validate_exclusion_semantics(value, target=target, side=side)
        return value, raw

    def _validate_exclusion_semantics(
        self, terminal: Mapping[str, Any], *, target: CaptureTarget, side: str
    ) -> None:
        """Replay every source exclusion that can be decided from retained bytes."""
        state = terminal.get("terminal_state")
        if terminal.get("players") != [] or not isinstance(terminal.get("detail"), str) or not terminal["detail"].strip():
            raise BatterWalkForwardLedgerError("shared-PA exclusion population or detail differs")
        lineup = terminal.get("lineup_raw")
        stats = terminal.get("stats_raw")
        if not isinstance(stats, list):
            raise BatterWalkForwardLedgerError("shared-PA exclusion stats references changed")
        if state == "missed_before_horizon":
            from datetime import datetime
            committed = datetime.fromisoformat(str(terminal["committed_utc"]).replace("Z", "+00:00"))
            horizon = datetime.fromisoformat(target.entry_target_at_utc.replace("Z", "+00:00"))
            if committed <= horizon or lineup is not None or stats:
                raise BatterWalkForwardLedgerError("missed-horizon exclusion does not replay")
            return
        if state == "source_error":
            # The current shared-PA v1 terminal has only a free-text detail and
            # no transport-attempt schema.  A self-hashed label cannot prove
            # source, target, request, parser, observation time, or failure
            # class.  Fail closed until the upstream ledger publishes and
            # independently replays a typed failed-attempt receipt.
            raise BatterWalkForwardLedgerError(
                "source-error exclusion lacks a typed retained failed-attempt transport receipt"
            )
        if not isinstance(lineup, Mapping) or set(lineup) != {"path", "sha256"}:
            raise BatterWalkForwardLedgerError("source exclusion lacks retained lineup bytes")
        source_path = _safe_child(self.source_ledger_root, str(lineup["path"]), "shared-PA exclusion raw")
        payload = source_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != lineup["sha256"]:
            raise BatterWalkForwardLedgerError("shared-PA exclusion raw differs")
        response = RawPregameResponse(payload, str(terminal["committed_utc"]))
        if state == "lineup_unavailable":
            try:
                parsed = projected_lineups_from_schedule(response=response, plan=self.plan, target=target)
            except SharedPAForwardCollectorError as exc:
                raise BatterWalkForwardLedgerError("lineup-unavailable exclusion does not replay") from exc
            if parsed.get(side) is not None or stats:
                raise BatterWalkForwardLedgerError("lineup-unavailable exclusion contains a lineup or stats")
            return
        try:
            if state == "raw_schema_changed":
                validate_schedule_input_surface(response)
            else:
                projected_lineups_from_schedule(response=response, plan=self.plan, target=target)
        except SharedPAForwardCollectorError as exc:
            detail = str(exc)
            expected = {
                "lineup_malformed": "partial or malformed",
                "game_identity_ambiguous": "uniquely identify",
                "raw_schema_changed": "",
            }.get(str(state))
            if expected is None or (expected and expected not in detail):
                raise BatterWalkForwardLedgerError("shared-PA exclusion failure class differs") from exc
            return
        raise BatterWalkForwardLedgerError("shared-PA exclusion label is not proven by retained raw")

    def _retain_source_chain(self, source_terminal: Mapping[str, Any], raw_terminal: bytes) -> None:
        identifier = str(source_terminal["side_target_id"])
        for reference in [source_terminal.get("lineup_raw"), *(source_terminal.get("stats_raw") or [])]:
            if reference is None:
                continue
            if not isinstance(reference, Mapping) or set(reference) != {"path", "sha256"}:
                raise BatterWalkForwardLedgerError("shared-PA raw reference is malformed")
            source_path = _safe_child(self.source_ledger_root, str(reference["path"]), "shared-PA raw")
            payload = source_path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != reference["sha256"]:
                raise BatterWalkForwardLedgerError("shared-PA raw bytes differ")
            _publish_once(self.root, Path("upstream") / str(reference["path"]), payload)
        _publish_once(self.root, Path("upstream/terminal") / f"{identifier}.json", raw_terminal)

    def append_complete(
        self, *, target: CaptureTarget, side: str,
        player_outputs: Sequence[Mapping[str, Any]],
    ) -> bool:
        key, _ = self._target_side(target, side)
        source_terminal, raw_terminal = self._source_terminal(target, side)
        if source_terminal.get("terminal_state") != "captured_complete":
            raise BatterWalkForwardLedgerError("complete walk publication requires a complete replayed source side")
        sources = source_terminal.get("players")
        outputs = list(player_outputs)
        if not isinstance(sources, list) or len(sources) != 9 or len(outputs) != 9:
            raise BatterWalkForwardLedgerError("complete publication requires nine source and output rows")
        source_by_hash = {str(value.get("snapshot_sha256")): value for value in sources if isinstance(value, Mapping)}
        output_by_hash: dict[str, Mapping[str, Any]] = {}
        if len(source_by_hash) != 9:
            raise BatterWalkForwardLedgerError("replayed source side has duplicate players")
        for output in outputs:
            source = source_by_hash.get(str(output.get("source_shared_pa_snapshot_sha256")))
            if source is None:
                raise BatterWalkForwardLedgerError("walk output lacks its replayed source snapshot")
            try:
                validate_player_output_against_source(output, source_snapshot=source, contract=self.contract)
            except BatterWalkForwardControlError as exc:
                raise BatterWalkForwardLedgerError("walk output does not reproduce from upstream source") from exc
            digest = str(output.get("output_sha256"))
            if digest in output_by_hash or f"{output['mlb_game_pk']}:{output['side']}:batter_walks" != key:
                raise BatterWalkForwardLedgerError("walk output population is duplicate or belongs to another side")
            output_by_hash[digest] = output
        if {str(value["source_shared_pa_snapshot_sha256"]) for value in outputs} != set(source_by_hash):
            raise BatterWalkForwardLedgerError("walk output population differs from replayed source players")
        implementation = self.contract.payload["implementation"]
        terminal = build_side_terminal(
            source_terminal=source_terminal, contract=self.contract,
            adapter_code_sha256=implementation["adapter"]["sha256"],
            test_suite_sha256=implementation["regression_and_mutation_tests"]["sha256"],
            player_output_sha256=sorted(output_by_hash),
        )
        terminal_relative = Path("terminal") / f"{side_file_id(key)}.json"
        terminal_payload = _json_bytes(terminal)
        existing = _safe_child(self.root, terminal_relative, "walk terminal")
        if existing.exists() and (existing.is_symlink() or existing.read_bytes() != terminal_payload):
            raise BatterWalkForwardLedgerError(f"immutable artifact differs: {existing.name}")
        self._retain_source_chain(source_terminal, raw_terminal)
        for digest, output in sorted(output_by_hash.items()):
            _publish_once(self.root, Path("outputs") / f"{digest}.json", _json_bytes(output))
        return _publish_once(self.root, terminal_relative, terminal_payload)

    def append_exclusion(self, *, target: CaptureTarget, side: str) -> bool:
        key, _ = self._target_side(target, side)
        source_terminal, raw_terminal = self._source_terminal(target, side)
        if source_terminal.get("terminal_state") == "captured_complete" or source_terminal.get("players") != []:
            raise BatterWalkForwardLedgerError("walk exclusion must mirror a replayed excluded source side")
        implementation = self.contract.payload["implementation"]
        terminal = build_side_terminal(
            source_terminal=source_terminal, contract=self.contract,
            adapter_code_sha256=implementation["adapter"]["sha256"],
            test_suite_sha256=implementation["regression_and_mutation_tests"]["sha256"],
        )
        terminal_relative = Path("terminal") / f"{side_file_id(key)}.json"
        terminal_payload = _json_bytes(terminal)
        existing = _safe_child(self.root, terminal_relative, "walk terminal")
        if existing.exists() and (existing.is_symlink() or existing.read_bytes() != terminal_payload):
            raise BatterWalkForwardLedgerError(f"immutable artifact differs: {existing.name}")
        self._retain_source_chain(source_terminal, raw_terminal)
        return _publish_once(self.root, terminal_relative, terminal_payload)

    def verify(self, *, require_complete_coverage: bool = False) -> dict[str, int]:
        self.contract = replay_contract(self.contract)
        manifest = _read_json(self.root, "ledger_manifest.json", "walk ledger manifest")
        if set(manifest) != _MANIFEST_FIELDS:
            raise BatterWalkForwardLedgerError("walk ledger manifest field set changed")
        try:
            replayed_plan = ShadowCapturePlan.from_mapping(manifest["plan"])
        except (TypeError, ValueError) as exc:
            raise BatterWalkForwardLedgerError("walk ledger plan does not replay") from exc
        implementation = self.contract.payload["implementation"]
        source_manifest_path = _safe_child(self.root, "upstream/ledger_manifest.json", "retained shared-PA manifest")
        source_manifest_bytes = source_manifest_path.read_bytes()
        expected_exact = {
            "schema_version": "batter-walk-forward-ledger-v1",
            "contract_sha256": self.contract.sha256,
            "settlement_contract_sha256": self.contract.settlement_sha256,
            "adapter_code_sha256": implementation["adapter"]["sha256"],
            "ledger_code_sha256": implementation["ledger"]["sha256"],
            "test_suite_sha256": implementation["regression_and_mutation_tests"]["sha256"],
            "plan": self.plan.to_dict(),
            "source_ledger_manifest_sha256": hashlib.sha256(source_manifest_bytes).hexdigest(),
            "source_contract_sha256": self.source_ledger.contract_sha256,
            "source_runtime_manifest_sha256": self.source_ledger.runtime_manifest_sha256,
            "source_collector_code_sha256": self.source_ledger.collector_code_sha256,
            "expected_side_keys": self.expected_side_keys,
            "expected_source_side_target_ids": sorted(
                side_target_id(plan=self.plan, target=target, side=side)
                for target in self.plan.targets for side in ("away", "home")
            ),
            "expected_side_file_ids": {key: side_file_id(key) for key in self.expected_side_keys},
            "append_only": True, "terminal_last": True, "research_only": True,
            "promotion_eligible": False, "betting_authorized": False, "activated": False,
        }
        if replayed_plan.to_dict() != self.plan.to_dict() or manifest != expected_exact:
            raise BatterWalkForwardLedgerError("walk ledger manifest differs from exact plan and contracts")
        copied_source = SharedPAForwardLedger.open_existing(self.root / "upstream", plan=self.plan)
        source_report = copied_source.verify(require_complete_coverage=require_complete_coverage)
        paths = sorted(_safe_child(self.root, "terminal", "walk terminals").glob("*.json")) if _safe_child(self.root, "terminal", "walk terminals").exists() else []
        terminal_by_key: dict[str, Mapping[str, Any]] = {}
        referenced_outputs: set[Path] = set()
        complete = excluded = 0
        for path in paths:
            if _is_linklike(path):
                raise BatterWalkForwardLedgerError("walk terminal cannot be a symlink, junction, or reparse point")
            terminal = _read_json(self.root, path.relative_to(self.root), f"walk terminal {path.name}")
            try:
                validate_side_terminal(terminal, contract=self.contract)
            except BatterWalkForwardControlError as exc:
                raise BatterWalkForwardLedgerError("walk terminal is invalid") from exc
            key = str(terminal["market_hard_side_key"])
            if key not in self.expected_side_keys or key in terminal_by_key or path.name != f"{side_file_id(key)}.json":
                raise BatterWalkForwardLedgerError("walk terminal identity or filename differs")
            terminal_by_key[key] = terminal
            source_id = manifest["expected_source_side_target_ids"]
            target = self._target_by_game[int(terminal["mlb_game_pk"])]
            expected_source_id = side_target_id(plan=self.plan, target=target, side=str(terminal["side"]))
            if expected_source_id not in source_id:
                raise BatterWalkForwardLedgerError("walk terminal source side is unplanned")
            source_terminal = _read_json(self.root, Path("upstream/terminal") / f"{expected_source_id}.json", "retained shared-PA terminal")
            if source_terminal.get("entry_sha256") != terminal["source_side_entry_sha256"]:
                raise BatterWalkForwardLedgerError("walk terminal source commitment differs")
            if source_terminal.get("terminal_state") != "captured_complete":
                self._validate_exclusion_semantics(
                    source_terminal, target=target, side=str(terminal["side"])
                )
            if terminal["terminal_state"] == "captured_complete":
                complete += 1
                source_by_hash = {str(value["snapshot_sha256"]): value for value in source_terminal["players"]}
                if len(terminal["player_output_sha256"]) != 9:
                    raise BatterWalkForwardLedgerError("complete walk terminal lacks outputs")
                used_sources: set[str] = set()
                for digest in terminal["player_output_sha256"]:
                    output_path = _safe_child(self.root, Path("outputs") / f"{digest}.json", "walk output")
                    output = _read_json(self.root, output_path.relative_to(self.root), f"walk output {digest}")
                    source_digest = str(output.get("source_shared_pa_snapshot_sha256"))
                    source = source_by_hash.get(source_digest)
                    if source is None:
                        raise BatterWalkForwardLedgerError("walk output source is absent upstream")
                    try:
                        validate_player_output_against_source(output, source_snapshot=source, contract=self.contract)
                    except BatterWalkForwardControlError as exc:
                        raise BatterWalkForwardLedgerError("retained walk output no longer reproduces") from exc
                    if output.get("output_sha256") != digest:
                        raise BatterWalkForwardLedgerError("walk output filename differs")
                    referenced_outputs.add(output_path.resolve())
                    used_sources.add(source_digest)
                if used_sources != set(source_by_hash):
                    raise BatterWalkForwardLedgerError("walk output population differs from upstream")
            else:
                excluded += 1
                if terminal["player_output_sha256"] or source_terminal.get("players") != []:
                    raise BatterWalkForwardLedgerError("excluded walk side contains predictions")
        if require_complete_coverage and set(terminal_by_key) != set(self.expected_side_keys):
            raise BatterWalkForwardLedgerError("walk ledger is missing planned side terminals")
        outputs_dir = _safe_child(self.root, "outputs", "walk outputs")
        actual_outputs = {path.resolve() for path in outputs_dir.glob("*.json")} if outputs_dir.exists() else set()
        if actual_outputs != referenced_outputs:
            raise BatterWalkForwardLedgerError("walk ledger contains orphaned or missing outputs")
        self._verify_exact_artifact_inventory(
            terminal_paths={path.resolve() for path in paths},
            output_paths=referenced_outputs,
            terminal_by_key=terminal_by_key,
        )
        return {
            "expected_sides": len(self.expected_side_keys), "terminal_sides": len(terminal_by_key),
            "missing_sides": len(self.expected_side_keys) - len(terminal_by_key),
            "complete_sides": complete, "excluded_sides": excluded,
            "prediction_rows": len(referenced_outputs),
            "replayed_source_terminals": source_report["terminal"],
        }

    def _verify_exact_artifact_inventory(
        self, *, terminal_paths: set[Path], output_paths: set[Path],
        terminal_by_key: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """Reject every byte not reachable from the exact manifests/terminals."""
        allowed_directories = {
            self.root.resolve(),
            (self.root / "terminal").resolve(),
            (self.root / "outputs").resolve(),
            (self.root / "upstream").resolve(),
            (self.root / "upstream" / "terminal").resolve(),
            (self.root / "upstream" / "raw").resolve(),
        }
        actual_files: set[Path] = set()
        for path in self.root.rglob("*"):
            if _is_linklike(path):
                raise BatterWalkForwardLedgerError("walk ledger inventory contains a symlink, junction, or reparse point")
            resolved = path.resolve()
            if path.is_dir():
                if resolved not in allowed_directories:
                    raise BatterWalkForwardLedgerError("walk ledger inventory contains an unknown directory")
                continue
            if not path.is_file():
                raise BatterWalkForwardLedgerError("walk ledger inventory contains a non-file artifact")
            if path.name.startswith(".") or path.suffix != ".json" or ".tmp" in path.name:
                raise BatterWalkForwardLedgerError("walk ledger inventory contains a hidden, temporary, or unknown-extension artifact")
            actual_files.add(resolved)

        expected_files = {
            (self.root / "ledger_manifest.json").resolve(),
            (self.root / "upstream" / "ledger_manifest.json").resolve(),
            *terminal_paths,
            *output_paths,
        }
        for terminal in terminal_by_key.values():
            target = self._target_by_game[int(terminal["mlb_game_pk"])]
            source_id = side_target_id(
                plan=self.plan, target=target, side=str(terminal["side"])
            )
            source_relative = Path("upstream/terminal") / f"{source_id}.json"
            source = _read_json(self.root, source_relative, "retained shared-PA terminal")
            expected_files.add((self.root / source_relative).resolve())
            for reference in [source.get("lineup_raw"), *(source.get("stats_raw") or [])]:
                if reference is None:
                    continue
                if not isinstance(reference, Mapping) or set(reference) != {"path", "sha256"}:
                    raise BatterWalkForwardLedgerError("retained source raw reference is malformed")
                expected_files.add((self.root / "upstream" / str(reference["path"])).resolve())
        if actual_files != expected_files:
            raise BatterWalkForwardLedgerError("walk ledger exact artifact inventory differs")

    def verified_population(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return exact retained terminals/outputs only after full replay."""
        self.verify(require_complete_coverage=True)
        terminals: list[dict[str, Any]] = []
        outputs: list[dict[str, Any]] = []
        for path in sorted((self.root / "terminal").glob("*.json")):
            terminal = _read_json(self.root, path.relative_to(self.root), "walk terminal")
            terminals.append(terminal)
            for digest in terminal["player_output_sha256"]:
                outputs.append(_read_json(
                    self.root, Path("outputs") / f"{digest}.json", "walk output"
                ))
        return terminals, outputs
