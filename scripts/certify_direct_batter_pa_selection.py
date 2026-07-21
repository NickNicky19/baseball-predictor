#!/usr/bin/env python3
"""Independently recompute and certify the direct batter PA rejection."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path: os.sys.path.insert(0, str(ROOT))
from scripts.select_direct_batter_pa_foundation import _component, _scores  # noqa: E402
from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.learning.shared_pa_model import proper_scores  # noqa: E402


def sha256_file(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1<<20),b""): digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists(): raise FileExistsError(f"refusing to overwrite certification evidence: {path}")
    path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent,text=True)
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as handle:
            json.dump(value,handle,sort_keys=True,indent=2,allow_nan=False); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp,path)
    except Exception:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
        raise


def _close(actual: float, expected: float) -> None:
    if not np.isclose(float(actual),float(expected),rtol=0,atol=1e-12):
        raise ValueError(f"selection arithmetic mismatch: {actual} != {expected}")


def certify(*, panel: Path, panel_certificate: Path, report: Path, predictions: Path, certificate: Path, rejection: Path) -> dict[str, Any]:
    source_certificate=json.loads(panel_certificate.read_text(encoding="utf-8")); result=json.loads(report.read_text(encoding="utf-8"))
    if source_certificate.get("status")!="DIRECT_BATTER_PA_PANEL_CERTIFIED_RESEARCH_ONLY": raise ValueError("panel is not certified")
    if result.get("status")!="SELECTION_REJECTED_NO_CANDIDATE": raise ValueError("selection is not a rejection")
    if result["chronology"]!={"fit_year":2023,"selection_year":2024,"confirmation_opened":False,"spent_2025_hr_reused":False,"may_2026_opened":False}: raise ValueError("selection chronology flags changed")
    frame=pd.read_csv(panel,low_memory=False); eligible=outcome_counts(frame).sum(axis=1).gt(0); selection=frame.loc[eligible & frame["season"].eq(2024)].reset_index(drop=True)
    raw_truth=frame[[f"target_{name}" for name in PA_OUTCOMES]].rename(columns={f"target_{name}":name for name in PA_OUTCOMES}).astype(int)
    if not outcome_counts(frame).loc[:,PA_OUTCOMES].astype(int).equals(raw_truth): raise ValueError("probability consumer outcome columns do not equal raw terminal truth")
    prediction=pd.read_csv(predictions,low_memory=False)
    if len(selection)!=len(prediction) or prediction.duplicated(["game_pk","player_id"]).any(): raise ValueError("prediction identity population changed")
    if not selection[["game_pk","player_id","game_date"]].astype(str).reset_index(drop=True).equals(prediction[["game_pk","player_id","game_date"]].astype(str).reset_index(drop=True)): raise ValueError("prediction identity order differs from panel")
    counts=outcome_counts(selection)
    recomputed: dict[str,Any]={}
    matrices: dict[str,np.ndarray]={}
    for label in ("candidate","core","simple"):
        matrix=prediction[[f"{label}_{outcome}" for outcome in PA_OUTCOMES]].to_numpy(float)
        if not np.isfinite(matrix).all() or (matrix<0).any() or not np.allclose(matrix.sum(axis=1),1,atol=1e-9): raise ValueError(f"invalid {label} probabilities")
        matrices[label]=matrix; recomputed[label]=proper_scores(counts,matrix)
    for metric,value in result["candidate_scores"].items(): _close(recomputed["candidate"][metric],value)
    _close(recomputed["core"]["multiclass_brier"],result["overall_comparators"]["all_prior_core"]["scores"]["multiclass_brier"])
    _close(recomputed["simple"]["multiclass_log_loss"],result["overall_comparators"]["empirical_bayes_player_rate_pa_200"]["scores"]["multiclass_log_loss"])
    component_recomputed={}
    for name in ("hits","hr_over_0_5","total_bases"):
        component_counts,candidate_p=_component(selection,matrices["candidate"],name); scores=_scores(component_counts,candidate_p)
        for metric in ("brier","log_loss","auc"): _close(scores[metric],result["components"][name]["candidate"][metric])
        if result["components"][name]["selection_eligible"] is not False: raise ValueError(f"rejected component reclassified: {name}")
        component_recomputed[name]=scores
    cert={
        "schema_version":"direct-batter-pa-selection-certificate-v1","status":"DIRECT_BATTER_PA_SELECTION_REJECTION_CERTIFIED",
        "panel_certificate":{"path":str(panel_certificate),"sha256":sha256_file(panel_certificate)},
        "report":{"path":str(report),"sha256":sha256_file(report)},"predictions":{"path":str(predictions),"sha256":sha256_file(predictions),"rows":len(prediction)},
        "recomputed_scores":recomputed,"recomputed_components":component_recomputed,
        "validation":{"identity_rows":len(prediction),"probability_sum_violations":0,"probability_consumer_raw_truth_parity":True,"arithmetic_tolerance":1e-12,"rejected_components":["hits","hr_over_0_5","total_bases"]},
        "protected_invariants":{"confirmation_2025_opened":False,"may_2026_opened":False,"production_changed":False,"betting_authorized":False},
        "certifier_sha256":sha256_file(Path(__file__)),
    }
    atomic_json(certificate,cert)
    rejection_record={
        "schema_version":"direct-batter-pa-rejection-v1","status":"REJECTED_BEFORE_CONFIRMATION_PRODUCTION_MARKET_OR_POLICY",
        "selection_report_sha256":sha256_file(report),"selection_certificate_sha256":sha256_file(certificate),
        "reasons":[
            "The direct identity-and-recency candidate was worse than the all-prior batter-only core on both multiclass proper scores, with both paired date-block intervals wholly above zero.",
            "The candidate improved on the empirical-Bayes control only modestly and missed the locked 1% materiality floor for both Brier score and log loss.",
            "Hits was worse than both controls at the proper-score point estimates and worse than the all-prior core in discrimination.",
            "HR gains versus the empirical-Bayes control were immaterial; uncertainty crossed zero versus the all-prior core and discrimination was inferior to that core.",
            "Total Bases was worse than the all-prior core; its Brier interval crossed zero versus empirical Bayes and its discrimination was inferior to both controls."
        ],
        "forbidden_rescues":["2025 HR confirmation reuse","May 2026","threshold or subgroup tuning","post-hoc calibration","pitcher or actual-starter backfill","market pooling","production installation","betting authorization"],
        "highest_value_next_action":"Verify whether fresh prospective T-minus-4 probable-starter receipts satisfy the existing exact identity lifecycle. If they do not, keep the pitcher block excluded and collect new prospective receipts; do not guess or backfill starters.",
        "full_game_market_status":{"hits":"not_eligible","hr_over_0_5":"not_eligible","total_bases":"not_eligible","reason":"per-PA component rejection; pregame PA-volume confirmation also absent"},
        "confirmation_2025_opened":False,"may_2026_opened":False,"production_changed":False,"betting_authorized":False,
    }
    atomic_json(rejection,rejection_record); return cert


def main()->int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--panel",required=True,type=Path); p.add_argument("--panel-certificate",required=True,type=Path); p.add_argument("--report",required=True,type=Path); p.add_argument("--predictions",required=True,type=Path); p.add_argument("--certificate",required=True,type=Path); p.add_argument("--rejection",required=True,type=Path)
    r=certify(**vars(p.parse_args())); print(json.dumps({"status":r["status"],"validation":r["validation"]},sort_keys=True)); return 0


if __name__=="__main__": raise SystemExit(main())
