#!/usr/bin/env python3
"""Mutation checks for research-only market presentation policy."""

from __future__ import annotations

import json
import hashlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.market_output_policy import (  # noqa: E402
    BETTING_AUTHORIZED,
    RESEARCH_ONLY,
    load_market_output_policy,
)


def write_policy(root: Path, payload: dict) -> None:
    path = root / "config" / "policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_evidence(root: Path, name: str, content: str) -> dict:
    path = root / "evidence" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"path": str(path.relative_to(root)), "sha256": sha256(path)}


def write_certificate(root: Path, evidence: dict, approved_markets: list[dict]) -> dict:
    path = root / "evidence" / "authorization_certificate.json"
    payload = {
        "schema_version": "market-authorization-certificate-v1",
        "verdict": BETTING_AUTHORIZED,
        "evidence": evidence,
        "approved_markets": approved_markets,
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return {"certificate_path": str(path.relative_to(root)), "certificate_sha256": sha256(path)}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = {"market_output": {"policy_path": "config/policy.json"}}

        write_policy(root, {"verdict": RESEARCH_ONLY})
        policy = load_market_output_policy(root, cfg)
        assert not policy.actionable and policy.status == RESEARCH_ONLY
        print("[OK] declared research-only policy cannot produce an actionable signal")

        # Mutation: merely flipping the verdict must not authorize output.
        write_policy(root, {"verdict": BETTING_AUTHORIZED})
        policy = load_market_output_policy(root, cfg)
        assert not policy.actionable and policy.status == RESEARCH_ONLY
        print("[OK] mutation: bare BETTING_AUTHORIZED verdict fails without evidence")

        # Mutation: pasted hashes without evidence files must not make an
        # editable policy look authoritative.
        write_policy(root, {
            "verdict": BETTING_AUTHORIZED,
            "authorization": {
                "certificate_path": "evidence/missing.json",
                "certificate_sha256": "a" * 64,
            },
        })
        policy = load_market_output_policy(root, cfg)
        assert not policy.actionable and policy.status == RESEARCH_ONLY
        print("[OK] mutation: pasted hashes without certificate artifacts fail closed")

        evidence = {
            "clean_market_baseline": write_evidence(root, "baseline", '{"clean": true}'),
            "forward_shadow": write_evidence(root, "shadow", '{"forward": true}'),
            "policy_fit": write_evidence(root, "policy_fit", '{"fitted": true}'),
        }
        approved = [{
            "sportsbook": "draftkings",
            "category": "hits",
            "selection_sides": ["over"],
            "market_contract_sha256": "d" * 64,
        }]
        authorization = write_certificate(root, evidence, approved)
        write_policy(root, {"verdict": BETTING_AUTHORIZED, "authorization": authorization})
        policy = load_market_output_policy(root, cfg)
        assert policy.actionable and policy.status == BETTING_AUTHORIZED
        assert policy.authorizes("draftkings", "hits", "over")
        assert not policy.authorizes("draftkings", "hits", "under")
        assert not policy.authorizes("pinnacle", "hits", "over")
        assert not policy.authorizes("draftkings", "home_runs", "over")
        print("[OK] verified certificate authorizes only its exact market scope")

        # Mutation: source evidence is altered after the certificate was made.
        (root / "evidence" / "shadow.json").write_text('{"forward": false}', encoding="utf-8")
        policy = load_market_output_policy(root, cfg)
        assert not policy.actionable and policy.status == RESEARCH_ONLY
        print("[OK] mutation: changed shadow evidence breaks authorization")

        (root / "config" / "policy.json").unlink()
        policy = load_market_output_policy(root, cfg)
        assert not policy.actionable and policy.status == RESEARCH_ONLY
        print("[OK] missing policy fails closed")

    print("6/6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
