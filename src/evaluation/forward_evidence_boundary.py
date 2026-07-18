"""Fail-closed validation for the locked forward economic-look boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.utils.provenance import sha256_file


class ForwardEvidenceBoundaryError(ValueError):
    """Raised when the boundary could be weakened or is not source-derived."""


@dataclass(frozen=True)
class ForwardEvidenceBoundary:
    path: Path
    sha256: str
    minimum_complete_official_date_blocks: int
    bootstrap_draws: int
    bootstrap_seed: int
    capture_lower_bound: float
    roi_lower_bound: float


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ForwardEvidenceBoundaryError(f"{label} must be an object")
    return value


def validate_boundary_mapping(payload: Mapping[str, Any], *, root: Path) -> int:
    if payload.get("schema_version") != "forward-shadow-evidence-boundary-v1":
        raise ForwardEvidenceBoundaryError("unsupported forward evidence boundary schema")
    if payload.get("status") != "LOCKED_BEFORE_FORWARD_EVIDENCE_ERA":
        raise ForwardEvidenceBoundaryError("boundary was not locked before the evidence era")
    if payload.get("betting_authorized") is not False:
        raise ForwardEvidenceBoundaryError("evidence boundary cannot authorize betting")

    scope = _object(payload.get("scope"), "scope")
    if (
        str(scope.get("reference_sportsbook")) != "draftkings"
        or str(scope.get("category")) != "hits"
        or str(scope.get("entry_horizon")) != "T-4h"
        or list(scope.get("execution_products_evaluated_separately", []))
        != ["onyx", "novig", "chalkboard", "prizepicks"]
    ):
        raise ForwardEvidenceBoundaryError("scope or separate execution-product order changed")

    derivation = _object(payload.get("open_data_derivation"), "open_data_derivation")
    if derivation.get("may_2026_used") is not False:
        raise ForwardEvidenceBoundaryError("May 2026 cannot derive the forward boundary")
    source_dates: set[str] = set()
    declared_total = 0
    for label in ("march_april_manifest", "june_manifest"):
        source = _object(derivation.get(label), label)
        path = root / str(source.get("path", ""))
        expected_sha = str(source.get("sha256", "")).lower()
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise ForwardEvidenceBoundaryError(f"{label} is missing or hash-mismatched")
        manifest = _object(json.loads(path.read_text(encoding="utf-8")), label)
        dates = manifest.get("official_date_universe")
        if not isinstance(dates, list) or not dates or len(dates) != len(set(dates)):
            raise ForwardEvidenceBoundaryError(f"{label} official-date universe is invalid")
        if any(str(value).startswith("2026-05-") for value in dates):
            raise ForwardEvidenceBoundaryError(f"{label} contains sealed May 2026")
        if int(source.get("official_dates", -1)) != len(dates):
            raise ForwardEvidenceBoundaryError(f"{label} declared date count differs from source")
        declared_total += len(dates)
        source_dates.update(str(value) for value in dates)
    if declared_total != len(source_dates):
        raise ForwardEvidenceBoundaryError("open source date universes overlap")
    if int(derivation.get("union_official_dates", -1)) != len(source_dates):
        raise ForwardEvidenceBoundaryError("derived union date count changed")

    boundary = _object(payload.get("first_economic_look_boundary"), "first_economic_look_boundary")
    minimum_dates = int(boundary.get("minimum_complete_official_date_blocks", -1))
    if minimum_dates != len(source_dates):
        raise ForwardEvidenceBoundaryError("first-look date boundary is not the exact open-data union")
    if boundary.get("operational_smoke_counts") is not False:
        raise ForwardEvidenceBoundaryError("operational smoke cannot count as economic evidence")
    completion = boundary.get("a_date_block_is_complete_only_if")
    if not isinstance(completion, list) or len(completion) < 6:
        raise ForwardEvidenceBoundaryError("complete-date operational requirements are missing")
    joined = " ".join(str(value).lower() for value in completion)
    for required in ("source_error", "prestart", "official mlb", "independent lifecycle verifier"):
        if required not in joined:
            raise ForwardEvidenceBoundaryError(f"complete-date rule omitted {required}")

    evaluation = _object(payload.get("locked_first_look_evaluation"), "locked_first_look_evaluation")
    if (
        int(evaluation.get("bootstrap_draws", 0)) != 20000
        or int(evaluation.get("bootstrap_seed", -1)) != 7
        or float(evaluation.get("capture_lower_95_bound_strictly_greater_than", -1)) != 0.1
        or float(evaluation.get("net_roi_lower_95_bound_strictly_greater_than", -1)) != 0.0
        or evaluation.get("market_and_product_pooling_permitted") is not False
        or evaluation.get("point_estimates_alone_can_authorize") is not False
    ):
        raise ForwardEvidenceBoundaryError("locked economic evaluation was weakened or changed")
    triggers = payload.get("era_reset_triggers")
    if not isinstance(triggers, list) or len(triggers) < 8:
        raise ForwardEvidenceBoundaryError("material-change era reset triggers are incomplete")
    return minimum_dates


def load_forward_evidence_boundary(path: str | Path, *, root: str | Path) -> ForwardEvidenceBoundary:
    source = Path(path).resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardEvidenceBoundaryError(f"cannot read forward evidence boundary: {exc}") from exc
    mapping = _object(payload, "boundary")
    project_root = Path(root).resolve()
    minimum_dates = validate_boundary_mapping(mapping, root=project_root)
    evaluation = _object(mapping["locked_first_look_evaluation"], "locked_first_look_evaluation")
    return ForwardEvidenceBoundary(
        path=source,
        sha256=sha256_file(source),
        minimum_complete_official_date_blocks=minimum_dates,
        bootstrap_draws=int(evaluation["bootstrap_draws"]),
        bootstrap_seed=int(evaluation["bootstrap_seed"]),
        capture_lower_bound=float(evaluation["capture_lower_95_bound_strictly_greater_than"]),
        roi_lower_bound=float(evaluation["net_roi_lower_95_bound_strictly_greater_than"]),
    )
