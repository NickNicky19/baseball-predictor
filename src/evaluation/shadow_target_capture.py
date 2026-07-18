"""Atomic, provider-neutral finalizer for one forward T-horizon capture.

Fetching and hard identity resolution remain provider adapters.  This boundary
accepts only their immutable raw payload and already hard-resolved quotes,
then proves timing, model, quote, and target agreement before publishing one
content-addressed bundle.  It never selects bets or appends the betting ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

from src.evaluation.live_market_identity import (
    LiveMarketIdentityError,
    ResolvedLiveMarketQuote,
    require_unique_selected_market_keys,
)
from src.evaluation.shadow_capture_plan import CaptureAttempt, CaptureTarget, ShadowCapturePlan
from src.evaluation.shadow_prediction_snapshot import load_shadow_prediction_snapshot
from src.utils.provenance import sha256_file


SCHEMA_VERSION = "shadow-target-capture-bundle-v2"


class ShadowTargetCaptureError(ValueError):
    """Raised when one target cannot become immutable shadow evidence."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _hash_payload(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc_dt(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ShadowTargetCaptureError("capture timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ShadowTargetCaptureError("capture timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _target(plan: ShadowCapturePlan, target_id: str) -> CaptureTarget:
    found = [target for target in plan.targets if target.target_id == target_id]
    if len(found) != 1:
        raise ShadowTargetCaptureError("target_id is absent or ambiguous in the capture plan")
    return found[0]


def load_resolved_quotes(path: str | Path) -> tuple[ResolvedLiveMarketQuote, ...]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ShadowTargetCaptureError(f"resolved quote artifact is malformed: {source}") from exc
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and payload.get("schema_version") == "shadow-live-resolved-hits-v1":
        # Provider-specific funnel integrity is part of the evidence boundary;
        # the generic row loader must not silently ignore a tampered exclusion.
        from src.evaluation.shadow_live_provider import validate_resolved_hits_payload

        validate_resolved_hits_payload(payload)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ShadowTargetCaptureError("resolved quote artifact must be a row list or {'rows': [...]} object")
    try:
        return tuple(ResolvedLiveMarketQuote(**row) for row in rows)
    except (TypeError, LiveMarketIdentityError) as exc:
        raise ShadowTargetCaptureError(f"resolved quote artifact fails identity contract: {exc}") from exc


@dataclass(frozen=True)
class TargetCaptureBundle:
    plan_sha256: str
    plan: dict[str, Any]
    target: dict[str, Any]
    attempt: dict[str, Any]
    prediction_artifact_path: str
    prediction_artifact_sha256: str
    expected_config_sha256: str
    raw_provider_artifact_path: str
    raw_provider_artifact_sha256: str
    resolved_quote_artifact_path: str
    resolved_quote_artifact_sha256: str
    quote_sha256: tuple[str, ...]
    bundle_sha256: str

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("bundle_sha256")
        value["quote_sha256"] = list(self.quote_sha256)
        return {"schema_version": SCHEMA_VERSION, **value}

    def to_dict(self) -> dict[str, Any]:
        return {**self.unsigned(), "bundle_sha256": self.bundle_sha256}

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "TargetCaptureBundle":
        if row.get("schema_version") != SCHEMA_VERSION:
            raise ShadowTargetCaptureError("unknown target capture bundle schema")
        keys = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        missing = sorted(keys - set(row))
        if missing:
            raise ShadowTargetCaptureError(f"target capture bundle is missing fields: {missing}")
        payload = {key: row[key] for key in keys}
        payload["quote_sha256"] = tuple(payload["quote_sha256"])
        bundle = cls(**payload)
        if bundle.bundle_sha256 != _hash_payload(bundle.unsigned()):
            raise ShadowTargetCaptureError("target capture bundle hash does not match its contents")
        plan = ShadowCapturePlan.from_mapping(bundle.plan)
        if plan.plan_sha256 != bundle.plan_sha256:
            raise ShadowTargetCaptureError("target capture bundle plan hash does not match its plan")
        target = CaptureTarget.from_mapping(bundle.target)
        attempt = CaptureAttempt.from_mapping(bundle.attempt)
        if attempt.target_id != target.target_id or attempt.plan_sha256 != bundle.plan_sha256:
            raise ShadowTargetCaptureError("target capture attempt is not bound to its bundle target/plan")
        plan_targets = [value for value in plan.targets if value.target_id == target.target_id]
        if len(plan_targets) != 1:
            raise ShadowTargetCaptureError("target capture bundle target is absent from its plan")
        if target.to_dict() != plan_targets[0].to_dict():
            raise ShadowTargetCaptureError("target capture bundle target differs from its plan target")
        target_at = _utc_dt(target.entry_target_at_utc)
        if _utc_dt(attempt.started_at_utc) > target_at or _utc_dt(attempt.completed_at_utc) > target_at:
            raise ShadowTargetCaptureError("target capture bundle attempt is after its entry target")
        quote_hashes = bundle.quote_sha256
        if len(set(quote_hashes)) != len(quote_hashes):
            raise ShadowTargetCaptureError("target capture bundle repeats a quote hash")
        if any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value) for value in quote_hashes):
            raise ShadowTargetCaptureError("target capture bundle contains an invalid quote hash")
        if attempt.resolved_two_sided_quote_count != len(quote_hashes):
            raise ShadowTargetCaptureError("target capture bundle quote count differs from its attempt")
        return bundle


