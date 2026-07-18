#!/usr/bin/env python3
"""Mutation checks for policy-source model reconstruction validation."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_policy_source import (  # noqa: E402
    DEFERRED_FRESHNESS_RULE,
    POLICY_SOURCE_KEY,
    POLICY_SOURCE_KIND,
)
from src.evaluation.market_reconstruct_validation import validate_smoke  # noqa: E402


DATE = "2026-04-21"


def source_row(*, start: str, player: int = 10) -> dict:
    return {
        "mlb_game_pk": 700001,
        "player_id": player,
        "category": "hits",
        "line": 0.5,
        "vendor_game_id": "vendor-1",
        "start_time": start,
        "player": f"Player {player}",
        "player_key": f"player {player}",
        "official_game_date": DATE,
        "entry_over_age_min": 120.0,
        "entry_under_age_min": 125.0,
        "entry_age_min": 125.0,
        "entry_p_over": 0.51,
        "close_p_over": 0.52,
        "entry_overround": 0.04,
        "entry_over_odds_decimal": 2.0,
        "entry_under_odds_decimal": 1.9,
        "close_over_odds_decimal": 1.95,
        "close_under_odds_decimal": 1.95,
        "is_starter": True,
        "official_pa": 4,
        "base_rule_eligible": True,
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_provenance(
    path: Path, manifest: Path, artifact_hash: str, *, kind: str = "policy_source_manifest"
) -> None:
    write_json(path, {
        "dates": [DATE],
        "date_source": {
            "kind": kind,
            "path": str(manifest.resolve()),
            "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "policy_source_artifact_sha256": artifact_hash,
        },
    })


def expect_error(fn, text: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert text in str(exc), (str(exc), text)
    else:
        raise AssertionError(f"expected failure containing {text!r}")


def main() -> int:
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        root = Path(tmp)
        source = pd.DataFrame([
            source_row(start="2026-04-21 19:00:00"),
            source_row(start="2026-04-21 19:01:00"),
        ])
        artifact = root / "source.csv"
        source.to_csv(artifact, index=False)
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        manifest = root / "source_manifest.json"
        write_json(manifest, {
            "artifact_kind": POLICY_SOURCE_KIND,
            "selection_rule": "dk_base_pregame_starter_and_pa_v1",
            "price_freshness_rule": DEFERRED_FRESHNESS_RULE,
            "max_quote_age": None,
            "source_key": POLICY_SOURCE_KEY,
            "raw_decimal_odds_included": True,
            "markets": ["hits"],
            "hashes": {"artifact": artifact_hash[:16]},
            "funnel": {"policy_source_rows": 2},
            "official_date_universe": [DATE],
        })

        model = pd.DataFrame([{
            "mlb_game_pk": 700001, "player_id": 10, "category": "hits",
            "line": 0.5, "game_date": DATE, "sim_p_over": 0.40,
        }])
        frozen, candidate = root / "f.csv", root / "c.csv"
        model.to_csv(frozen, index=False)
        model.assign(sim_p_over=0.45).to_csv(candidate, index=False)
        outcomes = pd.DataFrame([{
            "mlb_game_pk": 700001, "player_id": 10, "category": "hits",
            "game_date": DATE, "actual_value": 1,
        }])
        fo, co = root / "fo.csv", root / "co.csv"
        outcomes.to_csv(fo, index=False)
        outcomes.to_csv(co, index=False)
        fp, cp = root / "fp.json", root / "cp.json"
        write_provenance(fp, manifest, artifact_hash)
        write_provenance(cp, manifest, artifact_hash)

        result = validate_smoke(
            frozen, candidate, fo, co, fp, cp, artifact, manifest, DATE,
        )
        assert result["policy_source_rows"] == 2
        assert result["policy_source_model_keys"] == 1
        print("[PASS] fragment duplicates may map to one reconstructed model key")

        write_provenance(cp, manifest, artifact_hash, kind="strict_market_manifest")
        expect_error(
            lambda: validate_smoke(
                frozen, candidate, fo, co, fp, cp, artifact, manifest, DATE,
            ),
            "expected policy_source_manifest",
        )
        write_provenance(cp, manifest, artifact_hash)
        print("[PASS] MUTATION strict provenance cannot masquerade as source provenance")

        empty_model = model.assign(player_id=99)
        empty_model.to_csv(candidate, index=False)
        expect_error(
            lambda: validate_smoke(
                frozen, candidate, fo, co, fp, cp, artifact, manifest, DATE,
            ),
            "MODEL_KEY sets differ",
        )
        model.assign(sim_p_over=0.45).to_csv(candidate, index=False)
        print("[PASS] MUTATION candidate cannot omit a policy-source model key")

        duplicate_source = pd.concat([source.iloc[[0]], source.iloc[[0]]], ignore_index=True)
        duplicate_source.to_csv(artifact, index=False)
        changed_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["hashes"]["artifact"] = changed_hash[:16]
        write_json(manifest, payload)
        write_provenance(fp, manifest, changed_hash)
        write_provenance(cp, manifest, changed_hash)
        expect_error(
            lambda: validate_smoke(
                frozen, candidate, fo, co, fp, cp, artifact, manifest, DATE,
            ),
            "duplicate exact vendor source keys",
        )
        print("[PASS] MUTATION duplicate exact source identity fails validation")

        source.to_csv(artifact, index=False)
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        payload["hashes"]["artifact"] = artifact_hash[:16]
        write_json(manifest, payload)
        write_provenance(fp, manifest, artifact_hash)
        write_provenance(cp, manifest, artifact_hash)
        provenance_payload = json.loads(cp.read_text(encoding="utf-8"))
        provenance_payload["date_source"]["sha256"] = "0" * 64
        write_json(cp, provenance_payload)
        expect_error(
            lambda: validate_smoke(
                frozen, candidate, fo, co, fp, cp, artifact, manifest, DATE,
            ),
            "manifest hash differs",
        )
        print("[PASS] MUTATION provenance bound to changed manifest bytes fails")

    print("5/5 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
