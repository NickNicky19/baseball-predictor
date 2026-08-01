#!/usr/bin/env python3
"""Authorization-gated raw Statcast CSV capture; never invoked by preflight."""
from __future__ import annotations
import argparse, json, os, re, socket, ssl, subprocess, sys, time, urllib.error, urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterator
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts.capture_direct_batter_pa_source_transport_v2 import (
    CapturedResponse, RuntimeAuthorization, TransportFailure, _NoRedirect,
    _enumerate_files, _is_link_or_reparse, _manifest_digest, _read_limited,
    _validate_response, authorize_runtime,
)
from scripts.build_shared_pa_statcast_request_plans_v1 import QUERY_BASE
from src.data.shared_pa_statcast_source_v1 import canonical_json_bytes, load_contract, parse_csv_bytes, sha256_bytes, sha256_file
from src.evaluation.shared_pa_statcast_historical_source_access_v1 import (
    VerifiedStatcastHistoricalSourceAccess, verify_statcast_historical_source_access,
)

class CaptureError(RuntimeError): pass

TransportCallable = Callable[[str, float, int], CapturedResponse]


@dataclass(frozen=True)
class TransportExecutionAuthorization:
    """Hash-bound proof that the real network transport may be invoked."""

    authorization_sha256: str
    runtime_attestation_sha256: str
    runtime_policy_sha256: str
    source_bundle_sha256: str
    source_contract_sha256: str
    request_plan_sha256: str
    carrier_commit: str

    def validate(self) -> None:
        values = (
            self.authorization_sha256,
            self.runtime_attestation_sha256,
            self.runtime_policy_sha256,
            self.source_bundle_sha256,
            self.source_contract_sha256,
            self.request_plan_sha256,
        )
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in values):
            raise CaptureError("real transport requires complete hash-bound authorization")
        if not isinstance(self.carrier_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", self.carrier_commit):
            raise CaptureError("real transport requires a hash-bound carrier commit")


def _execution_authorization(runtime: RuntimeAuthorization, access: VerifiedStatcastHistoricalSourceAccess, carrier_commit: str) -> TransportExecutionAuthorization:
    gate = TransportExecutionAuthorization(
        authorization_sha256=access.authorization_file_sha256,
        runtime_attestation_sha256=runtime.attestation_sha256,
        runtime_policy_sha256=runtime.policy_sha256,
        source_bundle_sha256=access.source_bundle_sha256,
        source_contract_sha256=access.source_contract_sha256,
        request_plan_sha256=access.request_plan_sha256,
        carrier_commit=carrier_commit,
    )
    gate.validate()
    return gate

def _utc() -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def _write_new(path: Path, value: object) -> None:
    with path.open("xb") as h:
        h.write(canonical_json_bytes(value) if not isinstance(value, bytes) else value)
        h.flush(); os.fsync(h.fileno())
    _fsync_dir(path.parent)

def _fsync_dir(path: Path) -> None:
    flag=getattr(os,"O_DIRECTORY",None)
    if flag is None: return
    fd=os.open(path,os.O_RDONLY|flag)
    try: os.fsync(fd)
    finally: os.close(fd)

def _validate_work_tree(work: Path, request_id: str) -> None:
    allowed_top={"capture_context.json",request_id}
    if {path.name for path in work.iterdir()}-allowed_top: raise CaptureError("resumable work state contains an unexpected path")
    context=work/"capture_context.json"
    if _is_link_or_reparse(context) or not context.is_file(): raise CaptureError("resumable capture context is unsafe")
    child=work/request_id
    if not child.exists(): return
    if _is_link_or_reparse(child) or not child.is_dir(): raise CaptureError("resumable request state is unsafe")
    allowed_exact={"terminal.json","response.csv","receipt.json"}
    allowed_pattern=re.compile(r"(?:reservation|result|validation-failure)-[0-9]{2}\.json|response-attempt-[0-9]{2}\.(?:csv|bin)\Z")
    for path in child.iterdir():
        if _is_link_or_reparse(path) or not path.is_file() or (path.name not in allowed_exact and allowed_pattern.fullmatch(path.name) is None):
            raise CaptureError("resumable request state contains an unexpected file")

def _normalized_headers(message) -> dict[str,str]:  # noqa: ANN001
    rows=message.raw_items() if hasattr(message,"raw_items") else message.items()
    allowed={"cache-control","content-disposition","content-encoding","content-length","content-type","date","etag","last-modified","retry-after"}
    result={}
    for raw_name,raw_value in rows:
        name=str(raw_name).strip().lower(); value=str(raw_value).strip()
        if name not in allowed: continue
        if name in result: raise CaptureError("response contains a duplicate safety-relevant header")
        if "\r" in value or "\n" in value: raise CaptureError("response header contains a line break")
        result[name]=value
    return {key:result[key] for key in sorted(result)}

def _transport(full_url: str, timeout: float, maximum: int, *, execution_authorization: TransportExecutionAuthorization) -> CapturedResponse:
    execution_authorization.validate()
    requested=_utc(); opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request=urllib.request.Request(full_url,method="GET",headers={"Accept":"text/csv","Accept-Encoding":"identity","User-Agent":"baseball-predictor-research-source/1.0"})
    try:
        with opener.open(request,timeout=timeout) as response:
            body=_read_limited(response,max_bytes=maximum)
            return CapturedResponse(int(response.status),body,_normalized_headers(response.headers),str(response.geturl()),requested,_utc())
    except urllib.error.HTTPError as exc:
        body=_read_limited(exc,max_bytes=maximum)
        return CapturedResponse(int(exc.code),body,_normalized_headers(exc.headers),str(exc.geturl()),requested,_utc())
    except CaptureError: raise
    except urllib.error.URLError as exc:
        tls=isinstance(getattr(exc,"reason",None),ssl.SSLError)
        raise TransportFailure(error_kind="tls_failure" if tls else "transport_io",retryable=not tls,requested_at_utc=requested,observed_at_utc=_utc()) from exc


SOURCE_BUNDLE_FILES = (
    "scripts/capture_shared_pa_statcast_source_v1.py",
    "scripts/capture_direct_batter_pa_source_transport_v2.py",
    "scripts/build_direct_batter_pa_source_release.py",
    "scripts/verify_direct_batter_pa_source_runtime_authority.py",
    "scripts/verify_shared_pa_statcast_capture_v1.py",
    "src/data/__init__.py",
    "src/data/shared_pa_statcast_source_v1.py",
    "src/evaluation/shared_pa_statcast_historical_source_access_v1.py",
)

ATTEMPT_HISTORY_PATH = ROOT / "config/shared_pa_statcast_sample_attempt_history_20260731_v1.json"


def _validate_attempt_history(path: Path, *, request_id: str, source_access: VerifiedStatcastHistoricalSourceAccess, maximum_attempts: int) -> int:
    if not path.is_file() or _is_link_or_reparse(path):
        raise CaptureError("quarantined incident attempt history is missing or unsafe")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "shared-pa-statcast-attempt-history-v1":
        raise CaptureError("attempt history schema is invalid")
    if sha256_file(path) != source_access.attempt_history_sha256:
        raise CaptureError("attempt history differs from authorization")
    if payload.get("request_id") != request_id or payload.get("attempt_number") != 1:
        raise CaptureError("attempt history does not bind the exact first attempt")
    if payload.get("status") != "UNAUTHORIZED_QUARANTINED" or payload.get("promoted_to_source_release") is not False:
        raise CaptureError("attempt history does not preserve quarantine state")
    if payload.get("remaining_lifetime_attempts") != maximum_attempts - 1:
        raise CaptureError("attempt history lifetime accounting differs from policy")
    return int(payload["attempt_number"])


@contextmanager
def network_denial_guard() -> Iterator[None]:
    """Block socket-level escapes even if an injected transport is bypassed."""
    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def blocked(*_args, **_kwargs):
        raise CaptureError("lower-layer network guard blocked external transport")

    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    try:
        yield
    finally:
        socket.create_connection = original_create_connection
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex


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


def _validate_sample_plan(plan: dict, contract: dict, contract_path: Path) -> None:
    expected_keys={"schema_version","status","plan_kind","research_only","external_requests_permitted","season","historical_scope","source_contract_sha256","request_policy","requests","request_count","certified_game_count","protected_boundaries"}
    if set(plan) != expected_keys or plan.get("schema_version") != "shared-pa-statcast-request-plan-v1" or plan.get("status") != "PREPARED_NOT_AUTHORIZED":
        raise CaptureError("bounded request-plan schema or status differs")
    if plan.get("plan_kind") != "BOUNDED_SAMPLE" or plan.get("request_count") != 1 or plan.get("certified_game_count") != 15:
        raise CaptureError("bounded request-plan count or kind differs")
    if plan.get("research_only") is not True or plan.get("external_requests_permitted") is not False or plan.get("season") != 2023:
        raise CaptureError("bounded request-plan safety state differs")
    if plan.get("historical_scope") != contract["historical_scope"] or plan.get("request_policy") != contract["transport_policy"] or plan.get("protected_boundaries") != contract["protected_boundaries"] or plan.get("source_contract_sha256") != sha256_file(contract_path):
        raise CaptureError("bounded request-plan contract binding differs")
    request=plan["requests"][0]
    expected_request_keys={"request_id","method","url","query","full_url","expected"}
    if set(request) != expected_request_keys or request.get("request_id") != "statcast-2023-07-25" or request.get("method") != "GET":
        raise CaptureError("bounded request identity differs")
    base=f"{contract['source']['scheme']}://{contract['source']['host']}{contract['source']['path']}"
    query=dict(QUERY_BASE); query.update({"game_date_gt":"2023-07-25","game_date_lt":"2023-07-25"}); query={key:query[key] for key in sorted(query)}
    from urllib.parse import urlencode
    if request.get("url") != base or request.get("query") != query or request.get("full_url") != f"{base}?{urlencode(query)}":
        raise CaptureError("bounded request URL or unfiltered query differs")
    expected=request.get("expected")
    if not isinstance(expected,dict) or set(expected)!={"official_date","certified_games"} or expected.get("official_date")!="2023-07-25":
        raise CaptureError("bounded request date or expected schema differs")
    games=expected.get("certified_games")
    if not isinstance(games,list) or len(games)!=15 or [item.get("game_pk") for item in games] != sorted(item.get("game_pk") for item in games):
        raise CaptureError("bounded certified-game universe differs")
    for item in games:
        if set(item)!={"game_pk","home_team_id","away_team_id","home_team_code","away_team_code"}:
            raise CaptureError("bounded certified-game identity schema differs")
        if item["home_team_code"] != contract["team_source_codes"].get(str(item["home_team_id"])) or item["away_team_code"] != contract["team_source_codes"].get(str(item["away_team_id"])):
            raise CaptureError("bounded certified-game team mapping differs")


def _retry_after(value: str | None, observed_at_utc: str, maximum: float) -> float | None:
    if not value:
        return None
    try:
        seconds=float(value)
    except ValueError:
        try:
            observed=datetime.fromisoformat(observed_at_utc.replace("Z","+00:00"))
            seconds=(parsedate_to_datetime(value)-observed).total_seconds()
        except Exception as exc:
            raise CaptureError("Retry-After is invalid") from exc
    if seconds < 0 or seconds > maximum:
        raise CaptureError("Retry-After lies outside the locked bound")
    return seconds


def _write_manifest(work: Path, manifest: dict) -> dict:
    manifest["files"]=_enumerate_files(work,exclude_manifest=True)
    manifest["observed_capture_digest"]=None
    manifest["observed_capture_digest"]=_manifest_digest(manifest)
    _write_new(work/"manifest.json",manifest)
    return manifest

def capture(
    plan_path: Path, contract_path: Path, output_dir: Path, *,
    runtime_authorization: RuntimeAuthorization,
    source_access: VerifiedStatcastHistoricalSourceAccess,
    carrier_commit: str,
    transport: TransportCallable,
    attempt_history_path: Path | None = None,
    sleep=time.sleep, wall_time=time.time,
) -> dict:
    plan=json.loads(plan_path.read_text(encoding="utf-8")); contract=load_contract(contract_path)
    _validate_sample_plan(plan,contract,contract_path)
    _validate_authorities(plan_path=plan_path, contract_path=contract_path, runtime=runtime_authorization, access=source_access, carrier_commit=carrier_commit)
    root=Path(os.path.abspath(os.fspath(output_dir))); work=root.with_name(root.name+".work")
    authorized_output=Path(source_access.output_path)
    if not authorized_output.is_absolute(): authorized_output=ROOT/authorized_output
    if root != Path(os.path.abspath(os.fspath(authorized_output))): raise CaptureError("output path differs from authorization-scoped attempt identity")
    if attempt_history_path is None and not source_access.attempt_history_sha256:
        # Synthetic/offline fixtures predating the incident ledger remain usable;
        # every externally authorized access has a non-empty bound digest.
        prior_attempt_count = 0
    else:
        prior_attempt_count = _validate_attempt_history(
            attempt_history_path or ATTEMPT_HISTORY_PATH,
            request_id=plan["requests"][0]["request_id"],
            source_access=source_access,
            maximum_attempts=int(contract["transport_policy"]["maximum_lifetime_attempts_per_exact_request"]),
        )
    if root.exists(): raise CaptureError("output path already exists")
    for ancestor in (root.parent, *root.parent.parents):
        if ancestor.exists() and _is_link_or_reparse(ancestor): raise CaptureError("output path has a symlink or reparse ancestor")
    root.parent.mkdir(parents=True,exist_ok=True)
    lock=root.with_name(root.name+".lock")
    try: lock_fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError as exc: raise CaptureError("capture writer lock already exists") from exc
    plan_sha=sha256_file(plan_path); contract_sha=sha256_file(contract_path)
    parser_sha=sha256_file(ROOT/"src/data/shared_pa_statcast_source_v1.py")
    context={"schema_version":"shared-pa-statcast-capture-context-v1","request_plan_sha256":plan_sha,"source_contract_sha256":contract_sha,"parser_sha256":parser_sha,"authorization_sha256":source_access.authorization_file_sha256,"runtime_attestation_sha256":runtime_authorization.attestation_sha256,"runtime_policy_sha256":runtime_authorization.policy_sha256,"source_bundle_sha256":source_access.source_bundle_sha256,"attempt_history_sha256":source_access.attempt_history_sha256,"prior_attempt_count":prior_attempt_count,"carrier_commit":carrier_commit}
    if work.exists():
        context_path=work/"capture_context.json"
        if _is_link_or_reparse(work) or not context_path.is_file() or _is_link_or_reparse(context_path) or json.loads(context_path.read_text(encoding="utf-8")) != context:
            raise CaptureError("existing work state is not the exact owned resumable capture")
        _validate_work_tree(work,plan["requests"][0]["request_id"])
    else:
        work.mkdir(parents=True,exist_ok=False); _fsync_dir(work.parent); _write_new(work/"capture_context.json",context)
    previous_epoch=None
    for reservation in work.glob("*/reservation-*.json"):
        value=json.loads(reservation.read_text(encoding="utf-8")); previous_epoch=max(previous_epoch or 0.0,float(value["request_start_epoch"] or 0.0))
    successes=0
    try:
      for request in plan["requests"]:
        request_dir=work/request["request_id"]
        if not request_dir.exists(): request_dir.mkdir(exist_ok=False); _fsync_dir(work)
        if (request_dir/"terminal.json").exists():
            terminal=json.loads((request_dir/"terminal.json").read_text(encoding="utf-8")); successes += int(terminal.get("state") == "SUCCESS"); continue
        completed=prior_attempt_count + len(list(request_dir.glob("reservation-*.json")))
        for attempt in range(completed+1, contract["transport_policy"]["maximum_lifetime_attempts_per_exact_request"]+1):
            now=wall_time()
            if previous_epoch is not None:
                wait=max(0.0, contract["transport_policy"]["minimum_request_start_interval_seconds"]-(now-previous_epoch))
                if wait:
                    before=wall_time(); sleep(wait); after=wall_time()
                    if after-before+1e-6 < wait: raise CaptureError("pacing sleeper returned before the locked interval")
            started=_utc(); request_epoch=wall_time(); previous_epoch=request_epoch
            _write_new(request_dir/f"reservation-{attempt:02d}.json", {"request":request,"attempt":attempt,"request_started_at_utc":started,"request_start_epoch":request_epoch})
            try:
                response=transport(request["full_url"], contract["transport_policy"]["timeout_seconds"], contract["transport_policy"]["maximum_response_bytes"])
                status,headers,body=response.status,dict(response.headers),response.body
            except TransportFailure as exc:
                record={"attempt":attempt,"request_started_at_utc":exc.requested_at_utc,"observed_at_utc":exc.observed_at_utc,"outcome":"RETRYABLE_FAILURE" if exc.retryable else "NONRETRYABLE_FAILURE","http_status":exc.status,"error_kind":exc.error_kind,"retry_after_header":exc.retry_after,"backoff_seconds":0.0}
                if not exc.retryable: _write_new(request_dir/f"result-{attempt:02d}.json",record); break
                retry=_retry_after(exc.retry_after,exc.observed_at_utc,float(contract["transport_policy"]["maximum_retry_after_seconds"]))
                delay=retry if retry is not None else float(contract["transport_policy"]["bounded_backoff_seconds"][min(attempt-1,2)])
                record["backoff_seconds"]=delay; _write_new(request_dir/f"result-{attempt:02d}.json",record)
                if attempt < contract["transport_policy"]["maximum_lifetime_attempts_per_exact_request"]:
                    before=wall_time(); sleep(delay); after=wall_time()
                    if after-before+1e-6 < delay: raise CaptureError("retry sleeper returned before required backoff")
                    continue
                break
            except Exception as exc:
                _write_new(request_dir/f"result-{attempt:02d}.json", {"attempt":attempt,"request_started_at_utc":started,"observed_at_utc":_utc(),"outcome":"NONRETRYABLE_FAILURE","http_status":None,"error_kind":type(exc).__name__,"retry_after_header":None,"backoff_seconds":0.0}); break
            safe={k:v for k,v in headers.items() if k in contract["transport_policy"]["safe_response_headers"]}
            result={"attempt":attempt,"request_started_at_utc":response.requested_at_utc,"observed_at_utc":response.observed_at_utc,"outcome":"SUCCESS" if status==200 else "RETRYABLE_FAILURE" if status in contract["transport_policy"]["retryable_http_statuses"] else "NONRETRYABLE_FAILURE","http_status":status,"final_url":response.final_url,"response_headers":safe,"byte_count":len(body),"sha256":sha256_bytes(body),"retry_after_header":safe.get("retry-after"),"backoff_seconds":0.0}
            _write_new(request_dir/f"response-attempt-{attempt:02d}.{'csv' if status==200 else 'bin'}",body)
            if safe.get("content-length") not in (None,"",str(len(body))):
                result["outcome"]="NONRETRYABLE_FAILURE"; result["error_kind"]="content_length_mismatch"; _write_new(request_dir/f"result-{attempt:02d}.json",result); break
            if status==200:
                try:
                    _validate_response(response,{"full_url":request["full_url"],"source_kind":"statcast"},{"sources":{"statcast":{"content_type_prefixes":["text/csv","application/csv"]}}})
                except Exception as exc:
                    result["outcome"]="NONRETRYABLE_FAILURE"; result["error_kind"]=type(exc).__name__; _write_new(request_dir/f"result-{attempt:02d}.json",result); _write_new(request_dir/f"validation-failure-{attempt:02d}.json",{"error_type":type(exc).__name__,"message":str(exc)}); break
                certified={int(item["game_pk"]): {"official_date":request["expected"]["official_date"], "home_team_id":item["home_team_id"], "away_team_id":item["away_team_id"], "home_team_code":item["home_team_code"], "away_team_code":item["away_team_code"]} for item in request["expected"]["certified_games"]}
                try:
                    parse_csv_bytes(body,contract=contract,expected_date=request["expected"]["official_date"],certified_games=certified)
                except Exception as exc:
                    result["outcome"]="NONRETRYABLE_FAILURE"; result["error_kind"]=type(exc).__name__; _write_new(request_dir/f"result-{attempt:02d}.json",result); _write_new(request_dir/f"validation-failure-{attempt:02d}.json",{"error_type":type(exc).__name__,"message":str(exc)})
                    break
                _write_new(request_dir/f"result-{attempt:02d}.json",result)
                _write_new(request_dir/"response.csv",body)
                receipt={"schema_version":"shared-pa-statcast-raw-receipt-v1","request":request,"request_plan_sha256":plan_sha,"source_contract_sha256":contract_sha,"parser_sha256":parser_sha,"attempt_number":attempt,"request_started_at_utc":response.requested_at_utc,"observed_at_utc":result["observed_at_utc"],"http_status":status,"response_headers":safe,"byte_count":len(body),"sha256":sha256_bytes(body),"terminal_state":"SUCCESS"}
                _write_new(request_dir/"receipt.json",receipt); _write_new(request_dir/"terminal.json",{"state":"SUCCESS","attempt":attempt,"sha256":sha256_bytes(body)}); successes+=1; break
            if status not in contract["transport_policy"]["retryable_http_statuses"]: _write_new(request_dir/f"result-{attempt:02d}.json",result); break
            retry=_retry_after(safe.get("retry-after"),response.observed_at_utc,float(contract["transport_policy"]["maximum_retry_after_seconds"]))
            delay=retry if retry is not None else float(contract["transport_policy"]["bounded_backoff_seconds"][min(attempt-1,2)])
            result["backoff_seconds"]=delay; _write_new(request_dir/f"result-{attempt:02d}.json",result)
            if attempt < contract["transport_policy"]["maximum_lifetime_attempts_per_exact_request"]:
                before=wall_time(); sleep(delay); after=wall_time()
                if after-before+1e-6 < delay: raise CaptureError("retry sleeper returned before required backoff")
        if not (request_dir/"terminal.json").exists(): _write_new(request_dir/"terminal.json",{"state":"FAILURE","attempts":attempt})
      manifest={"schema_version":"shared-pa-statcast-raw-capture-manifest-v1","request_plan_sha256":plan_sha,"source_contract_sha256":contract_sha,"parser_sha256":parser_sha,"authorization_sha256":source_access.authorization_file_sha256,"runtime_attestation_sha256":runtime_authorization.attestation_sha256,"runtime_policy_sha256":runtime_authorization.policy_sha256,"source_bundle_sha256":source_access.source_bundle_sha256,"carrier_commit":carrier_commit,"attempt_history_sha256":source_access.attempt_history_sha256,"prior_attempt_count":prior_attempt_count,"request_count":plan["request_count"],"success_count":successes,"failure_count":plan["request_count"]-successes}
      manifest=_write_manifest(work,manifest); _fsync_dir(work); os.replace(work,root); _fsync_dir(root.parent); return manifest
    finally:
      os.close(lock_fd)
      try: lock.unlink()
      except FileNotFoundError: pass

def _parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(); p.add_argument("--request-plan",type=Path,required=True); p.add_argument("--contract",type=Path,required=True); p.add_argument("--authorization",type=Path,required=True); p.add_argument("--expected-authorization-sha256",required=True); p.add_argument("--runtime-policy",type=Path,required=True); p.add_argument("--runtime-attestation",type=Path,required=True); p.add_argument("--expected-runtime-attestation-sha256",required=True); p.add_argument("--expected-source-bundle-sha256",required=True); p.add_argument("--carrier-commit",required=True); p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--attempt-history",type=Path,required=True); return p


def main(argv: list[str] | None = None, *, transport: TransportCallable) -> int:
    a=_parser().parse_args(argv)
    runtime=authorize_runtime(attestation_path=a.runtime_attestation,expected_attestation_sha256=a.expected_runtime_attestation_sha256,policy_path=a.runtime_policy)
    access=verify_statcast_historical_source_access(authorization_path=a.authorization,expected_authorization_sha256=a.expected_authorization_sha256,expected_carrier_commit=a.carrier_commit,expected_runtime_policy_sha256=runtime.policy_sha256,expected_source_bundle_sha256=a.expected_source_bundle_sha256,expected_source_contract_sha256=sha256_file(a.contract),expected_request_plan_sha256=sha256_file(a.request_plan),access_time_utc=_utc())
    _validate_authorities(plan_path=a.request_plan, contract_path=a.contract, runtime=runtime, access=access, carrier_commit=a.carrier_commit)
    print(json.dumps(capture(a.request_plan,a.contract,a.output_dir,runtime_authorization=runtime,source_access=access,carrier_commit=a.carrier_commit,attempt_history_path=a.attempt_history,transport=transport),sort_keys=True)); return 0


def authorized_main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtime=authorize_runtime(attestation_path=args.runtime_attestation,expected_attestation_sha256=args.expected_runtime_attestation_sha256,policy_path=args.runtime_policy)
    access=verify_statcast_historical_source_access(authorization_path=args.authorization,expected_authorization_sha256=args.expected_authorization_sha256,expected_carrier_commit=args.carrier_commit,expected_runtime_policy_sha256=runtime.policy_sha256,expected_source_bundle_sha256=args.expected_source_bundle_sha256,expected_source_contract_sha256=sha256_file(args.contract),expected_request_plan_sha256=sha256_file(args.request_plan),access_time_utc=_utc())
    _validate_authorities(plan_path=args.request_plan, contract_path=args.contract, runtime=runtime, access=access, carrier_commit=args.carrier_commit)
    gate = _execution_authorization(runtime, access, args.carrier_commit)
    return main(argv, transport=lambda full_url, timeout, maximum: _transport(full_url, timeout, maximum, execution_authorization=gate))
if __name__ == "__main__": raise SystemExit(authorized_main())
