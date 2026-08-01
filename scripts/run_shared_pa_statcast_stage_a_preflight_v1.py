#!/usr/bin/env python3
"""Run the no-network Statcast Stage-A engineering preflight."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from src.data.shared_pa_statcast_source_v1 import canonical_json_bytes, load_contract, sha256_file
from src.evaluation.shared_pa_statcast_stage_a_v1 import run_stage_a

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--parser", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(os.path.abspath(os.fspath(args.output_dir)))
    if root.exists(): raise ValueError("output directory already exists")
    root.mkdir(parents=True, exist_ok=False)
    archive = run_stage_a(load_contract(args.contract), sha256_file(args.contract), sha256_file(args.parser))
    path = root / "stage_a_preflight.json"
    with path.open("xb") as handle: handle.write(canonical_json_bytes(archive))
    manifest = {"schema_version":"shared-pa-statcast-stage-a-manifest-v1","files":[{"path":path.name,"size":path.stat().st_size,"sha256":sha256_file(path)}],"external_request_count":0}
    with (root / "manifest.json").open("xb") as handle: handle.write(canonical_json_bytes(manifest))
    print(json.dumps(manifest, sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
