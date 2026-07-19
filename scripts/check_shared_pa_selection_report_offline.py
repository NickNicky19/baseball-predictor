#!/usr/bin/env python3
"""Mutation checks for the independent shared-PA selection certifier."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import PA_OUTCOMES, sha256  # noqa: E402
from src.evaluation.shared_pa_selection_report import validate_selection_report  # noqa: E402


def write_report(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    source_relative = "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/training/training_hitters_2023_2025_statcast.csv.gz"
    evidence_root = ROOT.parent
    source = evidence_root / source_relative
    protocol = ROOT / "config/shared_pa_benchmark_protocol.json"
    runtime_file = ROOT / "src/learning/shared_pa_model.py"
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        rows = []
        for fold in range(4):
            row = {
                "game_pk": 100 + fold,
                "player_id": 200 + fold,
                "game_date": f"2024-0{4 + fold}-01",
                "lineup_slot": fold + 1,
                "_selection_fold": fold,
            }
            for index, outcome in enumerate(PA_OUTCOMES):
                row[f"actual_{outcome}"] = 1 if index == fold else 0
                for prefix in ("best_simple", "candidate_raw", "candidate_crossfit_calibrated"):
                    row[f"{prefix}_{outcome}"] = 1.0 / len(PA_OUTCOMES)
            rows.append(row)
        oof = work / "selection_oof_predictions.csv"
        pd.DataFrame(rows).to_csv(oof, index=False)
        report = {
            "schema_version": "shared-pa-selection-report-v1",
            "status": "SELECTION_REJECTED_NO_CANDIDATE",
            "betting_authorized": False,
            "may_2026_opened": False,
            "confirmation_2025_opened": False,
            "selection_seasons": [2023, 2024],
            "pa_target_population": {
                "source_rows_retained": 87462,
                "positive_pa_source_rows": 87430,
                "zero_pa_rows_zero_weight": 32,
                "zero_pa_rows_by_season": {"2023": 14, "2024": 18},
                "market_settlement_inferred": False,
                "model_coverage_inferred": False,
            },
            "historical_feature_sanitization": {
                "rows_sanitized": 27,
                "source_values_removed": ["actual_starter"],
            },
            "source": {"path": source_relative, "sha256": sha256(source)},
            "protocol": {"path": "config/shared_pa_benchmark_protocol.json", "sha256": sha256(protocol)},
            "runtime": {
                "thread_count": 1,
                "file_hashes": {"src/learning/shared_pa_model.py": sha256(runtime_file)},
            },
            "selection_oof": {"path": oof.name, "sha256": sha256(oof), "rows": 4, "seasons": [2024]},
            "selected_variant": None,
        }
        report_path = work / "selection_report.json"
        write_report(report_path, report)
        validate_selection_report(report_path, repo_root=ROOT, evidence_root=evidence_root)
        print("[OK] rejected selection artifact chain validates without publishing a model")

        mutations = [
            ("May opened", lambda item: item.update(may_2026_opened=True)),
            ("OOF hash", lambda item: item["selection_oof"].update(sha256="0" * 64)),
            ("runtime hash", lambda item: item["runtime"]["file_hashes"].update({"src/learning/shared_pa_model.py": "0" * 64})),
            ("candidate on rejection", lambda item: item.update(selected_variant="core")),
            ("zero PA called settlement", lambda item: item["pa_target_population"].update(market_settlement_inferred=True)),
        ]
        for label, mutate in mutations:
            candidate = copy.deepcopy(report)
            mutate(candidate)
            write_report(report_path, candidate)
            try:
                validate_selection_report(report_path, repo_root=ROOT, evidence_root=evidence_root)
            except ValueError:
                print(f"[OK] MUTATION {label} fails")
            else:
                raise AssertionError(f"selection-report mutation passed: {label}")
    print("6/6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
