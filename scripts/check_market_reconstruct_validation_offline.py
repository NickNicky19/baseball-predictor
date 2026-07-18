#!/usr/bin/env python3
"""Mutation checks for strict-market reconstruction-smoke validation."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.market_reconstruct_validation import validate_full, validate_smoke  # noqa: E402


DATE = "2026-06-01"


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def provenance(
    path: Path, market_manifest: Path, artifact_hash: str,
    chronological_protocol: Path | None = None, role: str | None = None,
) -> None:
    payload = {
        "dates": [DATE],
        "date_source": {
            "kind": "strict_market_manifest",
            "path": str(market_manifest.resolve()),
            "sha256": hashlib.sha256(market_manifest.read_bytes()).hexdigest(),
            "strict_market_artifact_sha256": artifact_hash,
        },
    }
    if chronological_protocol is not None:
        payload["chronological_protocol"] = {
            "protocol_path": str(chronological_protocol.resolve()),
            "protocol_sha256": hashlib.sha256(
                chronological_protocol.read_bytes()
            ).hexdigest(),
            "role": role,
            "market_manifest_sha256": hashlib.sha256(
                market_manifest.read_bytes()
            ).hexdigest(),
            "market_artifact_sha256": artifact_hash,
        }
    write_json(path, payload)


def assert_fails(fn, text: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError(f"expected failure containing {text!r}")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifact = root / "base_rule_strict_hits.csv"
        manifest = root / "base_rule_strict_hits_manifest.json"
        strict = pd.DataFrame([
            dict(mlb_game_pk=700001, player_id=10, category="hits", line=0.5,
                 official_game_date=DATE),
        ])
        strict.to_csv(artifact, index=False)
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        write_json(manifest, {
            "hashes": {"artifact": artifact_hash[:16]},
            "official_date_universe": [DATE],
            "selection_rule": "dk_base_pregame_starter_and_pa_v1",
            "price_freshness_rule": "both_entry_sides_fresh_v1",
        })

        model = pd.DataFrame([
            dict(mlb_game_pk=700001, player_id=10, category="hits", line=0.5,
                 game_date=DATE, sim_p_over=0.40),
        ])
        frozen, candidate = root / "frozen.csv", root / "candidate.csv"
        model.to_csv(frozen, index=False)
        model.assign(sim_p_over=0.45).to_csv(candidate, index=False)
        outcomes = pd.DataFrame([
            dict(mlb_game_pk=700001, player_id=10, category="hits",
                 game_date=DATE, actual_value=1),
        ])
        frozen_out, candidate_out = root / "fo.csv", root / "co.csv"
        outcomes.to_csv(frozen_out, index=False)
        outcomes.to_csv(candidate_out, index=False)
        fp, cp = root / "f.json", root / "c.json"
        provenance(fp, manifest, artifact_hash)
        provenance(cp, manifest, artifact_hash)

        result = validate_smoke(
            frozen, candidate, frozen_out, candidate_out, fp, cp, artifact,
            manifest, DATE,
        )
        assert result["changed_probabilities"] == 1
        print("[PASS] valid strict smoke has targets, exact keys, provenance, and non-inert drift")

        protocol = root / "chronological.json"
        write_json(protocol, {"schema": "fixture", "fit_before": "holdout"})
        provenance(fp, manifest, artifact_hash, protocol, "fit")
        provenance(cp, manifest, artifact_hash, protocol, "fit")
        result = validate_smoke(
            frozen, candidate, frozen_out, candidate_out, fp, cp, artifact,
            manifest, DATE, chronological_protocol=protocol,
            chronological_role="fit",
        )
        assert result["changed_probabilities"] == 1
        print("[PASS] validator enforces the requested chronological protocol binding")
        payload = json.loads(cp.read_text())
        payload["chronological_protocol"]["role"] = "holdout"
        write_json(cp, payload)
        assert_fails(
            lambda: validate_smoke(
                frozen, candidate, frozen_out, candidate_out, fp, cp, artifact,
                manifest, DATE, chronological_protocol=protocol,
                chronological_role="fit",
            ),
            "chronological role differs",
        )
        print("[PASS] MUTATION one arm cannot masquerade as another chronological role")
        provenance(fp, manifest, artifact_hash)
        provenance(cp, manifest, artifact_hash)

        full = validate_full(
            frozen, candidate, frozen_out, candidate_out, fp, cp, artifact, manifest,
        )
        assert full["strict_market_keys"] == 1
        print("[PASS] full validator requires the exact strict manifest universe")

        payload = json.loads(manifest.read_text())
        payload.pop("price_freshness_rule")
        write_json(manifest, payload)
        assert_fails(
            lambda: validate_full(frozen, candidate, frozen_out, candidate_out,
                                  fp, cp, artifact, manifest),
            "two-sided price freshness",
        )
        print("[PASS] MUTATION one-sided/unspecified freshness cannot validate")
        payload["price_freshness_rule"] = "both_entry_sides_fresh_v1"
        payload["selection_rule"] = "dk_base_pregame_starter_and_pa_v1"
        write_json(manifest, payload)

        payload = json.loads(fp.read_text())
        payload["date_source"]["smoke_date"] = DATE
        write_json(fp, payload)
        assert_fails(
            lambda: validate_full(frozen, candidate, frozen_out, candidate_out,
                                  fp, cp, artifact, manifest),
            "unexpectedly carries a smoke date",
        )
        print("[PASS] MUTATION a smoke provenance cannot validate as a full run")
        provenance(fp, manifest, artifact_hash)

        model.to_csv(candidate, index=False)
        assert_fails(
            lambda: validate_smoke(frozen, candidate, frozen_out, candidate_out,
                                   fp, cp, artifact, manifest, DATE),
            "candidate is inert",
        )
        print("[PASS] MUTATION identical candidate probabilities fail")
        model.assign(sim_p_over=0.45).to_csv(candidate, index=False)

        strict.loc[0, "player_id"] = 11
        strict.to_csv(artifact, index=False)
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        write_json(manifest, {
            "hashes": {"artifact": artifact_hash[:16]},
            "official_date_universe": [DATE],
            "selection_rule": "dk_base_pregame_starter_and_pa_v1",
            "price_freshness_rule": "both_entry_sides_fresh_v1",
        })
        provenance(fp, manifest, artifact_hash)
        provenance(cp, manifest, artifact_hash)
        assert_fails(
            lambda: validate_smoke(frozen, candidate, frozen_out, candidate_out,
                                   fp, cp, artifact, manifest, DATE),
            "absent from the smoke",
        )
        print("[PASS] MUTATION strict key without model coverage fails")

        strict.loc[0, "player_id"] = 10
        strict.to_csv(artifact, index=False)
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        write_json(manifest, {
            "hashes": {"artifact": artifact_hash[:16]},
            "official_date_universe": [DATE],
            "selection_rule": "dk_base_pregame_starter_and_pa_v1",
            "price_freshness_rule": "both_entry_sides_fresh_v1",
        })
        provenance(fp, manifest, "not-the-artifact")
        provenance(cp, manifest, artifact_hash)
        assert_fails(
            lambda: validate_smoke(frozen, candidate, frozen_out, candidate_out,
                                   fp, cp, artifact, manifest, DATE),
            "provenance market artifact hash differs",
        )
        print("[PASS] MUTATION provenance bound to another market artifact fails")
    print("9/9 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
