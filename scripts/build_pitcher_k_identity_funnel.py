"""Publish the locked, outcome-blind pitcher-strikeout identity funnel."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.pitcher_k_identity_funnel import (  # noqa: E402
    build_funnel,
    load_protocol,
    sha256,
)


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        frame.to_csv(handle, index=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        type=Path,
        default=ROOT / "config/pitcher_k_identity_funnel_protocol.json",
    )
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    protocol = load_protocol(
        args.protocol, code_root=ROOT, evidence_root=args.evidence_root
    )
    terminal, fragments, summary = build_funnel(protocol, evidence_root=args.evidence_root)

    terminal_path = args.out_dir / "pitcher_k_identity_terminal.csv"
    fragments_path = args.out_dir / "pitcher_k_fragment_identity.csv"
    report_path = args.out_dir / "pitcher_k_identity_funnel_report.json"
    atomic_csv(terminal, terminal_path)
    atomic_csv(fragments, fragments_path)

    module_path = ROOT / "src/evaluation/pitcher_k_identity_funnel.py"
    report = {
        "schema_version": "pitcher-k-identity-funnel-report-v1",
        "status": "OUTCOME_BLIND_IDENTITY_FUNNEL_COMPLETE",
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "economic_evidence_eligible": False,
        "protocol": {
            "path": args.protocol.resolve().relative_to(ROOT.resolve()).as_posix(),
            "sha256": sha256(args.protocol),
            "status": protocol["status"],
        },
        "implementation": {
            "module_path": module_path.resolve().relative_to(ROOT.resolve()).as_posix(),
            "module_sha256": sha256(module_path),
            "builder_path": Path(__file__).resolve().relative_to(ROOT.resolve()).as_posix(),
            "builder_sha256": sha256(Path(__file__)),
        },
        "source_bindings": protocol["inputs"],
        "market_contract": protocol["market_contract"],
        "identity_contract": protocol["identity_contract"],
        "summary": summary,
        "artifacts": {
            "terminal": {
                "path": terminal_path.resolve().relative_to(args.evidence_root.resolve()).as_posix(),
                "sha256": sha256(terminal_path),
                "rows": int(len(terminal)),
            },
            "fragments": {
                "path": fragments_path.resolve().relative_to(args.evidence_root.resolve()).as_posix(),
                "sha256": sha256(fragments_path),
                "rows": int(len(fragments)),
            },
        },
        "protected_invariants": protocol["protected_invariants"],
        "interpretation": (
            "This report measures hard identity and duplicate coverage only. It reads no vendor "
            "result/won field, no official statistics, no model probability, and no economic result. "
            "Historical price executability remains unverified."
        ),
    }
    atomic_json(report, report_path)
    print(json.dumps({"report": str(report_path), "sha256": sha256(report_path), **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
