#!/usr/bin/env python3
"""Offline mutations for exact-schema enriched assembly."""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_assemble_enriched import assemble  # noqa: E402


def fails(fn) -> bool:
    try:
        fn()
    except SystemExit:
        return True
    return False


def write_fixture(root: Path, schema: str = "a3.2") -> None:
    season = root / "2025"
    season.mkdir(parents=True)
    hitter = pd.DataFrame(
        [{
            "builder_schema": schema, "roller_schema": "a4.1",
            "game_date": "2025-04-01", "game_pk": 1, "player_id": 10,
            "roll15_xwoba": 0.3,
        }]
    )
    pitcher = pd.DataFrame(
        [{
            "builder_schema": schema, "game_date": "2025-04-01",
            "game_pk": 1, "player_id": 20,
        }]
    )
    hitter.drop(columns=["roller_schema", "roll15_xwoba"]).to_csv(
        season / "hitters_2025-04-01.csv", index=False
    )
    hitter.to_csv(season / "hitters_2025-04-01_statcast.csv", index=False)
    pitcher.to_csv(season / "pitchers_2025-04-01.csv", index=False)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="assemble_schema_"))
    try:
        valid = work / "valid"
        write_fixture(valid)
        outputs = assemble([2025], valid, builder_schema="a3.2")
        assert all(path.is_file() for path in outputs.values())
        print("[OK] exact a3.2 hitter/pitcher assembly passes")

        old = work / "old"
        write_fixture(old, schema="a3.1")
        assert fails(lambda: assemble([2025], old, builder_schema="a3.2"))
        print("[OK] MUTATION a3.1 rows cannot enter an a3.2 assembly")

        mixed = work / "mixed"
        write_fixture(mixed)
        path = mixed / "2025" / "hitters_2025-04-01_statcast.csv"
        frame = pd.read_csv(path)
        frame.loc[0, "builder_schema"] = "a3.1"
        frame.to_csv(path, index=False)
        assert fails(lambda: assemble([2025], mixed, builder_schema="a3.2"))
        print("[OK] MUTATION mixed hitter schema hard-fails")

        missing = work / "missing"
        write_fixture(missing)
        (missing / "2025" / "hitters_2025-04-01_statcast.csv").unlink()
        assert fails(lambda: assemble([2025], missing, builder_schema="a3.2"))
        print("[OK] MUTATION missing enriched twin hard-fails")

        duplicate = work / "duplicate"
        write_fixture(duplicate)
        path = duplicate / "2025" / "hitters_2025-04-01_statcast.csv"
        frame = pd.read_csv(path)
        pd.concat([frame, frame], ignore_index=True).to_csv(path, index=False)
        assert fails(lambda: assemble([2025], duplicate, builder_schema="a3.2"))
        print("[OK] MUTATION duplicate player-game identity hard-fails")

        assert fails(lambda: assemble([2025], valid, builder_schema="a9.9"))
        print("[OK] MUTATION unknown schema hard-fails")
        print("6/6")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
