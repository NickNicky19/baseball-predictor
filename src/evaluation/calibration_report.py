"""
Calibration report.

Reads the graded prediction->outcome pairs
(data/learning/prediction_outcomes.csv) and answers the questions you cannot
answer without outcome data:

1. ACCURACY per category — is the model systematically over- or
   under-projecting? (mean error / bias, MAE, RMSE). This is what tells you,
   with evidence, whether HR is really your strongest category and whether the
   short-outing strikeout over-projection is real.

2. CONFIDENCE RELIABILITY — does the model's confidence mean anything? We
   bucket predictions by stated confidence and check whether higher-confidence
   predictions are actually more accurate. If a 0.72-confidence bucket is no
   better than a 0.62 bucket, the confidence score isn't yet usable and you
   should NOT size bets by it.

This module is read-only and has zero effect on the model. It is safe to run
any time; it just needs enough pairs to be meaningful (default gate: 20 per
category before reporting that category's numbers as trustworthy).

Nothing here is a betting signal on its own — it tells you which categories the
model predicts well, which is the prerequisite for trusting any edge.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CategoryAccuracy:
    category: str
    n: int
    bias: float          # mean(predicted - actual); + = over-projecting
    mae: float           # mean absolute error
    rmse: float          # root mean squared error
    actual_mean: float
    predicted_mean: float
    trustworthy: bool    # n >= min_samples
    notes: list[str] = field(default_factory=list)


@dataclass
class ConfidenceBucket:
    label: str           # e.g. "0.60-0.70"
    n: int
    avg_confidence: float
    mae: float
    bias: float


@dataclass
class CalibrationReport:
    total_pairs: int
    date_range: tuple[str, str]
    by_category: dict[str, CategoryAccuracy]
    confidence_buckets: list[ConfidenceBucket]
    confidence_is_informative: Optional[bool]  # None if not enough data
    summary_notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        """Human-readable text report (for CLI / mobile markdown)."""
        lines: list[str] = []
        lines.append(f"Calibration report — {self.total_pairs} pairs, "
                     f"{self.date_range[0]} to {self.date_range[1]}")
        lines.append("")

        if not self.by_category:
            lines.append("No graded pairs yet. Let the collection run.")
            return "\n".join(lines)

        lines.append("ACCURACY BY CATEGORY (bias = predicted - actual; + means over-projecting)")
        lines.append(f"  {'category':12s} {'n':>5s} {'bias':>8s} {'MAE':>7s} "
                     f"{'RMSE':>7s} {'proj':>7s} {'actual':>7s}")
        for cat, a in sorted(self.by_category.items()):
            flag = "" if a.trustworthy else "  (thin — not yet trustworthy)"
            lines.append(
                f"  {a.category:12s} {a.n:5d} {a.bias:+8.3f} {a.mae:7.3f} "
                f"{a.rmse:7.3f} {a.predicted_mean:7.3f} {a.actual_mean:7.3f}{flag}"
            )
        lines.append("")

        # Interpretation hints per category.
        for cat, a in sorted(self.by_category.items()):
            if not a.trustworthy:
                continue
            # "Well-centered" if bias is small relative to the model's typical
            # error (MAE), not just relative to the mean. A bias well under the
            # noise floor isn't an actionable over/under-projection.
            if abs(a.bias) < 0.35 * max(a.mae, 1e-6):
                lines.append(f"  {cat}: well-centered (bias small vs typical error).")
            elif a.bias > 0:
                lines.append(f"  {cat}: OVER-projecting by {a.bias:+.3f} on average "
                             f"— corrections should pull this down.")
            else:
                lines.append(f"  {cat}: UNDER-projecting by {a.bias:+.3f} on average "
                             f"— corrections should push this up.")
        lines.append("")

        lines.append("CONFIDENCE RELIABILITY (does higher confidence = more accurate?)")
        if not self.confidence_buckets:
            lines.append("  Not enough spread in confidence values to assess.")
        else:
            lines.append(f"  {'bucket':12s} {'n':>5s} {'avg_conf':>9s} {'MAE':>7s} {'bias':>8s}")
            for b in self.confidence_buckets:
                lines.append(
                    f"  {b.label:12s} {b.n:5d} {b.avg_confidence:9.3f} "
                    f"{b.mae:7.3f} {b.bias:+8.3f}"
                )
            lines.append("")
            if self.confidence_is_informative is True:
                lines.append("  -> Higher-confidence predictions ARE more accurate. "
                             "Confidence is informative; sorting/sizing by it is defensible.")
            elif self.confidence_is_informative is False:
                lines.append("  -> Higher-confidence predictions are NOT more accurate. "
                             "Confidence is not yet usable — do NOT size bets by it.")
            else:
                lines.append("  -> Not enough data to say whether confidence is informative.")

        if self.summary_notes:
            lines.append("")
            lines.append("NOTES")
            for n in self.summary_notes:
                lines.append(f"  - {n}")

        return "\n".join(lines)


class CalibrationAnalyzer:
    """Computes a CalibrationReport from the pairs CSV."""

    def __init__(self, min_samples: int = 20):
        self.min_samples = min_samples

    def analyze(
        self,
        pairs_csv_path: str | Path = "data/learning/prediction_outcomes.csv",
    ) -> CalibrationReport:
        rows = self._load(Path(pairs_csv_path))
        if not rows:
            return CalibrationReport(
                total_pairs=0,
                date_range=("-", "-"),
                by_category={},
                confidence_buckets=[],
                confidence_is_informative=None,
                summary_notes=["No pairs found. Collection has not produced graded outcomes yet."],
            )

        dates = sorted(r["game_date"] for r in rows if r.get("game_date"))
        date_range = (dates[0], dates[-1]) if dates else ("-", "-")

        by_category = self._accuracy_by_category(rows)
        buckets, informative = self._confidence_reliability(rows)

        notes: list[str] = []
        thin = [c for c, a in by_category.items() if not a.trustworthy]
        if thin:
            notes.append(
                f"Thin categories (< {self.min_samples} pairs): {', '.join(sorted(thin))}. "
                "Keep collecting before trusting these."
            )

        return CalibrationReport(
            total_pairs=len(rows),
            date_range=date_range,
            by_category=by_category,
            confidence_buckets=buckets,
            confidence_is_informative=informative,
            summary_notes=notes,
        )

    # ------------------------------------------------------------------

    def _load(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows: list[dict] = []
        with path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    r["_pred"] = float(r["predicted_value"])
                    r["_act"] = float(r["actual_value"])
                    r["_conf"] = float(r.get("confidence") or "nan")
                except (TypeError, ValueError, KeyError):
                    continue
                rows.append(r)
        return rows

    def _accuracy_by_category(self, rows: list[dict]) -> dict[str, CategoryAccuracy]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[r.get("category", "unknown")].append(r)

        out: dict[str, CategoryAccuracy] = {}
        for cat, rs in groups.items():
            n = len(rs)
            errs = [r["_pred"] - r["_act"] for r in rs]
            abs_errs = [abs(e) for e in errs]
            sq_errs = [e * e for e in errs]
            bias = sum(errs) / n
            mae = sum(abs_errs) / n
            rmse = math.sqrt(sum(sq_errs) / n)
            out[cat] = CategoryAccuracy(
                category=cat,
                n=n,
                bias=bias,
                mae=mae,
                rmse=rmse,
                actual_mean=sum(r["_act"] for r in rs) / n,
                predicted_mean=sum(r["_pred"] for r in rs) / n,
                trustworthy=n >= self.min_samples,
            )
        return out

    def _confidence_reliability(
        self, rows: list[dict]
    ) -> tuple[list[ConfidenceBucket], Optional[bool]]:
        # Bucket by confidence in 0.10-wide bands.
        bands: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            c = r["_conf"]
            if c != c:  # NaN
                continue
            lo = math.floor(c * 10) / 10
            label = f"{lo:.2f}-{lo + 0.10:.2f}"
            bands[label].append(r)

        buckets: list[ConfidenceBucket] = []
        for label in sorted(bands):
            rs = bands[label]
            n = len(rs)
            if n == 0:
                continue
            abs_errs = [abs(r["_pred"] - r["_act"]) for r in rs]
            errs = [r["_pred"] - r["_act"] for r in rs]
            buckets.append(ConfidenceBucket(
                label=label,
                n=n,
                avg_confidence=sum(r["_conf"] for r in rs) / n,
                mae=sum(abs_errs) / n,
                bias=sum(errs) / n,
            ))

        # Informative if buckets with meaningful n show MAE decreasing as
        # confidence rises. Require at least two buckets each with >= min_samples.
        solid = [b for b in buckets if b.n >= self.min_samples]
        informative: Optional[bool] = None
        if len(solid) >= 2:
            # Compare lowest-confidence solid bucket vs highest.
            low = min(solid, key=lambda b: b.avg_confidence)
            high = max(solid, key=lambda b: b.avg_confidence)
            # Informative if the higher-confidence bucket is meaningfully more
            # accurate (>= 5% lower MAE).
            if high.mae <= low.mae * 0.95:
                informative = True
            else:
                informative = False
        return buckets, informative
