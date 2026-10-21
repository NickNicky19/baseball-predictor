"""Finite provisional tournament for strictly-prior batter recency.

The registered family asks whether the timing of a batter's prior PA outcomes
adds information beyond the receipt-bound empirical-Bayes outcome simplex.
It consumes only the already-spent, hash-bound 2023 direct-batter panel.  No
same-day event, target-opponent context, protected season, or mutable source is
eligible, and every result remains non-promotable pending source qualification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from src.evaluation.shared_pa_contact_quality_tournament import (
    _count_losses,
    _evaluate_variant,
    total_bases_pmf,
)
from src.evaluation.shared_pa_eb_offset_catboost import PA_OUTCOMES, target_counts
from src.evaluation.shared_pa_plate_discipline_tournament import (
    DEVELOPMENT_SEASON,
    chronological_splits,
    count_pmf,
    empirical_bayes_probability,
    fit_dirichlet_concentration,
    league_probability,
    pooled_pa_distribution,
    score_count_market,
)


RESEARCH_STATUS = "PROVISIONAL_NON_PROMOTABLE_PENDING_QUALIFIED_SOURCE_CONFIRMATION"
BOOTSTRAP_SEED = 20260805
L2_PENALTY = 1.0
OUTCOME_INDEX = {name: index for index, name in enumerate(PA_OUTCOMES)}

RECENCY_PAIRS = (
    ("history_strikeout_count", "days_since_strikeout"),
    ("history_walk_count", "days_since_walk"),
    ("history_single_count", "days_since_single"),
    ("history_double_count", "days_since_double"),
    ("history_triple_count", "days_since_triple"),
    ("history_home_run_count", "days_since_home_run"),
    ("history_bip_out_count", "days_since_bip_out"),
    ("history_other_non_ab_count", "days_since_other_non_ab"),
)
AGE_COLUMNS = (
    "history_pa_age_days_mean",
    "history_pa_age_days_sd",
    "days_since_pa",
)
SUBSTANTIVE_VARIANTS = ("outcome_recency", "outcome_recency_plus_age")
CONTROL_VARIANTS = (
    "feature_off",
    "support_only",
    "missingness_only",
    "deterministic_shuffle",
)
ALL_ARMS = (
    "league_rate",
    "eb_fixed_200",
    "eb_fitted",
    *SUBSTANTIVE_VARIANTS,
    *CONTROL_VARIANTS,
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_batter_recency_matrix(frame: pd.DataFrame) -> None:
    """Fail closed on strict chronology and recency/missingness lineage."""

    required = {
        "season",
        "game_date",
        "game_pk",
        "player_id",
        "max_source_date",
        "history_pa",
        "history_pa_age_days_count",
        "history_pa_age_days_missing_count",
        *AGE_COLUMNS,
        *(name for pair in RECENCY_PAIRS for name in pair),
    }
    missing = sorted(required.difference(frame.columns))
    _require(not missing, f"batter-recency matrix missing columns: {missing}")
    _require(
        not frame[["game_pk", "player_id"]].duplicated().any(),
        "batter-recency identities are duplicated",
    )
    season = pd.to_numeric(frame["season"], errors="coerce")
    _require(not season.isna().any(), "batter-recency season is missing or nonnumeric")
    _require(season.eq(DEVELOPMENT_SEASON).all(), "later season entered batter-recency matrix")

    target = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    source = pd.to_datetime(frame["max_source_date"], format="%Y-%m-%d", errors="coerce")
    _require(not target.isna().any(), "batter-recency target date is malformed")
    _require(
        not (source.notna() & source.ge(target)).any(),
        "batter-recency matrix contains same-day or future evidence",
    )

    history_pa = pd.to_numeric(frame["history_pa"], errors="coerce")
    age_count = pd.to_numeric(frame["history_pa_age_days_count"], errors="coerce")
    age_missing = pd.to_numeric(frame["history_pa_age_days_missing_count"], errors="coerce")
    for name, values in (
        ("history_pa", history_pa),
        ("history_pa_age_days_count", age_count),
        ("history_pa_age_days_missing_count", age_missing),
    ):
        _require(not values.isna().any(), f"{name} is missing or nonnumeric")
        _require((values >= 0).all(), f"{name} is negative")
        _require(np.equal(values, np.floor(values)).all(), f"{name} is nonintegral")
    _require((age_count + age_missing).eq(history_pa).all(), "PA-age partition differs from history PA")

    positive_history = history_pa.gt(0)
    _require(source.loc[positive_history].notna().all(), "positive history lacks a source date")
    _require(source.loc[~positive_history].isna().all(), "zero history has a fabricated source date")
    for name in AGE_COLUMNS:
        values = pd.to_numeric(frame[name], errors="coerce")
        _require(values.loc[positive_history].notna().all(), f"positive history lacks {name}")
        _require(values.loc[~positive_history].isna().all(), f"zero history has {name}")
        _require((values.loc[positive_history] >= 0).all(), f"{name} is negative")
    _require(
        pd.to_numeric(frame["days_since_pa"], errors="coerce").loc[positive_history].ge(1).all(),
        "days since PA is not strictly prior",
    )

    for count_name, recency_name in RECENCY_PAIRS:
        count = pd.to_numeric(frame[count_name], errors="coerce")
        recency = pd.to_numeric(frame[recency_name], errors="coerce")
        _require(not count.isna().any(), f"{count_name} is missing or nonnumeric")
        _require((count >= 0).all(), f"{count_name} is negative")
        _require(np.equal(count, np.floor(count)).all(), f"{count_name} is nonintegral")
        observed = count.gt(0)
        _require(recency.loc[observed].notna().all(), f"positive count lacks {recency_name}")
        _require(recency.loc[~observed].isna().all(), f"zero count has {recency_name}")
        _require((recency.loc[observed] >= 1).all(), f"{recency_name} is not strictly prior")


@dataclass(frozen=True)
class BatterRecencyTransform:
    variant: str
    names: tuple[str, ...]
    fill: np.ndarray
    mean: np.ndarray
    scale: np.ndarray
    retained: np.ndarray


def _missingness_features(frame: pd.DataFrame) -> tuple[list[str], list[np.ndarray]]:
    names: list[str] = []
    values: list[np.ndarray] = []
    for _, recency_name in RECENCY_PAIRS:
        names.append(f"{recency_name}_missing")
        values.append(pd.to_numeric(frame[recency_name], errors="coerce").isna().to_numpy(float))
    names.append("days_since_pa_missing")
    values.append(pd.to_numeric(frame["days_since_pa"], errors="coerce").isna().to_numpy(float))
    return names, values


def _raw_features(frame: pd.DataFrame, variant: str) -> tuple[list[str], np.ndarray]:
    history_pa = pd.to_numeric(frame["history_pa"], errors="raise").to_numpy(float)
    age_missing = pd.to_numeric(
        frame["history_pa_age_days_missing_count"], errors="raise"
    ).to_numpy(float)
    support_names = ["recency_log1p_history_pa", "recency_pa_age_missing_share"]
    support_values = [
        np.log1p(history_pa),
        np.divide(age_missing, history_pa, out=np.zeros_like(history_pa), where=history_pa > 0),
    ]
    if variant == "support_only":
        return support_names, np.column_stack(support_values)

    missing_names, missing_values = _missingness_features(frame)
    if variant == "missingness_only":
        return missing_names, np.column_stack(missing_values)

    if variant in {*SUBSTANTIVE_VARIANTS, "deterministic_shuffle"}:
        names = list(missing_names)
        values = list(missing_values)
        for _, recency_name in RECENCY_PAIRS:
            names.append(f"{recency_name}_log1p")
            raw = pd.to_numeric(frame[recency_name], errors="coerce").to_numpy(float)
            values.append(np.log1p(raw))
        if variant in {"outcome_recency_plus_age", "deterministic_shuffle"}:
            for name in AGE_COLUMNS:
                names.append(f"{name}_log1p")
                raw = pd.to_numeric(frame[name], errors="coerce").to_numpy(float)
                values.append(np.log1p(raw))
            names.extend(support_names)
            values.extend(support_values)
        return names, np.column_stack(values)
    raise ValueError(f"unknown batter-recency variant: {variant}")


def fit_batter_recency_transform(
    frame: pd.DataFrame, variant: str
) -> tuple[BatterRecencyTransform, np.ndarray]:
    effective = "outcome_recency_plus_age" if variant == "deterministic_shuffle" else variant
    names, raw = _raw_features(frame, effective)
    fill = np.zeros(raw.shape[1], dtype=float)
    for index in range(raw.shape[1]):
        finite = np.isfinite(raw[:, index])
        _require(finite.any(), f"fold training feature is entirely missing: {names[index]}")
        fill[index] = float(np.mean(raw[finite, index]))
    filled = np.where(np.isfinite(raw), raw, fill)
    mean = filled.mean(axis=0)
    scale = filled.std(axis=0)
    retained = scale > 1e-12
    _require(retained.any(), f"batter-recency variant has no variable features: {variant}")
    transformed = (filled[:, retained] - mean[retained]) / scale[retained]
    contract = BatterRecencyTransform(
        variant=variant,
        names=tuple(np.asarray(names, dtype=object)[retained].tolist()),
        fill=fill,
        mean=mean,
        scale=scale,
        retained=retained,
    )
    return contract, transformed


def apply_batter_recency_transform(
    frame: pd.DataFrame, transform: BatterRecencyTransform
) -> np.ndarray:
    effective = (
        "outcome_recency_plus_age"
        if transform.variant == "deterministic_shuffle"
        else transform.variant
    )
    _, raw = _raw_features(frame, effective)
    filled = np.where(np.isfinite(raw), raw, transform.fill)
    return (
        filled[:, transform.retained] - transform.mean[transform.retained]
    ) / transform.scale[transform.retained]


def adjust_full_simplex_probability(
    base: np.ndarray, features: np.ndarray, coefficients: np.ndarray
) -> np.ndarray:
    _require(
        coefficients.shape == (len(PA_OUTCOMES) - 1, features.shape[1]),
        "coefficient shape differs",
    )
    offsets = np.column_stack([features @ coefficients.T, np.zeros(len(features))])
    logits = np.log(np.clip(base, 1e-15, 1.0)) + offsets
    logits -= logits.max(axis=1, keepdims=True)
    adjusted = np.exp(logits)
    adjusted /= adjusted.sum(axis=1, keepdims=True)
    return adjusted


def fit_full_simplex_offset(
    base: np.ndarray, features: np.ndarray, counts: np.ndarray
) -> np.ndarray:
    shape = (len(PA_OUTCOMES) - 1, features.shape[1])

    def objective(flat: np.ndarray) -> tuple[float, np.ndarray]:
        coefficients = flat.reshape(shape)
        probability = adjust_full_simplex_probability(base, features, coefficients)
        value = float(-(counts * np.log(np.clip(probability, 1e-15, 1.0))).sum())
        value += 0.5 * L2_PENALTY * float(np.square(coefficients).sum())
        residual = probability * counts.sum(axis=1, keepdims=True) - counts
        gradient = residual[:, :-1].T @ features + L2_PENALTY * coefficients
        return value, gradient.ravel()

    result = minimize(
        lambda value: objective(value)[0],
        np.zeros(np.prod(shape), dtype=float),
        jac=lambda value: objective(value)[1],
        method="L-BFGS-B",
        options={"maxiter": 2_000, "ftol": 1e-10, "gtol": 1e-6},
    )
    _require(bool(result.success), f"batter-recency offset fit failed: {result.message}")
    return np.asarray(result.x, dtype=float).reshape(shape)


def _observed(frame: pd.DataFrame, market: str) -> np.ndarray:
    if market == "strikeouts":
        return pd.to_numeric(frame["out_k"], errors="raise").astype(int).to_numpy()
    if market == "walks":
        return pd.to_numeric(frame["out_bb"], errors="raise").astype(int).to_numpy()
    if market == "hits":
        return pd.to_numeric(frame["out_hits"], errors="raise").astype(int).to_numpy()
    if market == "home_runs":
        return pd.to_numeric(frame["out_hr"], errors="raise").astype(int).to_numpy()
    if market == "total_bases":
        return (
            pd.to_numeric(frame["out_hits"], errors="raise")
            + pd.to_numeric(frame["out_doubles"], errors="raise")
            + 2 * pd.to_numeric(frame["out_triples"], errors="raise")
            + 3 * pd.to_numeric(frame["out_hr"], errors="raise")
        ).astype(int).to_numpy()
    raise ValueError(f"unknown batter-recency market: {market}")


def _pmf(
    probability: np.ndarray,
    distributions: Sequence[Mapping[int, float]],
    max_pa: int,
    market: str,
) -> np.ndarray:
    if market == "strikeouts":
        return count_pmf(probability[:, OUTCOME_INDEX["strikeout"]], distributions, max_pa)
    if market == "walks":
        return count_pmf(probability[:, OUTCOME_INDEX["walk"]], distributions, max_pa)
    if market == "hits":
        indices = [OUTCOME_INDEX[name] for name in ("single", "double", "triple", "home_run")]
        return count_pmf(probability[:, indices].sum(axis=1), distributions, max_pa)
    if market == "home_runs":
        return count_pmf(probability[:, OUTCOME_INDEX["home_run"]], distributions, max_pa)
    if market == "total_bases":
        return total_bases_pmf(probability, distributions, max_pa)
    raise ValueError(f"unknown batter-recency market: {market}")


def run_provisional_batter_recency_tournament(panel: pd.DataFrame) -> dict[str, Any]:
    """Run the two registered recency variants on spent 2023 outcomes."""

    validate_batter_recency_matrix(panel)
    frame = panel.loc[
        pd.to_numeric(panel["out_pa"], errors="coerce").fillna(0).gt(0)
    ].reset_index(drop=True)
    folds = chronological_splits(frame, 4)
    evaluated = np.zeros(len(frame), dtype=bool)
    probabilities = {
        arm: np.full((len(frame), len(PA_OUTCOMES)), np.nan) for arm in ALL_ARMS
    }
    pa_distributions: list[Mapping[int, float] | None] = [None] * len(frame)
    fold_records: list[dict[str, Any]] = []
    for fold_number, (train_rows, validation_rows) in enumerate(folds, start=1):
        train = frame.iloc[train_rows]
        validation = frame.iloc[validation_rows]
        league = league_probability(train)
        concentration = fit_dirichlet_concentration(train, league)
        base_train = empirical_bayes_probability(train, league, concentration)
        base_validation = empirical_bayes_probability(validation, league, concentration)
        probabilities["league_rate"][validation_rows] = np.tile(league, (len(validation), 1))
        probabilities["eb_fixed_200"][validation_rows] = empirical_bayes_probability(
            validation, league, 200.0
        )
        probabilities["eb_fitted"][validation_rows] = base_validation
        probabilities["feature_off"][validation_rows] = base_validation
        counts = target_counts(train)
        for variant in ("support_only", "missingness_only", *SUBSTANTIVE_VARIANTS):
            transform, train_features = fit_batter_recency_transform(train, variant)
            validation_features = apply_batter_recency_transform(validation, transform)
            coefficients = fit_full_simplex_offset(base_train, train_features, counts)
            probabilities[variant][validation_rows] = adjust_full_simplex_probability(
                base_validation, validation_features, coefficients
            )
        transform, train_features = fit_batter_recency_transform(
            train, "deterministic_shuffle"
        )
        validation_features = apply_batter_recency_transform(validation, transform)
        train_rng = np.random.default_rng(BOOTSTRAP_SEED + 100 * fold_number)
        validation_rng = np.random.default_rng(BOOTSTRAP_SEED + 100 * fold_number + 1)
        coefficients = fit_full_simplex_offset(
            base_train,
            train_features[train_rng.permutation(len(train_features))],
            counts,
        )
        probabilities["deterministic_shuffle"][validation_rows] = (
            adjust_full_simplex_probability(
                base_validation,
                validation_features[validation_rng.permutation(len(validation_features))],
                coefficients,
            )
        )
        distribution = pooled_pa_distribution(train)
        for row in validation_rows:
            pa_distributions[row] = distribution
        evaluated[validation_rows] = True
        fold_records.append(
            {
                "fold": fold_number,
                "train_rows": int(len(train)),
                "validation_rows": int(len(validation)),
                "train_date_max": str(train["game_date"].max()),
                "validation_date_min": str(validation["game_date"].min()),
                "validation_date_max": str(validation["game_date"].max()),
                "fitted_dirichlet_concentration": concentration,
            }
        )
    _require(
        np.array_equal(
            probabilities["feature_off"][evaluated], probabilities["eb_fitted"][evaluated]
        ),
        "batter-recency feature-off control differs from parent",
    )
    _require(
        all(np.isfinite(probabilities[arm][evaluated]).all() for arm in ALL_ARMS),
        "batter-recency OOF probability is incomplete",
    )
    for arm in ALL_ARMS:
        _require(
            np.allclose(
                probabilities[arm][evaluated].sum(axis=1), 1.0, rtol=0.0, atol=1e-12
            ),
            f"batter-recency probability simplex is incoherent: {arm}",
        )

    selection = frame.loc[evaluated].reset_index(drop=True)
    distributions = [pa_distributions[row] for row in np.flatnonzero(evaluated)]
    _require(all(value is not None for value in distributions), "OOF PA distribution is incomplete")
    max_pa = int(pd.to_numeric(frame["out_pa"], errors="raise").max())
    comparators = (
        "league_rate",
        "eb_fixed_200",
        "eb_fitted",
        "support_only",
        "missingness_only",
    )
    primary: dict[str, Any] = {}
    for market in ("strikeouts", "walks", "hits", "home_runs", "total_bases"):
        observed = _observed(selection, market)
        scores: dict[str, Mapping[str, float | int]] = {}
        losses: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for arm in ALL_ARMS:
            market_pmf = _pmf(probabilities[arm][evaluated], distributions, max_pa, market)
            scores[arm] = score_count_market(observed, market_pmf)
            losses[arm] = _count_losses(observed, market_pmf)
        strongest = min(comparators, key=lambda arm: float(scores[arm]["log_loss"]))
        variants = {
            variant: _evaluate_variant(
                variant=variant,
                scores=scores,
                losses=losses,
                comparators=comparators,
                selection=selection,
            )
            for variant in (*SUBSTANTIVE_VARIANTS, "deterministic_shuffle")
        }
        selected = min(
            SUBSTANTIVE_VARIANTS, key=lambda arm: float(scores[arm]["log_loss"])
        )
        passed = bool(
            variants[selected]["passed"] and not variants["deterministic_shuffle"]["passed"]
        )
        primary[market] = {
            "strongest_eligible_simpler_parent": strongest,
            "parent_scores": {arm: scores[arm] for arm in comparators},
            "selected_finite_variant": selected,
            "variants": variants,
            "feature_off_parent_byte_equal": True,
            "shuffle_control_did_not_pass_family_gate": not bool(
                variants["deterministic_shuffle"]["passed"]
            ),
            "provisional_market_verdict": (
                "PROVISIONAL_INCREMENTAL_VALUE_DETECTED_NON_PROMOTABLE"
                if passed
                else "PROVISIONAL_NO_MATERIAL_INCREMENTAL_VALUE_NON_PROMOTABLE"
            ),
        }
    return {
        "schema_version": "shared-pa-batter-recency-provisional-tournament-v1",
        "status": RESEARCH_STATUS,
        "development_season": DEVELOPMENT_SEASON,
        "family_definition": (
            "strictly-prior batter outcome recency, prior-PA age/inactivity, support, "
            "and explicit missingness; no same-day or opponent context"
        ),
        "rows_physical": int(len(panel)),
        "rows_fit_eligible": int(len(frame)),
        "rows_oof": int(evaluated.sum()),
        "initial_training_only_rows": int((~evaluated).sum()),
        "folds": fold_records,
        "candidate_budget": list(SUBSTANTIVE_VARIANTS),
        "controls": list(CONTROL_VARIANTS),
        "primary_markets": primary,
        "attribution_policy": (
            "No alternative windows, decay constants, or transformations unless this exact "
            "registered family passes a market gate."
        ),
        "invariants": {
            "same_rows_all_arms": True,
            "same_folds_all_arms": True,
            "same_pa_distribution_all_arms": True,
            "shared_eight_class_pa_simplex": True,
            "feature_off_equals_parent_exactly": True,
            "same_day_inputs_consumed": False,
            "target_pitcher_identity_consumed": False,
            "expected_stat_fields_consumed": False,
            "2024_opened": False,
            "2025_opened": False,
            "may_2026_opened": False,
            "network_request_performed": False,
            "production_changed": False,
            "promotion_eligible": False,
            "betting_authorized": False,
        },
    }


def synthetic_signal_control() -> bool:
    """Prove the full-simplex residual learner recovers a known signal."""

    feature = np.linspace(-2.0, 2.0, 500)[:, None]
    base = np.tile(
        np.asarray([0.22, 0.09, 0.14, 0.05, 0.01, 0.04, 0.40, 0.05]),
        (len(feature), 1),
    )
    truth_coefficients = np.asarray(
        [[0.8], [-0.2], [0.3], [0.5], [0.1], [0.4], [-0.3]]
    )
    truth = adjust_full_simplex_probability(base, feature, truth_coefficients)
    counts = truth * 200.0
    fitted = fit_full_simplex_offset(base, feature, counts)
    learned = adjust_full_simplex_probability(base, feature, fitted)
    parent_loss = float(-(counts * np.log(base)).sum())
    learned_loss = float(-(counts * np.log(learned)).sum())
    return learned_loss < parent_loss * 0.99 and np.allclose(
        learned.sum(axis=1), 1.0
    )