def finalize_target_capture(
    *,
    plan: ShadowCapturePlan,
    target_id: str,
    started_at_utc: str,
    completed_at_utc: str,
    source_name: str,
    expected_config_sha256: str,
    prediction_archive: str | Path,
    raw_provider_artifact: str | Path,
    resolved_quote_artifact: str | Path,
) -> TargetCaptureBundle:
    """Validate and bind one target without selecting or authorizing a wager."""

    target = _target(plan, target_id)
    target_at = _utc_dt(target.entry_target_at_utc)
    if _utc_dt(started_at_utc) > target_at or _utc_dt(completed_at_utc) > target_at:
        raise ShadowTargetCaptureError("provider/model capture must start and complete by the entry target")

    prediction_path = Path(prediction_archive).resolve()
    raw_path = Path(raw_provider_artifact).resolve()
    quote_path = Path(resolved_quote_artifact).resolve()
    for path, label in (
        (prediction_path, "prediction archive"),
        (raw_path, "raw provider artifact"),
        (quote_path, "resolved quote artifact"),
    ):
        if not path.is_file():
            raise ShadowTargetCaptureError(f"{label} does not exist: {path}")

    raw_sha = sha256_file(raw_path)
    prediction = load_shadow_prediction_snapshot(prediction_path)
    if prediction.game_date != target.official_game_date:
        raise ShadowTargetCaptureError("prediction archive date differs from target date")
    if prediction.effective_config_sha256 != expected_config_sha256:
        raise ShadowTargetCaptureError("prediction archive was not generated by the expected effective config")
    if _utc_dt(prediction.captured_at_utc) > target_at:
        raise ShadowTargetCaptureError("prediction archive was captured after the entry target")

    quotes = load_resolved_quotes(quote_path)
    for quote in quotes:
        if quote.source_name != source_name.strip().lower():
            raise ShadowTargetCaptureError("resolved quote source differs from the declared provider")
        if quote.source_payload_sha256 != raw_sha:
            raise ShadowTargetCaptureError("resolved quote is not bound to the retained raw provider artifact")
        if quote.mlb_game_pk != target.mlb_game_pk:
            raise ShadowTargetCaptureError("resolved quote belongs to a different MLB game")
        if quote.game_date != target.official_game_date:
            raise ShadowTargetCaptureError("resolved quote game date differs from the capture target")
        if quote.official_start_time_utc != target.official_start_time_utc:
            raise ShadowTargetCaptureError("resolved quote official start differs from the capture target")
        if quote.sportsbook != "draftkings" or quote.category != "hits":
            raise ShadowTargetCaptureError("this collector lane accepts DraftKings Hits quotes only")
        if _utc_dt(quote.source_quote_at_utc) > target_at:
            raise ShadowTargetCaptureError("post-target quote cannot enter the T-horizon universe")
        prediction.probability_for(quote.mlb_game_pk, quote.player_id, quote.category, quote.line)
    try:
        require_unique_selected_market_keys(quotes)
    except LiveMarketIdentityError as exc:
        raise ShadowTargetCaptureError(str(exc)) from exc

    outcome = "captured" if quotes else "no_eligible_market"
    attempt = CaptureAttempt(
        target_id=target.target_id,
        plan_sha256=plan.plan_sha256,
        started_at_utc=started_at_utc,
        completed_at_utc=completed_at_utc,
        source_name=source_name,
        source_payload_sha256=raw_sha,
        outcome=outcome,
        resolved_two_sided_quote_count=len(quotes),
        detail="",
    )
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "plan_sha256": plan.plan_sha256,
        "plan": plan.to_dict(),
        "target": target.to_dict(),
        "attempt": attempt.to_dict(),
        "prediction_artifact_path": str(prediction_path),
        "prediction_artifact_sha256": prediction.prediction_artifact_sha256,
        "expected_config_sha256": expected_config_sha256,
        "raw_provider_artifact_path": str(raw_path),
        "raw_provider_artifact_sha256": raw_sha,
        "resolved_quote_artifact_path": str(quote_path),
        "resolved_quote_artifact_sha256": sha256_file(quote_path),
        "quote_sha256": [quote.quote_sha256 for quote in quotes],
    }
    return TargetCaptureBundle(
        **{
            key: value
            for key, value in unsigned.items()
            if key not in {"schema_version", "quote_sha256"}
        },
        quote_sha256=tuple(unsigned["quote_sha256"]),
        bundle_sha256=_hash_payload(unsigned),
    )


