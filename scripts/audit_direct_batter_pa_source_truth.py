#!/usr/bin/env python3
"""Reconcile raw terminal PA events to official target counts for every identity."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path: os.sys.path.insert(0,str(ROOT))
from src.features.direct_batter_pa_history import EVENT_TO_OUTCOME, NON_PA_EVENTS, OUTCOMES  # noqa: E402

KNOWN_GAME_TYPES={"S","R","F","D","L","W","C","N","P","A","I","E"}


def sha256_file(path: Path)->str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1<<20),b""): digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path:Path,value:dict[str,Any])->None:
    if path.exists(): raise FileExistsError(f"refusing to overwrite source-truth audit: {path}")
    path.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent,text=True)
    try:
        with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as handle:
            json.dump(value,handle,sort_keys=True,indent=2,allow_nan=False); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp,path)
    except Exception:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
        raise


def reconcile(official:pd.DataFrame, raw_counts:pd.DataFrame)->tuple[pd.DataFrame,pd.DataFrame]:
    required={"game_pk","player_id",*[f"target_{name}" for name in OUTCOMES]}
    if missing:=sorted(required.difference(official.columns)): raise ValueError(f"official reconciliation columns missing: {missing}")
    if official.duplicated(["game_pk","player_id"]).any() or raw_counts.duplicated(["game_pk","player_id"]).any(): raise ValueError("source-truth reconciliation identity is duplicated")
    merged=official[["game_pk","player_id",*[f"target_{name}" for name in OUTCOMES]]].merge(raw_counts,on=["game_pk","player_id"],how="left",validate="one_to_one")
    for name in OUTCOMES:
        merged[f"raw_{name}"]=pd.to_numeric(merged.get(f"raw_{name}"),errors="coerce").fillna(0).astype(int)
        merged[f"target_{name}"]=pd.to_numeric(merged[f"target_{name}"],errors="raise").astype(int)
    mismatch=pd.Series(False,index=merged.index)
    for name in OUTCOMES: mismatch |= merged[f"raw_{name}"].ne(merged[f"target_{name}"])
    return merged,merged.loc[mismatch].copy()


def audit(*,panel:Path,manifest:Path,raw_root:Path,output:Path)->dict[str,Any]:
    source=json.loads(manifest.read_text(encoding="utf-8"))
    if source.get("output",{}).get("sha256")!=sha256_file(panel): raise ValueError("source-truth panel hash mismatch")
    frame=pd.read_csv(panel,low_memory=False)
    parts=[]; terminal_rows=0
    for relative,expected_hash in sorted(source["raw_source_sha256"].items()):
        path=raw_root/relative
        if sha256_file(path)!=expected_hash: raise ValueError(f"source-truth raw hash mismatch: {relative}")
        raw=pd.read_csv(path,usecols=lambda name:name in {"game_pk","batter","game_type","events","at_bat_number","pitch_number"},low_memory=False)
        missing=sorted({"game_pk","batter","game_type","events","at_bat_number","pitch_number"}.difference(raw.columns))
        if missing: raise ValueError(f"source-truth raw columns missing: {relative}: {missing}")
        types=raw["game_type"].astype(str); unknown=sorted(set(types).difference(KNOWN_GAME_TYPES))
        if unknown: raise ValueError(f"unknown game types in {relative}: {unknown}")
        regular=raw.loc[types.eq("R") & raw["events"].notna()].copy()
        event=regular["events"].astype(str); unknown_events=sorted(set(event).difference(EVENT_TO_OUTCOME).difference(NON_PA_EVENTS))
        if unknown_events: raise ValueError(f"unmapped terminal events in {relative}: {unknown_events}")
        terminal=regular.loc[~event.isin(NON_PA_EVENTS)].copy()
        if terminal.duplicated(["game_pk","batter","at_bat_number"]).any(): raise ValueError(f"duplicate terminal PA identity in {relative}")
        terminal["outcome"]=terminal["events"].astype(str).map(EVENT_TO_OUTCOME)
        terminal_rows+=len(terminal)
        if not terminal.empty:
            grouped=terminal.groupby(["game_pk","batter","outcome"],sort=False).size().unstack(fill_value=0)
            for name in OUTCOMES:
                if name not in grouped: grouped[name]=0
            grouped=grouped.loc[:,OUTCOMES].reset_index().rename(columns={"batter":"player_id",**{name:f"raw_{name}" for name in OUTCOMES}})
            parts.append(grouped)
    raw_counts=pd.concat(parts,ignore_index=True).groupby(["game_pk","player_id"],as_index=False,sort=False).sum(numeric_only=True)
    merged,mismatch=reconcile(frame,raw_counts)
    result={
        "schema_version":"direct-batter-pa-source-truth-audit-v1",
        "status":"SOURCE_TRUTH_DEFECT_FOUND" if len(mismatch) else "SOURCE_TRUTH_EXACT_MATCH",
        "inputs":{"panel_sha256":sha256_file(panel),"manifest_sha256":sha256_file(manifest),"raw_files_rehashed":len(source["raw_source_sha256"])},
        "population":{"official_rows":len(frame),"raw_regular_terminal_pa":terminal_rows,"mismatch_rows":len(mismatch)},
        "mismatch_sample":mismatch.head(25).to_dict(orient="records"),
        "checks":{"terminal_pa_identity_unique":True,"truncated_pa_excluded":True,"unknown_events":0,"unknown_game_types":0},
        "production_changed":False,"confirmation_2025_opened":False,"may_2026_opened":False,"betting_authorized":False,
        "script_sha256":sha256_file(Path(__file__)),
    }
    atomic_json(output,result); return result


def main()->int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--panel",required=True,type=Path); p.add_argument("--manifest",required=True,type=Path); p.add_argument("--raw-root",required=True,type=Path); p.add_argument("--output",required=True,type=Path)
    r=audit(**vars(p.parse_args())); print(json.dumps({"status":r["status"],"population":r["population"]},sort_keys=True)); return 2 if r["status"]=="SOURCE_TRUTH_DEFECT_FOUND" else 0


if __name__=="__main__": raise SystemExit(main())
