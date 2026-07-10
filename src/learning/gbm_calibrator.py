"""
B2 calibration layer for the CatBoost per-category props models.

Turns CatBoost point predictions into calibrated P(over line L) per category.

Design (settled in the B2 chat, see repo context file "B2 KICKOFF"):
  Approach (a), residual/count-distribution, two-stage:
    stage 1 (count model): a per-category conditional count distribution keyed
      to the point estimate lambda = predicted_value, giving a RAW
      P(actual >= L). Family is chosen from the CONDITIONAL dispersion of the
      walk-forward pairs (measured var/mean within predicted-value strata),
      NOT the marginal dispersion (which is inflated by the spread of lambda):
        hits, home_runs -> Poisson         (cond var/mean ~ 1.0, or below)
        hrr             -> negative-binomial (cond var/mean ~ 2.2, stable)
        strikeouts      -> negative-binomial (cond var/mean ~ 1.0-1.3)
      lambda = predicted_value directly. Because the stage-2 calibrator is
      monotone in raw-P (which is monotone in lambda), any *monotone*
      conditional bias in the point estimate is absorbed downstream for the
      isotonic cats; the pairs also show E[actual|yhat] ~ yhat, so no separate
      mean recalibration is fit (keeps the count model to a single free
      parameter: dispersion alpha, MLE'd on the pairs).
    stage 2 (probability calibration): map raw-P -> calibrated-P against the
      realized over/under outcomes, POOLED across the standard lines of the
      category (each pair contributes one training example per line, so raw-P
      already carries the line and the calibrator stays line-agnostic and
      generalizes to any posted L):
        hitter cats (n ~ 3.2k) -> isotonic
        strikeouts  (n ~ 351)  -> Platt (logistic on logit(raw-P)), sample-eff.

Approach (b), direct per-line logistic on predicted_value, is provided as a
baseline (fit_direct_baseline) for the well-populated cats so Brier/log-loss
can arbitrate; it is NOT the deployed calibrator (fractures the thin K sample
across lines and does not generalize off-grid).

Dependencies: numpy, scipy, scikit-learn. No statsmodels, no network.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import numpy as np
from scipy import stats
from scipy.optimize import minimize_scalar
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

# Standard market lines per category (half-integers; "over L" == actual >= ceil(L)).
STANDARD_LINES: Dict[str, List[float]] = {
    "hits": [0.5, 1.5],
    "hrr": [1.5, 2.5],
    "home_runs": [0.5],
    "strikeouts": [4.5, 5.5, 6.5],
}

# Count family per category, from conditional-dispersion diagnostics on the pairs.
COUNT_FAMILY: Dict[str, str] = {
    "hits": "poisson",
    "home_runs": "poisson",
    "hrr": "nbinom",
    "strikeouts": "nbinom",
}

# Stage-2 calibrator per category (sample-size driven).
STAGE2: Dict[str, str] = {
    "hits": "isotonic",
    "hrr": "isotonic",
    "home_runs": "isotonic",
    "strikeouts": "platt",
}

_EPS = 1e-6
_LAMBDA_FLOOR = 1e-3  # keep lambda strictly positive for pmf/cdf stability


# --------------------------------------------------------------------------- #
# stage 1: count model
# --------------------------------------------------------------------------- #
def _nb_params(mean: np.ndarray, alpha: float):
    """NB2 (var = mean + alpha*mean^2) -> scipy nbinom (n=r, p)."""
    mean = np.clip(mean, _LAMBDA_FLOOR, None)
    r = 1.0 / alpha
    p = r / (r + mean)
    return r, p


def _neg_loglik_nb(log_alpha: float, y: np.ndarray, mean: np.ndarray) -> float:
    alpha = np.exp(log_alpha)
    r, p = _nb_params(mean, alpha)
    ll = stats.nbinom.logpmf(y, r, p)
    if not np.all(np.isfinite(ll)):
        return 1e18
    return -ll.sum()


def _fit_alpha(y: np.ndarray, mean: np.ndarray) -> float:
    """MLE the NB2 dispersion with lambda held at the point estimate.
    Returns alpha; ~0 means Poisson is preferred."""
    res = minimize_scalar(
        _neg_loglik_nb, args=(y, mean),
        bounds=(np.log(1e-4), np.log(10.0)), method="bounded",
    )
    alpha = float(np.exp(res.x))
    # compare against Poisson; if Poisson is >= as likely, collapse to it
    pois_ll = stats.poisson.logpmf(y, np.clip(mean, _LAMBDA_FLOOR, None)).sum()
    nb_ll = -_neg_loglik_nb(np.log(alpha), y, mean)
    if pois_ll >= nb_ll or alpha < 1e-3:
        return 0.0
    return alpha


def _prob_over(mean: np.ndarray, line: float, family: str, alpha: float) -> np.ndarray:
    """RAW P(actual >= line) = P(count >= k), k = ceil(line) = line + 0.5."""
    k = int(np.ceil(line))
    mean = np.clip(np.asarray(mean, float), _LAMBDA_FLOOR, None)
    if family == "poisson" or (family == "nbinom" and alpha <= 0.0):
        # P(X >= k) = 1 - P(X <= k-1)
        return stats.poisson.sf(k - 1, mean)
    r, p = _nb_params(mean, alpha)
    return stats.nbinom.sf(k - 1, r, p)


# --------------------------------------------------------------------------- #
# calibrator artifact
# --------------------------------------------------------------------------- #
@dataclass
class CategoryCalibrator:
    category: str
    family: str
    alpha: float
    stage2: str                       # 'isotonic' | 'platt'
    lines: List[float]
    # isotonic knots (raw_P -> cal_P), stored explicitly for portability
    iso_x: Optional[List[float]] = None
    iso_y: Optional[List[float]] = None
    # platt params on logit(raw_P): cal_P = sigmoid(a*logit(rawP) + b)
    platt_a: Optional[float] = None
    platt_b: Optional[float] = None
    n_train: int = 0
    # multiplicative mean recalibration: lambda = mean_scale * predicted_value.
    # Corrects residual bias in the point estimate (e.g. CatBoost runs ~5% hot
    # on HR rate). Fit as Poisson MLE c = sum(actual)/sum(pred) on TRAIN, then
    # kept by the runner only if it improves holdout calibration (else 1.0).
    mean_scale: float = 1.0
    # which transform is actually deployed at inference: 'identity' (raw count
    # model), 'isotonic', or 'platt'. Set by the runner's holdout selection.
    deployed_stage2: str = "identity"

    # ---- stage 1 ----
    def raw_prob_over(self, predicted_value, line: float) -> np.ndarray:
        mean = self.mean_scale * np.asarray(predicted_value, float)
        return _prob_over(mean, line, self.family, self.alpha)

    # ---- stage 2 ----
    def _apply_stage2(self, raw_p: np.ndarray) -> np.ndarray:
        raw_p = np.clip(np.asarray(raw_p, float), _EPS, 1 - _EPS)
        if self.stage2 == "isotonic":
            # isotonic predict == clamped linear interpolation over the knots;
            # np.interp clamps to endpoint y-values outside [x0, xN] by default.
            return np.interp(raw_p, np.asarray(self.iso_x, float),
                             np.asarray(self.iso_y, float))
        # platt
        z = np.log(raw_p / (1 - raw_p))
        return 1.0 / (1.0 + np.exp(-(self.platt_a * z + self.platt_b)))

    def prob_over(self, predicted_value, line: float) -> np.ndarray:
        """Deployed calibrated P(over line) for the given point prediction(s).
        If deployed_stage2 == 'identity', the count model is used as-is (it
        validated as already-calibrated on the 2025 holdout; post-hoc stages
        did not improve out-of-sample calibration)."""
        raw = self.raw_prob_over(predicted_value, line)
        if self.deployed_stage2 == "identity":
            return np.clip(raw, _EPS, 1 - _EPS)
        return self._apply_stage2(raw)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CategoryCalibrator":
        return cls(**d)


# --------------------------------------------------------------------------- #
# fitting
# --------------------------------------------------------------------------- #
def _build_stage2_training(cal: CategoryCalibrator, pred: np.ndarray, actual: np.ndarray):
    """Pool (raw_P_at_L, 1[actual>=L]) over the category's standard lines."""
    xs, ys = [], []
    for L in cal.lines:
        xs.append(cal.raw_prob_over(pred, L))
        ys.append((actual >= L).astype(float))
    return np.concatenate(xs), np.concatenate(ys)


