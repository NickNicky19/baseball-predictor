"""Deterministic provisional contact-quality tournament on spent 2023 evidence.

The experimental parent is the receipt-bound, strict-prior eight-class
empirical-Bayes PA simplex.  A contact candidate may redistribute only the
conditional probabilities of single, double, triple, home run, and BIP out;
it cannot change the parent's total contact mass or any non-contact class.

This module is deliberately research-only and has no network, production, or
promotion path.  Hits, home runs, and total bases are adjudicated separately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.evaluation.shared_pa_eb_offset_catboost import PA_OUTCOMES, target_counts
from src.evaluation.shared_pa_plate_discipline_tournament import (
    DEVELOPMENT_SEASON,
    MATERIALITY_FRACTION,
    chronological_splits,
    count_pmf,
    empirical_bayes_probability,
    fit_dirichlet_concentration,
    league_probability,
    paired_cluster_interval,
    pooled_pa_distribution,
    score_count_market,
)


RESEARCH_STATUS = "PROVISIONAL_NON_PROMOTABLE_PENDING_QUALIFIED_SOURCE_CONFIRMATION"
BOOTSTRAP_DRAWS = 1_000
BOOTSTRAP_SEED = 20260805
L2_PENALTY = 1.0
OUTCOME_INDEX = {name: index for index, name in enumerate(PA_OUTCOMES)}
CONTACT_OUTCOMES = ("single", "double", "triple", "home_run", "bip_out")
CONTACT_INDICES = tuple(OUTCOME_INDEX[name] for name in CONTACT_OUTCOMES)
ADJUSTED_CONTACT_INDICES = CONTACT_INDICES[:-1]
NONCONTACT_INDICES = tuple(index for index in range(len(PA_OUTCOMES)) if index not in CONTACT_INDICES)

RATE_SPECS = (
    (
        "hard_hit_joint",
        "history_contact_hard_hit_count_joint",
        "history_contact_joint_denominator",
    ),
    (
        "sweet_spot_joint",
        "history_contact_sweet_spot_count_joint",
        "history_contact_joint_denominator",
    ),
    (
        "hard_hit_sweet_spot_joint",
        "history_contact_hard_hit_sweet_spot_count_joint",
        "history_contact_joint_denominator",
    ),
    ("line_joint", "history_contact_line_count_joint", "history_contact_joint_denominator"),
    ("fly_joint", "history_contact_fly_count_joint", "history_contact_joint_denominator"),
    ("popup_joint", "history_contact_popup_count_joint", "history_contact_joint_denominator"),
    (
        "barrel_classified",
        "history_contact_barrel_count_classified",
        "history_contact_speed_angle_denominator",
    ),
)

COUNT_COLUMNS = (
    "history_contact_bip",
    "history_contact_ev_denominator",
    "history_contact_ev_missing_count",
    "history_contact_ev50_count",
    "history_contact_joint_denominator",
    "history_contact_joint_missing_count",
    "history_contact_hard_hit_count_joint",
    "history_contact_sweet_spot_count_joint",
    "history_contact_hard_hit_sweet_spot_count_joint",
    "history_contact_ground_count_joint",
    "history_contact_line_count_joint",
    "history_contact_fly_count_joint",
    "history_contact_popup_count_joint",
    "history_contact_speed_angle_denominator",
    "history_contact_speed_angle_missing_count",
    "history_contact_barrel_count_classified",
    "history_contact_classified_hard_hit_count",
)

SUBSTANTIVE_VARIANTS = ("raw_contact_block", "fold_shrunk_contact_block")
CONTROL_VARIANTS = ("feature_off", "support_only", "deterministic_shuffle")
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


def validate_contact_matrix(frame: pd.DataFrame) -> None:
    """Validate count populations and strict-prior chronology without repair."""

    required = {
        "game_date",
        "game_pk",
        "player_id",
        "history_contact_ev50_mean",
        "history_contact_max_source_date",
        *COUNT_COLUMNS,
    }
    missing = sorted(required.difference(frame.columns))
    _require(not missing, f"contact matrix missing columns: {missing}")
    counts = frame.loc[:, COUNT_COLUMNS].apply(pd.to_numeric, errors="coerce")
    _require(not counts.isna().any().any(), "contact counts are missing or nonnumeric")
    _require((counts >= 0).all().all(), "contact counts are negative")
    _require(
        np.equal(counts.to_numpy(float), np.floor(counts.to_numpy(float))).all(),
        "contact counts are nonintegral",
    )
    bip = counts["history_contact_bip"]
    for denominator, missing_name in (
        ("history_contact_ev_denominator", "history_contact_ev_missing_count"),
        ("history_contact_joint_denominator", "history_contact_joint_missing_count"),
        ("history_contact_speed_angle_denominator", "history_contact_speed_angle_missing_count"),
    ):
        _require(
            (counts[denominator] + counts[missing_name]).eq(bip).all(),
            f"contact denominator/missing partition differs: {denominator}",
        )
    _require(
        counts["history_contact_joint_denominator"]
        .le(counts["history_contact_ev_denominator"])
        .all(),
        "joint contact denominator exceeds measured EV",
    )
    _require(
        counts["history_contact_speed_angle_denominator"]
        .le(counts["history_contact_joint_denominator"])
        .all(),
        "classified contact denominator exceeds joint EV/LA",
    )
    expected_ev50 = np.ceil(counts["history_contact_ev_denominator"] / 2.0).astype(int)
    _require(
        counts["history_contact_ev50_count"].eq(expected_ev50).all(),
        "EV50 support differs from its measured-EV population",
    )
    _require(
        (
            counts["history_contact_ground_count_joint"]
            + counts["history_contact_line_count_joint"]
            + counts["history_contact_fly_count_joint"]
            + counts["history_contact_popup_count_joint"]
        )
        .eq(counts["history_contact_joint_denominator"])
        .all(),
        "launch-angle band counts do not partition joint EV/LA",
    )
    for _, numerator, denominator in RATE_SPECS:
        _require(
            counts[numerator].le(counts[denominator]).all(),
            f"contact numerator exceeds denominator: {numerator}",
        )
    _require(
        counts["history_contact_barrel_count_classified"]
        .le(counts["history_contact_classified_hard_hit_count"])
        .all(),
        "barrel count exceeds classified hard-hit count",
    )
    ev50 = pd.to_numeric(frame["history_contact_ev50_mean"], errors="coerce")
    supported = counts["history_contact_ev50_count"].gt(0)
    _require(not ev50.loc[supported].isna().any(), "supported EV50 mean is missing")
    _require((ev50.loc[supported] > 0.0).all(), "supported EV50 mean is nonpositive")
    _require(ev50.loc[~supported].isna().all(), "zero-support EV50 carries a value")

    target = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    source = pd.to_datetime(
        frame["history_contact_max_source_date"], format="%Y-%m-%d", errors="coerce"
    )
    _require(not target.isna().any(), "contact target date is malformed")
    _require(target.dt.year.eq(DEVELOPMENT_SEASON).all(), "later season entered contact matrix")
    _require(
        not (source.notna() & source.ge(target)).any(),
        "contact matrix contains same-day or future evidence",
    )


def _fit_beta_prior(frame: pd.DataFrame, numerator: str, denominator: str) -> tuple[float, float]:
    ordered = frame.sort_values(["player_id", "game_date"], kind="mergesort")
    latest = ordered.groupby("player_id", sort=True).tail(1)
    n = latest[numerator].to_numpy(float)
    d = latest[denominator].to_numpy(float)
    _require((n <= d).all(), f"{numerator} exceeds {denominator}")
    total = float(d.sum())
    _require(total > 0.0, f"{denominator} has no fold-training support")
    mean = min(max(float(n.sum() / total), 1e-6), 1.0 - 1e-6)

    def score(concentration: float) -> float:
        alpha = mean * concentration
        beta = (1.0 - mean) * concentration
        return float(
            sum(
                math.lgamma(float(ni) + alpha)
                + math.lgamma(float(di - ni) + beta)
                - math.lgamma(float(di) + concentration)
                - math.lgamma(alpha)
                - math.lgamma(beta)
                + math.lgamma(concentration)
                for ni, di in zip(n, d)
            )
        )

    grid = np.linspace(math.log(0.1), math.log(100_000.0), 121)
    values = np.asarray([score(math.exp(float(value))) for value in grid])
    concentration = math.exp(float(grid[int(np.argmax(values))]))
    return mean * concentration, (1.0 - mean) * concentration


def _fit_ev50_fill(frame: pd.DataFrame) -> float:
    ordered = frame.sort_values(["player_id", "game_date"], kind="mergesort")
    latest = ordered.groupby("player_id", sort=True).tail(1)
    support = latest["history_contact_ev50_count"].to_numpy(float)
    values = pd.to_numeric(latest["history_contact_ev50_mean"], errors="coerce").to_numpy(float)
    present = (support > 0.0) & np.isfinite(values)
    _require(present.any(), "fold training lacks EV50 support")
    return float(np.average(values[present], weights=support[present]))


@dataclass(frozen=True)
class ContactFeatureTransform:
    variant: str
    names: tuple[str, ...]
    beta_priors: Mapping[str, tuple[float, float]]
    ev50_fill: float
    mean: np.ndarray
    scale: np.ndarray
    retained: np.ndarray


def _support_columns(frame: pd.DataFrame) -> tuple[list[str], list[np.ndarray]]:
    bip = frame["history_contact_bip"].to_numpy(float)
    joint = frame["history_contact_joint_denominator"].to_numpy(float)
    joint_missing = frame["history_contact_joint_missing_count"].to_numpy(float)
    ev50_support = frame["history_contact_ev50_count"].to_numpy(float)
    missing_share = np.divide(
        joint_missing,
        bip,
        out=np.zeros_like(joint_missing),
        where=bip > 0.0,
    )
    return (
        [
            "contact_log1p_bip",
            "contact_log1p_joint_support",
            "contact_joint_missing_share",
            "contact_log1p_ev50_support",
            "contact_zero_joint_support",
        ],
        [
            np.log1p(bip),
            np.log1p(joint),
            missing_share,
            np.log1p(ev50_support),
            (joint == 0.0).astype(float),
        ],
    )


def _unscaled_features(
    frame: pd.DataFrame,
    *,
    variant: str,
    beta_priors: Mapping[str, tuple[float, float]],
    ev50_fill: float,
) -> tuple[list[str], np.ndarray]:
    support_names, support_columns = _support_columns(frame)
    if variant == "support_only":
        return support_names, np.column_stack(support_columns)
    effective = "fold_shrunk_contact_block" if variant == "deterministic_shuffle" else variant
    _require(effective in SUBSTANTIVE_VARIANTS, f"unknown contact feature variant: {variant}")
    names: list[str] = []
    columns: list[np.ndarray] = []
    ev50_support = frame["history_contact_ev50_count"].to_numpy(float)
    ev50 = pd.to_numeric(frame["history_contact_ev50_mean"], errors="coerce").to_numpy(float)
    ev50 = np.where(ev50_support > 0.0, ev50, ev50_fill)
    names.extend(["contact_ev50_mean", "contact_ev50_zero_support"])
    columns.extend([ev50, (ev50_support == 0.0).astype(float)])
    for label, numerator, denominator in RATE_SPECS:
        n = frame[numerator].to_numpy(float)
        d = frame[denominator].to_numpy(float)
        missing = d == 0.0
        alpha, beta = beta_priors[label]
        if effective == "raw_contact_block":
            pooled = alpha / (alpha + beta)
            value = np.divide(n, d, out=np.full_like(n, pooled), where=~missing)
            names.append(f"contact_{label}_raw_rate")
        else:
            value = (n + alpha) / (d + alpha + beta)
            names.append(f"contact_{label}_fold_shrunk_rate")
        columns.append(value)
    names.extend(support_names)
    columns.extend(support_columns)
    return names, np.column_stack(columns)


def fit_contact_transform(
    frame: pd.DataFrame, variant: str
) -> tuple[ContactFeatureTransform, np.ndarray]:
    validate_contact_matrix(frame)
    beta_priors = {
        label: _fit_beta_prior(frame, numerator, denominator)
        for label, numerator, denominator in RATE_SPECS
    }
    ev50_fill = _fit_ev50_fill(frame)
    names, raw = _unscaled_features(
        frame,
        variant=variant,
        beta_priors=beta_priors,
        ev50_fill=ev50_fill,
    )
    mean = raw.mean(axis=0)
    scale = raw.std(axis=0, ddof=0)
    retained = scale > 1e-12
    _require(retained.any(), f"contact feature variant is constant: {variant}")
    transform = ContactFeatureTransform(
        variant=variant,
        names=tuple(name for name, keep in zip(names, retained) if keep),
        beta_priors=beta_priors,
        ev50_fill=ev50_fill,
        mean=mean,
        scale=np.where(retained, scale, 1.0),
        retained=retained,
    )
    return transform, ((raw - mean) / transform.scale)[:, retained]


def apply_contact_transform(frame: pd.DataFrame, transform: ContactFeatureTransform) -> np.ndarray:
    validate_contact_matrix(frame)
    _, raw = _unscaled_features(
        frame,
        variant=transform.variant,
        beta_priors=transform.beta_priors,
        ev50_fill=transform.ev50_fill,
    )
    return ((raw - transform.mean) / transform.scale)[:, transform.retained]


def adjust_contact_probability(
    base: np.ndarray, features: np.ndarray, coefficients: np.ndarray
) -> np.ndarray:
    """Redistribute the parent's conditional contact outcomes, preserving BIP mass."""

    _require(
        coefficients.shape == (len(ADJUSTED_CONTACT_INDICES), features.shape[1]),
        "contact coefficient shape differs",
    )
    result = base.copy()
    contact = base[:, CONTACT_INDICES]
    contact_mass = contact.sum(axis=1)
    _require((contact_mass > 0.0).all(), "parent contact mass is zero")
    conditional = contact / contact_mass[:, None]
    logits = np.log(conditional)
    logits[:, : len(ADJUSTED_CONTACT_INDICES)] += features @ coefficients.T
    logits -= logits.max(axis=1, keepdims=True)
    adjusted = np.exp(logits)
    adjusted /= adjusted.sum(axis=1, keepdims=True)
    result[:, CONTACT_INDICES] = adjusted * contact_mass[:, None]
    _require(
        np.array_equal(result[:, NONCONTACT_INDICES], base[:, NONCONTACT_INDICES]),
        "contact adjustment changed a non-contact class",
    )
    _require(
        np.allclose(
            result[:, CONTACT_INDICES].sum(axis=1),
            contact_mass,
            rtol=0.0,
            atol=1e-12,
        ),
        "contact adjustment changed total contact mass",
    )
    _require(
        np.allclose(result.sum(axis=1), 1.0, rtol=0.0, atol=1e-12),
        "adjusted contact simplex differs",
    )
    return result


