"""Read-only decomposition of a certified rejected shared-PA challenger."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.evaluation.multi_market_foundation import PA_OUTCOMES
from src.evaluation.shared_pa_cumulative_selection import validate_pa_distribution_payload
from src.learning.shared_pa_model import derived_market_probabilities


SCHEMA = "shared-pa-cumulative-rejection-diagnostic-protocol-v1"
STATUS = "LOCKED_BEFORE_CERTIFIED_REJECTION_DIAGNOSIS"
ARMS = ["strongest_simple", "canonical_v1", "cumulative_core"]
MARKETS = [
    "hits_0.5", "hits_1.5", "home_runs_0.5",
    "total_bases_0.5", "total_bases_1.5", "total_bases_2.5",
    "total_bases_3.5", "total_bases_4.5", "total_bases_5.5",
]
EPSILON = 1e-12


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _root(record: dict[str, Any], *, code_root: Path, evidence_root: Path) -> Path:
    if record.get("root") == "code":
        return code_root
    if record.get("root") == "evidence":
        return evidence_root
    raise ValueError("diagnostic input root is invalid")


def load_protocol(path: Path, *, code_root: Path, evidence_root: Path) -> dict[str, Any]:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if protocol.get("schema_version") != SCHEMA or protocol.get("status") != STATUS:
        raise ValueError("rejection diagnostic protocol is not locked")
    forbidden = (
        "betting_authorized", "model_fitting_forbidden", "candidate_selection_forbidden",
        "confirmation_2025_forbidden", "may_2026_forbidden",
    )
    expected = (False, True, True, True, True)
    if tuple(protocol.get(name) for name in forbidden) != expected:
        raise ValueError("rejection diagnostic safety boundary changed")
    if not protocol.get("production_unchanged"):
        raise ValueError("rejection diagnostic altered production")
    if protocol.get("identity_key") != ["game_pk", "player_id"]:
        raise ValueError("rejection diagnostic identity changed")
    if protocol.get("arms") != ARMS or protocol.get("comparators") != ARMS[:2]:
        raise ValueError("rejection diagnostic arms changed")
    if protocol.get("candidate_arm") != "cumulative_core":
        raise ValueError("rejection diagnostic candidate changed")
    if protocol.get("pa_outcomes") != PA_OUTCOMES or protocol.get("derived_markets") != MARKETS:
        raise ValueError("rejection diagnostic outcomes or markets changed")
    chronology = protocol.get("chronology", {})
    if chronology.get("allowed_scoring_season") != 2024 or chronology.get("allowed_selection_dates") != 183:
        raise ValueError("rejection diagnostic chronology changed")
    metrics = protocol.get("metrics", {})
    if metrics.get("bootstrap_draws") != 10000 or metrics.get("bootstrap_seed") != 260723:
        raise ValueError("rejection diagnostic inference changed")
    if metrics.get("uncertainty_unit") != "official_game_date" or metrics.get("interval") != [0.025, 0.975]:
        raise ValueError("rejection diagnostic interval contract changed")
    invariants = protocol.get("protected_invariants", {})
    if not invariants or not all(value is True for value in invariants.values()):
        raise ValueError("rejection diagnostic invariant changed")
    for name, record in protocol.get("inputs", {}).items():
        artifact = _root(record, code_root=code_root, evidence_root=evidence_root) / record["path"]
        if not artifact.is_file() or sha256(artifact) != record["sha256"]:
            raise ValueError(f"rejection diagnostic input changed: {name}")
        if "required_status" in record:
            payload = json.loads(artifact.read_text(encoding="utf-8"))
            if payload.get("status") != record["required_status"]:
                raise ValueError(f"rejection diagnostic status changed: {name}")
    rejection = json.loads((code_root / protocol["inputs"]["certified_rejection"]["path"]).read_text(encoding="utf-8"))
    if rejection.get("decision", {}).get("selection_passed") or not rejection.get("decision", {}).get("confirmation_2025_must_remain_unread"):
        raise ValueError("certified rejection no longer blocks confirmation")
    report = json.loads((evidence_root / protocol["inputs"]["selection_report"]["path"]).read_text(encoding="utf-8"))
    if report.get("selection_passed") or report.get("model_artifact") is not None:
        raise ValueError("rejected selector unexpectedly published a model")
    return protocol


def load_oof(protocol: dict[str, Any], *, evidence_root: Path) -> pd.DataFrame:
    record = protocol["inputs"]["selection_oof"]
    frame = pd.read_csv(evidence_root / record["path"], low_memory=False)
    if len(frame) != int(record["rows"]):
        raise ValueError("rejection diagnostic OOF row count changed")
    identity = [*protocol["identity_key"], "game_date", "lineup_slot", "_selection_fold"]
    required = set(identity)
    for outcome in PA_OUTCOMES:
        required.add(f"actual_{outcome}")
        for arm in ARMS:
            required.add(f"{arm}_{outcome}")
    if not required.issubset(frame.columns):
        raise ValueError("rejection diagnostic OOF schema changed")
    if frame.duplicated(protocol["identity_key"]).any() or frame[identity].isna().any().any():
        raise ValueError("rejection diagnostic OOF identity is invalid")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise")
    if set(dates.dt.year) != {2024} or dates.dt.date.nunique() != 183:
        raise ValueError("rejection diagnostic OOF crossed chronology")
    slots = frame["lineup_slot"].astype(int)
    if not slots.between(1, 9).all():
        raise ValueError("rejection diagnostic lineup slot is invalid")
    actual = frame[[f"actual_{outcome}" for outcome in PA_OUTCOMES]].to_numpy(float)
    if not np.isfinite(actual).all() or (actual < 0).any() or not np.allclose(actual, np.rint(actual)):
        raise ValueError("rejection diagnostic actual counts are invalid")
    if (actual.sum(axis=1) <= 0).any():
        raise ValueError("rejection diagnostic contains zero-PA scoring rows")
    for arm in ARMS:
        probability = frame[[f"{arm}_{outcome}" for outcome in PA_OUTCOMES]].to_numpy(float)
        if not np.isfinite(probability).all() or (probability < 0).any():
            raise ValueError(f"rejection diagnostic probability is invalid: {arm}")
        if not np.allclose(probability.sum(axis=1), 1.0, atol=1e-9):
            raise ValueError(f"rejection diagnostic probability does not sum to one: {arm}")
    return frame.sort_values(["game_date", "game_pk", "player_id"]).reset_index(drop=True)


def load_pa_distribution(protocol: dict[str, Any], *, evidence_root: Path) -> dict[str, dict[str, float]]:
    record = protocol["inputs"]["pa_distribution"]
    payload = json.loads((evidence_root / record["path"]).read_text(encoding="utf-8"))
    return validate_pa_distribution_payload(payload)


def _bootstrap_counts(dates: pd.Series, *, draws: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    normalized = pd.to_datetime(dates, format="%Y-%m-%d", errors="raise").dt.strftime("%Y-%m-%d")
    unique = np.array(sorted(normalized.unique()))
    index = pd.Categorical(normalized, categories=unique).codes
    rng = np.random.default_rng(int(seed))
    weights = rng.multinomial(len(unique), np.repeat(1.0 / len(unique), len(unique)), size=int(draws))
    return index, weights


def _interval(
    candidate_numerator: np.ndarray,
    baseline_numerator: np.ndarray,
    exposure: np.ndarray,
    date_index: np.ndarray,
    bootstrap_weights: np.ndarray,
) -> dict[str, float | int]:
    n_dates = bootstrap_weights.shape[1]
    delta_by_date = np.bincount(date_index, weights=candidate_numerator - baseline_numerator, minlength=n_dates)
    exposure_by_date = np.bincount(date_index, weights=exposure, minlength=n_dates)
    point = float(delta_by_date.sum() / exposure_by_date.sum())
    sampled = (bootstrap_weights @ delta_by_date) / (bootstrap_weights @ exposure_by_date)
    lower, upper = np.quantile(sampled, [0.025, 0.975])
    return {
        "point": point, "lower": float(lower), "upper": float(upper),
        "dates": int(n_dates), "draws": int(len(sampled)),
    }


def _class_numerators(actual: np.ndarray, probability: np.ndarray, class_index: int) -> tuple[np.ndarray, np.ndarray]:
    exposure = actual.sum(axis=1)
    positive = actual[:, class_index]
    negative = exposure - positive
    p = np.clip(probability[:, class_index], EPSILON, 1.0 - EPSILON)
    log_numerator = -positive * np.log(p)
    brier_numerator = positive * np.square(1.0 - p) + negative * np.square(p)
    return log_numerator, brier_numerator


def _binary_numerators(actual: np.ndarray, probability: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(actual, dtype=float)
    p = np.clip(np.asarray(probability, dtype=float), EPSILON, 1.0 - EPSILON)
    return -(y * np.log(p) + (1.0 - y) * np.log1p(-p)), np.square(y - p)


def _score(numerator: np.ndarray, exposure: np.ndarray) -> float:
    return float(np.asarray(numerator, dtype=float).sum() / np.asarray(exposure, dtype=float).sum())


def _arm_probabilities(frame: pd.DataFrame, arm: str) -> np.ndarray:
    return frame[[f"{arm}_{outcome}" for outcome in PA_OUTCOMES]].to_numpy(float)


def evaluate(protocol: dict[str, Any], frame: pd.DataFrame, pa_distribution: dict[str, dict[str, float]]) -> dict[str, Any]:
    actual = frame[[f"actual_{outcome}" for outcome in PA_OUTCOMES]].to_numpy(float)
    exposure = actual.sum(axis=1)
    draws = int(protocol["metrics"]["bootstrap_draws"])
    seed = int(protocol["metrics"]["bootstrap_seed"])
    date_index, bootstrap_weights = _bootstrap_counts(frame["game_date"], draws=draws, seed=seed)
    probabilities = {arm: _arm_probabilities(frame, arm) for arm in ARMS}

    class_report: dict[str, Any] = {}
    total_gains = {metric: 0.0 for metric in ("multiclass_log_loss", "multiclass_brier")}
    for class_index, outcome in enumerate(PA_OUTCOMES):
        numerators = {
            arm: _class_numerators(actual, probability, class_index)
            for arm, probability in probabilities.items()
        }
        scores = {
            arm: {
                "multiclass_log_loss": _score(value[0], exposure),
                "multiclass_brier": _score(value[1], exposure),
            }
            for arm, value in numerators.items()
        }
        comparisons: dict[str, Any] = {}
        for comparator in protocol["comparators"]:
            comparisons[f"vs_{comparator}"] = {
                "multiclass_log_loss": _interval(
                    numerators["cumulative_core"][0], numerators[comparator][0], exposure,
                    date_index, bootstrap_weights,
                ),
                "multiclass_brier": _interval(
                    numerators["cumulative_core"][1], numerators[comparator][1], exposure,
                    date_index, bootstrap_weights,
                ),
            }
        eligible = all(
            float(comparisons[f"vs_{comparator}"][metric]["point"]) < 0.0
            and float(comparisons[f"vs_{comparator}"][metric]["upper"]) < 0.0
            for comparator in protocol["comparators"]
            for metric in ("multiclass_log_loss", "multiclass_brier")
        )
        for metric in total_gains:
            total_gains[metric] += max(0.0, -float(comparisons["vs_strongest_simple"][metric]["point"]))
        class_report[outcome] = {
            "actual_pa": int(actual[:, class_index].sum()),
            "actual_rate": float(actual[:, class_index].sum() / exposure.sum()),
            "scores": scores,
            "candidate_comparisons": comparisons,
            "robust_target_eligible": bool(eligible),
        }

    for outcome in PA_OUTCOMES:
        shares = []
        for metric in total_gains:
            gain = max(0.0, -float(class_report[outcome]["candidate_comparisons"]["vs_strongest_simple"][metric]["point"]))
            shares.append(gain / total_gains[metric] if total_gains[metric] > 0 else 0.0)
        class_report[outcome]["mean_share_of_positive_improvement"] = float(np.mean(shares))
    eligible_classes = [outcome for outcome in PA_OUTCOMES if class_report[outcome]["robust_target_eligible"]]
    eligible_classes.sort(key=lambda outcome: (-class_report[outcome]["mean_share_of_positive_improvement"], PA_OUTCOMES.index(outcome)))

    derived = {
        arm: derived_market_probabilities(probability, frame["lineup_slot"], pa_distribution)
        for arm, probability in probabilities.items()
    }
    hits = actual[:, PA_OUTCOMES.index("single")] + actual[:, PA_OUTCOMES.index("double")] + actual[:, PA_OUTCOMES.index("triple")] + actual[:, PA_OUTCOMES.index("home_run")]
    total_bases = actual[:, PA_OUTCOMES.index("single")] + 2 * actual[:, PA_OUTCOMES.index("double")] + 3 * actual[:, PA_OUTCOMES.index("triple")] + 4 * actual[:, PA_OUTCOMES.index("home_run")]
    home_runs = actual[:, PA_OUTCOMES.index("home_run")]
    market_actual: dict[str, np.ndarray] = {
        "hits_0.5": (hits >= 1).astype(float),
        "hits_1.5": (hits >= 2).astype(float),
        "home_runs_0.5": (home_runs >= 1).astype(float),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
        market_actual[f"total_bases_{line}"] = (total_bases > line).astype(float)

    market_report: dict[str, Any] = {}
    for market in MARKETS:
        binary = {arm: _binary_numerators(market_actual[market], derived[arm][market]) for arm in ARMS}
        scores = {
            arm: {"log_loss": float(value[0].mean()), "brier": float(value[1].mean())}
            for arm, value in binary.items()
        }
        comparisons: dict[str, Any] = {}
        unit_exposure = np.ones(len(frame), dtype=float)
        for comparator in protocol["comparators"]:
            comparisons[f"vs_{comparator}"] = {
                "log_loss": _interval(binary["cumulative_core"][0], binary[comparator][0], unit_exposure, date_index, bootstrap_weights),
                "brier": _interval(binary["cumulative_core"][1], binary[comparator][1], unit_exposure, date_index, bootstrap_weights),
            }
        eligible = all(
            float(comparisons[f"vs_{comparator}"][metric]["point"]) < 0.0
            and float(comparisons[f"vs_{comparator}"][metric]["upper"]) < 0.0
            for comparator in protocol["comparators"]
            for metric in ("log_loss", "brier")
        )
        relative = np.mean([
            max(0.0, -float(comparisons["vs_strongest_simple"][metric]["point"]))
            / max(float(scores["strongest_simple"][metric]), EPSILON)
            for metric in ("log_loss", "brier")
        ])
        market_report[market] = {
            "positive_rows": int(market_actual[market].sum()),
            "negative_rows": int(len(frame) - market_actual[market].sum()),
            "scores": scores,
            "candidate_comparisons": comparisons,
            "robust_target_eligible": bool(eligible),
            "mean_relative_point_improvement": float(relative),
        }
    eligible_markets = [market for market in MARKETS if market_report[market]["robust_target_eligible"]]
    eligible_markets.sort(key=lambda market: (-market_report[market]["mean_relative_point_improvement"], MARKETS.index(market)))

    return {
        "population": {
            "rows": int(len(frame)), "dates": int(frame["game_date"].nunique()),
            "pa": int(exposure.sum()), "seasons": [2024], "coverage_loss": 0,
        },
        "class_contributions": class_report,
        "derived_markets": market_report,
        "diagnostic_ranking": {
            "eligible_pa_outcomes": eligible_classes,
            "highest_pa_outcome_target": eligible_classes[0] if eligible_classes else "NO_ROBUST_TARGET_IDENTIFIED",
            "eligible_derived_markets": eligible_markets,
            "highest_derived_market_target": eligible_markets[0] if eligible_markets else "NO_ROBUST_TARGET_IDENTIFIED",
            "diagnostic_only_not_candidate_selection": True,
        },
    }


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-14)
    return left == right


def validate_report(
    report: dict[str, Any],
    *,
    protocol: dict[str, Any],
    protocol_path: Path,
    code_root: Path,
    expected_analysis: dict[str, Any],
) -> None:
    if report.get("schema_version") != "shared-pa-cumulative-rejection-diagnostic-report-v1":
        raise ValueError("rejection diagnostic report schema changed")
    if report.get("status") != "CERTIFIED_REJECTION_DIAGNOSIS_COMPLETE":
        raise ValueError("rejection diagnostic report is incomplete")
    if report.get("betting_authorized") or report.get("production_changed"):
        raise ValueError("rejection diagnostic changed authorization or production")
    if report.get("confirmation_2025_opened") or report.get("may_2026_opened"):
        raise ValueError("rejection diagnostic crossed protected evidence")
    if report.get("model_artifact") is not None:
        raise ValueError("rejection diagnostic published a model")
    protocol_record = report.get("protocol", {})
    if protocol_record.get("path") != str(protocol_path.relative_to(code_root)).replace("\\", "/"):
        raise ValueError("rejection diagnostic protocol path changed")
    if protocol_record.get("sha256") != sha256(protocol_path):
        raise ValueError("rejection diagnostic protocol hash changed")
    if report.get("inputs") != protocol.get("inputs"):
        raise ValueError("rejection diagnostic input bindings changed")
    if report.get("protected_invariants") != protocol.get("protected_invariants"):
        raise ValueError("rejection diagnostic protected invariants changed")
    runtime = report.get("runtime", {})
    hashes = runtime.get("file_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("rejection diagnostic runtime hashes are missing")
    for relative, expected_hash in hashes.items():
        path = code_root / relative
        if not path.is_file() or sha256(path) != expected_hash:
            raise ValueError(f"rejection diagnostic runtime changed: {relative}")
    if not _same(report.get("analysis"), expected_analysis):
        raise ValueError("rejection diagnostic analysis does not independently reproduce")
