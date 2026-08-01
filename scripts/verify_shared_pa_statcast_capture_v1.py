#!/usr/bin/env python3
"""Independent verifier for one completed bounded Statcast sample capture."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.capture_direct_batter_pa_source_transport_v2 import _enumerate_files, _is_link_or_reparse, _manifest_digest
from scripts.capture_shared_pa_statcast_source_v1 import _validate_sample_plan
from src.data.shared_pa_statcast_source_v1 import (
    load_contract, parse_csv_bytes, sha256_file, validate_raw_receipt,
)


class VerificationError(ValueError):
    pass


def verify(root: Path, plan_path: Path, contract_path: Path, expected_digest: str) -> dict:
    if not root.is_dir() or _is_link_or_reparse(root):
        raise VerificationError("capture root is absent or unsafe")
    manifest_path=root/"manifest.json"
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version")!="shared-pa-statcast-raw-capture-manifest-v1":
        raise VerificationError("capture manifest schema differs")
    if re.fullmatch(r"[0-9a-f]{64}",expected_digest or "") is None:
        raise VerificationError("external expected capture digest is required")
    observed=_manifest_digest(manifest)
    if observed!=manifest.get("observed_capture_digest") or observed!=expected_digest:
        raise VerificationError("capture digest differs")
    if _enumerate_files(root,exclude_manifest=True)!=manifest.get("files"):
        raise VerificationError("capture exact file set, sizes, or hashes differ")
    contract=load_contract(contract_path); plan=json.loads(plan_path.read_text(encoding="utf-8"))
    _validate_sample_plan(plan,contract,contract_path)
    if manifest.get("request_plan_sha256")!=sha256_file(plan_path) or manifest.get("source_contract_sha256")!=sha256_file(contract_path):
        raise VerificationError("capture plan or contract binding differs")
    request=plan["requests"][0]; request_dir=root/request["request_id"]
    if not request_dir.is_dir() or _is_link_or_reparse(request_dir):
        raise VerificationError("capture request directory is absent or unsafe")
    context=json.loads((root/"capture_context.json").read_text(encoding="utf-8"))
    required_context={"schema_version","request_plan_sha256","source_contract_sha256","parser_sha256","authorization_sha256","runtime_attestation_sha256","runtime_policy_sha256","source_bundle_sha256","carrier_commit"}
    if set(context)!=required_context or context.get("schema_version")!="shared-pa-statcast-capture-context-v1" or context.get("request_plan_sha256")!=sha256_file(plan_path) or context.get("source_contract_sha256")!=sha256_file(contract_path):
        raise VerificationError("capture context differs")
    for key in ("parser_sha256","authorization_sha256","runtime_attestation_sha256","runtime_policy_sha256","source_bundle_sha256","carrier_commit"):
        if context.get(key)!=manifest.get(key):
            raise VerificationError(f"capture context and manifest disagree on {key}")
    terminal=json.loads((request_dir/"terminal.json").read_text(encoding="utf-8"))
    if terminal.get("state")!="SUCCESS" or manifest.get("success_count")!=1 or manifest.get("failure_count")!=0:
        raise VerificationError("bounded sample is not one terminal success")
    reservations=sorted(request_dir.glob("reservation-*.json")); results=sorted(request_dir.glob("result-*.json"))
    if not 1<=len(reservations)==len(results)<=contract["transport_policy"]["maximum_lifetime_attempts_per_exact_request"]:
        raise VerificationError("attempt journal count differs")
    starts=[]; expected_files={"capture_context.json",f"{request['request_id']}/terminal.json",f"{request['request_id']}/response.csv",f"{request['request_id']}/receipt.json"}
    for index,path in enumerate(reservations,1):
        if path.name!=f"reservation-{index:02d}.json" or results[index-1].name!=f"result-{index:02d}.json":
            raise VerificationError("attempt journal filenames are noncanonical")
        value=json.loads(path.read_text(encoding="utf-8"))
        if value.get("attempt")!=index or value.get("request")!=request:
            raise VerificationError("attempt reservation identity differs")
        starts.append(datetime.fromisoformat(value["request_started_at_utc"].replace("Z","+00:00")))
        result=json.loads(results[index-1].read_text(encoding="utf-8"))
        if result.get("attempt")!=index or result.get("outcome") not in {"SUCCESS","RETRYABLE_FAILURE","NONRETRYABLE_FAILURE"}:
            raise VerificationError("attempt result schema or order differs")
        expected_files.update({f"{request['request_id']}/{path.name}",f"{request['request_id']}/{results[index-1].name}"})
        response_name=f"response-attempt-{index:02d}.{'csv' if result.get('http_status')==200 else 'bin'}"
        response_path=request_dir/response_name
        if result.get("http_status") is not None:
            if not response_path.is_file() or sha256_file(response_path)!=result.get("sha256") or response_path.stat().st_size!=result.get("byte_count"):
                raise VerificationError("attempt response bytes differ from result")
            expected_files.add(f"{request['request_id']}/{response_name}")
        if index>1:
            prior_result=json.loads(results[index-2].read_text(encoding="utf-8"))
            prior_observed=datetime.fromisoformat(prior_result["observed_at_utc"].replace("Z","+00:00"))
            required=max(float(contract["transport_policy"]["minimum_request_start_interval_seconds"]),float(prior_result.get("backoff_seconds",0.0)))
            if (starts[-1]-prior_observed).total_seconds()<required:
                raise VerificationError("retained attempt journal violates pacing or retry backoff")
    actual_files={row["path"] for row in manifest["files"]}
    if actual_files!=expected_files:
        raise VerificationError("capture contains a producer-listed but structurally unauthorized file")
    raw=(request_dir/"response.csv").read_bytes(); receipt=json.loads((request_dir/"receipt.json").read_text(encoding="utf-8"))
    validate_raw_receipt(raw=raw,receipt=receipt,expected_request=request,contract_sha256=sha256_file(contract_path),parser_sha256=manifest["parser_sha256"],request_plan_sha256=sha256_file(plan_path))
    certified={int(item["game_pk"]):{"official_date":request["expected"]["official_date"],"home_team_id":item["home_team_id"],"away_team_id":item["away_team_id"],"home_team_code":item["home_team_code"],"away_team_code":item["away_team_code"]} for item in request["expected"]["certified_games"]}
    rows=parse_csv_bytes(raw,contract=contract,expected_date=request["expected"]["official_date"],certified_games=certified)
    return {"status":"VERIFIED_BOUNDED_RAW_SAMPLE","observed_capture_digest":observed,"request_count":1,"row_count":len(rows),"external_request_count":0}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--capture",type=Path,required=True); parser.add_argument("--request-plan",type=Path,required=True); parser.add_argument("--contract",type=Path,required=True); parser.add_argument("--expected-capture-digest",required=True); args=parser.parse_args()
    print(json.dumps(verify(args.capture,args.request_plan,args.contract,args.expected_capture_digest),sort_keys=True)); return 0


if __name__=="__main__": raise SystemExit(main())
