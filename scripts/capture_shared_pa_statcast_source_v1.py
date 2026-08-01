#!/usr/bin/env python3
"""Authorization-gated raw Statcast CSV capture; never invoked by preflight."""
from __future__ import annotations
import argparse, json, os, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts.capture_direct_batter_pa_source_transport_v2 import (
    HTTPSHistoricalTransport, RuntimeAuthorization, authorize_runtime,
)
from src.data.shared_pa_statcast_source_v1 import canonical_json_bytes, load_contract, parse_csv_bytes, sha256_bytes, sha256_file
from src.evaluation.shared_pa_statcast_historical_source_access_v1 import (
    VerifiedStatcastHistoricalSourceAccess, verify_statcast_historical_source_access,
)

class CaptureError(RuntimeError): pass

def _utc() -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def _write_new(path: Path, value: object) -> None:
    with path.open("xb") as h: h.write(canonical_json_bytes(value) if not isinstance(value, bytes) else value)

def _transport(full_url: str, timeout: float, maximum: int) -> tuple[int, dict[str,str], bytes]:
    response = HTTPSHistoricalTransport().fetch(
        {"full_url": full_url}, timeout_seconds=timeout, max_bytes=maximum,
    )
    if response.final_url != full_url:
        raise CaptureError("source redirect or final URL change rejected")
    return response.status, dict(response.headers), response.body


SOURCE_BUNDLE_FILES = (
    "scripts/capture_shared_pa_statcast_source_v1.py",
    "scripts/capture_direct_batter_pa_source_transport_v2.py",
    "scripts/build_direct_batter_pa_source_release.py",
    "scripts/verify_direct_batter_pa_source_runtime_authority.py",
    "src/data/shared_pa_statcast_source_v1.py",
    "src/evaluation/shared_pa_statcast_historical_source_access_v1.py",
)


def source_bundle_sha256() -> str:
    rows = [{"path": path, "sha256": sha256_file(ROOT / path)} for path in SOURCE_BUNDLE_FILES]
    return sha256_bytes(canonical_json_bytes(rows))


def _validate_authorities(
    *, plan_path: Path, contract_path: Path, runtime: RuntimeAuthorization,
    access: VerifiedStatcastHistoricalSourceAccess, carrier_commit: str,
) -> None:
    if runtime.policy_sha256 != access.runtime_policy_sha256:
        raise CaptureError("runtime policy differs from source-access authorization")
    if access.request_plan_sha256 != sha256_file(plan_path):
        raise CaptureError("request plan differs from source-access authorization")
    if access.source_contract_sha256 != sha256_file(contract_path):
        raise CaptureError("source contract differs from source-access authorization")
    if access.source_bundle_sha256 != source_bundle_sha256():
        raise CaptureError("source bundle differs from source-access authorization")
    if access.carrier_commit != carrier_commit:
        raise CaptureError("carrier commit differs from source-access authorization")
    active = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()
    if active != carrier_commit:
        raise CaptureError("active checkout is not the authorized carrier commit")

