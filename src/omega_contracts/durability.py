"""Cross-platform durability for new immutable candidate artifacts only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import BinaryIO

EVIDENCE_FILE_MODE = 0o640
HARDENING_APPLIED = "applied_mode_0o640"
HARDENING_UNSUPPORTED = "unsupported_platform"


@dataclass(frozen=True)
class DurabilityReport:
    flushed: bool
    fsynced: bool
    permission_hardening: str


def flush_sync_and_harden(
    handle: BinaryIO, *, mode: int = EVIDENCE_FILE_MODE
) -> DurabilityReport:
    if mode != EVIDENCE_FILE_MODE:
        raise ValueError("evidence file mode is fixed at 0o640 by contract")
    handle.flush()
    if hasattr(os, "fchmod"):
        os.fchmod(handle.fileno(), mode)  # type: ignore[attr-defined]
        hardening = HARDENING_APPLIED
    else:
        hardening = HARDENING_UNSUPPORTED
    os.fsync(handle.fileno())
    return DurabilityReport(True, True, hardening)
