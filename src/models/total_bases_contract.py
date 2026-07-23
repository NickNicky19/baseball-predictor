"""Total-bases candidate contract.

Total bases is intentionally a *candidate-only* market. It is not added to the
normal daily category list or to any aggregate score until it earns a separate
hard-keyed market gate.

The line grid is not a fitted model parameter. It is the observed interface of
the May--June 2026 close artifacts: 0.5, 1.5, 2.5, 3.5, 4.5, and 5.5 total
bases. For a half-point line L, ``Over L`` is exactly ``actual >= ceil(L)``.
The simulator must therefore archive exact tail probabilities for totals 1..6.
Any future line outside this observed contract is unsupported and must be
excluded explicitly; it must never be priced with a normal approximation.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any


TOTAL_BASES_MARKET_LINES: tuple[float, ...] = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)
TOTAL_BASES_THRESHOLDS: tuple[float, ...] = tuple(
    float(math.ceil(line)) for line in TOTAL_BASES_MARKET_LINES
)


def over_threshold_for_half_point_line(line: float) -> float:
    """Return the integer event count required to win a half-point Over.

    Integer lines have push semantics and cannot be represented by the binary
    ``p_ge_threshold`` contract. Reject them rather than quietly pricing a
    different bet. The same applies to quarter/decimal alternate lines.
    """
    value = float(line)
    doubled = value * 2.0
    if not math.isfinite(value) or not math.isclose(doubled, round(doubled), abs_tol=1e-9):
        raise ValueError(f"line {line!r} is not a half-point market line")
    if int(round(doubled)) % 2 == 0:
        raise ValueError(
            f"line {line!r} has push semantics; a p_ge_threshold cannot price it"
        )
    return float(math.ceil(value))


def require_supported_total_bases_line(line: float) -> float:
    """Validate an observed total-bases market line and return its threshold."""
    threshold = over_threshold_for_half_point_line(line)
    if float(line) not in TOTAL_BASES_MARKET_LINES:
        raise ValueError(
            f"total_bases line {line!r} is outside the observed candidate contract "
            f"{list(TOTAL_BASES_MARKET_LINES)}; do not approximate it"
        )
    return threshold


def candidate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a provenance-forked config for an explicit total-bases gate.

    The added block contains no fitted coefficient. It records that this
    artifact is the unpromoted total-bases candidate and pins the observed
    market interface it is allowed to price. ``model_version`` includes this
    block, so candidate rows cannot silently mix with the live model.
    """
    out = deepcopy(config)
    if "total_bases" in out:
        raise ValueError(
            "config already contains a total_bases block; refuse to overwrite "
            "candidate provenance"
        )
    out["total_bases"] = {
        "status": "candidate_unpromoted",
        "market_line_contract": list(TOTAL_BASES_MARKET_LINES),
        "threshold_contract": list(TOTAL_BASES_THRESHOLDS),
        "source": "observed closes_2026-05/06; structural interface, not fitted",
    }
    return out


def shared_pa_forward_candidate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Fork config for the outcome-blind shared-PA Total Bases output.

    This is deliberately separate from the older candidate provenance above.
    Its structural line/threshold interface is locked by the prospective
    shared-PA adjudication contract.  It adds no coefficient and does not
    promote Total Bases into production.
    """
    out = deepcopy(config)
    if "total_bases" in out:
        raise ValueError(
            "config already contains a total_bases block; refuse to overwrite "
            "shared-PA candidate provenance"
        )
    out["total_bases"] = {
        "status": "candidate_unpromoted",
        "market_line_contract": list(TOTAL_BASES_MARKET_LINES),
        "threshold_contract": list(TOTAL_BASES_THRESHOLDS),
        "source": (
            "shared-pa-forward-adjudication-contract-v1; "
            "outcome-blind structural output interface, not fitted"
        ),
    }
    return out
