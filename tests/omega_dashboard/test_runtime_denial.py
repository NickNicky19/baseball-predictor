from __future__ import annotations

import builtins
import importlib
import os
import socket
import sqlite3
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from dashboard.app import PACKAGE_ROOT, DashboardSettings, create_app
from tests.omega_dashboard.conftest import NOW


def test_dashboard_requests_cross_no_forbidden_runtime_capability(
    tmp_path: Path, monkeypatch
) -> None:
    settings = DashboardSettings(
        snapshot_root=tmp_path,
        manifest_path=None,
        allowed_producers=frozenset({"authorized-runner-v1"}),
        trusted_manifest_sha256=None,
        maximum_snapshot_age_seconds=None,
        maximum_manifest_bytes=None,
        maximum_snapshot_bytes=None,
        immutability_anchor=None,
        auth_secret=None,
        source_commit="67efa1427987517d2c283cd502c1898b97c6bb2b",
    )
    client = TestClient(
        create_app(
            settings=settings,
            auth_verifier=lambda request: True,
            clock=lambda: NOW,
        )
    )
    forbidden_calls: list[str] = []
    original_open = builtins.open

    def deny(name: str):
        def denied(*args, **kwargs):
            forbidden_calls.append(name)
            raise AssertionError(f"forbidden dashboard runtime capability: {name}")

        return denied

    def read_only_open(file, mode="r", *args, **kwargs):
        if any(token in mode for token in "wax+"):
            return deny("filesystem-write")(file, mode, *args, **kwargs)
        resolved = Path(file).resolve(strict=False)
        allowed = (PACKAGE_ROOT.resolve(), tmp_path.resolve())
        if not any(resolved == root or root in resolved.parents for root in allowed):
            return deny("filesystem-read-outside-allowlist")(
                file, mode, *args, **kwargs
            )
        return original_open(file, mode, *args, **kwargs)

    # Starlette's in-process test transport uses an internal loopback socketpair
    # on Windows. Deny the public resolution/connection APIs instead; the
    # transitive AST gate separately rejects any dashboard socket import.
    monkeypatch.setattr(socket, "create_connection", deny("socket-connect"))
    monkeypatch.setattr(socket, "getaddrinfo", deny("dns"))
    monkeypatch.setattr(subprocess, "Popen", deny("subprocess"))
    monkeypatch.setattr(subprocess, "run", deny("subprocess-run"))
    monkeypatch.setattr(os, "system", deny("shell"))
    monkeypatch.setattr(os, "kill", deny("process-control"), raising=False)
    monkeypatch.setattr(sqlite3, "connect", deny("database-write"))
    monkeypatch.setattr(importlib, "import_module", deny("dynamic-import"))
    monkeypatch.setattr(builtins, "open", read_only_open)
    monkeypatch.setattr(Path, "write_text", deny("path-write-text"))
    monkeypatch.setattr(Path, "write_bytes", deny("path-write-bytes"))
    monkeypatch.setattr(Path, "touch", deny("path-touch"))
    monkeypatch.setattr(Path, "unlink", deny("path-unlink"))

    assert client.get("/healthz").status_code == 200
    response = client.get("/")
    assert response.status_code == 200
    assert "No legitimate prediction snapshot is available" in response.text
    assert forbidden_calls == []
