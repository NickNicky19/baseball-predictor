#!/usr/bin/env python3
"""Mutation-test the hierarchical selector certifier against published artifacts."""
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

from scripts.validate_shared_pa_hierarchical_selection import validate_report  # noqa: E402
from src.evaluation.shared_pa_hierarchical_selection import load_protocol  # noqa: E402


def rejected(report: dict, oof: pd.DataFrame, protocol: dict, base: dict, evidence_root: Path) -> bool:
    try:
        validate_report(report, oof, protocol, base, evidence_root=evidence_root)
    except (KeyError, TypeError, ValueError):
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    protocol_path = ROOT / report["protocol"]["path"]
    protocol, _, base = load_protocol(protocol_path, code_root=ROOT, evidence_root=evidence_root)
    oof = pd.read_csv(Path(report["oof_artifact"]["path"]), low_memory=False)
    validate_report(report, oof, protocol, base, evidence_root=evidence_root)

    def report_mutation(fn: object) -> bool:
        changed = copy.deepcopy(report); fn(changed)  # type: ignore[operator]
        return rejected(changed, oof, protocol, base, evidence_root)

    def frame_mutation(fn: object) -> bool:
        changed = oof.copy(deep=True); fn(changed)  # type: ignore[operator]
        return rejected(report, changed, protocol, base, evidence_root)

    checks = [
        ("baseline accepted", True),
        ("authorization rejected", report_mutation(lambda r: r.update(betting_authorized=True))),
        ("production rejected", report_mutation(lambda r: r.update(production_changed=True))),
        ("2025 rejected", report_mutation(lambda r: r.update(confirmation_2025_opened=True))),
        ("May rejected", report_mutation(lambda r: r.update(may_2026_opened=True))),
        ("status rejected", report_mutation(lambda r: r.update(status="HIERARCHICAL_SELECTION_PASSED_CANDIDATE_FROZEN" if not r["selection_passed"] else "HIERARCHICAL_SELECTION_REJECTED_NO_CANDIDATE"))),
        ("raw score rejected", report_mutation(lambda r: r["raw"]["scores"].update(multiclass_log_loss=0.0))),
        ("interval rejected", report_mutation(lambda r: r["selected"]["intervals_vs_canonical_v1"]["multiclass_brier"].update(upper=-99.0))),
        ("market decision rejected", report_mutation(lambda r: r["derived_markets"]["hits_0.5"].update(eligible_for_confirmation=not r["derived_markets"]["hits_0.5"]["eligible_for_confirmation"]))),
        ("final decision rejected", report_mutation(lambda r: r.update(selection_passed=not r["selection_passed"]))),
        ("duplicate identity rejected", frame_mutation(lambda f: f.loc.__setitem__(1, f.loc[0]))),
        ("probability rejected", frame_mutation(lambda f: f.__setitem__("hierarchical_selected_strikeout", -1.0))),
        ("outcome rejected", frame_mutation(lambda f: f.__setitem__("actual_strikeout", 0.5))),
        ("chronology rejected", frame_mutation(lambda f: f.__setitem__("game_date", "2025-01-01"))),
    ]
    if report["stability"]["records"]:
        checks.append(("stability omission rejected", report_mutation(lambda r: r["stability"]["records"].pop())))
    else:
        checks.append(("fabricated stability rejected", report_mutation(lambda r: r["stability"]["records"].append({"seed": 260725}))))
    if report["selection_passed"]:
        checks.append(("model omission rejected", report_mutation(lambda r: r.update(model_artifacts=None))))
    else:
        checks.append(("rejected model publication rejected", report_mutation(lambda r: r.update(model_artifacts={"stage_1": {}}))))
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"hierarchical validator mutation checks failed: {failed}")
    print(f"HIERARCHICAL SELECTION VALIDATOR MUTATIONS VALID: {len(checks)}/{len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
