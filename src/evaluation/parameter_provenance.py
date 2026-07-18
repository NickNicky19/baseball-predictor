"""Fail-closed provenance for prediction-affecting configuration values.

This module deliberately does *not* infer that a value is fitted merely
because a nearby comment or an inventory says it should be.  A configuration
value earns a non-research classification only when its exact dot-path has an
explicit provenance record.

It is intentionally generic so the same report can cover simulator, feature,
lineup, calibration, and edge-policy settings without maintaining a second
copy of their numeric values.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ALLOWED_ORIGINS = frozenset({"fitted", "predeclared_structural", "placeholder"})
PROMOTION_ORIGINS = frozenset({"fitted", "predeclared_structural"})


@dataclass(frozen=True)
class ProvenanceRow:
    """One active configuration leaf and the evidence attached to it."""

    path: str
    value: Any
    origin: str
    evidence: str
    artifact_sha256: str | None
    status: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "value": self.value,
            "origin": self.origin,
            "evidence": self.evidence,
            "artifact_sha256": self.artifact_sha256,
            "status": self.status,
            "reason": self.reason,
        }


def sha256_file(path: Path) -> str:
    """Return the byte-level hash used to pin a supplied config artifact."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def active_config_leaves(config: dict[str, Any], roots: Iterable[str]) -> dict[str, Any]:
    """Flatten only the declared prediction-affecting blocks.

    ``_comment`` values are documentation, not executable parameters, and are
    excluded.  Empty dictionaries are not parameters either.
    """

    out: dict[str, Any] = {}

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key == "_comment":
                    continue
                visit(nested, f"{path}.{key}" if path else str(key))
            return
        if isinstance(value, list):
            for index, nested in enumerate(value):
                visit(nested, f"{path}[{index}]")
            return
        out[path] = value

    for root in roots:
        if root in config:
            visit(config[root], root)
    return out


def _validate_record(path: str, record: Any) -> tuple[str, str, str | None, str]:
    """Validate an explicit record without upgrading an unsupported claim."""

    if record is None:
        return "unverified", "", None, "missing provenance record"
    if not isinstance(record, dict):
        return "unverified", "", None, "provenance record is not an object"

    origin = str(record.get("origin", "unverified"))
    evidence = str(record.get("evidence", "")).strip()
    artifact_sha256 = record.get("artifact_sha256")
    artifact_sha256 = str(artifact_sha256).lower() if artifact_sha256 else None

    if origin not in ALLOWED_ORIGINS:
        return "unverified", evidence, artifact_sha256, f"unknown origin {origin!r}"
    if origin == "placeholder":
        return origin, evidence, artifact_sha256, "explicit placeholder"
    if not evidence:
        return "unverified", evidence, artifact_sha256, "missing evidence"
    if origin == "fitted" and not artifact_sha256:
        return "unverified", evidence, artifact_sha256, "fitted value lacks artifact hash"
    return origin, evidence, artifact_sha256, "explicit provenance record"


def audit_parameter_provenance(
    config: dict[str, Any],
    *,
    roots: Iterable[str],
) -> tuple[list[ProvenanceRow], str]:
    """Return rows and a fail-closed verdict for active config values.

    The optional config block is:

    ``parameter_provenance: {"weights.season": {"origin": ..., ...}}``

    Paths without a record are *unverified*, never silently promoted by an
    inventory's description or a code comment.
    """

    records = config.get("parameter_provenance", {})
    if not isinstance(records, dict):
        records = {}

    rows: list[ProvenanceRow] = []
    for path, value in sorted(active_config_leaves(config, roots).items()):
        origin, evidence, artifact_sha256, reason = _validate_record(path, records.get(path))
        status = "approved" if origin in PROMOTION_ORIGINS else "research_only"
        rows.append(
            ProvenanceRow(
                path=path,
                value=value,
                origin=origin,
                evidence=evidence,
                artifact_sha256=artifact_sha256,
                status=status,
                reason=reason,
            )
        )

    verdict = "PROMOTION_ELIGIBLE" if rows and all(r.status == "approved" for r in rows) else "RESEARCH_ONLY"
    return rows, verdict


def audit_report(
    config_path: Path,
    config: dict[str, Any],
    *,
    roots: Iterable[str],
) -> dict[str, Any]:
    """Create a serializable report pinned to exact config bytes."""

    rows, verdict = audit_parameter_provenance(config, roots=roots)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.origin] = counts.get(row.origin, 0) + 1
    return {
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "roots": list(roots),
        "verdict": verdict,
        "verdict_reason": (
            "Every active audited value has explicit fitted or predeclared structural provenance."
            if verdict == "PROMOTION_ELIGIBLE"
            else "At least one active audited value is a placeholder or has no valid provenance record."
        ),
        "counts_by_origin": counts,
        "rows": [row.to_dict() for row in rows],
    }


def canonical_json(value: Any) -> str:
    """Stable JSON for reports and test comparisons."""

    return json.dumps(value, sort_keys=True, indent=2) + "\n"