def capture(
    plan_path: Path, contract_path: Path, output_dir: Path, *,
    runtime_authorization: RuntimeAuthorization,
    source_access: VerifiedStatcastHistoricalSourceAccess,
    carrier_commit: str,
    transport=_transport, sleep=time.sleep, wall_time=time.time,
) -> dict:
    plan=json.loads(plan_path.read_text(encoding="utf-8")); contract=load_contract(contract_path)
    _validate_authorities(plan_path=plan_path, contract_path=contract_path, runtime=runtime_authorization, access=source_access, carrier_commit=carrier_commit)
    if plan.get("plan_kind") != "BOUNDED_SAMPLE" or plan.get("request_count") != 1: raise CaptureError("only the bounded sample plan is executable")
    root=Path(os.path.abspath(os.fspath(output_dir))); work=root.with_name(root.name+".work")
    if root.exists(): raise CaptureError("output path already exists")
    plan_sha=sha256_file(plan_path); contract_sha=sha256_file(contract_path)
    parser_sha=sha256_file(ROOT/"src/data/shared_pa_statcast_source_v1.py")
    context={"schema_version":"shared-pa-statcast-capture-context-v1","request_plan_sha256":plan_sha,"source_contract_sha256":contract_sha,"parser_sha256":parser_sha,"authorization_sha256":source_access.authorization_file_sha256,"runtime_attestation_sha256":runtime_authorization.attestation_sha256,"runtime_policy_sha256":runtime_authorization.policy_sha256,"source_bundle_sha256":source_access.source_bundle_sha256,"carrier_commit":carrier_commit}
    if work.exists():
        if work.is_symlink() or not (work/"capture_context.json").is_file() or json.loads((work/"capture_context.json").read_text(encoding="utf-8")) != context:
            raise CaptureError("existing work state is not the exact owned resumable capture")
    else:
        work.mkdir(parents=True,exist_ok=False); _write_new(work/"capture_context.json",context)
    previous_epoch=None
    for reservation in work.glob("*/reservation-*.json"):
        value=json.loads(reservation.read_text(encoding="utf-8")); previous_epoch=max(previous_epoch or 0.0,float(value["request_start_epoch"] or 0.0))
    successes=0
    for request in plan["requests"]:
        request_dir=work/request["request_id"]; request_dir.mkdir(exist_ok=True)
        if (request_dir/"terminal.json").exists():
            terminal=json.loads((request_dir/"terminal.json").read_text(encoding="utf-8")); successes += int(terminal.get("state") == "SUCCESS"); continue
        completed=len(list(request_dir.glob("reservation-*.json")))
        for attempt in range(completed+1, contract["transport_policy"]["maximum_lifetime_attempts_per_exact_request"]+1):
            now=wall_time()
            if previous_epoch is not None:
                sleep(max(0.0, contract["transport_policy"]["minimum_request_start_interval_seconds"]-(now-previous_epoch)))
            started=_utc(); request_epoch=wall_time(); previous_epoch=request_epoch
            _write_new(request_dir/f"reservation-{attempt:02d}.json", {"request":request,"attempt":attempt,"request_started_at_utc":started,"request_start_epoch":request_epoch})
            try:
                status, headers, body=transport(request["full_url"], contract["transport_policy"]["timeout_seconds"], contract["transport_policy"]["maximum_response_bytes"])
            except Exception as exc:
                _write_new(request_dir/f"transport-failure-{attempt:02d}.json", {"attempt":attempt,"error_type":type(exc).__name__,"message":str(exc),"observed_at_utc":_utc()})
                if attempt < contract["transport_policy"]["maximum_lifetime_attempts_per_exact_request"]:
                    sleep(float(contract["transport_policy"]["bounded_backoff_seconds"][min(attempt-1,2)])); continue
                break
            safe={k:v for k,v in headers.items() if k in contract["transport_policy"]["safe_response_headers"]}
            result={"attempt":attempt,"request_started_at_utc":started,"observed_at_utc":_utc(),"http_status":status,"response_headers":safe,"byte_count":len(body),"sha256":sha256_bytes(body)}
            _write_new(request_dir/f"result-{attempt:02d}.json",result)
            _write_new(request_dir/f"response-attempt-{attempt:02d}.csv",body)
            if safe.get("content-length") not in (None,"",str(len(body))):
                _write_new(request_dir/f"validation-failure-{attempt:02d}.json",{"error_type":"CaptureError","message":"Content-Length differs from retained bytes"}); break
            if status==200:
                certified={int(item["game_pk"]): {"official_date":request["expected"]["official_date"], "home_team_id":item["home_team_id"], "away_team_id":item["away_team_id"], "home_team_code":item["home_team_code"], "away_team_code":item["away_team_code"]} for item in request["expected"]["certified_games"]}
                try:
                    parse_csv_bytes(body,contract=contract,expected_date=request["expected"]["official_date"],certified_games=certified)
                except Exception as exc:
                    _write_new(request_dir/f"validation-failure-{attempt:02d}.json",{"error_type":type(exc).__name__,"message":str(exc)})
                    break
                _write_new(request_dir/"response.csv",body)
                receipt={"schema_version":"shared-pa-statcast-raw-receipt-v1","request":request,"request_plan_sha256":plan_sha,"source_contract_sha256":contract_sha,"parser_sha256":parser_sha,"attempt_number":attempt,"request_started_at_utc":started,"observed_at_utc":result["observed_at_utc"],"http_status":status,"response_headers":safe,"byte_count":len(body),"sha256":sha256_bytes(body),"terminal_state":"SUCCESS"}
                _write_new(request_dir/"receipt.json",receipt); _write_new(request_dir/"terminal.json",{"state":"SUCCESS","attempt":attempt,"sha256":sha256_bytes(body)}); successes+=1; break
            if status not in contract["transport_policy"]["retryable_http_statuses"]: break
            retry_after=safe.get("retry-after","")
            try: delay=float(retry_after) if retry_after else float(contract["transport_policy"]["bounded_backoff_seconds"][attempt-1])
            except (ValueError,IndexError): delay=float(contract["transport_policy"]["bounded_backoff_seconds"][min(attempt-1,2)])
            sleep(max(0.0,min(delay,float(contract["transport_policy"]["maximum_retry_after_seconds"]))))
        if not (request_dir/"terminal.json").exists(): _write_new(request_dir/"terminal.json",{"state":"FAILURE","attempts":attempt})
    manifest={"schema_version":"shared-pa-statcast-raw-capture-manifest-v1","request_plan_sha256":plan_sha,"source_contract_sha256":contract_sha,"parser_sha256":parser_sha,"authorization_sha256":source_access.authorization_file_sha256,"runtime_attestation_sha256":runtime_authorization.attestation_sha256,"runtime_policy_sha256":runtime_authorization.policy_sha256,"source_bundle_sha256":source_access.source_bundle_sha256,"carrier_commit":carrier_commit,"request_count":plan["request_count"],"success_count":successes,"failure_count":plan["request_count"]-successes}
    _write_new(work/"capture_manifest.json",manifest); os.replace(work,root); return manifest

def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--request-plan",type=Path,required=True); p.add_argument("--contract",type=Path,required=True); p.add_argument("--authorization",type=Path,required=True); p.add_argument("--expected-authorization-sha256",required=True); p.add_argument("--runtime-policy",type=Path,required=True); p.add_argument("--runtime-attestation",type=Path,required=True); p.add_argument("--expected-runtime-attestation-sha256",required=True); p.add_argument("--expected-source-bundle-sha256",required=True); p.add_argument("--carrier-commit",required=True); p.add_argument("--output-dir",type=Path,required=True); a=p.parse_args()
    runtime=authorize_runtime(attestation_path=a.runtime_attestation,expected_attestation_sha256=a.expected_runtime_attestation_sha256,policy_path=a.runtime_policy)
    access=verify_statcast_historical_source_access(authorization_path=a.authorization,expected_authorization_sha256=a.expected_authorization_sha256,expected_carrier_commit=a.carrier_commit,expected_runtime_policy_sha256=runtime.policy_sha256,expected_source_bundle_sha256=a.expected_source_bundle_sha256,expected_source_contract_sha256=sha256_file(a.contract),expected_request_plan_sha256=sha256_file(a.request_plan),access_time_utc=_utc())
    print(json.dumps(capture(a.request_plan,a.contract,a.output_dir,runtime_authorization=runtime,source_access=access,carrier_commit=a.carrier_commit),sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