def _objective(
    base: np.ndarray,
    features: np.ndarray,
    counts: np.ndarray,
    coefficients: np.ndarray,
) -> float:
    probability = adjust_contact_probability(base, features, coefficients)
    return float(
        -(counts * np.log(np.clip(probability, 1e-12, 1.0))).sum()
        + 0.5 * L2_PENALTY * np.square(coefficients).sum()
    )


def fit_contact_offset(base: np.ndarray, features: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Fit a deterministic ridge multinomial offset conditional on contact."""

    _require(len(base) == len(features) == len(counts), "contact model row count differs")
    p = features.shape[1]
    categories = len(ADJUSTED_CONTACT_INDICES)
    coefficients = np.zeros((categories, p), dtype=float)
    contact_counts = counts[:, CONTACT_INDICES]
    exposure = contact_counts.sum(axis=1)
    _require(float(exposure.sum()) > 0.0, "contact model has no BIP exposure")
    for _ in range(50):
        probability = adjust_contact_probability(base, features, coefficients)
        conditional = probability[:, CONTACT_INDICES]
        conditional /= conditional.sum(axis=1, keepdims=True)
        gradient = np.vstack(
            [
                features.T @ (exposure * conditional[:, j] - contact_counts[:, j])
                for j in range(categories)
            ]
        ) + L2_PENALTY * coefficients
        if float(np.max(np.abs(gradient))) < 1e-8:
            break
        blocks: list[list[np.ndarray]] = []
        for j in range(categories):
            row: list[np.ndarray] = []
            for k in range(categories):
                weight = exposure * conditional[:, j] * (
                    (1.0 if j == k else 0.0) - conditional[:, k]
                )
                block = features.T @ (features * weight[:, None])
                if j == k:
                    block = block + L2_PENALTY * np.eye(p)
                row.append(block)
            blocks.append(row)
        hessian = np.block(blocks)
        step = np.linalg.solve(hessian, gradient.reshape(-1))
        current = _objective(base, features, counts, coefficients)
        accepted = False
        fraction = 0.0
        for fraction in (1.0, 0.5, 0.25, 0.125, 0.0625, 0.03125, 0.015625):
            candidate = coefficients - fraction * step.reshape(categories, p)
            if _objective(base, features, counts, candidate) < current:
                coefficients = candidate
                accepted = True
                break
        if not accepted or float(np.max(np.abs(fraction * step))) < 1e-8:
            break
    return coefficients


def total_bases_pmf(
    probability: np.ndarray,
    distributions: Sequence[Mapping[int, float]],
    max_pa: int,
) -> np.ndarray:
    """Return a coherent compound PMF for total bases under the shared PA simplex."""

    per_pa = np.column_stack(
        [
            probability[:, NONCONTACT_INDICES].sum(axis=1)
            + probability[:, OUTCOME_INDEX["bip_out"]],
            probability[:, OUTCOME_INDEX["single"]],
            probability[:, OUTCOME_INDEX["double"]],
            probability[:, OUTCOME_INDEX["triple"]],
            probability[:, OUTCOME_INDEX["home_run"]],
        ]
    )
    _require(
        np.allclose(per_pa.sum(axis=1), 1.0, rtol=0.0, atol=1e-12),
        "per-PA total-bases distribution differs",
    )
    output = np.zeros((len(probability), 4 * max_pa + 1), dtype=float)
    for row, (q, distribution) in enumerate(zip(per_pa, distributions)):
        for pa, weight in distribution.items():
            _require(0 <= int(pa) <= max_pa, "PA distribution exceeds total-bases support")
            pmf = np.asarray([1.0])
            for _ in range(int(pa)):
                pmf = np.convolve(pmf, q)
            output[row, : len(pmf)] += float(weight) * pmf
    _require(
        np.allclose(output.sum(axis=1), 1.0, rtol=0.0, atol=1e-10),
        "total-bases PMF is not normalized",
    )
    return output


def _count_losses(observed: np.ndarray, pmf: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rows = np.arange(len(observed))
    _require((observed >= 0).all() and (observed < pmf.shape[1]).all(), "count lies outside PMF")
    brier = 1.0 + np.square(pmf).sum(axis=1) - 2.0 * pmf[rows, observed]
    log_loss = -np.log(np.clip(pmf[rows, observed], 1e-12, 1.0))
    return brier, log_loss


def _market_observed(frame: pd.DataFrame, market: str) -> np.ndarray:
    if market == "hits":
        return pd.to_numeric(frame["out_hits"], errors="raise").astype(int).to_numpy()
    if market == "home_runs":
        return pd.to_numeric(frame["out_hr"], errors="raise").astype(int).to_numpy()
    if market == "total_bases":
        hits = pd.to_numeric(frame["out_hits"], errors="raise").astype(int)
        doubles = pd.to_numeric(frame["out_doubles"], errors="raise").astype(int)
        triples = pd.to_numeric(frame["out_triples"], errors="raise").astype(int)
        home_runs = pd.to_numeric(frame["out_hr"], errors="raise").astype(int)
        return (hits + doubles + 2 * triples + 3 * home_runs).to_numpy()
    raise ValueError(f"unknown contact market: {market}")


def _market_pmf(
    probability: np.ndarray,
    distributions: Sequence[Mapping[int, float]],
    max_pa: int,
    market: str,
) -> np.ndarray:
    if market == "hits":
        q = probability[:, [OUTCOME_INDEX[name] for name in ("single", "double", "triple", "home_run")]].sum(axis=1)
        return count_pmf(q, distributions, max_pa)
    if market == "home_runs":
        return count_pmf(probability[:, OUTCOME_INDEX["home_run"]], distributions, max_pa)
    if market == "total_bases":
        return total_bases_pmf(probability, distributions, max_pa)
    raise ValueError(f"unknown contact market: {market}")


def _evaluate_variant(
    *,
    variant: str,
    scores: Mapping[str, Mapping[str, float | int]],
    losses: Mapping[str, tuple[np.ndarray, np.ndarray]],
    comparators: Sequence[str],
    selection: pd.DataFrame,
) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    passed = True
    for comparator in comparators:
        metrics_by_name: dict[str, Any] = {}
        for metric_index, metric in enumerate(("brier", "log_loss")):
            game_interval = paired_cluster_interval(
                losses[variant][metric_index],
                losses[comparator][metric_index],
                selection["game_pk"],
                draws=BOOTSTRAP_DRAWS,
                seed=BOOTSTRAP_SEED + metric_index,
            )
            player_interval = paired_cluster_interval(
                losses[variant][metric_index],
                losses[comparator][metric_index],
                selection["player_id"],
                draws=BOOTSTRAP_DRAWS,
                seed=BOOTSTRAP_SEED + 10 + metric_index,
            )
            material = (
                game_interval["point"]
                <= -MATERIALITY_FRACTION * float(scores[comparator][metric])
                and game_interval["upper"] < 0.0
                and player_interval["upper"] < 0.0
            )
            metrics_by_name[metric] = {
                "game_cluster": game_interval,
                "player_cluster": player_interval,
                "one_percent_materiality_passed": bool(material),
            }
            passed = passed and material
        calibration = abs(float(scores[variant]["mean_count_bias"])) <= abs(
            float(scores[comparator]["mean_count_bias"])
        ) + 1e-12
        discrimination = float(scores[variant]["over_0_5_auc"]) >= float(
            scores[comparator]["over_0_5_auc"]
        ) - 1e-12
        passed = passed and calibration and discrimination
        comparison[comparator] = {
            "paired": metrics_by_name,
            "mean_count_calibration_noninferior": bool(calibration),
            "over_0_5_auc_noninferior": bool(discrimination),
        }
    return {"score": scores[variant], "comparators": comparison, "passed": bool(passed)}


def run_provisional_contact_tournament(
    panel: pd.DataFrame, contact: pd.DataFrame
) -> dict[str, Any]:
    """Run the frozen finite contact block on spent 2023 development outcomes."""

    _require(len(panel) == len(contact), "panel and contact rows differ")
    identity = ["game_pk", "player_id", "game_date"]
    _require(
        panel[identity].reset_index(drop=True).equals(contact[identity].reset_index(drop=True)),
        "panel and contact identities differ",
    )
    frame = pd.concat(
        [panel.reset_index(drop=True), contact.drop(columns=identity).reset_index(drop=True)],
        axis=1,
    )
    validate_contact_matrix(frame)
    frame = frame.loc[pd.to_numeric(frame["out_pa"], errors="coerce").fillna(0).gt(0)].reset_index(drop=True)
    folds = chronological_splits(frame, 4)
    evaluated = np.zeros(len(frame), dtype=bool)
    probabilities = {
        arm: np.full((len(frame), len(PA_OUTCOMES)), np.nan) for arm in ALL_ARMS
    }
    fold_pa_distribution: list[Mapping[int, float] | None] = [None] * len(frame)
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
        for variant in ("support_only", *SUBSTANTIVE_VARIANTS):
            transform, train_features = fit_contact_transform(train, variant)
            validation_features = apply_contact_transform(validation, transform)
            coefficients = fit_contact_offset(base_train, train_features, counts)
            probabilities[variant][validation_rows] = adjust_contact_probability(
                base_validation, validation_features, coefficients
            )
        transform, train_features = fit_contact_transform(train, "deterministic_shuffle")
        validation_features = apply_contact_transform(validation, transform)
        train_rng = np.random.default_rng(BOOTSTRAP_SEED + 100 * fold_number)
        validation_rng = np.random.default_rng(BOOTSTRAP_SEED + 100 * fold_number + 1)
        shuffled_train = train_features[train_rng.permutation(len(train_features))]
        shuffled_validation = validation_features[
            validation_rng.permutation(len(validation_features))
        ]
        coefficients = fit_contact_offset(base_train, shuffled_train, counts)
        probabilities["deterministic_shuffle"][validation_rows] = adjust_contact_probability(
            base_validation, shuffled_validation, coefficients
        )
        distribution = pooled_pa_distribution(train)
        for row in validation_rows:
            fold_pa_distribution[row] = distribution
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
                "pooled_pa_distribution": {
                    str(key): value for key, value in distribution.items()
                },
            }
        )
    _require(
        np.array_equal(
            probabilities["feature_off"][evaluated], probabilities["eb_fitted"][evaluated]
        ),
        "contact feature-off control differs from parent",
    )
    _require(
        all(np.isfinite(probabilities[arm][evaluated]).all() for arm in ALL_ARMS),
        "contact OOF probability is incomplete",
    )
    parent_contact_mass = probabilities["eb_fitted"][evaluated][:, CONTACT_INDICES].sum(axis=1)
    for arm in ("support_only", *SUBSTANTIVE_VARIANTS, "deterministic_shuffle"):
        _require(
            np.allclose(
                probabilities[arm][evaluated][:, CONTACT_INDICES].sum(axis=1),
                parent_contact_mass,
                rtol=0.0,
                atol=1e-12,
            ),
            f"contact arm changed total BIP mass: {arm}",
        )
        _require(
            np.array_equal(
                probabilities[arm][evaluated][:, NONCONTACT_INDICES],
                probabilities["eb_fitted"][evaluated][:, NONCONTACT_INDICES],
            ),
            f"contact arm changed non-contact probabilities: {arm}",
        )

    selection = frame.loc[evaluated].reset_index(drop=True)
    distributions = [fold_pa_distribution[row] for row in np.flatnonzero(evaluated)]
    _require(all(value is not None for value in distributions), "OOF PA distribution is incomplete")
    max_pa = int(pd.to_numeric(frame["out_pa"], errors="raise").max())
    primary: dict[str, Any] = {}
    comparators = ("league_rate", "eb_fixed_200", "eb_fitted", "support_only")
    for market in ("hits", "home_runs", "total_bases"):
        observed = _market_observed(selection, market)
        scores: dict[str, Mapping[str, float | int]] = {}
        losses: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for arm in ALL_ARMS:
            pmf = _market_pmf(
                probabilities[arm][evaluated], distributions, max_pa, market
            )
            scores[arm] = score_count_market(observed, pmf)
            losses[arm] = _count_losses(observed, pmf)
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
        selected = min(SUBSTANTIVE_VARIANTS, key=lambda arm: float(scores[arm]["log_loss"]))
        shuffle_passed = bool(variants["deterministic_shuffle"]["passed"])
        verdict_passed = bool(variants[selected]["passed"] and not shuffle_passed)
        primary[market] = {
            "strongest_eligible_simpler_parent": strongest,
            "parent_scores": {arm: scores[arm] for arm in comparators},
            "selected_finite_variant": selected,
            "variants": variants,
            "feature_off_parent_byte_equal": True,
            "shuffle_control_did_not_pass_family_gate": not shuffle_passed,
            "provisional_market_verdict": (
                "PROVISIONAL_INCREMENTAL_VALUE_DETECTED_NON_PROMOTABLE"
                if verdict_passed
                else "PROVISIONAL_NO_MATERIAL_INCREMENTAL_VALUE_NON_PROMOTABLE"
            ),
        }
    return {
        "schema_version": "shared-pa-contact-quality-provisional-tournament-v1",
        "status": RESEARCH_STATUS,
        "development_season": DEVELOPMENT_SEASON,
        "rows_physical": int(len(panel)),
        "rows_fit_eligible": int(len(frame)),
        "rows_oof": int(evaluated.sum()),
        "initial_training_only_rows": int((~evaluated).sum()),
        "folds": fold_records,
        "candidate_budget": list(SUBSTANTIVE_VARIANTS),
        "controls": list(CONTROL_VARIANTS),
        "primary_markets": primary,
        "attribution_policy": (
            "Run only predeclared singleton and delete-one attribution if a contact block "
            "passes an exact primary market; otherwise stop that market formulation."
        ),
        "invariants": {
            "same_rows_all_arms": True,
            "same_folds_all_arms": True,
            "same_pa_distribution_all_arms": True,
            "shared_eight_class_pa_simplex": True,
            "total_contact_mass_preserved": True,
            "noncontact_probabilities_unchanged": True,
            "feature_off_equals_parent_exactly": True,
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
    """Prove the conditional contact learner recovers a known deterministic signal."""

    x = np.linspace(-2.0, 2.0, 400)[:, None]
    base = np.tile(
        np.asarray([0.22, 0.09, 0.14, 0.05, 0.01, 0.04, 0.40, 0.05]),
        (len(x), 1),
    )
    truth_coefficients = np.asarray([[0.5], [0.8], [0.2], [1.2]])
    truth = adjust_contact_probability(base, x, truth_coefficients)
    counts = truth * 200.0
    fitted = fit_contact_offset(base, x, counts)
    learned = adjust_contact_probability(base, x, fitted)
    parent_loss = float(-(counts * np.log(base)).sum())
    learned_loss = float(-(counts * np.log(learned)).sum())
    return (
        learned_loss < parent_loss * 0.99
        and np.all(fitted[:, 0] > 0.0)
        and np.allclose(
            learned[:, CONTACT_INDICES].sum(axis=1),
            base[:, CONTACT_INDICES].sum(axis=1),
            rtol=0.0,
            atol=1e-12,
        )
    )
