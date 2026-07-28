"""Fail-closed research-window and candidate-release governance.

The registry exists to prevent a copied worktree, renamed candidate, repaired
implementation, or derived market from making a spent evidence window appear
untouched again.  It validates metadata only; it never opens prediction,
outcome, price, settlement, or sealed evidence payloads.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class SharedPAResearchRegistryError(ValueError):
    """A research ledger or release manifest weakened a locked boundary."""


LEDGER_SCHEMA = "shared-pa-evidence-spend-ledger-v1"
CANDIDATE_SCHEMA = "shared-pa-candidate-release-manifest-v1"
EVALUATION_SCHEMA = "shared-pa-future-evaluation-principles-v2"

SPEND_STATES = {
    "DEVELOPMENT_USED",
    "SELECTION_SPENT",
    "CONFIRMATION_SPENT",
    "RESERVED_OUTCOME_SEALED",
    "NOT_ACCESSED_BUT_NOT_INDEPENDENT",
    "UNKNOWN_REQUIRES_PROOF",
}
SPENT_STATES = {
    "SELECTION_SPENT",
    "CONFIRMATION_SPENT",
    "NOT_ACCESSED_BUT_NOT_INDEPENDENT",
}
MARKETS = ("hits", "hr_over_0_5", "total_bases")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SharedPAResearchRegistryError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=_unique_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedPAResearchRegistryError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise SharedPAResearchRegistryError(f"{label} must be a JSON object")
    return value


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SharedPAResearchRegistryError(f"{label} must be a lowercase SHA-256")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SharedPAResearchRegistryError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SharedPAResearchRegistryError(f"{label} is invalid") from exc
    if parsed.tzinfo != timezone.utc:
        raise SharedPAResearchRegistryError(f"{label} must be UTC")
    return parsed


def _iso_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise SharedPAResearchRegistryError(f"{label} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SharedPAResearchRegistryError(f"{label} is invalid") from exc
    if parsed.isoformat() != value:
        raise SharedPAResearchRegistryError(f"{label} must be canonical ISO")
    return parsed


def _assert_not_sealed_month(value: date, label: str) -> None:
    if value.year == 2026 and value.month == 5:
        raise SharedPAResearchRegistryError(f"{label} enters the permanently sealed month")


def load_spend_ledger(path: Path) -> dict[str, Any]:
    ledger = _load_json(path, "evidence spend ledger")
    if set(ledger) != {
        "schema_version",
        "status",
        "as_of_utc",
        "scope",
        "rules",
        "windows",
    }:
        raise SharedPAResearchRegistryError("evidence spend ledger surface changed")
    if ledger.get("schema_version") != LEDGER_SCHEMA:
        raise SharedPAResearchRegistryError("evidence spend ledger schema changed")
    if ledger.get("status") != "LOCKED_GLOBAL_LINEAGE_SPEND_REGISTER":
        raise SharedPAResearchRegistryError("evidence spend ledger is not locked")
    _utc(ledger.get("as_of_utc"), "ledger.as_of_utc")
    if ledger.get("scope") != {
        "candidate_lineage": "shared_batter_pa_outcome_foundation",
        "markets": list(MARKETS),
        "market_pooling_allowed": False,
    }:
        raise SharedPAResearchRegistryError("evidence spend ledger scope changed")
    expected_rules = {
        "spend_is_global_across_branches_worktrees_repairs_and_renames": True,
        "derived_markets_inherit_parent_lineage_spend": True,
        "repair_does_not_restore_untouched_status": True,
        "unknown_is_ineligible_until_proven": True,
        "historical_or_missed_prospective_backfill_allowed": False,
        "sealed_month_access_allowed": False,
        "betting_authorized": False,
    }
    if ledger.get("rules") != expected_rules:
        raise SharedPAResearchRegistryError("evidence spend ledger rules changed")
    windows = ledger.get("windows")
    if not isinstance(windows, list) or not windows:
        raise SharedPAResearchRegistryError("evidence spend ledger has no windows")
    seen: set[str] = set()
    for index, window in enumerate(windows):
        if not isinstance(window, Mapping) or set(window) != {
            "window_id",
            "period",
            "role",
            "lineage_scope",
            "state",
            "permitted_use",
            "prohibited_use",
            "evidence",
            "limitation",
        }:
            raise SharedPAResearchRegistryError(f"window {index} surface changed")
        window_id = window.get("window_id")
        if not isinstance(window_id, str) or not window_id or window_id in seen:
            raise SharedPAResearchRegistryError("window IDs must be unique nonempty strings")
        seen.add(window_id)
        if window.get("state") not in SPEND_STATES:
            raise SharedPAResearchRegistryError(f"{window_id} has an invalid spend state")
        if not isinstance(window.get("lineage_scope"), list) or not window["lineage_scope"]:
            raise SharedPAResearchRegistryError(f"{window_id} has no lineage scope")
        if not isinstance(window.get("permitted_use"), list) or not isinstance(
            window.get("prohibited_use"), list
        ):
            raise SharedPAResearchRegistryError(f"{window_id} use boundaries are malformed")
        evidence = window.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise SharedPAResearchRegistryError(f"{window_id} lacks primary evidence")
        for item in evidence:
            if not isinstance(item, Mapping) or set(item) != {
                "commit",
                "path",
                "sha256",
                "claim",
            }:
                raise SharedPAResearchRegistryError(f"{window_id} evidence surface changed")
            commit = item.get("commit")
            if not isinstance(commit, str) or len(commit) != 40:
                raise SharedPAResearchRegistryError(f"{window_id} commit identity is invalid")
            _sha256(item.get("sha256"), f"{window_id}.evidence.sha256")
            if not all(isinstance(item.get(key), str) and item.get(key) for key in ("path", "claim")):
                raise SharedPAResearchRegistryError(f"{window_id} evidence metadata is incomplete")
    return ledger


def spent_window_ids(ledger: Mapping[str, Any]) -> set[str]:
    windows = ledger.get("windows")
    if not isinstance(windows, list):
        raise SharedPAResearchRegistryError("validated ledger windows are missing")
    return {
        str(window["window_id"])
        for window in windows
        if isinstance(window, Mapping) and window.get("state") in SPENT_STATES
    }


def verify_ledger_evidence(*, repository: Path, ledger: Mapping[str, Any]) -> None:
    """Verify every primary reference against immutable Git bytes.

    Git is used only as a local content-addressed object reader.  The command
    does not fetch, checkout, update refs, or touch a working tree.
    """
    root = repository.resolve()
    if not (root / ".git").exists():
        raise SharedPAResearchRegistryError("evidence repository is not a Git checkout")
    windows = ledger.get("windows")
    if not isinstance(windows, list):
        raise SharedPAResearchRegistryError("validated ledger windows are missing")
    for window in windows:
        if not isinstance(window, Mapping):
            raise SharedPAResearchRegistryError("validated ledger window is malformed")
        for evidence in window["evidence"]:
            object_name = f"{evidence['commit']}:{evidence['path']}"
            try:
                completed = subprocess.run(
                    [
                        "git",
                        "-c",
                        f"safe.directory={root.as_posix()}",
                        "-C",
                        str(root),
                        "show",
                        object_name,
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                raise SharedPAResearchRegistryError(
                    f"primary evidence is unavailable: {object_name}"
                ) from exc
            actual = hashlib.sha256(completed.stdout).hexdigest()
            if actual != evidence["sha256"]:
                raise SharedPAResearchRegistryError(
                    f"primary evidence hash differs: {object_name}"
                )


def load_candidate_release_manifest(
    path: Path, *, ledger: Mapping[str, Any], ledger_path: Path,
) -> dict[str, Any]:
    manifest = _load_json(path, "candidate release manifest")
    if manifest.get("schema_version") != CANDIDATE_SCHEMA:
        raise SharedPAResearchRegistryError("candidate release manifest schema changed")
    required = {
        "schema_version",
        "status",
        "candidate_id",
        "parent_lineage",
        "research_only",
        "betting_authorized",
        "fit_periods",
        "development_evaluation_periods",
        "selection_window_ids",
        "confirmation_window_ids",
        "prospective_window",
        "markets",
        "pitcher_feature_policy",
        "artifacts",
        "ledger_binding",
    }
    if set(manifest) != required:
        raise SharedPAResearchRegistryError("candidate release manifest surface changed")
    if manifest.get("status") != "FROZEN_RESEARCH_CHALLENGER_NOT_PROMOTED":
        raise SharedPAResearchRegistryError("candidate is not a frozen research challenger")
    if manifest.get("research_only") is not True or manifest.get("betting_authorized") is not False:
        raise SharedPAResearchRegistryError("candidate research boundary changed")
    if manifest.get("parent_lineage") != "shared_batter_pa_outcome_foundation":
        raise SharedPAResearchRegistryError("candidate parent lineage changed")
    if manifest.get("fit_periods") != ["2023"] or manifest.get(
        "development_evaluation_periods"
    ) != ["2023"]:
        raise SharedPAResearchRegistryError("candidate must remain 2023-only in development")
    selection_windows = manifest.get("selection_window_ids")
    confirmation_windows = manifest.get("confirmation_window_ids")
    if not isinstance(selection_windows, list) or not isinstance(confirmation_windows, list):
        raise SharedPAResearchRegistryError("candidate window claims are malformed")
    # No window in the current ledger is available to a new candidate.  This
    # includes development-used and sealed-reserved windows: neither state is
    # independent selection or confirmation evidence for bytes frozen later.
    claimed_windows = set(selection_windows) | set(confirmation_windows)
    if claimed_windows:
        raise SharedPAResearchRegistryError(
            "candidate claimed an unavailable historical or existing forward window: "
            + ",".join(sorted(claimed_windows))
        )
    if manifest.get("markets") != {
        "outputs": list(MARKETS),
        "adjudicate_separately": True,
        "one_market_can_rescue_another": False,
    }:
        raise SharedPAResearchRegistryError("candidate market separation changed")
    if manifest.get("pitcher_feature_policy") != {
        "enabled": False,
        "enable_only_with_receipt_proven_probable_starter": True,
        "guessed_or_postgame_actual_starter_allowed": False,
    }:
        raise SharedPAResearchRegistryError("candidate pitcher boundary changed")
    prospective = manifest.get("prospective_window")
    if not isinstance(prospective, Mapping) or set(prospective) != {
        "locked_at_utc",
        "first_official_date",
        "last_official_date",
        "backfill_allowed",
    }:
        raise SharedPAResearchRegistryError("prospective window surface changed")
    locked_at = _utc(prospective.get("locked_at_utc"), "prospective.locked_at_utc")
    first = _iso_date(prospective.get("first_official_date"), "prospective.first_official_date")
    last = _iso_date(prospective.get("last_official_date"), "prospective.last_official_date")
    _assert_not_sealed_month(first, "prospective.first_official_date")
    _assert_not_sealed_month(last, "prospective.last_official_date")
    if first > last or datetime.combine(first, datetime.min.time(), timezone.utc) <= locked_at:
        raise SharedPAResearchRegistryError("prospective window was not locked before its first date")
    if prospective.get("backfill_allowed") is not False:
        raise SharedPAResearchRegistryError("prospective backfill boundary changed")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {
        "code",
        "configuration",
        "data_manifest",
        "feature_manifest",
        "model",
        "tests",
        "output_schema",
    }:
        raise SharedPAResearchRegistryError("candidate artifact binding is incomplete")
    for label, artifact in artifacts.items():
        if not isinstance(artifact, Mapping) or set(artifact) != {"path", "sha256"}:
            raise SharedPAResearchRegistryError(f"candidate {label} binding is malformed")
        if not isinstance(artifact.get("path"), str) or not artifact.get("path"):
            raise SharedPAResearchRegistryError(f"candidate {label} path is missing")
        _sha256(artifact.get("sha256"), f"candidate.{label}.sha256")
    binding = manifest.get("ledger_binding")
    if not isinstance(binding, Mapping) or set(binding) != {"path", "sha256"}:
        raise SharedPAResearchRegistryError("candidate ledger binding is malformed")
    if binding.get("path") != "config/shared_pa_evidence_spend_ledger_v1.json":
        raise SharedPAResearchRegistryError("candidate ledger binding path changed")
    expected_ledger_hash = ledger_sha256(ledger_path)
    if _sha256(binding.get("sha256"), "candidate.ledger_binding.sha256") != expected_ledger_hash:
        raise SharedPAResearchRegistryError("candidate ledger binding hash mismatch")
    return manifest


def load_future_evaluation_principles(path: Path) -> dict[str, Any]:
    value = _load_json(path, "future evaluation principles")
    if set(value) != {
        "schema_version",
        "status",
        "markets",
        "proper_scores",
        "discrimination",
        "hr_high_probability_tail",
        "coverage",
        "comparators",
        "protected_boundaries",
    }:
        raise SharedPAResearchRegistryError("future evaluation surface changed")
    if value.get("schema_version") != EVALUATION_SCHEMA:
        raise SharedPAResearchRegistryError("future evaluation schema changed")
    if value.get("status") != "PREDECLARED_DESIGN_ONLY_NOT_BOUND_TO_A_WINDOW":
        raise SharedPAResearchRegistryError("future evaluation principles were relabelled active")
    if value.get("markets") != {
        "evaluate_separately": list(MARKETS),
        "pooling_allowed": False,
        "cross_market_rescue_allowed": False,
    }:
        raise SharedPAResearchRegistryError("future market separation changed")
    if value.get("proper_scores") != {
        "metrics": ["brier", "log_loss"],
        "material_fraction": 0.01,
        "candidate_point_and_paired_upper_95_must_both_clear": True,
        "unit": "official_date_cluster",
        "method": "paired_percentile_bootstrap",
    }:
        raise SharedPAResearchRegistryError("proper-score materiality gate changed")
    if value.get("discrimination") != {
        "metric": "auc",
        "undefined_disposition": "INSUFFICIENT_EVIDENCE_BLOCKS_PROMOTION",
        "both_candidate_and_comparator_undefined_is_a_pass": False,
    }:
        raise SharedPAResearchRegistryError("undefined AUC no longer fails closed")
    if value.get("hr_high_probability_tail") != {
        "cohort": "candidate_fixed_top_decile_reused_for_every_comparator",
        "uncertainty_unit": "official_date_cluster",
        "uncertainty_method": "paired_percentile_bootstrap",
        "confidence": 0.95,
        "support_requires_both_outcome_classes": True,
        "support_requires_at_least_two_official_date_clusters": True,
        "insufficient_support_disposition": "INSUFFICIENT_EVIDENCE_BLOCKS_PROMOTION",
    }:
        raise SharedPAResearchRegistryError("HR tail uncertainty or support gate changed")
    if value.get("coverage") != {
        "planned_gradeable_and_terminal_missingness_required": True,
        "silent_row_deletion_allowed": False,
        "minimum_gradeable_fraction": 0.95,
    }:
        raise SharedPAResearchRegistryError("coverage gate changed")
    if value.get("comparators") != {
        "league_rate": "required",
        "time_safe_player_empirical_bayes": "required",
        "frozen_production_probability": "required_exact_pregame_capture",
        "market_implied_probability": "required_only_when_receipt_verified",
        "missing_required_comparator_disposition": "BLOCK_PROMOTION",
    }:
        raise SharedPAResearchRegistryError("comparator gate changed")
    boundaries = value.get("protected_boundaries")
    if boundaries != {
        "retrofit_existing_forward_window_allowed": False,
        "historical_or_missed_prospective_backfill_allowed": False,
        "sealed_month_access_allowed": False,
        "spent_confirmation_reuse_allowed": False,
        "historical_prices_executable": False,
        "betting_authorized": False,
    }:
        raise SharedPAResearchRegistryError("future evaluation boundaries changed")
    return value


def ledger_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