def fit_category(category: str, pred: np.ndarray, actual: np.ndarray,
                 lines: Optional[List[float]] = None,
                 stage2: Optional[str] = None,
                 mean_scale: Optional[float] = None) -> CategoryCalibrator:
    """Fit stage-1 dispersion + stage-2 calibrator for one category on TRAIN pairs.
    stage2 overrides the STAGE2 default ('isotonic'|'platt'|'identity').
    mean_scale: multiplicative point-estimate bias correction. None -> fit the
    Poisson-MLE c = sum(actual)/sum(pred); pass 1.0 to disable."""
    pred = np.asarray(pred, float)
    actual = np.asarray(actual, float)
    lines = lines if lines is not None else STANDARD_LINES[category]
    family = COUNT_FAMILY[category]
    stage2 = stage2 if stage2 is not None else STAGE2[category]
    c = (float(actual.sum() / max(pred.sum(), _LAMBDA_FLOOR))
         if mean_scale is None else float(mean_scale))
    scaled = c * pred
    if stage2 == "identity":
        alpha = _fit_alpha(actual, scaled) if family == "nbinom" else 0.0
        return CategoryCalibrator(category=category, family=family, alpha=alpha,
                                  stage2="identity", lines=list(lines),
                                  n_train=len(pred), mean_scale=c,
                                  deployed_stage2="identity")

    alpha = _fit_alpha(actual, scaled) if family == "nbinom" else 0.0
    cal = CategoryCalibrator(category=category, family=family, alpha=alpha,
                             stage2=stage2, lines=list(lines), n_train=len(pred),
                             mean_scale=c)

    xtr, ytr = _build_stage2_training(cal, pred, actual)
    if stage2 == "isotonic":
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(xtr, ytr)
        cal.iso_x = [float(v) for v in iso.X_thresholds_]
        cal.iso_y = [float(v) for v in iso.y_thresholds_]
    else:  # platt on logit(raw_P)
        z = np.log(np.clip(xtr, _EPS, 1 - _EPS) / (1 - np.clip(xtr, _EPS, 1 - _EPS)))
        lr = LogisticRegression(C=1e6, solver="lbfgs")
        lr.fit(z.reshape(-1, 1), ytr)
        cal.platt_a = float(lr.coef_[0, 0])
        cal.platt_b = float(lr.intercept_[0])
    return cal