def publish_bundle(bundle: TargetCaptureBundle, path: str | Path) -> bool:
    """Atomically publish once; identical retry is idempotent, conflict fails."""

    output = Path(path)
    encoded = json.dumps(bundle.to_dict(), indent=2, sort_keys=True) + "\n"
    if output.exists():
        existing = TargetCaptureBundle.from_mapping(json.loads(output.read_text(encoding="utf-8")))
        if existing.bundle_sha256 != bundle.bundle_sha256:
            raise ShadowTargetCaptureError("target capture output already exists with different evidence")
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + f".{os.getpid()}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, output)
    return True


def relocate_bundle_artifact_path(
    original_path: str | Path,
    *,
    target_id: str,
    relocation_root: str | Path,
) -> Path:
    """Map a machine-local bundle path into a copied target evidence tree."""

    root = Path(relocation_root).resolve()
    candidates = (
        PurePosixPath(str(original_path)).parts,
        PureWindowsPath(str(original_path)).parts,
    )
    suffix: tuple[str, ...] | None = None
    for parts in candidates:
        if target_id in parts:
            index = parts.index(target_id)
            if index + 1 < len(parts):
                suffix = tuple(parts[index + 1:])
                break
    if not suffix:
        raise ShadowTargetCaptureError(
            "bundle artifact path cannot be relocated below its immutable target_id"
        )
    relocated = root.joinpath(*suffix).resolve()
    if not relocated.is_relative_to(root):
        raise ShadowTargetCaptureError("relocated bundle artifact escapes the target evidence tree")
    return relocated


def load_target_capture_bundle(
    path: str | Path,
    *,
    relocation_root: str | Path | None = None,
) -> TargetCaptureBundle:
    source = Path(path)
    try:
        row = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ShadowTargetCaptureError(f"target capture bundle is malformed: {source}") from exc
    if not isinstance(row, dict):
        raise ShadowTargetCaptureError("target capture bundle root must be an object")
    bundle = TargetCaptureBundle.from_mapping(row)
    bound = {
        Path(bundle.prediction_artifact_path): bundle.prediction_artifact_sha256,
        Path(bundle.raw_provider_artifact_path): bundle.raw_provider_artifact_sha256,
        Path(bundle.resolved_quote_artifact_path): bundle.resolved_quote_artifact_sha256,
    }
    for original, expected in bound.items():
        artifact = original
        if relocation_root is not None:
            artifact = relocate_bundle_artifact_path(
                original,
                target_id=str(bundle.target["target_id"]),
                relocation_root=relocation_root,
            )
        if not artifact.is_file() or sha256_file(artifact) != expected:
            raise ShadowTargetCaptureError(f"bound artifact is missing or tampered: {artifact}")
    return bundle
