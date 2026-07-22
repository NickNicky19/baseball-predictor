#!/usr/bin/env python3
"""Mutation checks for the uncensored, strict-unique HR reconstruction source."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_gate_reconstruct import load_strict_market_manifest  # noqa: E402
from src.evaluation.market_reconstruct_validation import validate_smoke  # noqa: E402
from src.evaluation.hr_over_mapped_source import load_json  # noqa: E402
from src.evaluation.hr_over_reconstruct_source import (  # noqa: E402
    strict_unique,
    validate_derivation,
    validate_mapped_source,
    validate_protocol,
)
from src.evaluation.strict_market_artifact import (  # noqa: E402
    HR_OVER_05_COMPLETE_PATH_RULE,
    ONE_SIDED_FRESHNESS_DEFERRED_RULE,
)


DATE = "2026-03-28"


def expect_failure(label: str, operation) -> None:
    try:
        operation()
    except (AssertionError, ValueError):
        print(f"  [OK] {label}")
        return
    raise AssertionError(f"mutation escaped: {label}")


def source_fixture() -> pd.DataFrame:
    rows = []
    for game_id, player, player_id, age, settled, official_date, month in [
        ("m~a", "jazz chisholm", 10, 30.0, True, DATE, "2026-03"),
        ("m~b", "jazz chisholm jr.", 10, 31.0, True, DATE, "2026-03"),
        ("m~c", "old unique", 11, 500.0, True, "2026-04-01", "2026-04"),
        ("m~d", "unsettled unique", 12, 10.0, False, "2026-06-01", "2026-06"),
    ]:
        rows.append({
            "mlb_game_pk": 1,
            "player_id": player_id,
            "category": "home_runs",
            "line": 0.5,
            "vendor_game_id": game_id,
            "start_time": f"{official_date}T20:0{len(rows)}:00Z",
            "player": player,
            "player_key": player.replace(" jr.", ""),
            "official_game_date": official_date,
            "sportsbook": "draftkings",
            "vendor_market": "player home runs",
            "selection_side": "over",
            "market_month": month,
            "entry_age_min": age,
            "entry_decimal_odds": 8.0,
            "close_decimal_odds": 7.5,
            "settlement_present": settled,
            "mapping_status": "hard_mapped",
        })
    return pd.DataFrame(rows)


def main() -> int:
    checks = 0
    print("HR-OVER RECONSTRUCTION SOURCE — OFFLINE MUTATION HARNESS")

    protocol = load_json(
        ROOT / "data/analysis/hr_over_contract_v1/reconstruct_source_protocol.json"
    )
    validate_protocol(protocol)
    print("  [OK] locked reconstruction-only protocol validates")
    checks += 1

    source = validate_mapped_source(source_fixture())
    unique, excluded = strict_unique(source)
    assert len(unique) == 2 and len(excluded) == 2
    assert set(unique.player_id) == {11, 12}
    assert excluded.vendor_game_id.tolist() == ["m~a", "m~b"]
    print("  [OK] every row of the duplicate final MARKET_KEY is excluded")
    checks += 1

    keep_first = pd.concat([unique, excluded.iloc[[0]]], ignore_index=True)
    expect_failure(
        "MUTATION keep-first duplicate fails",
        lambda: validate_derivation(source, keep_first, excluded.iloc[[1]].copy()),
    )
    checks += 1

    expect_failure(
        "MUTATION dropping the old unique quote fails",
        lambda: validate_derivation(
            source,
            unique[unique.player_id.ne(11)].copy(),
            excluded,
        ),
    )
    checks += 1

    expect_failure(
        "MUTATION dropping settlement-absent unique row fails",
        lambda: validate_derivation(
            source,
            unique[unique.player_id.ne(12)].copy(),
            excluded,
        ),
    )
    checks += 1

    may = source.copy()
    may["official_game_date"] = "2026-05-01"
    expect_failure("MUTATION May fails", lambda: validate_mapped_source(may))
    checks += 1

    expect_failure(
        "MUTATION numeric truth fails",
        lambda: validate_mapped_source(source.assign(result=1.0)),
    )
    checks += 1

    wrong_protocol = json.loads(json.dumps(protocol))
    wrong_protocol["selection_rule"] = "dk_base_pregame_starter_and_pa_v1"
    expect_failure(
        "MUTATION Hits selection rule fails",
        lambda: validate_protocol(wrong_protocol),
    )
    checks += 1
    wrong_protocol = json.loads(json.dumps(protocol))
    wrong_protocol["price_freshness_rule"] = "both_entry_sides_fresh_v1"
    expect_failure(
        "MUTATION two-sided Hits freshness fails",
        lambda: validate_protocol(wrong_protocol),
    )
    checks += 1

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        artifact = root / "hr_over_reconstruct_source.csv"
        manifest = root / "hr_over_reconstruct_source_manifest.json"
        consumer = unique[[
            "mlb_game_pk", "player_id", "category", "line", "official_game_date"
        ]].copy()
        consumer.to_csv(artifact, index=False)

        def write_manifest(**updates) -> None:
            payload = {
                "artifact_kind": "strict_unique_reconstruction_only",
                "official_date_universe": ["2026-04-01", "2026-06-01"],
                "markets": ["home_runs"],
                "selection_rule": HR_OVER_05_COMPLETE_PATH_RULE,
                "price_freshness_rule": ONE_SIDED_FRESHNESS_DEFERRED_RULE,
                "max_quote_age": None,
                "settlement_presence_filter_applied": False,
                "betting_authorized": False,
                "funnel": {"strict_unique": len(consumer)},
                "hashes": {
                    "artifact": hashlib.sha256(artifact.read_bytes()).hexdigest()
                },
            }
            payload.update(updates)
            manifest.write_text(json.dumps(payload), encoding="utf-8")

        write_manifest()
        assert load_strict_market_manifest(manifest) == ["2026-04-01", "2026-06-01"]
        print("  [OK] real reconstruction consumer accepts exact HR contract")
        checks += 1

        write_manifest(markets=["hits"])
        expect_failure(
            "MUTATION HR manifest cannot masquerade as Hits",
            lambda: load_strict_market_manifest(manifest),
        )
        checks += 1

        write_manifest(price_freshness_rule="both_entry_sides_fresh_v1")
        expect_failure(
            "MUTATION HR manifest cannot inherit two-sided freshness",
            lambda: load_strict_market_manifest(manifest),
        )
        checks += 1

        write_manifest(max_quote_age=90)
        expect_failure(
            "MUTATION inherited 90-minute cutoff fails",
            lambda: load_strict_market_manifest(manifest),
        )
        checks += 1

        consumer.loc[0, "category"] = "hits"
        consumer.to_csv(artifact, index=False)
        write_manifest()
        expect_failure(
            "MUTATION artifact category drift fails",
            lambda: load_strict_market_manifest(manifest),
        )
        checks += 1

        # Reconstruction-only is the one narrowly declared case where model
        # availability is measured instead of treated as a scoring failure.
        # The manifest still contains every unique market key; no row is
        # silently removed from the artifact.
        coverage = unique[[
            "mlb_game_pk", "player_id", "category", "line", "official_game_date"
        ]].copy()
        coverage["official_game_date"] = DATE
        consumer = coverage
        consumer.to_csv(artifact, index=False)
        write_manifest(official_date_universe=[DATE])
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()

        frozen = root / "frozen.csv"
        candidate = root / "candidate.csv"
        model = pd.DataFrame([
            {
                "mlb_game_pk": 1,
                "player_id": 11,
                "category": "home_runs",
                "line": 0.5,
                "game_date": DATE,
                "sim_p_over": 0.10,
            },
            {
                "mlb_game_pk": 1,
                "player_id": 99,
                "category": "hits",
                "line": 0.5,
                "game_date": DATE,
                "sim_p_over": 0.40,
            },
        ])
        model.to_csv(frozen, index=False)
        model.assign(sim_p_over=[0.15, 0.45]).to_csv(candidate, index=False)
        outcome = pd.DataFrame([
            {
                "mlb_game_pk": 1,
                "player_id": 11,
                "category": "home_runs",
                "game_date": DATE,
                "actual_value": 0,
            }
        ])
        frozen_out = root / "frozen_out.csv"
        candidate_out = root / "candidate_out.csv"
        outcome.to_csv(frozen_out, index=False)
        outcome.to_csv(candidate_out, index=False)

        def write_provenance(path: Path) -> None:
            path.write_text(json.dumps({
                "dates": [DATE],
                "date_source": {
                    "kind": "strict_market_manifest",
                    "path": str(manifest.resolve()),
                    "sha256": manifest_hash,
                    "strict_market_artifact_sha256": artifact_hash,
                    "smoke_date": DATE,
                },
            }), encoding="utf-8")

        frozen_provenance = root / "frozen.manifest.json"
        candidate_provenance = root / "candidate.manifest.json"
        write_provenance(frozen_provenance)
        write_provenance(candidate_provenance)
        result = validate_smoke(
            frozen,
            candidate,
            frozen_out,
            candidate_out,
            frozen_provenance,
            candidate_provenance,
            artifact,
            manifest,
            DATE,
        )
        assert result["reconstruction_source_keys"] == 2
        assert result["model_covered_market_keys"] == 1
        assert result["model_unavailable_market_keys"] == 1
        print("  [OK] reconstruction-only reports model-unavailable keys without scoring them")
        checks += 1

        model.assign(sim_p_over=[0.10, 0.45]).to_csv(candidate, index=False)
        expect_failure(
            "MUTATION other-category drift cannot hide inert HR probabilities",
            lambda: validate_smoke(
                frozen,
                candidate,
                frozen_out,
                candidate_out,
                frozen_provenance,
                candidate_provenance,
                artifact,
                manifest,
                DATE,
            ),
        )
        checks += 1

        payload = json.loads(manifest.read_text())
        payload.pop("artifact_kind")
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        expect_failure(
            "MUTATION missing reconstruction-only purpose restores fail-closed behavior",
            lambda: load_strict_market_manifest(manifest),
        )
        checks += 1

    print(f"\n  {checks}/{checks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