# --------------------------------------------------------------------------- #
# approach (b) baseline: direct per-line logistic on predicted_value
# --------------------------------------------------------------------------- #
@dataclass
class DirectBaseline:
    category: str
    # per-line logistic on predicted_value: P = sigmoid(a*pred + b)
    params: Dict[str, List[float]] = field(default_factory=dict)  # str(line)->[a,b]

    def prob_over(self, predicted_value, line: float) -> np.ndarray:
        a, b = self.params[str(line)]
        return 1.0 / (1.0 + np.exp(-(a * np.asarray(predicted_value, float) + b)))


def fit_direct_baseline(category: str, pred: np.ndarray, actual: np.ndarray,
                        lines: Optional[List[float]] = None) -> DirectBaseline:
    lines = lines if lines is not None else STANDARD_LINES[category]
    pred = np.asarray(pred, float)
    actual = np.asarray(actual, float)
    db = DirectBaseline(category=category)
    for L in lines:
        y = (actual >= L).astype(int)
        if y.min() == y.max():  # degenerate line (no variation) -> constant
            base = float(np.clip(y.mean(), _EPS, 1 - _EPS))
            db.params[str(L)] = [0.0, float(np.log(base / (1 - base)))]
            continue
        lr = LogisticRegression(C=1e6, solver="lbfgs")
        lr.fit(pred.reshape(-1, 1), y)
        db.params[str(L)] = [float(lr.coef_[0, 0]), float(lr.intercept_[0])]
    return db


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(np.asarray(p, float), _EPS, 1 - _EPS)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def ece(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    """Expected calibration error over equal-count bins."""
    mp, of, cnt = reliability(p, y, n_bins)
    w = cnt / cnt.sum()
    return float(np.sum(w * np.abs(mp - of)))


def reliability(p: np.ndarray, y: np.ndarray, n_bins: int = 10):
    """Equal-count (quantile) bins -> (mean_pred, obs_freq, count) per bin."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    order = np.argsort(p)
    p, y = p[order], y[order]
    edges = np.linspace(0, len(p), n_bins + 1).astype(int)
    mp, of, cnt = [], [], []
    for i in range(n_bins):
        a, b = edges[i], edges[i + 1]
        if b <= a:
            continue
        mp.append(p[a:b].mean())
        of.append(y[a:b].mean())
        cnt.append(b - a)
    return np.array(mp), np.array(of), np.array(cnt)
