"""Fail-closed forward evidence for the shared batter PA probability control.

The record validated here contains only inputs observed no later than the
declared T-minus-4 horizon.  It turns one coherent PA-outcome distribution
into Hits, HR-over-0.5, and Total-Bases game distributions without independent
market models.  It deliberately excludes pitcher, price, outcome, settlement,
and policy fields.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


class SharedPAForwardEvidenceError(ValueError):
    """A forward PA record is incomplete, contradictory, late, or unbound."""


PA_OUTCOMES = (
    "strikeout",
    "walk",
    "single",
    "double",
    "triple",
    "home_run",
    "bip_out",
    "other_non_ab",
)
HIT_OUTCOMES = ("single", "double", "triple", "home_run")
TB_VALUES = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
CONTROL_ID = "empirical_bayes_player_rate_pa_200_2023_control_v1"
CONTROL_CONFIG_SHA256 = "033934ec8dc06bc49d02fd46e223f840ef5cef01bb545c09054fe9b740628c6c"
PA_VOLUME_ARTIFACT_SHA256 = "7ffd6a8fecb1c4f8aed1966c234a61499050346f87f884f731c440daca793c90"
PA_DISTRIBUTION_SHA256_BY_SCOPE = {
    "pooled_projected_lineup": "6c892a69f51e64fc118581b3184da9bb18c1ffd4d0d72be1019ac91fde3aafcf",
    "confirmed_slot_1": "9f2e194bb210ddd40d68d7b3b9a70b333333af028b13e5a6b8b26cd3fad94916",
    "confirmed_slot_2": "5f29d631ae316aad152e9e39208492b4a049abf9bf061018118e6220db1b404b",
    "confirmed_slot_3": "a57fe8f10afe709719c77740764f36f12537956e7eb0f6ef458f043a6f351a5d",
    "confirmed_slot_4": "7b4ae35106a9f17e9af57da8d11b1bde1f551c840d46960c40e77493529ba217",
    "confirmed_slot_5": "cd40770cfef3326b82c64837003b4360938791cd81cb7829b1c135d5409d1600",
    "confirmed_slot_6": "5dd6e1ac3726e6459e89aef12bc20a31c584e55e20e005cab3ea9226a4e79516",
    "confirmed_slot_7": "0aaf50d5d2d6afd7059f0b2641a99b9ac60556fd47eec10d00196f2b8a115375",
    "confirmed_slot_8": "af7ad7f575371c9eae24780343b09416ecfb8763407cc7270816dc54a3d6024e",
    "confirmed_slot_9": "65768c609dac0ddffcbbf35b7258b3ff67c7350721d8a77646a91ba08d271c81",
}
LEAGUE_PRIOR_COUNTS = {
    "strikeout": 40005, "walk": 15128, "single": 25128, "double": 7971,
    "triple": 690, "home_run": 5698, "bip_out": 78930, "other_non_ab": 3676,
}
SCHEMA_VERSION = "shared-pa-forward-player-snapshot-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_REQUIRED_FIELDS = {
    "schema_version", "terminal_state", "research_only", "betting_authorized",
    "promotion_eligible", "collector_instance_id", "monotonic_receipt_sequence",
    "receipt_utc", "source_observation_utc", "collector_code_sha256",
    "runtime_manifest_sha256", "plan_sha256", "target_id", "mlb_game_pk",
    "official_game_date", "official_start_utc", "target_horizon_utc", "side",
    "home_team_id", "away_team_id", "game_identity_sha256", "lineup_state",
    "source_lineup_slot", "effective_lineup_slot", "lineup_receipt_utc",
    "raw_lineup_payload_sha256", "player_id", "player_identity_sha256",
    "hard_player_key", "stats_receipt_utc", "raw_stats_payload_sha256",
    "stats_counts", "stats_pa", "stats_season", "control_id", "control_config_sha256",
    "prior_strength_pa", "league_prior_probability", "per_pa_probability",
    "feature_snapshot_sha256", "rate_fallback_labels", "pa_volume_candidate_id",
    "pa_volume_artifact_sha256", "pa_distribution_scope", "pa_support", "pa_mass",
    "pa_distribution_sha256", "expected_pa", "market_distributions",
    "prediction_method", "pitcher_block_status", "policy_status", "snapshot_sha256",
}


@dataclass(frozen=True)
class ForwardPAVolumeArtifact:
    candidate_id: str
    pooled: dict[int, float]
    by_lineup_slot: dict[int, dict[int, float]]


def _pa_mass(value: Any, label: str) -> dict[int, float]:
    if not isinstance(value, Mapping) or not value:
        raise SharedPAForwardEvidenceError(f"{label} must be a non-empty object")
    parsed: dict[int, float] = {}
    for raw_support, raw_probability in value.items():
        try:
            support = int(raw_support)
        except (TypeError, ValueError) as exc:
            raise SharedPAForwardEvidenceError(f"{label} support must be integer") from exc
        if str(support) != str(raw_support) or support < 0:
            raise SharedPAForwardEvidenceError(f"{label} support must be canonical non-negative integers")
        if isinstance(raw_probability, bool) or not isinstance(raw_probability, (int, float)):
            raise SharedPAForwardEvidenceError(f"{label} probability must be numeric")
        probability = float(raw_probability)
        if not math.isfinite(probability) or probability < 0.0:
            raise SharedPAForwardEvidenceError(f"{label} probability must be finite and non-negative")
        parsed[support] = probability
    if not math.isclose(sum(parsed.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise SharedPAForwardEvidenceError(f"{label} probabilities must sum to one")
    return parsed


def _load_forward_pa_volume(path: Path, expected_sha256: Any) -> ForwardPAVolumeArtifact:
    expected = _sha(expected_sha256, "pa_volume.sha256")
    try:
        payload = path.read_bytes()
        raw = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise SharedPAForwardEvidenceError("strict PA-volume artifact is unreadable") from exc
    if hashlib.sha256(payload).hexdigest() != expected:
        raise SharedPAForwardEvidenceError("strict PA-volume artifact hash mismatch")
    if not isinstance(raw, Mapping) or set(raw) != {
        "schema_version", "candidate_id", "chronology", "population",
        "source", "by_lineup_slot", "pooled",
    } or raw.get("schema_version") != "pa-volume-distribution-v2":
        raise SharedPAForwardEvidenceError("strict PA-volume artifact schema changed")
    if raw.get("candidate_id") != "pa_volume_2023_only_v1" or raw.get("population") != "original_sequence_zero_starters":
        raise SharedPAForwardEvidenceError("strict PA-volume candidate identity changed")
    chronology = raw.get("chronology")
    if chronology != {"fit_season": 2023, "selection_season": 2024}:
        raise SharedPAForwardEvidenceError("strict PA-volume chronology changed")
    source = raw.get("source")
    if not isinstance(source, Mapping) or set(source) != {
        "path", "sha256", "projection_sha256", "columns", "fit_rows",
        "fit_games", "date_min", "date_max", "rows_by_lineup_slot",
    }:
        raise SharedPAForwardEvidenceError("strict PA-volume source lineage is incomplete")
    if source.get("columns") != ["game_date", "game_pk", "player_id", "lineup_slot", "out_pa"]:
        raise SharedPAForwardEvidenceError("strict PA-volume source projection changed")
    if source.get("fit_rows") != 43740 or source.get("fit_games") != 2430:
        raise SharedPAForwardEvidenceError("strict PA-volume fit population changed")
    if source.get("date_min") != "2023-03-30" or source.get("date_max") != "2023-10-01":
        raise SharedPAForwardEvidenceError("strict PA-volume fit date boundary changed")
    rows_by_slot = source.get("rows_by_lineup_slot")
    if not isinstance(rows_by_slot, Mapping) or rows_by_slot != {str(slot): 4860 for slot in range(1, 10)}:
        raise SharedPAForwardEvidenceError("strict PA-volume lineup-slot population changed")
    by_slot = raw.get("by_lineup_slot")
    if not isinstance(by_slot, Mapping) or set(by_slot) != {str(slot) for slot in range(1, 10)}:
        raise SharedPAForwardEvidenceError("strict PA-volume slot distributions are incomplete")
    parsed_by_slot = {
        slot: _pa_mass(by_slot[str(slot)], f"pa_volume.by_lineup_slot.{slot}")
        for slot in range(1, 10)
    }
    return ForwardPAVolumeArtifact(
        candidate_id="pa_volume_2023_only_v1",
        pooled=_pa_mass(raw.get("pooled"), "pa_volume.pooled"),
        by_lineup_slot=parsed_by_slot,
    )


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _sha(value: Any, label: str) -> str:
    text = str(value).strip().lower()
    if not _SHA256.fullmatch(text):
        raise SharedPAForwardEvidenceError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise SharedPAForwardEvidenceError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SharedPAForwardEvidenceError(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SharedPAForwardEvidenceError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SharedPAForwardEvidenceError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SharedPAForwardEvidenceError(f"{label} must be a non-negative integer")
    return value


def _probability_mapping(value: Any, label: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(PA_OUTCOMES):
        raise SharedPAForwardEvidenceError(f"{label} must contain exactly the PA outcomes")
    result: dict[str, float] = {}
    for outcome in PA_OUTCOMES:
        raw = value[outcome]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise SharedPAForwardEvidenceError(f"{label}.{outcome} must be numeric")
        probability = float(raw)
        if not math.isfinite(probability) or probability < 0.0 or probability > 1.0:
            raise SharedPAForwardEvidenceError(f"{label}.{outcome} is outside [0,1]")
        result[outcome] = probability
    if not math.isclose(sum(result.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise SharedPAForwardEvidenceError(f"{label} must sum to one")
    return result


def empirical_bayes_pa_probability(
    *, counts: Mapping[str, Any], league_prior: Mapping[str, Any], prior_strength_pa: float
) -> dict[str, float]:
    """The locked player-rate control; no learned or hand-set baseball effects."""
    if set(counts) != set(PA_OUTCOMES):
        raise SharedPAForwardEvidenceError("stats_counts must contain exactly the PA outcomes")
    parsed_counts = {name: _nonnegative_int(counts[name], f"stats_counts.{name}") for name in PA_OUTCOMES}
    prior = _probability_mapping(league_prior, "league_prior_probability")
    if isinstance(prior_strength_pa, bool) or not isinstance(prior_strength_pa, (int, float)):
        raise SharedPAForwardEvidenceError("prior_strength_pa must be numeric")
    strength = float(prior_strength_pa)
    if not math.isfinite(strength) or strength != 200.0:
        raise SharedPAForwardEvidenceError("the locked empirical-Bayes prior must be exactly 200 PA")
    exposure = sum(parsed_counts.values())
    denominator = exposure + strength
    return {
        name: (parsed_counts[name] + strength * prior[name]) / denominator
        for name in PA_OUTCOMES
    }


def derive_market_distributions(
    *, per_pa_probability: Mapping[str, Any], support: list[int], mass: list[float]
) -> dict[str, Any]:
    """Derive mutually consistent full-game hit, HR, and total-base distributions."""
    probability = _probability_mapping(per_pa_probability, "per_pa_probability")
    if not isinstance(support, list) or not isinstance(mass, list) or not support or len(support) != len(mass):
        raise SharedPAForwardEvidenceError("PA support and mass must be aligned non-empty lists")
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in support):
        raise SharedPAForwardEvidenceError("PA support must contain non-negative integers")
    if support != sorted(set(support)):
        raise SharedPAForwardEvidenceError("PA support must be sorted and unique")
    weights: list[float] = []
    for value in mass:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SharedPAForwardEvidenceError("PA mass must be numeric")
        weight = float(value)
        if not math.isfinite(weight) or weight < 0.0:
            raise SharedPAForwardEvidenceError("PA mass must be finite and non-negative")
        weights.append(weight)
    if not math.isclose(sum(weights), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise SharedPAForwardEvidenceError("PA mass must sum to one")

    q_hit = sum(probability[name] for name in HIT_OUTCOMES)
    q_hr = probability["home_run"]
    one_pa_tb = [0.0] * 5
    one_pa_tb[0] = 1.0 - sum(probability[name] for name in TB_VALUES)
    for name, bases in TB_VALUES.items():
        one_pa_tb[bases] = probability[name]

    max_pa = max(support)
    hits = [0.0] * (max_pa + 1)
    home_runs = [0.0] * (max_pa + 1)
    total_bases = [0.0] * (4 * max_pa + 1)
    for pa, mixture in zip(support, weights):
        hit_pmf = [math.comb(pa, k) * q_hit**k * (1.0 - q_hit) ** (pa - k) for k in range(pa + 1)]
        hr_pmf = [math.comb(pa, k) * q_hr**k * (1.0 - q_hr) ** (pa - k) for k in range(pa + 1)]
        tb_pmf = [1.0]
        for _ in range(pa):
            convolved = [0.0] * (len(tb_pmf) + 4)
            for left_index, left_value in enumerate(tb_pmf):
                for right_index, right_value in enumerate(one_pa_tb):
                    convolved[left_index + right_index] += left_value * right_value
            tb_pmf = convolved
        for index, value in enumerate(hit_pmf):
            hits[index] += mixture * value
        for index, value in enumerate(hr_pmf):
            home_runs[index] += mixture * value
        for index, value in enumerate(tb_pmf):
            total_bases[index] += mixture * value

    for label, pmf in (("hits", hits), ("home_runs", home_runs), ("total_bases", total_bases)):
        if any(not math.isfinite(value) or value < -1e-14 for value in pmf) or not math.isclose(
            sum(pmf), 1.0, rel_tol=0.0, abs_tol=1e-11
        ):
            raise SharedPAForwardEvidenceError(f"derived {label} distribution is invalid")
    return {
        "hits_pmf": hits,
        "home_runs_pmf": home_runs,
        "total_bases_pmf": total_bases,
        "tails": {
            "hits_over_0.5": 1.0 - hits[0],
            "hits_over_1.5": 1.0 - sum(hits[:2]),
            "home_runs_over_0.5": 1.0 - home_runs[0],
            **{
                f"total_bases_over_{threshold - 0.5:.1f}": 1.0 - sum(total_bases[:threshold])
                for threshold in range(1, 7)
            },
        },
    }


def pa_distribution_sha256(*, support: list[int], mass: list[float]) -> str:
    return sha256_value({"mass": [float(value) for value in mass], "support": support})


def snapshot_sha256(record: Mapping[str, Any]) -> str:
    unsigned = dict(record)
    unsigned.pop("snapshot_sha256", None)
    return sha256_value(unsigned)


def _bound_path(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise SharedPAForwardEvidenceError(f"{label} path must be a non-empty string")
    base = root.resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise SharedPAForwardEvidenceError(f"{label} path escapes the repository root") from exc
    return candidate


def load_forward_contract(*, root: str | Path, contract_path: str | Path) -> dict[str, Any]:
    """Load and verify the contract, EB control, and 2023 PA-volume artifact."""
    repository = Path(root).resolve()
    source = Path(contract_path).resolve()
    try:
        contract = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SharedPAForwardEvidenceError("shared PA forward contract is unreadable") from exc
    if not isinstance(contract, Mapping):
        raise SharedPAForwardEvidenceError("shared PA forward contract must be an object")
    if contract.get("schema_version") != "shared-pa-forward-evidence-contract-v1" or contract.get("status") != "LOCKED_FORWARD_ONLY_RESEARCH":
        raise SharedPAForwardEvidenceError("shared PA forward contract identity changed")
    if contract.get("decision_horizon") != "T-4h" or contract.get("markets") != [
        "hits", "home_runs_over_0_5", "total_bases"
    ]:
        raise SharedPAForwardEvidenceError("shared PA market or horizon contract changed")
    protected = contract.get("protected_boundaries")
    if not isinstance(protected, Mapping) or any(
        protected.get(key) is not True
        for key in (
            "may_2026_fetch_read_parse_write_forbidden",
            "outcome_fields_forbidden_in_pregame_records",
            "pitcher_block_excluded_until_receipt_contract_passes",
            "prices_and_executability_excluded",
            "operational_smoke_separate_and_non_economic",
            "frozen_production_unchanged",
            "spent_2025_hr_not_new_confirmation",
            "market_pooling_forbidden",
        )
    ) or protected.get("betting_authorized") is not False:
        raise SharedPAForwardEvidenceError("protected research boundaries were weakened")
    publication = contract.get("publication")
    if not isinstance(publication, Mapping) or any(value is not True for value in publication.values()):
        raise SharedPAForwardEvidenceError("forward publication safeguards were weakened")

    control_binding = contract.get("control")
    if not isinstance(control_binding, Mapping) or control_binding.get("candidate_id") != CONTROL_ID:
        raise SharedPAForwardEvidenceError("EB control binding changed")
    control_path = _bound_path(repository, control_binding.get("path"), "control")
    expected_control_sha = _sha(control_binding.get("sha256"), "control.sha256")
    try:
        control_bytes = control_path.read_bytes()
        control = json.loads(control_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise SharedPAForwardEvidenceError("EB control artifact is unreadable") from exc
    if hashlib.sha256(control_bytes).hexdigest() != expected_control_sha:
        raise SharedPAForwardEvidenceError("EB control artifact hash differs")
    if not isinstance(control, Mapping) or control.get("schema_version") != "shared-pa-forward-eb-control-v1" or control.get("candidate_id") != CONTROL_ID:
        raise SharedPAForwardEvidenceError("EB control schema or identity changed")
    if control.get("fit_season") != 2023 or control.get("selection_season") != 2024 or control.get("prior_strength_pa") != 200.0:
        raise SharedPAForwardEvidenceError("EB control chronology or prior changed")
    if control.get("outcome_order") != list(PA_OUTCOMES):
        raise SharedPAForwardEvidenceError("EB control outcome order changed")
    counts = control.get("league_prior_counts")
    if not isinstance(counts, Mapping) or set(counts) != set(PA_OUTCOMES):
        raise SharedPAForwardEvidenceError("EB control prior counts are incomplete")
    parsed_counts = {name: _nonnegative_int(counts[name], f"league_prior_counts.{name}") for name in PA_OUTCOMES}
    if control.get("league_prior_total_pa") != sum(parsed_counts.values()):
        raise SharedPAForwardEvidenceError("EB control prior denominator differs from counts")
    boundaries = control.get("boundaries")
    if not isinstance(boundaries, Mapping) or any(
        boundaries.get(key) is not True
        for key in (
            "batter_only", "pitcher_features_excluded", "lineup_slot_not_a_pa_outcome_feature",
            "may_2026_forbidden", "historical_backfill_forbidden",
        )
    ) or any(
        boundaries.get(key) is not False
        for key in ("market_policy_applied", "betting_authorized", "production_changed")
    ):
        raise SharedPAForwardEvidenceError("EB control governance boundary changed")

    pa_binding = contract.get("pa_volume")
    if not isinstance(pa_binding, Mapping) or pa_binding.get("candidate_id") != "pa_volume_2023_only_v1":
        raise SharedPAForwardEvidenceError("PA-volume binding changed")
    pa_path = _bound_path(repository, pa_binding.get("path"), "pa_volume")
    artifact = _load_forward_pa_volume(pa_path, pa_binding.get("sha256"))
    return {
        "contract": dict(contract),
        "contract_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "control": dict(control),
        "control_sha256": expected_control_sha,
        "league_prior_probability": {
            name: parsed_counts[name] / sum(parsed_counts.values()) for name in PA_OUTCOMES
        },
        "pa_volume_artifact": artifact,
        "pa_volume_sha256": str(pa_binding["sha256"]),
    }


def validate_player_snapshot(record: Mapping[str, Any]) -> None:
    """Validate one complete player snapshot without accessing any outcome."""
    missing = _REQUIRED_FIELDS - set(record)
    unexpected = set(record) - _REQUIRED_FIELDS
    if missing or unexpected:
        raise SharedPAForwardEvidenceError(
            f"player snapshot schema differs: missing={sorted(missing)} unexpected={sorted(unexpected)}"
        )
    if record.get("schema_version") != SCHEMA_VERSION or record.get("terminal_state") != "captured_complete":
        raise SharedPAForwardEvidenceError("player snapshot schema or terminal state is invalid")
    if record.get("research_only") is not True or record.get("betting_authorized") is not False or record.get("promotion_eligible") is not False:
        raise SharedPAForwardEvidenceError("forward control must remain research-only and promotion-ineligible")
    if not isinstance(record.get("collector_instance_id"), str) or not record["collector_instance_id"].strip():
        raise SharedPAForwardEvidenceError("collector instance identity is blank")
    game_pk = _positive_int(record.get("mlb_game_pk"), "mlb_game_pk")
    player_id = _positive_int(record.get("player_id"), "player_id")
    _positive_int(record.get("home_team_id"), "home_team_id")
    _positive_int(record.get("away_team_id"), "away_team_id")
    _positive_int(record.get("monotonic_receipt_sequence"), "monotonic_receipt_sequence")
    side = record.get("side")
    if side not in {"home", "away"}:
        raise SharedPAForwardEvidenceError("side must be home or away")
    expected_key = f"{game_pk}:{side}:{player_id}:shared_pa"
    if record.get("hard_player_key") != expected_key:
        raise SharedPAForwardEvidenceError("hard_player_key does not match game/side/player identity")
    raw_date = record.get("official_game_date")
    try:
        official_date = date.fromisoformat(str(raw_date))
    except ValueError as exc:
        raise SharedPAForwardEvidenceError("official_game_date must be YYYY-MM-DD") from exc
    if official_date.year == 2026 and official_date.month == 5:
        raise SharedPAForwardEvidenceError("May 2026 is sealed")
    start = _utc(record.get("official_start_utc"), "official_start_utc")
    horizon = _utc(record.get("target_horizon_utc"), "target_horizon_utc")
    if horizon != start - timedelta(hours=4):
        raise SharedPAForwardEvidenceError("target horizon must be exact T-minus-4")
    receipt = _utc(record.get("receipt_utc"), "receipt_utc")
    observed = _utc(record.get("source_observation_utc"), "source_observation_utc")
    lineup_receipt = _utc(record.get("lineup_receipt_utc"), "lineup_receipt_utc")
    stats_receipt = _utc(record.get("stats_receipt_utc"), "stats_receipt_utc")
    if any(value > horizon for value in (receipt, observed, lineup_receipt, stats_receipt)):
        raise SharedPAForwardEvidenceError("snapshot contains post-horizon evidence")
    if any(value > receipt for value in (observed, lineup_receipt, stats_receipt)):
        raise SharedPAForwardEvidenceError("source evidence was observed after the record receipt")
    if receipt != max(lineup_receipt, stats_receipt) or observed != receipt:
        raise SharedPAForwardEvidenceError("record receipt is not the latest source observation")
    for field in (
        "collector_code_sha256", "runtime_manifest_sha256", "plan_sha256", "target_id",
        "game_identity_sha256", "raw_lineup_payload_sha256", "player_identity_sha256",
        "raw_stats_payload_sha256", "control_config_sha256", "feature_snapshot_sha256",
        "pa_volume_artifact_sha256", "pa_distribution_sha256", "snapshot_sha256",
    ):
        _sha(record.get(field), field)

    lineup_state = record.get("lineup_state")
    source_slot = record.get("source_lineup_slot")
    effective_slot = record.get("effective_lineup_slot")
    if lineup_state not in {"confirmed", "projected"}:
        raise SharedPAForwardEvidenceError("complete player snapshot needs a confirmed or projected lineup")
    if isinstance(source_slot, bool) or not isinstance(source_slot, int) or not 1 <= source_slot <= 9:
        raise SharedPAForwardEvidenceError("source lineup slot must be 1 through 9")
    scope = record.get("pa_distribution_scope")
    if lineup_state == "confirmed":
        if effective_slot != source_slot or scope != f"confirmed_slot_{source_slot}":
            raise SharedPAForwardEvidenceError("confirmed lineup must consume its exact slot distribution")
    elif effective_slot is not None or scope != "pooled_projected_lineup":
        raise SharedPAForwardEvidenceError("projected lineup must consume the pooled PA distribution")
    if record.get("monotonic_receipt_sequence") != source_slot:
        raise SharedPAForwardEvidenceError("receipt sequence differs from the source lineup order")

    expected_game_identity = sha256_value({
        "mlb_game_pk": game_pk,
        "official_game_date": str(raw_date),
        "official_start_utc": record.get("official_start_utc"),
        "home_team_id": record.get("home_team_id"),
        "away_team_id": record.get("away_team_id"),
    })
    if record.get("game_identity_sha256") != expected_game_identity:
        raise SharedPAForwardEvidenceError("game identity hash differs from the hard game fields")
    if record.get("player_identity_sha256") != sha256_value({
        "mlb_game_pk": game_pk, "side": side, "player_id": player_id
    }):
        raise SharedPAForwardEvidenceError("player identity hash differs from the hard player fields")

    counts = record.get("stats_counts")
    if not isinstance(counts, Mapping):
        raise SharedPAForwardEvidenceError("stats_counts must be an object")
    parsed_counts = {name: _nonnegative_int(counts.get(name), f"stats_counts.{name}") for name in PA_OUTCOMES}
    if set(counts) != set(PA_OUTCOMES):
        raise SharedPAForwardEvidenceError("stats_counts contains unknown or missing outcomes")
    if _nonnegative_int(record.get("stats_pa"), "stats_pa") != sum(parsed_counts.values()):
        raise SharedPAForwardEvidenceError("stats_pa does not equal terminal PA counts")
    if record.get("stats_season") != official_date.year:
        raise SharedPAForwardEvidenceError("stats season differs from the target season")
    raw_strength = record.get("prior_strength_pa")
    if (
        record.get("control_id") != CONTROL_ID
        or isinstance(raw_strength, bool)
        or not isinstance(raw_strength, (int, float))
        or not math.isfinite(float(raw_strength))
        or float(raw_strength) != 200.0
    ):
        raise SharedPAForwardEvidenceError("probability control identity changed")
    prior = _probability_mapping(record.get("league_prior_probability"), "league_prior_probability")
    prior_total = sum(LEAGUE_PRIOR_COUNTS.values())
    expected_prior = {name: LEAGUE_PRIOR_COUNTS[name] / prior_total for name in PA_OUTCOMES}
    if any(not math.isclose(prior[name], expected_prior[name], rel_tol=0.0, abs_tol=1e-15) for name in PA_OUTCOMES):
        raise SharedPAForwardEvidenceError("league prior differs from the locked 2023 control")
    if record.get("control_config_sha256") != CONTROL_CONFIG_SHA256:
        raise SharedPAForwardEvidenceError("control configuration hash differs")
    posterior = empirical_bayes_pa_probability(
        counts=parsed_counts, league_prior=prior, prior_strength_pa=200.0
    )
    actual = _probability_mapping(record.get("per_pa_probability"), "per_pa_probability")
    if any(not math.isclose(actual[name], posterior[name], rel_tol=0.0, abs_tol=1e-12) for name in PA_OUTCOMES):
        raise SharedPAForwardEvidenceError("stored PA probability is not the locked empirical-Bayes control")
    labels = record.get("rate_fallback_labels")
    if not isinstance(labels, list) or any(not isinstance(value, str) or not value for value in labels) or len(labels) != len(set(labels)):
        raise SharedPAForwardEvidenceError("rate fallback labels are invalid")
    expected_labels = ["league_prior_only"] if sum(parsed_counts.values()) == 0 else []
    if labels != expected_labels:
        raise SharedPAForwardEvidenceError(
            "rate fallback labels differ from the exact league-prior-only history state"
        )
    feature_payload = {
        "counts": parsed_counts,
        "league_prior_probability": prior,
        "prior_strength_pa": 200.0,
        "raw_stats_payload_sha256": record.get("raw_stats_payload_sha256"),
        "stats_receipt_utc": record.get("stats_receipt_utc"),
    }
    if record.get("feature_snapshot_sha256") != sha256_value(feature_payload):
        raise SharedPAForwardEvidenceError("feature snapshot hash differs from the consumed stats inputs")

    if record.get("pa_volume_candidate_id") != "pa_volume_2023_only_v1":
        raise SharedPAForwardEvidenceError("PA-volume artifact identity changed")
    if record.get("pa_volume_artifact_sha256") != PA_VOLUME_ARTIFACT_SHA256:
        raise SharedPAForwardEvidenceError("PA-volume artifact hash differs")
    support = record.get("pa_support")
    mass = record.get("pa_mass")
    if not isinstance(support, list) or not isinstance(mass, list):
        raise SharedPAForwardEvidenceError("PA support and mass must be lists")
    derived = derive_market_distributions(
        per_pa_probability=actual, support=support, mass=mass
    )
    if record.get("pa_distribution_sha256") != pa_distribution_sha256(support=support, mass=mass):
        raise SharedPAForwardEvidenceError("PA distribution content hash differs")
    if record.get("pa_distribution_sha256") != PA_DISTRIBUTION_SHA256_BY_SCOPE.get(str(scope)):
        raise SharedPAForwardEvidenceError("PA distribution differs from the locked scope")
    expected_pa = sum(n * float(weight) for n, weight in zip(support, mass))
    raw_expected = record.get("expected_pa")
    if isinstance(raw_expected, bool) or not isinstance(raw_expected, (int, float)) or not math.isclose(
        float(raw_expected), expected_pa, rel_tol=0.0, abs_tol=1e-12
    ):
        raise SharedPAForwardEvidenceError("expected PA differs from consumed PA distribution")
    if record.get("market_distributions") != derived:
        raise SharedPAForwardEvidenceError("stored market distributions do not reproduce from PA inputs")
    if record.get("prediction_method") != "exact_iid_pa_mixture_v1":
        raise SharedPAForwardEvidenceError("prediction method changed")
    if record.get("pitcher_block_status") != "excluded_batter_only" or record.get("policy_status") != "none_research_probability_only":
        raise SharedPAForwardEvidenceError("pitcher or policy input entered the batter-only control")
    if record.get("snapshot_sha256") != snapshot_sha256(record):
        raise SharedPAForwardEvidenceError("snapshot hash does not match serialized probability inputs")
