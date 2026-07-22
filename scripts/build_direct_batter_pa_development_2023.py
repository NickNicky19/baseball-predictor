#!/usr/bin/env python3
"""Extract and enrich the certified 2023-only PA development panel."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from src.features.direct_batter_pa_history import batted_ball_composition  # noqa: E402


CERTIFIED_2023_ROWS = 43_740
KEYS = ["game_pk", "player_id"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite 2023 development evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def add_composition(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "history_batted_ball_denominator", "history_barrel_count",
        "history_hard_hit_count", "history_pa",
    }
    if missing := sorted(required.difference(frame.columns)):
        raise ValueError(f"composition source columns missing: {missing}")
    additions = []
    for row in frame.loc[:, sorted(required)].itertuples(index=False):
        values = row._asdict()
        converted: dict[str, int | None] = {}
        for name, value in values.items():
            converted[name] = None if pd.isna(value) else int(value)
        additions.append(batted_ball_composition(
            denominator=converted["history_batted_ball_denominator"],
            barrel_count=converted["history_barrel_count"],
            hard_hit_count=converted["history_hard_hit_count"],
            history_pa=int(converted["history_pa"] or 0),
        ))
    result = frame.copy()
    addition_frame = pd.DataFrame(additions, index=result.index)
    overlap = sorted(set(result.columns).intersection(addition_frame.columns))
    if overlap:
        raise ValueError(f"composition output already exists: {overlap}")
    return pd.concat([result, addition_frame], axis=1)


def build(*, panel: Path, certificate: Path, protocol: Path, output: Path, manifest: Path) -> dict[str, Any]:
    if manifest.exists():
        raise FileExistsError(f"refusing to overwrite 2023 development manifest: {manifest}")
    contract = json.loads(protocol.read_text(encoding="utf-8"))
    if contract.get("status") != "LOCKED_BEFORE_2023_EXTRACTION_OR_FOLD_SCORING":
        raise ValueError("2023 development protocol is not locked")
    cert = json.loads(certificate.read_text(encoding="utf-8"))
    if cert.get("status") != "DIRECT_BATTER_PA_PANEL_CERTIFIED_RESEARCH_ONLY":
        raise ValueError("source panel is not certified")
    if cert.get("panel", {}).get("sha256") != sha256_file(panel):
        raise ValueError("certified source panel hash mismatch")
    if cert.get("validation", {}).get("fit_rows_2023") != CERTIFIED_2023_ROWS:
        raise ValueError("certified 2023 row count drifted")

    # nrows is the evidence boundary: no 2024 record is parsed or consumed.
    frame = pd.read_csv(panel, nrows=CERTIFIED_2023_ROWS, low_memory=False)
    if len(frame) != CERTIFIED_2023_ROWS:
        raise ValueError("2023 prefix row count is incomplete")
    years = pd.to_numeric(frame["season"], errors="raise").astype(int)
    if not years.eq(2023).all():
        raise ValueError("certified prefix contains a non-2023 row")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    source_dates = pd.to_datetime(frame["max_source_date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or not dates.dt.year.eq(2023).all():
        raise ValueError("2023 target dates are invalid")
    if (source_dates.notna() & source_dates.ge(dates)).any():
        raise ValueError("2023 development chronology violation")
    if frame.duplicated(KEYS).any():
        raise ValueError("2023 development identity duplicated")
    enriched = add_composition(frame)

    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0, filename="") as zipped:
        zipped.write(enriched.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    atomic_bytes(output, buffer.getvalue())
    payload: dict[str, Any] = {
        "schema_version": "direct-batter-pa-2023-development-manifest-v1",
        "status": "CERTIFIED_2023_ONLY_DEVELOPMENT_RESEARCH_ONLY",
        "protocol": {"path": str(protocol), "sha256": sha256_file(protocol)},
        "source_panel": {"path": str(panel), "sha256": sha256_file(panel), "rows_consumed": CERTIFIED_2023_ROWS},
        "source_certificate": {"path": str(certificate), "sha256": sha256_file(certificate)},
        "output": {"path": str(output), "sha256": sha256_file(output), "rows": len(enriched)},
        "validation": {
            "years": [2023], "identity_duplicates": 0, "chronology_violations": 0,
            "composition_columns": 6, "2024_rows_parsed": 0,
            "2025_confirmation_opened": False, "may_2026_opened": False,
        },
        "script_sha256": sha256_file(Path(__file__)),
        "production_changed": False,
        "betting_authorized": False,
    }
    atomic_bytes(manifest, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--certificate", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    result = build(**vars(args))
    print(json.dumps({"status": result["status"], "output": result["output"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
