"""Integrated, receipt-bound shared-PA research candidate.

This module is deliberately an adapter over the independently retained
projected-opportunity evidence records.  It does not fetch data, infer an MLB
identity from a name, or fall back to the frozen simulator.  If a required
receipt-bound input is absent, the caller gets a typed abstention.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.shared_pa_forward_evidence import (
    PA_OUTCOMES,
    derive_market_distributions,
    pa_distribution_sha256,
    sha256_value,
)


MODEL_ID = "shared_pa_candidate_v1"
FROZEN_MODEL_ID = "frozen_baseline"
SCHEMA_VERSION = "integrated-shared-pa-candidate-v1"
MAY_FIRST = date(2026, 5, 1)
MAY_LAST = date(2026, 5, 31)
BLOCKED_PA_VOLUME_SHA256S = {
    # Its bytes are internally stable, but PR #47 established that its upstream
    # source was a machine-specific mixed-season aggregate rather than a
    # reproducible official 2023 release.  A matching hash is not qualification.
    "7ffd6a8fecb1c4f8aed1966c234a61499050346f87f884f731c440daca793c90",
}


class CandidateEvidenceError(ValueError):
    """The supplied evidence cannot lawfully enter candidate probabilities."""


@dataclass(frozen=True)
class CandidateAbstention:
    reason_code: str
    detail: str
    mlb_game_pk: int | None = None
    team_id: int | None = None
    side: str | None = None
    player_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "terminal_state": "abstained",
            "reason_code": self.reason_code,
            "detail": self.detail,
            "mlb_game_pk": self.mlb_game_pk,
            "team_id": self.team_id,
            "side": self.side,
            "player_id": self.player_id,
        }


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise CandidateEvidenceError(f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateEvidenceError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise CandidateEvidenceError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CandidateEvidenceError(f"{label} must be a positive integer")
    return value


def _record_hash(record: Mapping[str, Any]) -> str:
    unsigned = dict(record)
    unsigned.pop("candidate_record_sha256", None)
    return sha256_value(unsigned)


def _side_hash(bundle: Mapping[str, Any]) -> str:
    unsigned = dict(bundle)
    unsigned.pop("side_bundle_sha256", None)
    return sha256_value(unsigned)


def _binomial_mixture(q: float, support: list[int], mass: list[float]) -> list[float]:
    max_pa = max(support)
    result = [0.0] * (max_pa + 1)
    for pa, weight in zip(support, mass):
        for k in range(pa + 1):
            result[k] += weight * math.comb(pa, k) * q**k * (1.0 - q) ** (pa - k)
    if any(not math.isfinite(value) or value < -1e-14 for value in result):
        raise CandidateEvidenceError("derived count distribution is invalid")
    if not math.isclose(sum(result), 1.0, rel_tol=0.0, abs_tol=1e-11):
        raise CandidateEvidenceError("derived count distribution does not sum to one")
    return result


def _mean(pmf: list[float]) -> float:
    return sum(index * probability for index, probability in enumerate(pmf))


def _tails(pmf: list[float]) -> dict[str, float]:
    return {
        f"over_{threshold - 0.5:.1f}": 1.0 - sum(pmf[:threshold])
        for threshold in range(1, min(len(pmf), 7))
    }


def _market(name: str, pmf: list[float]) -> dict[str, Any]:
    return {
        "market": name,
        "mean": _mean(pmf),
        "pmf": pmf,
        "threshold_probabilities": _tails(pmf),
        "probability_bounds": None,
        "probability_bounds_status": "unavailable_no_qualified_parameter_uncertainty",
    }


def validate_and_project_record(record: Mapping[str, Any], *, expected_date: str) -> dict[str, Any]:
    """Validate a retained candidate record and expose five exact markets."""
    if record.get("schema_version") != "shared-pa-source-bound-opportunity-player-v3":
        raise CandidateEvidenceError("candidate record schema is not source-bound v3")
    if record.get("candidate_id") != MODEL_ID:
        raise CandidateEvidenceError("candidate record model identity differs")
    if record.get("candidate_record_sha256") != _record_hash(record):
        raise CandidateEvidenceError("candidate record hash differs")
    official_date = date.fromisoformat(str(record.get("official_game_date", "")))
    if MAY_FIRST <= official_date <= MAY_LAST:
        raise CandidateEvidenceError("May 2026 is sealed")
    if official_date.isoformat() != expected_date:
        raise CandidateEvidenceError("candidate record date differs from requested slate")
    generated = _parse_utc(record.get("prediction_generated_at_utc"), "prediction_generated_at_utc")
    horizon = _parse_utc(record.get("target_horizon_utc"), "target_horizon_utc")
    start = _parse_utc(record.get("official_start_utc"), "official_start_utc")
    if generated > horizon or horizon >= start:
        raise CandidateEvidenceError("prediction chronology is ineligible")
    stats_cutoff = date.fromisoformat(str(record.get("stats_cutoff_date", "")))
    if stats_cutoff >= official_date:
        raise CandidateEvidenceError("stats cutoff is not strictly before the target date")
    stats_age_days = (official_date - stats_cutoff).days
    player_id = _positive_int(record.get("player_id"), "player_id")
    game_pk = _positive_int(record.get("mlb_game_pk"), "mlb_game_pk")
    team_id = _positive_int(record.get("team_id"), "team_id")
    side = record.get("side")
    if side not in {"home", "away"}:
        raise CandidateEvidenceError("side must be home or away")
    required_hashes = (
        "source_manifest_sha256",
        "runtime_release_receipt_sha256",
        "evidence_envelope_sha256",
        "candidate_protocol_sha256",
    )
    for label in required_hashes:
        value = record.get(label)
        if not isinstance(value, str) or len(value) != 64:
            raise CandidateEvidenceError(f"{label} is missing")
    if record.get("source_authority_state") != "QUALIFIED_2023_OFFICIAL_SOURCE_RELEASE":
        raise CandidateEvidenceError("opportunity source authority is not qualified")
    pa_volume_sha256 = record.get("pa_volume_artifact_sha256")
    if not isinstance(pa_volume_sha256, str) or len(pa_volume_sha256) != 64:
        raise CandidateEvidenceError("PA-volume artifact identity is missing")
    if pa_volume_sha256 in BLOCKED_PA_VOLUME_SHA256S:
        raise CandidateEvidenceError("blocked PA-volume artifact cannot enter candidate probabilities")
    pa_source_manifest = record.get("pa_volume_source_manifest_sha256")
    if not isinstance(pa_source_manifest, str) or len(pa_source_manifest) != 64:
        raise CandidateEvidenceError("PA-volume source manifest identity is missing")
    for label in ("stats_raw_sha256s", "stats_transport_receipt_sha256s"):
        values = record.get(label)
        if not isinstance(values, list) or not values or any(
            not isinstance(value, str) or len(value) != 64 for value in values
        ):
            raise CandidateEvidenceError(f"{label} is incomplete")
    support = record.get("candidate_pa_support")
    mass = record.get("candidate_pa_mass")
    if not isinstance(support, list) or not isinstance(mass, list):
        raise CandidateEvidenceError("candidate PA distribution is missing")
    if record.get("candidate_pa_distribution_sha256") != pa_distribution_sha256(
        support=support, mass=mass
    ):
        raise CandidateEvidenceError("candidate PA distribution hash differs")
    per_pa = record.get("per_pa_probability")
    if not isinstance(per_pa, Mapping) or set(per_pa) != set(PA_OUTCOMES):
        raise CandidateEvidenceError("per-PA outcome schema differs")
    start_probability = float(record.get("projected_start_probability", -1.0))
    if not math.isfinite(start_probability) or not 0.0 <= start_probability <= 1.0:
        raise CandidateEvidenceError("projected start probability is invalid")
    unconditional = record.get("projected_slot_probability_unconditional")
    conditional = record.get("projected_slot_probability_given_start")
    if not isinstance(unconditional, Mapping) or not isinstance(conditional, Mapping):
        raise CandidateEvidenceError("projected slot distributions are missing")
    unconditional_values = {
        int(slot): float(probability) for slot, probability in unconditional.items()
    }
    slot_values = {int(slot): float(probability) for slot, probability in conditional.items()}
    if (
        set(unconditional_values) != set(range(1, 10))
        or set(slot_values) != set(range(1, 10))
        or any(
            not math.isfinite(value) or value < 0.0
            for value in [*unconditional_values.values(), *slot_values.values()]
        )
        or not math.isclose(
            sum(unconditional_values.values()), start_probability,
            rel_tol=0.0, abs_tol=1e-10,
        )
        or (
            start_probability > 0.0
            and not math.isclose(sum(slot_values.values()), 1.0, rel_tol=0.0, abs_tol=1e-10)
        )
        or (
            start_probability == 0.0
            and not math.isclose(sum(slot_values.values()), 0.0, rel_tol=0.0, abs_tol=1e-10)
        )
        or any(
            not math.isclose(
                unconditional_values[slot],
                start_probability * slot_values[slot],
                rel_tol=0.0,
                abs_tol=1e-10,
            )
            for slot in range(1, 10)
        )
    ):
        raise CandidateEvidenceError("projected slot distribution is invalid")
    recomputed = derive_market_distributions(
        per_pa_probability=per_pa, support=support, mass=mass
    )
    if record.get("candidate_market_distributions") != recomputed:
        raise CandidateEvidenceError("candidate market distributions do not replay")
    strikeouts = _binomial_mixture(float(per_pa["strikeout"]), support, mass)
    walks = _binomial_mixture(float(per_pa["walk"]), support, mass)
    markets = {
        "hits": _market("hits", recomputed["hits_pmf"]),
        "home_runs": _market("home_runs", recomputed["home_runs_pmf"]),
        "total_bases": _market("total_bases", recomputed["total_bases_pmf"]),
        "hitter_strikeouts": _market("hitter_strikeouts", strikeouts),
        "hitter_walks": _market("hitter_walks", walks),
    }
    return {
        "model_id": MODEL_ID,
        "terminal_state": "prediction_complete",
        "research_only": True,
        "betting_authorized": False,
        "official_game_date": official_date.isoformat(),
        "prediction_generated_at_utc": generated.isoformat().replace("+00:00", "Z"),
        "decision_horizon_utc": horizon.isoformat().replace("+00:00", "Z"),
        "mlb_game_pk": game_pk,
        "team_id": team_id,
        "side": side,
        "player_id": player_id,
        "projected_start_probability": start_probability,
        "projected_slot_probability": {str(slot): slot_values[slot] for slot in range(1, 10)},
        "projected_slot_probability_unconditional": {
            str(slot): unconditional_values[slot] for slot in range(1, 10)
        },
        "pa_support": support,
        "pa_mass": mass,
        "opportunity_mean_pa": sum(pa * weight for pa, weight in zip(support, mass)),
        "per_pa_probability": dict(per_pa),
        "markets": markets,
        "input_health": {
            "status": "receipt_bound_candidate_record_validated",
            "pitcher_matchup": "excluded_batter_only",
            "mutable_savant_override": "excluded",
            "full_raw_receipt_replay": "required_at_producer_boundary",
            "stats_age_days": stats_age_days,
        },
        "source_receipts": {
            label: record[label] for label in required_hashes
        },
        "source_candidate_id": record.get("candidate_id"),
        "source_candidate_record_sha256": record["candidate_record_sha256"],
    }


def load_candidate_archive(path: Path, *, expected_date: str) -> dict[str, Any]:
    """Load one or more side bundles without inventing missing predictions."""
    source_paths = sorted(path.glob("*.json")) if path.is_dir() else [path]
    if not source_paths:
        raise CandidateEvidenceError("candidate evidence path contains no JSON files")
    predictions: list[dict[str, Any]] = []
    abstentions: list[dict[str, Any]] = []
    seen: set[tuple[int, str, int]] = set()
    input_hashes: list[dict[str, Any]] = []
    for source in source_paths:
        raw = source.read_bytes()
        loaded = json.loads(raw.decode("utf-8-sig"))
        bundles = loaded if isinstance(loaded, list) else [loaded]
        for bundle in bundles:
            if not isinstance(bundle, Mapping):
                raise CandidateEvidenceError("candidate evidence entry is not an object")
            if "candidate_records" not in bundle:
                raise CandidateEvidenceError("candidate evidence is not a retained side bundle")
            if bundle.get("side_bundle_sha256") != _side_hash(bundle):
                raise CandidateEvidenceError("side bundle hash differs")
            if str(bundle.get("official_game_date")) != expected_date:
                raise CandidateEvidenceError("side bundle date differs from requested slate")
            game_pk = _positive_int(bundle.get("mlb_game_pk"), "bundle.mlb_game_pk")
            team_id = _positive_int(bundle.get("team_id"), "bundle.team_id")
            side = str(bundle.get("side"))
            key = (game_pk, side, team_id)
            if key in seen:
                raise CandidateEvidenceError("duplicate game/team/side bundle")
            seen.add(key)
            for record in bundle.get("candidate_records", []):
                projected = validate_and_project_record(record, expected_date=expected_date)
                if (projected["mlb_game_pk"], projected["side"], projected["team_id"]) != key:
                    raise CandidateEvidenceError("candidate record contradicts side identity")
                predictions.append(projected)
            for item in bundle.get("abstentions", []):
                abstentions.append(dict(item))
            input_hashes.append({
                "path": str(source.resolve()),
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
    identities = [(p["mlb_game_pk"], p["side"], p["team_id"], p["player_id"]) for p in predictions]
    if len(identities) != len(set(identities)):
        raise CandidateEvidenceError("duplicate hard player identity")
    implementation_path = Path(__file__)
    upstream_path = Path(__file__).resolve().parents[1] / "evaluation" / "shared_pa_forward_evidence.py"
    code_manifest = [
        {
            "path": str(candidate.resolve()),
            "size": candidate.stat().st_size,
            "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        }
        for candidate in (implementation_path, upstream_path)
    ]
    feature_schema = {
        "identity": ["mlb_game_pk", "side", "team_id", "player_id"],
        "opportunity": ["projected_start_probability", "projected_slot_probability", "pa_support", "pa_mass"],
        "pa_outcomes": list(PA_OUTCOMES),
        "excluded": ["mutable_savant_override", "direct_bvp", "unreceipted_pitcher_matchup"],
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "game_date": expected_date,
        "research_only": True,
        "betting_authorized": False,
        "predictions": predictions,
        "abstentions": abstentions,
        "coverage": {
            "predicted_players": len(predictions),
            "abstained_players": len(abstentions),
        },
        "input_manifest": input_hashes,
        "code_manifest": code_manifest,
        "feature_schema": feature_schema,
        "feature_schema_sha256": sha256_value(feature_schema),
        "model_config_sha256s": sorted({
            prediction["source_receipts"]["candidate_protocol_sha256"] for prediction in predictions
        }),
        "deferred_markets": {
            "pitcher_markets": "no qualified receipt-bound joint pitcher-opportunity artifact",
            "rbi_hrr_earned_runs": "no qualified point-in-time ordered run-context state",
        },
    }
    return {**payload, "archive_sha256": sha256_value(payload)}


def unavailable_candidate_archive(*, game_date: str, reason_code: str, detail: str) -> dict[str, Any]:
    """Publish an explicit slate-level missingness state without a fallback."""
    parsed = date.fromisoformat(game_date)
    if MAY_FIRST <= parsed <= MAY_LAST:
        raise CandidateEvidenceError("May 2026 is sealed")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "game_date": parsed.isoformat(),
        "research_only": True,
        "betting_authorized": False,
        "predictions": [],
        "abstentions": [CandidateAbstention(reason_code=reason_code, detail=detail).to_dict()],
        "coverage": {"predicted_players": 0, "abstained_players": 1},
        "input_manifest": [],
        "deferred_markets": {
            "all_candidate_markets": "required qualified opportunity evidence is unavailable"
        },
    }
    return {**payload, "archive_sha256": sha256_value(payload)}


def atomic_write_json(payload: Mapping[str, Any], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination
