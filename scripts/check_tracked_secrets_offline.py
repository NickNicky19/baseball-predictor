#!/usr/bin/env python3
"""Offline mutations for the tracked-source credential scanner."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_tracked_secrets import scan  # noqa: E402


def main() -> int:
    passed = 0
    with tempfile.TemporaryDirectory(prefix="secret_scan_", dir=ROOT / "data" / "analysis") as tmp:
        root = Path(tmp)
        clean = root / "clean.txt"
        clean.write_text("ODDS_API_KEY comes from the environment\n", encoding="utf-8")
        if not scan([clean]):
            passed += 1
            print("  [OK] an environment-variable reference is not treated as a credential")

        leaked = root / "leaked.txt"
        leaked.write_text(
            "ODDS_API_KEY=" + "abcdefghijklmnopqrstuvwxyz" + "123456\n",
            encoding="utf-8",
        )
        finding = scan([leaked])
        if finding and finding[0][2] == "odds_api_environment_assignment":
            passed += 1
            print("  [OK] MUTATION a literal odds credential is detected")

        key = root / "private.txt"
        key.write_text(
            "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n",
            encoding="utf-8",
        )
        finding = scan([key])
        if finding and finding[0][2] == "private_key_block":
            passed += 1
            print("  [OK] MUTATION a private-key block is detected")

    print(f"\n{passed}/3 checks passed")
    return 0 if passed == 3 else 1


if __name__ == "__main__":
    raise SystemExit(main())
