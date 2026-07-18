"""Fail-closed authorization for presenting market signals as actionable.

The model may calculate research diagnostics while the market policy remains
research-only.  That is deliberately different from authorizing a bet: an
actionable result must be backed by a specifically authorized policy artifact.
Missing, malformed, unknown, or research-only policy files always fail closed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RESEARCH_ONLY = "RESEARCH_ONLY"
BETTING_AUTHORIZED = "BETTING_AUTHORIZED"
AUTHORIZATION_CERTIFICATE_SCHEMA = "market-authorization-certificate-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ApprovedMarket:
    """One exact market contract that an evidence certificate authorizes.

    Authorization is intentionally narrower than a model version.  A clean
    result for DraftKings hits says nothing about HR, pitcher strikeouts, or
    another book.  Keeping the scope as structured data makes the daily/UI
    boundary enforce that distinction instead of relying on prose.
    """

    sportsbook: str
    category: str
    selection_sides: frozenset[str]
    market_contract_sha256: str


@dataclass(frozen=True)
class MarketOutputPolicy:
    """The presentation status pinned to an exact policy file."""

    status: str
    actionable: bool
    policy_path: str | None
    policy_sha256: str | None
    reason: str
    approved_markets: tuple[ApprovedMarket, ...] = ()

    def authorizes(self, sportsbook: str, category: str, selection_side: str) -> bool:
        """Return true only for a market explicitly named in the certificate."""

        if not self.actionable:
            return False
        book = str(sportsbook).strip().lower()
        market = str(category).strip()
        side = str(selection_side).strip().lower()
        return any(
            item.sportsbook == book
            and item.category == market
            and side in item.selection_sides
            for item in self.approved_markets
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: Any) -> bool:
    return bool(_SHA256.fullmatch(str(value).strip().lower()))


def _within_root(project_root: Path, path: Path) -> bool:
    """Reject an authorization certificate that points outside this project."""

    try:
        path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return False
    return True


def _resolve_project_artifact(project_root: Path, raw_path: Any, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{label} path is missing")
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    if not _within_root(project_root, path):
        raise ValueError(f"{label} must stay inside the project root")
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _validate_evidence_artifact(project_root: Path, label: str, raw: Any) -> str:
    """Verify an immutable evidence reference, not merely a pasted digest."""

    if not isinstance(raw, dict):
        raise ValueError(f"authorization evidence {label!r} is not an object")
    expected = str(raw.get("sha256", "")).strip().lower()
    if not _is_sha256(expected):
        raise ValueError(f"authorization evidence {label!r} lacks a SHA-256 digest")
    artifact = _resolve_project_artifact(project_root, raw.get("path"), f"evidence {label!r}")
    actual = _sha256(artifact)
    if actual != expected:
        raise ValueError(
            f"authorization evidence {label!r} hash mismatch: expected {expected}, got {actual}"
        )
    return actual


def _parse_approved_markets(raw: Any) -> tuple[ApprovedMarket, ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("authorization certificate has no approved markets")
    approved: list[ApprovedMarket] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"approved_markets[{index}] is not an object")
        book = str(item.get("sportsbook", "")).strip().lower()
        category = str(item.get("category", "")).strip()
        sides = item.get("selection_sides")
        contract_sha = str(item.get("market_contract_sha256", "")).strip().lower()
        if not book or not category:
            raise ValueError(f"approved_markets[{index}] requires sportsbook and category")
        if not isinstance(sides, list) or not sides:
            raise ValueError(f"approved_markets[{index}] requires non-empty selection_sides")
        normalized_sides = frozenset(str(side).strip().lower() for side in sides)
        if not normalized_sides <= {"over", "under"}:
            raise ValueError(f"approved_markets[{index}] has an unknown selection side")
        if not _is_sha256(contract_sha):
            raise ValueError(f"approved_markets[{index}] lacks market_contract_sha256")
        approved.append(ApprovedMarket(book, category, normalized_sides, contract_sha))
    keys = [(item.sportsbook, item.category, tuple(sorted(item.selection_sides))) for item in approved]
    if len(keys) != len(set(keys)):
        raise ValueError("authorization certificate repeats an approved market scope")
    return tuple(approved)


def _authorization_complete(project_root: Path, raw: dict[str, Any]) -> tuple[bool, str, tuple[ApprovedMarket, ...]]:
    """Verify a content-addressed certificate before making a signal actionable.

    A policy verdict is an editable claim.  The certificate binds that claim to
    three independently stored artifacts: the clean historical baseline, the
    prospective forward-shadow result, and the fitted-policy evidence.  Every
    file is re-hashed at load time.  This validates provenance; it deliberately
    does *not* decide whether the numeric evidence is sufficient for promotion.
    """

    authorization = raw.get("authorization")
    if not isinstance(authorization, dict):
        return False, "BETTING_AUTHORIZED lacks an authorization certificate", ()
    expected_certificate_sha = str(authorization.get("certificate_sha256", "")).strip().lower()
    if not _is_sha256(expected_certificate_sha):
        return False, "BETTING_AUTHORIZED lacks a valid authorization certificate hash", ()
    try:
        certificate_path = _resolve_project_artifact(
            project_root, authorization.get("certificate_path"), "authorization certificate"
        )
        if _sha256(certificate_path) != expected_certificate_sha:
            return False, "authorization certificate hash mismatch", ()
        certificate = json.loads(certificate_path.read_text(encoding="utf-8-sig"))
        if not isinstance(certificate, dict):
            return False, "authorization certificate root is not an object", ()
        if certificate.get("schema_version") != AUTHORIZATION_CERTIFICATE_SCHEMA:
            return False, "authorization certificate has an unknown schema", ()
        if certificate.get("verdict") != BETTING_AUTHORIZED:
            return False, "authorization certificate is not BETTING_AUTHORIZED", ()
        evidence = certificate.get("evidence")
        if not isinstance(evidence, dict):
            return False, "authorization certificate has no evidence block", ()
        for label in ("clean_market_baseline", "forward_shadow", "policy_fit"):
            _validate_evidence_artifact(project_root, label, evidence.get(label))
        approved = _parse_approved_markets(certificate.get("approved_markets"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"authorization certificate invalid: {exc}", ()
    return True, "verified authorization certificate and evidence hashes", approved


def load_market_output_policy(project_root: Path, config: dict[str, Any]) -> MarketOutputPolicy:
    """Load a market presentation policy, defaulting to no authorization."""

    block = config.get("market_output", {})
    if not isinstance(block, dict):
        block = {}
    raw_path = block.get("policy_path", "config/ab_policy.json")
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    if not path.is_file():
        return MarketOutputPolicy(
            status=RESEARCH_ONLY,
            actionable=False,
            policy_path=str(path),
            policy_sha256=None,
            reason="market policy file is missing; fail closed",
        )

    digest = _sha256(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return MarketOutputPolicy(
            status=RESEARCH_ONLY,
            actionable=False,
            policy_path=str(path),
            policy_sha256=digest,
            reason=f"market policy is unreadable: {exc}",
        )
    if not isinstance(raw, dict):
        return MarketOutputPolicy(
            status=RESEARCH_ONLY,
            actionable=False,
            policy_path=str(path),
            policy_sha256=digest,
            reason="market policy root is not an object",
        )

    verdict = raw.get("verdict")
    if verdict != BETTING_AUTHORIZED:
        return MarketOutputPolicy(
            status=RESEARCH_ONLY,
            actionable=False,
            policy_path=str(path),
            policy_sha256=digest,
            reason=f"policy verdict is {verdict!r}, not {BETTING_AUTHORIZED}",
        )

    complete, reason, approved_markets = _authorization_complete(project_root, raw)
    return MarketOutputPolicy(
        status=BETTING_AUTHORIZED if complete else RESEARCH_ONLY,
        actionable=complete,
        policy_path=str(path),
        policy_sha256=digest,
        reason=reason,
        approved_markets=approved_markets,
    )
