"""Official MLB facts -> fail-closed DraftKings Hits reference disposition."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.shadow_ledger import ShadowEntry
from src.evaluation.shadow_provider_adapter import OUTCOME_SCHEMA_VERSION
from src.utils.provenance import sha256_file


SETTLEMENT_SCHEMA = "shadow-draftkings-hits-reference-settlement-v1"


class ShadowOfficialHitsError(ValueError):
    """Raised when official facts or the locked reference rule are invalid."""


def load_reference_settlement(path: str | Path) -> tuple[dict[str, Any], str]:
    source = Path(path).resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ShadowOfficialHitsError("reference settlement artifact is malformed") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SETTLEMENT_SCHEMA:
        raise ShadowOfficialHitsError("reference settlement artifact has an unknown schema")
    if (
        payload.get("status") != "RESEARCH_ONLY_MODELED_REFERENCE_SETTLEMENT"
        or payload.get("betting_authorized") is not False
        or payload.get("sportsbook") != "draftkings"
        or payload.get("category") != "hits"
    ):
        raise ShadowOfficialHitsError("reference settlement artifact changed scope or authorization")
    evidence = Path(str(payload.get("rule_evidence_path", "")))
    if not evidence.is_absolute():
        evidence = (Path.cwd() / evidence).resolve()
    if not evidence.is_file() or sha256_file(evidence) != payload.get("rule_evidence_sha256"):
        raise ShadowOfficialHitsError("published DraftKings rule evidence is missing or tampered")
    return payload, sha256_file(source)


def official_hits_disposition(
    *,
    entry: ShadowEntry,
    official_role: Mapping[str, Any] | None,
    official_pa: int | None,
    official_hits: int | None,
    regular_game_completed: bool,
    settlement_rule_artifact: str | Path,
    official_game_feed_artifact_sha256: str,
) -> dict[str, Any]:
    """Classify one entry without turning missing official facts into a loss."""

    _, rule_sha = load_reference_settlement(settlement_rule_artifact)
    feed_sha = str(official_game_feed_artifact_sha256).strip().lower()
    if len(feed_sha) != 64 or any(char not in "0123456789abcdef" for char in feed_sha):
        raise ShadowOfficialHitsError("official game feed artifact hash must be SHA-256")
    base = {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "mlb_game_pk": entry.mlb_game_pk,
        "player_id": entry.player_id,
        "game_date": entry.game_date,
        "category": entry.category,
        "line": entry.line,
        "selection_side": entry.selection_side,
        "game_status": "final",
        "regular_game_completed": bool(regular_game_completed),
        "settlement_rule_artifact_sha256": rule_sha,
        "settlement_interpretation": "modeled_draftkings_reference_not_observed_execution_product_settlement",
        "official_source": "MLB Stats API game-keyed final feed",
        "official_game_feed_artifact_sha256": feed_sha,
        "betting_authorized": False,
    }
    if not regular_game_completed:
        return {
            **base,
            "settlement_status": "unscored",
            "reason": "official_game_not_verified_as_completed_regular_nine_inning_game",
            "official_starter": None,
            "official_pa": official_pa,
            "actual_value": None,
        }
    if official_role is None or official_pa is None or official_hits is None:
        return {
            **base,
            "settlement_status": "unscored",
            "reason": "missing_or_ambiguous_official_player_role_or_boxscore",
            "official_starter": None if official_role is None else bool(official_role.get("is_starter")),
            "official_pa": official_pa,
            "actual_value": None,
        }
    try:
        pa = int(official_pa)
        hits = int(official_hits)
    except (TypeError, ValueError) as exc:
        raise ShadowOfficialHitsError("official PA and Hits must be integers") from exc
    if pa < 0 or hits < 0 or hits > pa:
        raise ShadowOfficialHitsError("official PA/Hits values are impossible")
    starter = bool(official_role.get("is_starter"))
    replaced = bool(official_role.get("starter_replaced_in_slot"))
    facts = {
        "official_starter": starter,
        "official_pa": pa,
        "official_hits": hits,
        "starter_replaced_in_slot": replaced,
        "lineup_slot": official_role.get("lineup_slot"),
        "team_side": official_role.get("team_side"),
    }
    if not starter:
        return {
            **base,
            **facts,
            "settlement_status": "void",
            "reason": "official_nonstarter_substitute",
            "actual_value": None,
        }
    if pa == 0:
        return {
            **base,
            **facts,
            "settlement_status": "void",
            "reason": "official_starter_with_zero_plate_appearances",
            "actual_value": None,
        }
    if (
        entry.selection_side == "over"
        and pa == 1
        and replaced
        and not (float(hits) > entry.line)
    ):
        return {
            **base,
            **facts,
            "settlement_status": "void",
            "reason": "over_side_starter_early_exit_before_second_pa_not_unconditionally_won",
            "actual_value": None,
        }
    return {
        **base,
        **facts,
        "settlement_status": "graded",
        "reason": "",
        "actual_value": hits,
    }
