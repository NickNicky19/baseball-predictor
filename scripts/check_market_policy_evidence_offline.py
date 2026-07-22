#!/usr/bin/env python3
"""Mutation checks for evidence-bound market policy promotion.

This proves that an origin label cannot promote a parameter by itself.  The
production loader must validate the evidence file, its exact selected value,
and every source artifact the evidence names.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.market_eligibility import load_policy  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


VALUES = {
    "min_edge": 0.04,
    "max_quote_age": 90,
    "min_bets_for_capture": 30,
    "entry_hours": 4,
    "settlement_presence_rule": "official_base_rule",
    "base_pregame_hitter_eligibility_rule": "official_starter AND official_pa >= 1",
    "book": "draftkings",
}


def write_policy(root: Path, parameters: dict) -> Path:
    path = root / "config" / "policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "policy_version": "research-2026-07-13-v3",
        "parameters": parameters,
    }, sort_keys=True), encoding="utf-8")
    return path


def write_protocol(root: Path, parameter: str, origin: str) -> tuple[str, str]:
    path = root / "protocols" / f"{parameter}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data_use = ("outcome_blind_structural" if origin == "predeclared_structural"
                else "select_on_fit_evaluate_once_on_validation")
    path.write_text(json.dumps({
        "schema_version": "market-policy-parameter-protocol-v1",
        "parameter": parameter,
        "origin": origin,
        "method": "predeclared test method",
        "data_use": data_use,
    }, sort_keys=True), encoding="utf-8")
    return str(path.relative_to(root)), sha256(path)


def write_evidence(root: Path, parameter: str, value, origin: str,
                   source: Path, validation_source: Path) -> tuple[str, str]:
    path = root / "evidence" / f"{parameter}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    protocol_path, protocol_sha = write_protocol(root, parameter, origin)
    payload = {
        "schema_version": "market-policy-parameter-evidence-v1",
        "parameter": parameter,
        "selected_value": value,
        "origin": origin,
        "method": "predeclared test method",
        "protocol": {"path": protocol_path, "sha256": protocol_sha},
        "protocol_sha256": protocol_sha,
        "input_artifacts": {
            "source": {"path": str(source.relative_to(root)), "sha256": sha256(source)}
        },
    }
    if origin == "fitted":
        payload["input_artifacts"]["fit_universe"] = {
            "path": str(source.relative_to(root)), "sha256": sha256(source),
        }
        payload["input_artifacts"]["validation_universe"] = {
            "path": str(validation_source.relative_to(root)), "sha256": sha256(validation_source),
        }
        payload["temporal_evaluation"] = {
            "fit_dates": ["2026-03-21", "2026-03-22"],
            "validation_dates": ["2026-04-01", "2026-04-02"],
        }
    else:
        payload["outcome_values_used"] = False
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return str(path.relative_to(root)), sha256(path)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "evidence" / "source.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('{"input": "fixed"}', encoding="utf-8")
        validation_source = root / "evidence" / "validation.json"
        validation_source.write_text('{"input": "validation"}', encoding="utf-8")

        placeholders = {
            key: {"value": value, "origin": "placeholder"}
            for key, value in VALUES.items()
        }
        policy_path = write_policy(root, placeholders)
        _, _, research_only, unapproved = load_policy(policy_path)
        assert research_only and set(unapproved) == set(VALUES)
        print("[OK] placeholders remain research-only")

        # Mutation: a flattering origin label with no evidence must not remove
        # this parameter from the research-only list.
        no_evidence = json.loads(json.dumps(placeholders))
        no_evidence["min_edge"]["origin"] = "fitted"
        policy_path = write_policy(root, no_evidence)
        _, _, research_only, unapproved = load_policy(policy_path)
        assert research_only and "min_edge" in unapproved
        print("[OK] mutation: bare fitted origin cannot promote min_edge")

        approved: dict = {}
        for parameter, value in VALUES.items():
            origin = "predeclared_structural" if parameter in {"entry_hours", "book"} else "fitted"
            evidence_path, evidence_sha = write_evidence(
                root, parameter, value, origin, source, validation_source
            )
            approved[parameter] = {
                "value": value,
                "origin": origin,
                "evidence_artifact": evidence_path,
                "evidence_sha256": evidence_sha,
            }
        policy_path = write_policy(root, approved)
        _, _, research_only, unapproved = load_policy(policy_path)
        assert not research_only and not unapproved
        print("[OK] all parameter values require and accept matching hashed evidence")

        # Mutation: a rehashed evidence file that names a different value must
        # still fail.  This proves the policy is not checking only file hashes.
        min_edge_evidence = root / approved["min_edge"]["evidence_artifact"]
        payload = json.loads(min_edge_evidence.read_text(encoding="utf-8"))
        payload["selected_value"] = 0.05
        min_edge_evidence.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        approved["min_edge"]["evidence_sha256"] = sha256(min_edge_evidence)
        policy_path = write_policy(root, approved)
        _, _, research_only, unapproved = load_policy(policy_path)
        assert research_only and "min_edge" in unapproved
        print("[OK] mutation: rehashed evidence with a different value still fails")

        # Restore a valid evidence file, then alter a source artifact after it
        # was pinned.  The source hash inside the evidence must catch it.
        evidence_path, evidence_sha = write_evidence(
            root, "min_edge", VALUES["min_edge"], "fitted", source, validation_source
        )
        approved["min_edge"]["evidence_artifact"] = evidence_path
        approved["min_edge"]["evidence_sha256"] = evidence_sha
        source.write_text('{"input": "changed"}', encoding="utf-8")
        policy_path = write_policy(root, approved)
        _, _, research_only, unapproved = load_policy(policy_path)
        assert research_only and "min_edge" in unapproved
        print("[OK] mutation: changed evidence input cannot preserve promotion")

        # A protocol hash without a protocol artifact was the v1 escape hatch.
        # This must now fail even if every other source is correctly pinned.
        source.write_text('{"input": "fixed"}', encoding="utf-8")
        evidence_path, evidence_sha = write_evidence(
            root, "min_edge", VALUES["min_edge"], "fitted", source, validation_source
        )
        payload = json.loads((root / evidence_path).read_text(encoding="utf-8"))
        payload.pop("protocol")
        (root / evidence_path).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        approved["min_edge"]["evidence_artifact"] = evidence_path
        approved["min_edge"]["evidence_sha256"] = sha256(root / evidence_path)
        policy_path = write_policy(root, approved)
        _, _, research_only, unapproved = load_policy(policy_path)
        assert research_only and "min_edge" in unapproved
        print("[OK] mutation: a bare protocol digest without a protocol file fails")

        # Chronological claims are not formatting. An overlapping validation
        # window would let the selection see its own test data.
        evidence_path, evidence_sha = write_evidence(
            root, "min_edge", VALUES["min_edge"], "fitted", source, validation_source
        )
        payload = json.loads((root / evidence_path).read_text(encoding="utf-8"))
        payload["temporal_evaluation"]["validation_dates"] = ["2026-03-22", "2026-04-02"]
        (root / evidence_path).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        approved["min_edge"]["evidence_artifact"] = evidence_path
        approved["min_edge"]["evidence_sha256"] = sha256(root / evidence_path)
        policy_path = write_policy(root, approved)
        _, _, research_only, unapproved = load_policy(policy_path)
        assert research_only and "min_edge" in unapproved
        print("[OK] mutation: overlapping temporal fit/validation dates fail")

    print("7/7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
