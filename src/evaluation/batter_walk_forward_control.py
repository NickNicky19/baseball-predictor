"""Fail-closed batter-walk forward CONTROL adapter.

This module consumes one already-validated shared-PA player snapshot and
projects only the walk component through the snapshot's locked PA-volume
mixture.  It has no source-fetch, outcome, pitcher, price, policy, fitting, or
activation behavior.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.evaluation.shared_pa_forward_evidence import (
    snapshot_sha256 as shared_pa_snapshot_sha256,
    validate_player_snapshot,
)


class BatterWalkForwardControlError(ValueError):
    """The walk control contract or immutable record is invalid."""


CONTRACT_SCHEMA = "batter-walk-forward-control-contract-v1"
OUTPUT_SCHEMA = "batter-walk-forward-control-player-v1"
SIDE_SCHEMA = "batter-walk-forward-control-side-terminal-v1"
MARKET_ID = "batter_walks"
METHOD = "exact_iid_walk_pa_mixture_v1"
LINES = [0.5, 1.5]
CONTROL_ID = "empirical_bayes_player_rate_pa_200_2023_control_v1"
CONTROL_SHA256 = "033934ec8dc06bc49d02fd46e223f840ef5cef01bb545c09054fe9b740628c6c"
SOURCE_CONTRACT_SHA256 = "b93827a465e5145d1e725f84cbed53d413f0c2e8601e41021221e3d13d079401"
PA_VOLUME_ID = "pa_volume_2023_only_v1"
PA_VOLUME_SHA256 = "7ffd6a8fecb1c4f8aed1966c234a61499050346f87f884f731c440daca793c90"
EXCLUSION_STATES = {
    "lineup_unavailable",
    "lineup_malformed",
    "source_error",
    "missed_before_horizon",
    "game_identity_ambiguous",
    "raw_schema_changed",
}
EVALUATION_BOUNDARY = {
    "control_role": "required_time_safe_player_empirical_bayes_walk_baseline",
    "future_challenger_must_have_distinct_preoutcome_identity_and_outputs": True,
    "walk_lines_cannot_rescue_each_other": True,
    "other_markets_cannot_rescue_batter_walks": True,
    "missing_required_comparator_means_future_gate_unassessable": True,
    "post_hoc_line_threshold_or_policy_changes_forbidden": True,
}
RELEASE_AUTHORITY = {
    "status": "UNCOMMITTED_RESEARCH_CANDIDATE",
    "source_parent_commit": "9f9d839187550f7a7a6da3b4bfdb25b2dff3e794",
    "release_commit": None,
    "exact_release_manifest_required_before_activation": True,
    "dirty_or_uncommitted_bytes_activation_forbidden": True,
    "coverage_and_settlement_gradeability_inactive_until_release_bound": True,
    "activation_eligible": False,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_OUTPUT_FIELDS = {
    "schema_version", "terminal_state", "market_id", "research_only",
    "promotion_eligible", "betting_authorized", "contract_sha256",
    "settlement_contract_sha256", "adapter_code_sha256", "test_suite_sha256",
    "runtime_manifest_sha256", "plan_sha256", "target_id", "mlb_game_pk",
    "official_game_date", "official_start_utc", "target_horizon_utc", "side",
    "home_team_id", "away_team_id", "player_id", "source_hard_player_key",
    "market_hard_player_key", "source_shared_pa_snapshot_sha256", "receipt_utc",
    "derived_utc", "lineup_state", "source_lineup_slot", "effective_lineup_slot",
    "control_id", "control_config_sha256", "pa_volume_candidate_id",
    "pa_volume_artifact_sha256", "pa_distribution_scope",
    "pa_distribution_sha256", "pa_support", "pa_mass", "walk_per_pa_probability",
    "walks_pmf", "binary_lines", "tails", "prediction_method", "output_sha256",
}

_SIDE_FIELDS = {
    "schema_version", "terminal_state", "source_terminal_state", "market_id",
    "research_only", "promotion_eligible", "betting_authorized", "contract_sha256",
    "settlement_contract_sha256", "adapter_code_sha256", "test_suite_sha256",
    "plan_sha256", "target_id", "mlb_game_pk", "official_game_date", "side",
    "market_hard_side_key", "source_side_entry_sha256", "committed_utc", "detail",
    "player_output_sha256", "entry_sha256",
}


@dataclass(frozen=True)
class LoadedBatterWalkContract:
    payload: dict[str, Any]
    sha256: str
    settlement_sha256: str
    source_path: Path
    repository_root: Path
    provenance: object


_CONTRACT_PROVENANCE = object()
CONTRACT_RELATIVE_PATH = Path("config/batter_walk_forward_control_v1.json")
CONTRACT_DIGEST_RELATIVE_PATH = Path("config/batter_walk_forward_control_v1.sha256")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _sha(value: Any, label: str) -> str:
    text = str(value).strip().lower()
    if not _SHA256.fullmatch(text):
        raise BatterWalkForwardControlError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise BatterWalkForwardControlError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BatterWalkForwardControlError(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BatterWalkForwardControlError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _bound_path(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise BatterWalkForwardControlError(f"{label} path is blank")
    _assert_no_linklike_components(root, f"{label} repository root")
    base = root.resolve()
    unresolved = base / relative
    current = base
    if _is_linklike(base):
        raise BatterWalkForwardControlError(f"{label} repository root is link-like")
    for part in Path(relative).parts:
        current = current / part
        if _is_linklike(current):
            raise BatterWalkForwardControlError(f"{label} path crosses a symlink, junction, or reparse point")
    candidate = unresolved.resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise BatterWalkForwardControlError(f"{label} path escapes the repository") from exc
    return candidate


def _is_linklike(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return False


def _assert_no_linklike_components(path: Path, label: str) -> None:
    absolute = path if path.is_absolute() else Path.cwd() / path
    parts = absolute.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current = current / part
        if _is_linklike(current):
            raise BatterWalkForwardControlError(
                f"{label} crosses a symlink, junction, or reparse point"
            )


def _load_bound_json(root: Path, binding: Mapping[str, Any], label: str) -> tuple[dict[str, Any], str]:
    if not isinstance(binding, Mapping) or set(binding) != {"path", "sha256"}:
        raise BatterWalkForwardControlError(f"{label} binding is malformed")
    expected = _sha(binding["sha256"], f"{label}.sha256")
    path = _bound_path(root, binding["path"], label)
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise BatterWalkForwardControlError(f"{label} is unreadable") from exc
    if hashlib.sha256(raw).hexdigest() != expected or not isinstance(payload, dict):
        raise BatterWalkForwardControlError(f"{label} content or hash differs")
    return payload, expected


def _verify_bound_file(root: Path, binding: Mapping[str, Any], label: str) -> str:
    if not isinstance(binding, Mapping) or set(binding) != {"path", "sha256"}:
        raise BatterWalkForwardControlError(f"{label} binding is malformed")
    expected = _sha(binding["sha256"], f"{label}.sha256")
    path = _bound_path(root, binding["path"], label)
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise BatterWalkForwardControlError(f"{label} is unreadable") from exc
    if actual != expected:
        raise BatterWalkForwardControlError(f"{label} hash differs")
    return actual


def load_contract(*, root: str | Path, contract_path: str | Path) -> LoadedBatterWalkContract:
    raw_repository = Path(root)
    raw_contract_path = Path(contract_path)
    _assert_no_linklike_components(raw_repository, "walk control repository")
    _assert_no_linklike_components(raw_contract_path, "walk control contract")
    repository = raw_repository.resolve()
    path = raw_contract_path.resolve()
    expected_path = _bound_path(
        repository, CONTRACT_RELATIVE_PATH.as_posix(), "walk control contract"
    )
    if path != expected_path:
        raise BatterWalkForwardControlError("walk control contract path is not the immutable repository path")
    try:
        path.relative_to(repository)
    except ValueError as exc:
        raise BatterWalkForwardControlError("walk control contract path escapes the repository") from exc
    try:
        raw = path.read_bytes()
        contract = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise BatterWalkForwardControlError("walk control contract is unreadable") from exc
    if not isinstance(contract, dict):
        raise BatterWalkForwardControlError("walk control contract must be an object")
    digest_path = _bound_path(
        repository,
        CONTRACT_DIGEST_RELATIVE_PATH.as_posix(),
        "independent walk contract digest",
    )
    try:
        digest_path.relative_to(repository)
        expected_contract_sha256 = _sha(
            digest_path.read_text(encoding="ascii").strip(),
            "independent walk contract sha256",
        )
    except (ValueError, OSError, UnicodeError) as exc:
        raise BatterWalkForwardControlError("independent walk contract digest is unreadable") from exc
    actual_contract_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_contract_sha256 != expected_contract_sha256:
        raise BatterWalkForwardControlError("walk control contract differs from its independent digest")
    expected_top = {
        "schema_version", "status", "purpose", "market", "decision_horizon",
        "source_shared_pa", "lineup_rule", "publication", "source_terminal_states",
        "settlement", "implementation", "coverage", "evaluation_boundary",
        "release_authority", "protected_boundaries",
    }
    if set(contract) != expected_top:
        raise BatterWalkForwardControlError("walk control contract field set changed")
    if contract.get("schema_version") != CONTRACT_SCHEMA or contract.get("status") != "IMPLEMENTED_NOT_ACTIVATED":
        raise BatterWalkForwardControlError("walk control contract identity changed")
    market = contract.get("market")
    if not isinstance(market, Mapping) or market.get("market_id") != MARKET_ID:
        raise BatterWalkForwardControlError("walk market identity changed")
    if market.get("binary_lines") != LINES or market.get("full_count_distribution_required") is not True:
        raise BatterWalkForwardControlError("walk line grid or count requirement changed")
    if market.get("prediction_method") != METHOD or contract.get("decision_horizon") != "T-4h":
        raise BatterWalkForwardControlError("walk method or decision horizon changed")

    source = contract.get("source_shared_pa")
    if not isinstance(source, Mapping):
        raise BatterWalkForwardControlError("shared-PA source binding is missing")
    source_contract, source_contract_hash = _load_bound_json(repository, source.get("contract"), "source contract")
    if source_contract_hash != SOURCE_CONTRACT_SHA256 or source_contract.get("schema_version") != "shared-pa-forward-evidence-contract-v1":
        raise BatterWalkForwardControlError("shared-PA source contract identity changed")
    if source.get("player_snapshot_schema_version") != "shared-pa-forward-player-snapshot-v1" or source.get("required_terminal_state") != "captured_complete":
        raise BatterWalkForwardControlError("shared-PA source terminal contract changed")
    if source.get("walk_probability_path") != "per_pa_probability.walk" or source.get("source_snapshot_sha256_required") is not True:
        raise BatterWalkForwardControlError("shared-PA walk source path changed")

    control_binding = source.get("control")
    if not isinstance(control_binding, Mapping) or set(control_binding) != {"path", "candidate_id", "sha256"}:
        raise BatterWalkForwardControlError("control binding is malformed")
    control, control_hash = _load_bound_json(
        repository,
        {"path": control_binding["path"], "sha256": control_binding["sha256"]},
        "control",
    )
    if control_hash != CONTROL_SHA256 or control_binding.get("candidate_id") != CONTROL_ID or control.get("candidate_id") != CONTROL_ID:
        raise BatterWalkForwardControlError("control identity changed")

    pa_binding = source.get("pa_volume")
    if not isinstance(pa_binding, Mapping) or set(pa_binding) != {"path", "candidate_id", "sha256"}:
        raise BatterWalkForwardControlError("PA-volume binding is malformed")
    pa_volume, pa_hash = _load_bound_json(
        repository,
        {"path": pa_binding["path"], "sha256": pa_binding["sha256"]},
        "PA volume",
    )
    if pa_hash != PA_VOLUME_SHA256 or pa_binding.get("candidate_id") != PA_VOLUME_ID or pa_volume.get("candidate_id") != PA_VOLUME_ID:
        raise BatterWalkForwardControlError("PA-volume identity changed")

    settlement, settlement_hash = _load_bound_json(repository, contract.get("settlement"), "settlement contract")
    if settlement.get("schema_version") != "batter-walk-official-settlement-contract-v1":
        raise BatterWalkForwardControlError("settlement contract identity changed")

    implementation = contract.get("implementation")
    if not isinstance(implementation, Mapping) or set(implementation) != {
        "adapter", "ledger", "settlement_boundary", "regression_and_mutation_tests"
    }:
        raise BatterWalkForwardControlError("walk implementation binding is incomplete")
    _verify_bound_file(repository, implementation["adapter"], "walk adapter")
    _verify_bound_file(repository, implementation["ledger"], "walk ledger")
    _verify_bound_file(repository, implementation["settlement_boundary"], "settlement boundary")
    _verify_bound_file(repository, implementation["regression_and_mutation_tests"], "walk tests")

    protected = contract.get("protected_boundaries")
    required_true = {
        "outcomes_forbidden_during_control_collection", "pitcher_features_forbidden",
        "prices_and_economic_fields_forbidden", "source_shared_pa_snapshot_mutation_forbidden",
        "new_fit_or_parameter_tuning_forbidden", "historical_backfill_forbidden",
        "research_only",
    }
    if not isinstance(protected, Mapping) or any(protected.get(key) is not True for key in required_true):
        raise BatterWalkForwardControlError("walk control protections were weakened")
    if any(protected.get(key) is not False for key in ("production_changed", "promotion_eligible", "betting_authorized")):
        raise BatterWalkForwardControlError("walk control governance flags changed")
    if contract.get("source_terminal_states") != [
        "captured_complete", "lineup_unavailable", "lineup_malformed", "source_error",
        "missed_before_horizon", "game_identity_ambiguous", "raw_schema_changed",
    ]:
        raise BatterWalkForwardControlError("walk source terminal population changed")
    if contract.get("lineup_rule") != {
        "confirmed": "consume the exact confirmed-slot PA distribution already bound in the source snapshot",
        "projected": "consume the pooled projected-lineup PA distribution already bound in the source snapshot",
        "unknown_or_excluded": "mirror the source side terminal state and publish no player probability",
    }:
        raise BatterWalkForwardControlError("walk lineup rule changed")
    if contract.get("evaluation_boundary") != EVALUATION_BOUNDARY:
        raise BatterWalkForwardControlError("walk evaluation boundary changed")
    if contract.get("release_authority") != RELEASE_AUTHORITY:
        raise BatterWalkForwardControlError("walk release authority changed")
    publication = contract.get("publication")
    if not isinstance(publication, Mapping) or any(publication.get(key) is not True for key in (
        "separate_output_namespace", "append_only", "atomic", "immutable",
        "terminal_record_published_after_content_addressed_dependencies",
        "retained_source_snapshot_and_source_terminal_required",
        "orphaned_or_missing_artifact_is_health_failure",
        "one_terminal_state_per_planned_game_side",
        "complete_side_requires_exactly_nine_unique_player_outputs",
        "late_backfill_forbidden", "output_sha256_required",
        "source_snapshot_sha256_required", "contract_code_and_test_hashes_required",
        "source_error_requires_typed_failed_attempt_transport_receipt",
        "source_error_without_receipt_rejected",
    )):
        raise BatterWalkForwardControlError("walk publication contract changed")
    coverage = contract.get("coverage")
    if not isinstance(coverage, Mapping) or coverage != {
        "every_planned_side_requires_explicit_terminal_state": True,
        "excluded_sides_publish_zero_predictions": True,
        "predicted_rows_require_one_explicit_settlement_disposition_after_opening": True,
        "minimum_gradeable_fraction_for_any_future_promotion_protocol": 0.95,
        "control_collection_itself_is_not_promotion_eligible": True,
    }:
        raise BatterWalkForwardControlError("walk coverage contract changed")
    return LoadedBatterWalkContract(
        dict(contract), actual_contract_sha256, settlement_hash,
        path, repository, _CONTRACT_PROVENANCE,
    )


def replay_contract(contract: LoadedBatterWalkContract) -> LoadedBatterWalkContract:
    """Reload the exact independently pinned contract and reject forged/mutated objects."""
    if not isinstance(contract, LoadedBatterWalkContract) or contract.provenance is not _CONTRACT_PROVENANCE:
        raise BatterWalkForwardControlError("walk contract object was not produced by the immutable loader")
    replayed = load_contract(root=contract.repository_root, contract_path=contract.source_path)
    if (
        replayed.sha256 != contract.sha256
        or replayed.settlement_sha256 != contract.settlement_sha256
        or replayed.payload != contract.payload
    ):
        raise BatterWalkForwardControlError("walk contract object differs from immutable replay")
    return replayed


def walk_count_distribution(
    *, walk_probability: Any, pa_support: Sequence[Any], pa_mass: Sequence[Any]
) -> list[float]:
    """Return the exact PA-mixture of Binomial(n, p_walk) distributions."""
    if isinstance(walk_probability, bool) or not isinstance(walk_probability, (int, float)):
        raise BatterWalkForwardControlError("walk probability must be numeric")
    q = float(walk_probability)
    if not math.isfinite(q) or not 0.0 <= q <= 1.0:
        raise BatterWalkForwardControlError("walk probability is outside [0,1]")
    if not isinstance(pa_support, (list, tuple)) or not isinstance(pa_mass, (list, tuple)):
        raise BatterWalkForwardControlError("PA support and mass must be sequences")
    if not pa_support or len(pa_support) != len(pa_mass):
        raise BatterWalkForwardControlError("PA support and mass are not aligned")
    support: list[int] = []
    weights: list[float] = []
    for raw_n, raw_weight in zip(pa_support, pa_mass):
        if isinstance(raw_n, bool) or not isinstance(raw_n, int) or raw_n < 0:
            raise BatterWalkForwardControlError("PA support must contain non-negative integers")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)):
            raise BatterWalkForwardControlError("PA mass must be numeric")
        weight = float(raw_weight)
        if not math.isfinite(weight) or weight < 0.0:
            raise BatterWalkForwardControlError("PA mass must be finite and non-negative")
        support.append(raw_n)
        weights.append(weight)
    if support != sorted(set(support)):
        raise BatterWalkForwardControlError("PA support must be sorted and unique")
    if not math.isclose(sum(weights), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise BatterWalkForwardControlError("PA mass must sum to one")
    pmf = [0.0] * (max(support) + 1)
    for n, mixture in zip(support, weights):
        for k in range(n + 1):
            pmf[k] += mixture * math.comb(n, k) * q**k * (1.0 - q) ** (n - k)
    if any(not math.isfinite(value) or value < -1e-14 for value in pmf):
        raise BatterWalkForwardControlError("walk PMF is invalid")
    if not math.isclose(sum(pmf), 1.0, rel_tol=0.0, abs_tol=1e-11):
        raise BatterWalkForwardControlError("walk PMF does not sum to one")
    return pmf


def _output_sha256(record: Mapping[str, Any]) -> str:
    unsigned = dict(record)
    unsigned.pop("output_sha256", None)
    return sha256_value(unsigned)


def derive_player_output(
    *,
    source_snapshot: Mapping[str, Any],
    contract: LoadedBatterWalkContract,
    derived_utc: str,
    adapter_code_sha256: str | None = None,
    test_suite_sha256: str | None = None,
) -> dict[str, Any]:
    """Create one immutable walk-control record without changing the source."""
    contract = replay_contract(contract)
    try:
        validate_player_snapshot(source_snapshot)
    except ValueError as exc:
        raise BatterWalkForwardControlError("source shared-PA snapshot is invalid") from exc
    source_hash = str(source_snapshot.get("snapshot_sha256"))
    if source_hash != shared_pa_snapshot_sha256(source_snapshot):
        raise BatterWalkForwardControlError("source shared-PA snapshot hash differs")
    if source_snapshot.get("control_id") != CONTROL_ID or source_snapshot.get("control_config_sha256") != CONTROL_SHA256:
        raise BatterWalkForwardControlError("source control binding differs")
    if source_snapshot.get("pa_volume_candidate_id") != PA_VOLUME_ID or source_snapshot.get("pa_volume_artifact_sha256") != PA_VOLUME_SHA256:
        raise BatterWalkForwardControlError("source PA-volume binding differs")
    derived_time = _utc(derived_utc, "derived_utc")
    receipt_time = _utc(source_snapshot.get("receipt_utc"), "receipt_utc")
    horizon = _utc(source_snapshot.get("target_horizon_utc"), "target_horizon_utc")
    if derived_time < receipt_time or derived_time > horizon:
        raise BatterWalkForwardControlError("walk output was not derived between source receipt and T-minus-4")
    implementation = contract.payload["implementation"]
    adapter_hash = implementation["adapter"]["sha256"]
    tests_hash = implementation["regression_and_mutation_tests"]["sha256"]
    if adapter_code_sha256 is not None and _sha(adapter_code_sha256, "adapter_code_sha256") != adapter_hash:
        raise BatterWalkForwardControlError("walk output code hash differs from the contract")
    if test_suite_sha256 is not None and _sha(test_suite_sha256, "test_suite_sha256") != tests_hash:
        raise BatterWalkForwardControlError("walk output code or test hash differs from the contract")
    q = float(source_snapshot["per_pa_probability"]["walk"])
    support = list(source_snapshot["pa_support"])
    mass = [float(value) for value in source_snapshot["pa_mass"]]
    pmf = walk_count_distribution(walk_probability=q, pa_support=support, pa_mass=mass)
    game_pk = int(source_snapshot["mlb_game_pk"])
    side = str(source_snapshot["side"])
    player_id = int(source_snapshot["player_id"])
    record = {
        "schema_version": OUTPUT_SCHEMA,
        "terminal_state": "captured_complete",
        "market_id": MARKET_ID,
        "research_only": True,
        "promotion_eligible": False,
        "betting_authorized": False,
        "contract_sha256": contract.sha256,
        "settlement_contract_sha256": contract.settlement_sha256,
        "adapter_code_sha256": adapter_hash,
        "test_suite_sha256": tests_hash,
        "runtime_manifest_sha256": source_snapshot["runtime_manifest_sha256"],
        "plan_sha256": source_snapshot["plan_sha256"],
        "target_id": source_snapshot["target_id"],
        "mlb_game_pk": game_pk,
        "official_game_date": source_snapshot["official_game_date"],
        "official_start_utc": source_snapshot["official_start_utc"],
        "target_horizon_utc": source_snapshot["target_horizon_utc"],
        "side": side,
        "home_team_id": source_snapshot["home_team_id"],
        "away_team_id": source_snapshot["away_team_id"],
        "player_id": player_id,
        "source_hard_player_key": source_snapshot["hard_player_key"],
        "market_hard_player_key": f"{game_pk}:{side}:{player_id}:batter_walks",
        "source_shared_pa_snapshot_sha256": source_hash,
        "receipt_utc": source_snapshot["receipt_utc"],
        "derived_utc": derived_utc,
        "lineup_state": source_snapshot["lineup_state"],
        "source_lineup_slot": source_snapshot["source_lineup_slot"],
        "effective_lineup_slot": source_snapshot["effective_lineup_slot"],
        "control_id": CONTROL_ID,
        "control_config_sha256": CONTROL_SHA256,
        "pa_volume_candidate_id": PA_VOLUME_ID,
        "pa_volume_artifact_sha256": PA_VOLUME_SHA256,
        "pa_distribution_scope": source_snapshot["pa_distribution_scope"],
        "pa_distribution_sha256": source_snapshot["pa_distribution_sha256"],
        "pa_support": support,
        "pa_mass": mass,
        "walk_per_pa_probability": q,
        "walks_pmf": pmf,
        "binary_lines": LINES,
        "tails": {
            "walks_over_0.5": 1.0 - pmf[0],
            "walks_over_1.5": 1.0 - sum(pmf[:2]),
        },
        "prediction_method": METHOD,
    }
    record["output_sha256"] = _output_sha256(record)
    validate_player_output_against_source(
        record, source_snapshot=source_snapshot, contract=contract
    )
    return record


def validate_player_output(record: Mapping[str, Any], *, contract: LoadedBatterWalkContract) -> None:
    contract = replay_contract(contract)
    missing = _OUTPUT_FIELDS - set(record)
    unexpected = set(record) - _OUTPUT_FIELDS
    if missing or unexpected:
        raise BatterWalkForwardControlError(f"walk output schema differs: missing={sorted(missing)} unexpected={sorted(unexpected)}")
    if record.get("schema_version") != OUTPUT_SCHEMA or record.get("terminal_state") != "captured_complete" or record.get("market_id") != MARKET_ID:
        raise BatterWalkForwardControlError("walk output identity changed")
    if record.get("research_only") is not True or record.get("promotion_eligible") is not False or record.get("betting_authorized") is not False:
        raise BatterWalkForwardControlError("walk output governance changed")
    if record.get("contract_sha256") != contract.sha256 or record.get("settlement_contract_sha256") != contract.settlement_sha256:
        raise BatterWalkForwardControlError("walk output contract binding changed")
    implementation = contract.payload["implementation"]
    if record.get("adapter_code_sha256") != implementation["adapter"]["sha256"] or record.get("test_suite_sha256") != implementation["regression_and_mutation_tests"]["sha256"]:
        raise BatterWalkForwardControlError("walk output code or test hash differs from the contract")
    for field in (
        "adapter_code_sha256", "test_suite_sha256", "runtime_manifest_sha256",
        "plan_sha256", "target_id", "source_shared_pa_snapshot_sha256",
        "control_config_sha256", "pa_volume_artifact_sha256",
        "pa_distribution_sha256", "output_sha256",
    ):
        _sha(record.get(field), field)
    if record.get("control_id") != CONTROL_ID or record.get("control_config_sha256") != CONTROL_SHA256:
        raise BatterWalkForwardControlError("walk output control binding changed")
    if record.get("pa_volume_candidate_id") != PA_VOLUME_ID or record.get("pa_volume_artifact_sha256") != PA_VOLUME_SHA256:
        raise BatterWalkForwardControlError("walk output PA-volume binding changed")
    if record.get("binary_lines") != LINES or record.get("prediction_method") != METHOD:
        raise BatterWalkForwardControlError("walk output line grid or method changed")
    game_pk = record.get("mlb_game_pk")
    player_id = record.get("player_id")
    side = record.get("side")
    if isinstance(game_pk, bool) or not isinstance(game_pk, int) or game_pk <= 0 or isinstance(player_id, bool) or not isinstance(player_id, int) or player_id <= 0:
        raise BatterWalkForwardControlError("walk output game or player identity is invalid")
    if side not in {"home", "away"} or record.get("market_hard_player_key") != f"{game_pk}:{side}:{player_id}:batter_walks":
        raise BatterWalkForwardControlError("walk output hard key differs")
    if record.get("source_hard_player_key") != f"{game_pk}:{side}:{player_id}:shared_pa":
        raise BatterWalkForwardControlError("walk output source key differs")
    start = _utc(record.get("official_start_utc"), "official_start_utc")
    horizon = _utc(record.get("target_horizon_utc"), "target_horizon_utc")
    if horizon != start - timedelta(hours=4):
        raise BatterWalkForwardControlError("walk output horizon is not exact T-minus-4")
    if _utc(record.get("derived_utc"), "derived_utc") < _utc(record.get("receipt_utc"), "receipt_utc") or _utc(record.get("derived_utc"), "derived_utc") > horizon:
        raise BatterWalkForwardControlError("walk output timing differs")
    lineup_state = record.get("lineup_state")
    source_slot = record.get("source_lineup_slot")
    effective_slot = record.get("effective_lineup_slot")
    scope = record.get("pa_distribution_scope")
    if isinstance(source_slot, bool) or not isinstance(source_slot, int) or not 1 <= source_slot <= 9:
        raise BatterWalkForwardControlError("walk output source lineup slot is invalid")
    if lineup_state == "confirmed":
        if effective_slot != source_slot or scope != f"confirmed_slot_{source_slot}":
            raise BatterWalkForwardControlError("confirmed walk output changed its slot distribution")
    elif lineup_state == "projected":
        if effective_slot is not None or scope != "pooled_projected_lineup":
            raise BatterWalkForwardControlError("projected walk output did not retain pooled PA volume")
    else:
        raise BatterWalkForwardControlError("complete walk output has an invalid lineup state")
    q = record.get("walk_per_pa_probability")
    expected = walk_count_distribution(
        walk_probability=q, pa_support=record.get("pa_support"), pa_mass=record.get("pa_mass")
    )
    if record.get("pa_distribution_sha256") != sha256_value({
        "mass": [float(value) for value in record["pa_mass"]],
        "support": list(record["pa_support"]),
    }):
        raise BatterWalkForwardControlError("walk output PA distribution content hash differs")
    if record.get("walks_pmf") != expected:
        raise BatterWalkForwardControlError("walk PMF does not reproduce")
    expected_tails = {
        "walks_over_0.5": 1.0 - expected[0],
        "walks_over_1.5": 1.0 - sum(expected[:2]),
    }
    if record.get("tails") != expected_tails:
        raise BatterWalkForwardControlError("walk tails do not reproduce")
    if record.get("output_sha256") != _output_sha256(record):
        raise BatterWalkForwardControlError("walk output hash differs")


def validate_player_output_against_source(
    record: Mapping[str, Any], *, source_snapshot: Mapping[str, Any], contract: LoadedBatterWalkContract
) -> None:
    """Reproduce a walk output from the immutable shared-PA source commitment."""
    contract = replay_contract(contract)
    validate_player_output(record, contract=contract)
    try:
        validate_player_snapshot(source_snapshot)
    except ValueError as exc:
        raise BatterWalkForwardControlError("source shared-PA snapshot is invalid") from exc
    source_hash = shared_pa_snapshot_sha256(source_snapshot)
    if source_snapshot.get("snapshot_sha256") != source_hash or record.get("source_shared_pa_snapshot_sha256") != source_hash:
        raise BatterWalkForwardControlError("walk output does not bind the supplied shared-PA snapshot")
    exact_mappings = {
        "runtime_manifest_sha256": "runtime_manifest_sha256",
        "plan_sha256": "plan_sha256",
        "target_id": "target_id",
        "mlb_game_pk": "mlb_game_pk",
        "official_game_date": "official_game_date",
        "official_start_utc": "official_start_utc",
        "target_horizon_utc": "target_horizon_utc",
        "side": "side",
        "home_team_id": "home_team_id",
        "away_team_id": "away_team_id",
        "player_id": "player_id",
        "source_hard_player_key": "hard_player_key",
        "receipt_utc": "receipt_utc",
        "lineup_state": "lineup_state",
        "source_lineup_slot": "source_lineup_slot",
        "effective_lineup_slot": "effective_lineup_slot",
        "control_id": "control_id",
        "control_config_sha256": "control_config_sha256",
        "pa_volume_candidate_id": "pa_volume_candidate_id",
        "pa_volume_artifact_sha256": "pa_volume_artifact_sha256",
        "pa_distribution_scope": "pa_distribution_scope",
        "pa_distribution_sha256": "pa_distribution_sha256",
        "pa_support": "pa_support",
        "pa_mass": "pa_mass",
    }
    for output_field, source_field in exact_mappings.items():
        if record.get(output_field) != source_snapshot.get(source_field):
            raise BatterWalkForwardControlError(f"walk output {output_field} differs from its shared-PA source")
    if record.get("walk_per_pa_probability") != source_snapshot["per_pa_probability"]["walk"]:
        raise BatterWalkForwardControlError("walk probability differs from the shared-PA walk class")


def validate_side_terminal(record: Mapping[str, Any], *, contract: LoadedBatterWalkContract) -> None:
    contract = replay_contract(contract)
    missing = _SIDE_FIELDS - set(record)
    unexpected = set(record) - _SIDE_FIELDS
    if missing or unexpected:
        raise BatterWalkForwardControlError(f"walk side terminal schema differs: missing={sorted(missing)} unexpected={sorted(unexpected)}")
    state = record.get("terminal_state")
    if record.get("schema_version") != SIDE_SCHEMA or record.get("market_id") != MARKET_ID or state not in EXCLUSION_STATES | {"captured_complete"}:
        raise BatterWalkForwardControlError("walk side terminal identity changed")
    if record.get("source_terminal_state") != state:
        raise BatterWalkForwardControlError("walk side state does not mirror its source")
    if record.get("research_only") is not True or record.get("promotion_eligible") is not False or record.get("betting_authorized") is not False:
        raise BatterWalkForwardControlError("walk side governance changed")
    if record.get("contract_sha256") != contract.sha256 or record.get("settlement_contract_sha256") != contract.settlement_sha256:
        raise BatterWalkForwardControlError("walk side contract binding changed")
    implementation = contract.payload["implementation"]
    if record.get("adapter_code_sha256") != implementation["adapter"]["sha256"] or record.get("test_suite_sha256") != implementation["regression_and_mutation_tests"]["sha256"]:
        raise BatterWalkForwardControlError("walk side code or test hash differs from the contract")
    for field in (
        "contract_sha256", "settlement_contract_sha256", "adapter_code_sha256",
        "test_suite_sha256", "plan_sha256", "target_id", "source_side_entry_sha256",
        "entry_sha256",
    ):
        _sha(record.get(field), field)
    game_pk = record.get("mlb_game_pk")
    side = record.get("side")
    if isinstance(game_pk, bool) or not isinstance(game_pk, int) or game_pk <= 0 or side not in {"home", "away"}:
        raise BatterWalkForwardControlError("walk side identity is invalid")
    if record.get("market_hard_side_key") != f"{game_pk}:{side}:batter_walks":
        raise BatterWalkForwardControlError("walk side hard key differs")
    outputs = record.get("player_output_sha256")
    if not isinstance(outputs, list) or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in outputs):
        raise BatterWalkForwardControlError("walk side output hash list is invalid")
    if state == "captured_complete":
        if len(outputs) != 9 or len(set(outputs)) != 9 or record.get("detail") != "":
            raise BatterWalkForwardControlError("complete walk side must bind exactly nine unique outputs")
    elif outputs or not isinstance(record.get("detail"), str) or not record["detail"].strip():
        raise BatterWalkForwardControlError("excluded walk side must contain no outputs and a detail")
    unsigned = dict(record)
    digest = unsigned.pop("entry_sha256")
    if digest != sha256_value(unsigned):
        raise BatterWalkForwardControlError("walk side entry hash differs")


def build_side_terminal(
    *,
    source_terminal: Mapping[str, Any],
    contract: LoadedBatterWalkContract,
    adapter_code_sha256: str | None = None,
    test_suite_sha256: str | None = None,
    player_output_sha256: Sequence[str] = (),
) -> dict[str, Any]:
    """Mirror a source side terminal; exclusions can never contain predictions."""
    contract = replay_contract(contract)
    state = source_terminal.get("terminal_state")
    if state not in EXCLUSION_STATES | {"captured_complete"}:
        raise BatterWalkForwardControlError("source side terminal state is invalid")
    source_unsigned = dict(source_terminal)
    source_digest = source_unsigned.pop("entry_sha256", None)
    if source_digest != sha256_value(source_unsigned):
        raise BatterWalkForwardControlError("source side terminal hash differs")
    outputs = list(player_output_sha256)
    detail = "" if state == "captured_complete" else str(source_terminal.get("detail", "")).strip()
    implementation = contract.payload["implementation"]
    adapter_hash = implementation["adapter"]["sha256"]
    tests_hash = implementation["regression_and_mutation_tests"]["sha256"]
    if adapter_code_sha256 is not None and _sha(adapter_code_sha256, "adapter_code_sha256") != adapter_hash:
        raise BatterWalkForwardControlError("walk side code hash differs from the contract")
    if test_suite_sha256 is not None and _sha(test_suite_sha256, "test_suite_sha256") != tests_hash:
        raise BatterWalkForwardControlError("walk side code or test hash differs from the contract")
    entry = {
        "schema_version": SIDE_SCHEMA,
        "terminal_state": state,
        "source_terminal_state": state,
        "market_id": MARKET_ID,
        "research_only": True,
        "promotion_eligible": False,
        "betting_authorized": False,
        "contract_sha256": contract.sha256,
        "settlement_contract_sha256": contract.settlement_sha256,
        "adapter_code_sha256": adapter_hash,
        "test_suite_sha256": tests_hash,
        "plan_sha256": source_terminal["plan_sha256"],
        "target_id": source_terminal["target_id"],
        "mlb_game_pk": source_terminal["mlb_game_pk"],
        "official_game_date": source_terminal["official_game_date"],
        "side": source_terminal["side"],
        "market_hard_side_key": f"{source_terminal['mlb_game_pk']}:{source_terminal['side']}:batter_walks",
        "source_side_entry_sha256": source_digest,
        "committed_utc": source_terminal["committed_utc"],
        "detail": detail,
        "player_output_sha256": outputs,
    }
    entry["entry_sha256"] = sha256_value(entry)
    validate_side_terminal(entry, contract=contract)
    return entry
