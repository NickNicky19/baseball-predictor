#!/usr/bin/env python3
"""Mutation-test the cumulative selector certifier against its real artifacts."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_shared_pa_cumulative_selection import validate_report  # noqa: E402


def rejected(report: dict, oof: pd.DataFrame, protocol: dict) -> bool:
    try:
        validate_report(report, oof, protocol)
    except (KeyError, TypeError, ValueError):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    protocol = json.loads((ROOT / report["protocol"]["path"]).read_text(encoding="utf-8"))
    oof = pd.read_csv(Path(report["oof_artifact"]["path"]), low_memory=False)
    validate_report(report, oof, protocol)

    def report_mutation(fn: object) -> bool:
        mutated = copy.deepcopy(report)
        fn(mutated)  # type: ignore[operator]
        return rejected(mutated, oof, protocol)

    def frame_mutation(fn: object) -> bool:
        mutated = oof.copy(deep=True)
        fn(mutated)  # type: ignore[operator]
        return rejected(report, mutated, protocol)

    checks = [
        ("baseline accepted", True),
        ("authorization mutation rejected", report_mutation(lambda r: r.update(betting_authorized=True))),
        ("confirmation mutation rejected", report_mutation(lambda r: r.update(confirmation_2025_opened=True))),
        ("status mutation rejected", report_mutation(lambda r: r.update(status="CUMULATIVE_SELECTION_PASSED_CANDIDATE_FROZEN" if not r["selection_passed"] else "CUMULATIVE_SELECTION_REJECTED_NO_CANDIDATE"))),
        ("simple score mutation rejected", report_mutation(lambda r: r["simple_baselines"][r["simple_baselines"]["selected"]]["scores"].update(multiclass_brier=0.0))),
        ("core score mutation rejected", report_mutation(lambda r: r["core_scores"].update(multiclass_log_loss=0.0))),
        ("selected interval mutation rejected", report_mutation(lambda r: r["selected_intervals_vs_canonical_v1"]["multiclass_brier"].update(upper=-999.0))),
        ("decision mutation rejected", report_mutation(lambda r: r.update(selection_passed=not r["selection_passed"]))),
        ("duplicate identity rejected", frame_mutation(lambda f: f.loc.__setitem__(1, f.loc[0]))),
        ("probability mutation rejected", frame_mutation(lambda f: f.__setitem__("cumulative_selected_strikeout", -1.0))),
        ("outcome mutation rejected", frame_mutation(lambda f: f.__setitem__("actual_strikeout", 0.5))),
        ("chronology mutation rejected", frame_mutation(lambda f: f.__setitem__("game_date", "2025-01-01"))),
    ]
    if report["core_stability_audit"]:
        checks.append(("stability omission rejected", report_mutation(lambda r: r["core_stability_audit"].pop())))
    else:
        checks.append(("fabricated stability rejected", report_mutation(lambda r: r["core_stability_audit"].append({"seed": 260720}))))
    if report["selection_passed"]:
        checks.append(("passing model omission rejected", report_mutation(lambda r: r.update(model_artifact=None))))
    else:
        checks.append(("rejected model publication rejected", report_mutation(lambda r: r.update(model_artifact={"path": "none", "sha256": "0"}))))
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"cumulative validator mutation checks failed: {failed}")
    print(f"CUMULATIVE SELECTION VALIDATOR MUTATIONS VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
