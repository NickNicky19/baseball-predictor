"""Strict provider-evidence adapters for the forward shadow ledger.

This module is intentionally downstream of network access and identity
resolution.  It never fetches, name-matches, chooses a policy, or authorizes a
wager.  It turns one verified target-capture bundle plus one already locked
research policy into a ledger entry, and later turns retained close/outcome
artifacts into a linked graded resolution.

The separation is load-bearing: provider-specific code may change, while the
ledger boundary continues to require the same hashes, hard MLB identities,
timestamps, exact model tail, and research-only policy.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from src.evaluation.shadow_capture_plan import ShadowCapturePlan
from src.evaluation.market_economics import expected_profit_per_unit, fair_over_probability
from src.evaluation.shadow_ledger import ShadowEntry, ShadowResolution
from src.evaluation.shadow_prediction_snapshot import load_shadow_prediction_snapshot
from src.evaluation.shadow_target_capture import (
    TargetCaptureBundle,
    load_resolved_quotes,
    load_target_capture_bundle,
)
from src.utils.provenance import sha256_file


POLICY_SCHEMA_VERSION = "shadow-research-selection-policy-v1"
OUTCOME_SCHEMA_VERSION = "shadow-official-hits-outcome-v1"


class ShadowProviderAdapterError(ValueError):
    """Raised when retained provider/model/outcome evidence does not agree."""


def _json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ShadowProviderAdapterError(f"{label} does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ShadowProviderAdapterError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ShadowProviderAdapterError(f"{label} root must be an object")
    return value


def _positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ShadowProviderAdapterError(f"{label} must be a positive integer") from exc
    if parsed <= 0:
        raise ShadowProviderAdapterError(f"{label} must be a positive integer")
    return parsed


@dataclass(frozen=True)
class ResearchSelectionPolicy:
    policy_id: str
    sportsbook: str
    category: str
    selection_mode: Literal["fixed_side", "max_positive_posted_ev"]
    selection_side: Literal["over", "under"] | None
    entry_hours: int
    policy_sha256: str


def load_research_selection_policy(
    path: str | Path,
    *,
    expected_sha256: str,
) -> ResearchSelectionPolicy:
    """Load a fixed, research-only side policy bound by the capture plan."""

    source = Path(path).resolve()
    payload = _json_object(source, "selection policy")
    digest = sha256_file(source)
    if digest != expected_sha256:
        raise ShadowProviderAdapterError("selection policy hash differs from the capture plan")
    if payload.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise ShadowProviderAdapterError("selection policy has an unknown schema")
    if payload.get("status") != "RESEARCH_ONLY" or payload.get("betting_authorized") is not False:
        raise ShadowProviderAdapterError("selection policy must remain explicitly research-only")
    policy_id = str(payload.get("policy_id", "")).strip()
    sportsbook = str(payload.get("sportsbook", "")).strip().lower()
    category = str(payload.get("category", "")).strip()
    mode = str(payload.get("selection_mode", "fixed_side")).strip().lower()
    raw_side = payload.get("selection_side")
    side = str(raw_side).strip().lower() if raw_side is not None else ""
    if not policy_id:
        raise ShadowProviderAdapterError("selection policy_id cannot be blank")
    if sportsbook != "draftkings" or category != "hits":
        raise ShadowProviderAdapterError("this adapter lane accepts DraftKings Hits only")
    if mode not in {"fixed_side", "max_positive_posted_ev"}:
        raise ShadowProviderAdapterError("selection policy has an unknown selection_mode")
    if mode == "fixed_side" and side not in {"over", "under"}:
        raise ShadowProviderAdapterError("fixed-side policy must declare over or under")
    if mode == "max_positive_posted_ev" and side:
        raise ShadowProviderAdapterError("max-positive-EV policy must not preselect a side")
    entry_hours = _positive_int(payload.get("entry_hours", 4), "entry_hours")
    return ResearchSelectionPolicy(
        policy_id=policy_id,
        sportsbook=sportsbook,
        category=category,
        selection_mode=mode,  # type: ignore[arg-type]
        selection_side=(side if side else None),  # type: ignore[arg-type]
        entry_hours=entry_hours,
        policy_sha256=digest,
    )


def _selection_side(
    policy: ResearchSelectionPolicy,
    *,
    model_p_over: float,
    over_odds_american: int,
    under_odds_american: int,
) -> Literal["over", "under"]:
    """Apply the locked research policy without an inherited edge threshold."""

    if policy.selection_mode == "fixed_side":
        assert policy.selection_side in {"over", "under"}
        return policy.selection_side

    fair_over = fair_over_probability(over_odds_american, under_odds_american)
    candidates = {
        "over": (
            expected_profit_per_unit(model_p_over, over_odds_american),
            model_p_over - fair_over,
        ),
        "under": (
            expected_profit_per_unit(1.0 - model_p_over, under_odds_american),
            fair_over - model_p_over,
        ),
    }
    eligible = {
        side: values
        for side, values in candidates.items()
        if values[0] > 0.0 and values[1] > 0.0
    }
    if not eligible:
        raise ShadowProviderAdapterError(
            "quote has no side with positive posted-price EV and positive de-vigged edge"
        )
    return max(eligible, key=lambda side: eligible[side][0])  # type: ignore[return-value]


def build_shadow_entry_from_capture(
    *,
    bundle_path: str | Path,
    selection_policy_path: str | Path,
    quote_sha256: str,
) -> ShadowEntry:
    """Build one exact ledger entry; row order and heuristic confidence are unused."""

    bundle = load_target_capture_bundle(bundle_path)
    plan = ShadowCapturePlan.from_mapping(bundle.plan)
    policy = load_research_selection_policy(
        selection_policy_path,
        expected_sha256=plan.policy_sha256,
    )
    if bundle.attempt.get("outcome") != "captured":
        raise ShadowProviderAdapterError("a no-market/source-error receipt cannot create a ledger entry")

    quotes = load_resolved_quotes(bundle.resolved_quote_artifact_path)
    matches = [quote for quote in quotes if quote.quote_sha256 == quote_sha256]
    if len(matches) != 1:
        raise ShadowProviderAdapterError("quote hash is absent or ambiguous in the target capture")
    quote = matches[0]
    if quote.quote_sha256 not in bundle.quote_sha256:
        raise ShadowProviderAdapterError("quote is not bound by the target capture bundle")
    if quote.sportsbook != policy.sportsbook or quote.category != policy.category:
        raise ShadowProviderAdapterError("quote market differs from the locked selection policy")

    prediction = load_shadow_prediction_snapshot(bundle.prediction_artifact_path)
    model_p_over = prediction.probability_for(
        quote.mlb_game_pk,
        quote.player_id,
        quote.category,
        quote.line,
    )
    selection_side = _selection_side(
        policy,
        model_p_over=model_p_over,
        over_odds_american=quote.over_odds_american,
        under_odds_american=quote.under_odds_american,
    )
    target = bundle.target
    return ShadowEntry(
        mlb_game_pk=quote.mlb_game_pk,
        player_id=quote.player_id,
        game_date=quote.game_date,
        official_start_time_utc=quote.official_start_time_utc,
        sportsbook=quote.sportsbook,
        category=quote.category,
        line=float(quote.line),
        entry_target_at_utc=target["entry_target_at_utc"],
        entry_quote_at_utc=quote.source_quote_at_utc,
        entry_over_odds_american=quote.over_odds_american,
        entry_under_odds_american=quote.under_odds_american,
        model_p_over=model_p_over,
        selection_side=selection_side,
        selection_policy_id=policy.policy_id,
        selection_policy_sha256=policy.policy_sha256,
        model_version=prediction.model_version,
        config_sha256=prediction.effective_config_sha256,
        code_sha256=prediction.code_snapshot_sha256,
        prediction_artifact_sha256=prediction.prediction_artifact_sha256,
        quote_artifact_sha256=bundle.resolved_quote_artifact_sha256,
    )


def build_shadow_entry_decision(
    *,
    bundle_path: str | Path,
    selection_policy_path: str | Path,
    quote_sha256: str,
) -> tuple[ShadowEntry | None, str]:
    """Return one explicit research decision for every resolved quote.

    A quote with no positive posted-price EV and positive de-vigged edge is a
    named no-selection, not an exception that can disappear from the funnel.
    All identity, provenance, policy, and schema failures remain hard failures.
    """

    try:
        entry = build_shadow_entry_from_capture(
            bundle_path=bundle_path,
            selection_policy_path=selection_policy_path,
            quote_sha256=quote_sha256,
        )
    except ShadowProviderAdapterError as exc:
        if str(exc) == "quote has no side with positive posted-price EV and positive de-vigged edge":
            return None, "no_positive_posted_ev_and_devigged_edge"
        raise
    return entry, "selected_research_only"


def _official_hits_disposition(path: Path, entry: ShadowEntry) -> tuple[dict[str, Any], str]:
    payload = _json_object(path, "official outcome artifact")
    if payload.get("schema_version") != OUTCOME_SCHEMA_VERSION:
        raise ShadowProviderAdapterError("official outcome artifact has an unknown schema")
    status = str(payload.get("settlement_status", ""))
    if payload.get("game_status") != "final" or status not in {"graded", "void", "unscored"}:
        raise ShadowProviderAdapterError("official outcome must be final and graded, void, or unscored")
    expected: Mapping[str, Any] = {
        "mlb_game_pk": entry.mlb_game_pk,
        "player_id": entry.player_id,
        "game_date": entry.game_date,
        "category": entry.category,
        "line": entry.line,
        "selection_side": entry.selection_side,
    }
    for field, value in expected.items():
        if payload.get(field) != value:
            raise ShadowProviderAdapterError(f"official outcome {field} differs from the entry")
    rule_hash = str(payload.get("settlement_rule_artifact_sha256", ""))
    if len(rule_hash) != 64 or any(char not in "0123456789abcdef" for char in rule_hash.lower()):
        raise ShadowProviderAdapterError("official outcome lacks a settlement-rule artifact hash")
    feed_hash = str(payload.get("official_game_feed_artifact_sha256", ""))
    if len(feed_hash) != 64 or any(char not in "0123456789abcdef" for char in feed_hash.lower()):
        raise ShadowProviderAdapterError("official outcome lacks a retained MLB final-feed hash")
    if status != "graded":
        if payload.get("actual_value") not in (None, ""):
            raise ShadowProviderAdapterError(f"{status} official disposition cannot carry actual_value")
        if not str(payload.get("reason", "")).strip():
            raise ShadowProviderAdapterError(f"{status} official disposition requires a reason")
        return payload, sha256_file(path)
    if payload.get("official_starter") is not True or _positive_int(payload.get("official_pa"), "official_pa") < 1:
        raise ShadowProviderAdapterError("graded Hits outcome lacks the declared starter/PA base-rule evidence")
    try:
        actual = float(payload.get("actual_value"))
    except (TypeError, ValueError) as exc:
        raise ShadowProviderAdapterError("official Hits actual_value must be numeric") from exc
    if not math.isfinite(actual) or actual < 0 or not actual.is_integer():
        raise ShadowProviderAdapterError("official Hits actual_value must be a non-negative integer")
    return payload, sha256_file(path)


def _close_for_entry(
    *,
    entry: ShadowEntry,
    close_raw_provider_artifact: str | Path | None,
    close_resolved_quote_artifact: str | Path | None,
    close_quote_sha256: str | None,
):
    values = (
        close_raw_provider_artifact,
        close_resolved_quote_artifact,
        close_quote_sha256,
    )
    if all(value in (None, "") for value in values):
        return None
    if any(value in (None, "") for value in values):
        raise ShadowProviderAdapterError("close evidence requires raw, resolved, and quote hash together")
    raw_path = Path(str(close_raw_provider_artifact)).resolve()
    resolved_path = Path(str(close_resolved_quote_artifact)).resolve()
    if not raw_path.is_file():
        raise ShadowProviderAdapterError(f"close raw provider artifact does not exist: {raw_path}")
    raw_sha = sha256_file(raw_path)
    close_quotes = load_resolved_quotes(resolved_path)
    matches = [quote for quote in close_quotes if quote.quote_sha256 == close_quote_sha256]
    if len(matches) != 1:
        raise ShadowProviderAdapterError("close quote hash is absent or ambiguous")
    close = matches[0]
    if close.source_payload_sha256 != raw_sha:
        raise ShadowProviderAdapterError("close quote is not bound to the retained raw provider artifact")
    if (
        close.mlb_game_pk != entry.mlb_game_pk
        or close.player_id != entry.player_id
        or close.game_date != entry.game_date
        or close.official_start_time_utc != entry.official_start_time_utc
        or close.sportsbook != entry.sportsbook
        or close.category != entry.category
        or float(close.line) != entry.line
    ):
        raise ShadowProviderAdapterError("close quote MARKET_KEY differs from the ledger entry")
    return close, resolved_path


def build_hits_resolution(
    *,
    entry: ShadowEntry,
    settled_at_utc: str,
    official_outcome_artifact: str | Path,
    close_raw_provider_artifact: str | Path | None = None,
    close_resolved_quote_artifact: str | Path | None = None,
    close_quote_sha256: str | None = None,
) -> ShadowResolution:
    """Build one graded/void/unscored resolution from retained official facts."""

    outcome_path = Path(official_outcome_artifact).resolve()
    disposition, outcome_sha = _official_hits_disposition(outcome_path, entry)
    close_result = _close_for_entry(
        entry=entry,
        close_raw_provider_artifact=close_raw_provider_artifact,
        close_resolved_quote_artifact=close_resolved_quote_artifact,
        close_quote_sha256=close_quote_sha256,
    )
    close_fields: dict[str, Any] = {}
    if close_result is not None:
        close, resolved_path = close_result
        close_fields = {
            "close_quote_at_utc": close.source_quote_at_utc,
            "close_over_odds_american": close.over_odds_american,
            "close_under_odds_american": close.under_odds_american,
            "close_quote_artifact_sha256": sha256_file(resolved_path),
        }
    status = str(disposition["settlement_status"])
    if status == "graded" and close_result is None:
        return ShadowResolution(
            entry_id=entry.entry_id,
            settlement_status="unscored",
            settled_at_utc=settled_at_utc,
            settlement_evidence_artifact_sha256=outcome_sha,
            reason="missing_prestart_reference_for_officially_graded_entry",
        )
    if status == "graded":
        return ShadowResolution(
            entry_id=entry.entry_id,
            settlement_status="graded",
            settled_at_utc=settled_at_utc,
            **close_fields,
            official_actual_value=float(disposition["actual_value"]),
            official_outcome_artifact_sha256=outcome_sha,
        )
    return ShadowResolution(
        entry_id=entry.entry_id,
        settlement_status=status,  # type: ignore[arg-type]
        settled_at_utc=settled_at_utc,
        **close_fields,
        settlement_evidence_artifact_sha256=outcome_sha,
        reason=str(disposition["reason"]),
    )


def build_graded_hits_resolution(
    *,
    entry: ShadowEntry,
    settled_at_utc: str,
    close_raw_provider_artifact: str | Path,
    close_resolved_quote_artifact: str | Path,
    close_quote_sha256: str,
    official_outcome_artifact: str | Path,
) -> ShadowResolution:
    """Build one linked graded resolution from retained close and official evidence."""

    resolution = build_hits_resolution(
        entry=entry,
        settled_at_utc=settled_at_utc,
        close_raw_provider_artifact=close_raw_provider_artifact,
        close_resolved_quote_artifact=close_resolved_quote_artifact,
        close_quote_sha256=close_quote_sha256,
        official_outcome_artifact=official_outcome_artifact,
    )
    if resolution.settlement_status != "graded":
        raise ShadowProviderAdapterError("official disposition did not produce a graded resolution")
    return resolution
