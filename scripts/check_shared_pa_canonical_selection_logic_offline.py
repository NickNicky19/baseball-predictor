#!/usr/bin/env python3
"""Offline mutations for artifact joining, feature quarantine, and materiality."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_canonical_selection import (  # noqa: E402
    clears_materiality,
    complexity_benefit_floor,
    feature_columns,
)


def expect_failure(fn: object) -> bool:
    try:
        fn()  # type: ignore[operator]
    except ValueError:
        return True
    return False


def main() -> int:
    protocol = json.loads((ROOT / "config/shared_pa_canonical_selection_protocol.json").read_text(encoding="utf-8"))
    core = feature_columns(protocol, "canonical_hitter_46d")
    context = feature_columns(protocol, "canonical_hitter_46d_context")
    simple = {
        "selected": "empirical_bayes_player_rate_pa_100",
        "league_rate": {"scores": {"multiclass_log_loss": 1.0, "multiclass_brier": 0.8}},
        "empirical_bayes_player_rate_pa_100": {"scores": {"multiclass_log_loss": 0.98, "multiclass_brier": 0.79}},
    }
    floor = complexity_benefit_floor(simple)
    passing = {
        "multiclass_log_loss": {"point": -0.021, "upper": -0.0201},
        "multiclass_brier": {"point": -0.011, "upper": -0.0101},
    }
    merely_detectable = {
        "multiclass_log_loss": {"point": -0.019, "upper": -0.001},
        "multiclass_brier": {"point": -0.009, "upper": -0.001},
    }
    mutated = copy.deepcopy(protocol)
    mutated["feature_groups"]["raw_pregame_context_safe"].append("opp_sp_throws")
    checks = [
        ("core exact size", len(core) == 23),
        ("safe context exact", set(context) - set(core) == {"bats", "is_home", "venue"}),
        ("pitcher quarantine enforced", expect_failure(lambda: feature_columns(mutated, "canonical_hitter_46d_context"))),
        ("unknown variant rejected", expect_failure(lambda: feature_columns(protocol, "unknown"))),
        ("data-derived log floor", abs(floor["multiclass_log_loss"] - 0.02) < 1e-12),
        ("data-derived Brier floor", abs(floor["multiclass_brier"] - 0.01) < 1e-12),
        ("material improvement accepted", clears_materiality(passing, floor)[0]),
        ("mere detectability rejected", not clears_materiality(merely_detectable, floor)[0]),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"canonical selection logic checks failed: {failed}")
    print(f"CANONICAL SELECTION LOGIC VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
