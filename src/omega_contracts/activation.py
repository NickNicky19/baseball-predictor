"""Fail-closed research-only candidate state boundary."""

from __future__ import annotations

from collections.abc import Mapping

from .canonical import require_sha256
from .errors import ContractError

ALLOWED_CANDIDATE_STATES = {"RESEARCH_ONLY", "IMPLEMENTED_UNQUALIFIED", "DISABLED"}


def assert_research_only_manifest(manifest: Mapping[str, object]) -> None:
    state = manifest.get("qualification_state")
    if state not in ALLOWED_CANDIDATE_STATES:
        raise ContractError(
            "candidate state is not permitted in the isolated integration branch"
        )
    if manifest.get("active") is not False:
        raise ContractError("candidate must explicitly declare active=false")
    if manifest.get("betting_authorized") is not False:
        raise ContractError(
            "candidate must explicitly declare betting_authorized=false"
        )
    approval = manifest.get("promotion_approval_sha256")
    if approval is not None:
        require_sha256(approval, label="promotion approval sha256")
        raise ContractError(
            "promotion approval references are forbidden in this unqualified candidate"
        )
