#!/usr/bin/env python3
"""Mutation tests for canonical selection report validation."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.learning.shared_pa_model import proper_scores  # noqa: E402
from scripts.validate_shared_pa_canonical_selection import validate_report  # noqa: E402


def expect_failure(fn: object) -> bool:
    try:
        fn()  # type: ignore[operator]
    except ValueError:
        return True
    return False


def main() -> int:
    p = np.repeat(1.0 / len(PA_OUTCOMES), len(PA_OUTCOMES))
    rows = []
    for fold in range(4):
        row = {
            "game_pk": fold + 1,
            "player_id": fold + 10,
            "game_date": f"2024-0{fold + 4}-01",
            "lineup_slot": fold + 1,
            "_selection_fold": fold,
        }
        for index, name in enumerate(PA_OUTCOMES):
            row[f"actual_{name}"] = 1 if index == fold else 0
            row[f"strongest_simple_{name}"] = p[index]
            row[f"canonical_raw_{name}"] = p[index]
            row[f"canonical_selected_{name}"] = p[index]
        rows.append(row)
    oof = pd.DataFrame(rows)
    actual = oof[[f"actual_{name}" for name in PA_OUTCOMES]].copy()
    actual.columns = PA_OUTCOMES
    scores = proper_scores(actual, np.tile(p, (len(oof), 1)))
    zero_interval = {"point": 0.0, "lower": 0.0, "upper": 0.0, "dates": 4, "draws": 10}
    decisions = {
        metric: {
            "required_candidate_minus_simple_below": -0.0,
            "point": 0.0,
            "upper": 0.0,
            "passed": False,
        }
        for metric in ("multiclass_log_loss", "multiclass_brier")
    }
    base = {
        "schema_version": "shared-pa-canonical-selection-report-v1",
        "status": "SELECTION_REJECTED_NO_CANDIDATE",
        "betting_authorized": False,
        "production_changed": False,
        "confirmation_2025_opened": False,
        "may_2026_opened": False,
        "oof_artifact": {"rows": 4},
        "simple_baselines": {
            "selected": "league_rate",
            "league_rate": {"scores": scores},
        },
        "raw_scores": scores,
        "selected_scores": scores,
        "complexity_benefit_floor": {"multiclass_log_loss": 0.0, "multiclass_brier": 0.0},
        "core_intervals_vs_strongest_simple": {
            "multiclass_log_loss": zero_interval,
            "multiclass_brier": zero_interval,
        },
        "materiality_decisions": decisions,
        "core_passed": False,
        "model_artifact": None,
        "stability_audit": [],
        "stability_passed": False,
        "context_step": {"evaluated": False, "installed": False},
    }
    protocol = {
        "required_metrics": {
            "pa_primary": ["multiclass_log_loss", "multiclass_brier"],
            "bootstrap_draws": 10,
            "bootstrap_seed": 1,
        }
    }
    checks = [
        ("valid fixture", validate_report(base, oof, protocol)["rows"] == 4),
        ("confirmation mutation rejected", expect_failure(lambda: validate_report({**base, "confirmation_2025_opened": True}, oof, protocol))),
        ("May mutation rejected", expect_failure(lambda: validate_report({**base, "may_2026_opened": True}, oof, protocol))),
        ("authorization mutation rejected", expect_failure(lambda: validate_report({**base, "betting_authorized": True}, oof, protocol))),
        ("production mutation rejected", expect_failure(lambda: validate_report({**base, "production_changed": True}, oof, protocol))),
        ("duplicate identity rejected", expect_failure(lambda: validate_report({**base, "oof_artifact": {"rows": 8}}, pd.concat([oof, oof], ignore_index=True), protocol))),
        ("2025 OOF rejected", expect_failure(lambda: validate_report(base, oof.assign(game_date="2025-04-01"), protocol))),
        ("probability range rejected", expect_failure(lambda: validate_report(base, oof.assign(canonical_raw_strikeout=2.0), protocol))),
        ("zero PA rejected", expect_failure(lambda: validate_report(base, oof.assign(**{f"actual_{name}": 0 for name in PA_OUTCOMES}), protocol))),
        ("false pass status rejected", expect_failure(lambda: validate_report({**base, "status": "SELECTION_PASSED_CANDIDATE_FROZEN"}, oof, protocol))),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"canonical selection validator checks failed: {failed}")
    print(f"CANONICAL SELECTION VALIDATOR VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
