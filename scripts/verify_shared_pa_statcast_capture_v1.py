#!/usr/bin/env python3
"""Independent verifier for one completed bounded Statcast sample capture."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.capture_direct_batter_pa_source_transport_v2 import _enumerate_files, _manifest_digest
from scripts.capture_shared_pa_statcast_source_v1 import _validate_sample_plan
from src.data.shared_pa_statcast_source_v1 import (
    load_contract, parse_csv_bytes, sha256_file, validate_raw_receipt,
)


class VerificationError(ValueError):
    pass


def verify(root: Path, plan_path: Path, contract_path: Path, expected_digest: str | None) -> dict:
    if not root.is_dir() or root.is_symlink():
        raise VerificationError("capture root is absent or unsafe")
    manifest_path=root/"manifest.json"
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version")!="shared-pa-statcast-raw-capture-manifest-v1":
        raise VerificationError("capture manifest schema differs")
    observed=_manifest_digest(manifest)
    if observed!=manifest.get("observed_capture_digest") or (expected_digest is not None and observed!=expected_digest):
        raise VerificationError("capture digest differs")
    if _enumerate_files(root,exclude_manifest=True)!=manifest.get("files"):
        raise VerificationError("capture exact file set, sizes, or hashes differ")
    contract=load_contract(contract_path); plan=json.loads(plan_path.read_text(encoding="utf-8"))
    _validate_sample_plan(plan,contract,contract_path)
    if manifest.get("request_plan_sha256")!=sha256_file(plan_path) or manifest.get("source_contract_sha256")!=sha256_file(contract_path):
        raise VerificationError("capture plan or contract binding differs")
    request=plan["requests"][0]; request_dir=root/request["request_id"]
    terminal=json.loads((request_dir/"terminal.json").read_text(encoding="utf-8"))
    if terminal.get("state")!="SUCCESS" or manifest.get("success_count")!=1 or manifest.get("failure_count")!=0:
        raise VerificationError("bounded sample is not one terminal success")
    reservations=sorted(request_dir.glob("reservation-*.json")); results=sorted(request_dir.glob("result-*.json"))
    if not 1<=len(reservations)==len(results)<=contract["transport_policy"]["maximum_lifetime_attempts_per_exact_request"]:
        raise VerificationError("attempt journal count differs")
    starts=[]
    for index,path in enumerate(reservations,1):
        value=json.loads(path.read_text(encoding="utf-8"))
        if value.get("attempt")!=index or value.get("request")!=request:
            raise VerificationError("attempt reservation identity differs")
        starts.append(datetime.fromisoformat(value["request_started_at_utc"].replace("Z","+00:00")))
    for prior,current in zip(starts,starts[1:]):
        if (current-prior).total_seconds()<contract["transport_policy"]["minimum_request_start_interval_seconds"]:
            raise VerificationError("retained attempt journal violates pacing")
    raw=(request_dir/"response.csv").read_bytes(); receipt=json.loads((request_dir/"receipt.json").read_text(encoding="utf-8"))
    validate_raw_receipt(raw=raw,receipt=receipt,expected_request=request,contract_sha256=sha256_file(contract_path),parser_sha256=manifest["parser_sha256"],request_plan_sha256=sha256_file(plan_path))
    certified={int(item["game_pk"]):{"official_date":request["expected"]["official_date"],"home_team_id":item["home_team_id"],"away_team_id":item["away_team_id"],"home_team_code":item["home_team_code"],"away_team_code":item["away_team_code"]} for item in request["expected"]["certified_games"]}
    rows=parse_csv_bytes(raw,contract=contract,expected_date=request["expected"]["official_date"],certified_games=certified)
    return {"status":"VERIFIED_BOUNDED_RAW_SAMPLE","observed_capture_digest":observed,"request_count":1,"row_count":len(rows),"external_request_count":0}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--capture",type=Path,required=True); parser.add_argument("--request-plan",type=Path,required=True); parser.add_argument("--contract",type=Path,required=True); parser.add_argument("--expected-capture-digest"); args=parser.parse_args()
    print(json.dumps(verify(args.capture,args.request_plan,args.contract,args.expected_capture_digest),sort_keys=True)); return 0


if __name__=="__main__": raise SystemExit(main())
