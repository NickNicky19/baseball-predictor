from __future__ import annotations

import ast
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from dashboard.app import DashboardSettings, create_app
from tests.omega_dashboard.conftest import publish_fixture


def settings(root: Path, manifest: Path | None, *, secret: str | None = None) -> DashboardSettings:
    return DashboardSettings(
        snapshot_root=root,
        manifest_path=manifest,
        allowed_producers=frozenset({"authorized-runner-v1"}),
        auth_secret=secret,
        source_commit="67efa1427987517d2c283cd502c1898b97c6bb2b",
    )


def test_only_health_is_public_and_missing_auth_configuration_fails_closed(
    published_snapshot,
) -> None:
    root, manifest = published_snapshot
    client = TestClient(create_app(settings=settings(root, manifest)))
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 503
    assert client.get("/version").status_code == 503
    assert client.get("/static/style.css").status_code == 503
    assert client.get("/api/predictions").status_code == 503
    assert client.get("/exports/predictions.csv").status_code == 503


def test_bearer_and_session_authentication(published_snapshot) -> None:
    root, manifest = published_snapshot
    client = TestClient(create_app(settings=settings(root, manifest, secret="test-secret")))
    assert client.get("/").status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer test-secret"}).status_code == 200
    client.cookies.set("dashboard_session", "test-secret")
    assert client.get("/version").status_code == 200


def test_injected_verifier_and_truthful_no_data(tmp_path: Path) -> None:
    app = create_app(
        settings=settings(tmp_path, None),
        auth_verifier=lambda request: request.headers.get("x-test-auth") == "yes",
    )
    client = TestClient(app)
    assert client.get("/").status_code == 401
    response = client.get("/", headers={"x-test-auth": "yes"})
    assert response.status_code == 200
    assert "No legitimate prediction snapshot is available" in response.text
    assert "placeholder" not in response.text.casefold()
    assert client.get("/healthz").json()["snapshot_state"] == "unavailable"


def test_slate_filters_sorting_and_supported_market_tabs(published_snapshot) -> None:
    root, manifest = published_snapshot
    client = TestClient(
        create_app(settings=settings(root, manifest), auth_verifier=lambda request: True)
    )
    response = client.get("/?market=hits&team=AAA&show_abstentions=true")
    assert response.status_code == 200
    assert "Synthetic Batter" in response.text
    assert "Home Runs" in response.text
    assert "Pitcher Strikeouts" not in response.text
    assert "Betting authorization" in response.text
    assert ">No<" in response.text

    assert client.get("/api/predictions?market=rbi").status_code == 400
    empty = client.get("/api/predictions?team=ZZZ").json()
    assert empty["row_count"] == 0


def test_probability_product_side_and_line_are_visible(
    tmp_path: Path, snapshot_document
) -> None:
    row = snapshot_document["rows"][0]
    row["point_prediction_kind"] = "probability"
    row["point_prediction"] = 0.6
    row["product_id"] = "hits-over-half-research"
    row["market_side"] = "over"
    row["market_line"] = 0.5
    row["uncertainty"] = {"method": "display-bound", "lower": 0.5, "upper": 0.7}
    manifest = publish_fixture(tmp_path, snapshot=snapshot_document)
    client = TestClient(
        create_app(settings=settings(tmp_path, manifest), auth_verifier=lambda request: True)
    )
    response = client.get("/")
    assert response.status_code == 200
    assert "hits-over-half-research" in response.text
    assert "over 0.5" in response.text


def test_exports_are_hash_bound_and_exact(published_snapshot) -> None:
    root, manifest = published_snapshot
    client = TestClient(
        create_app(settings=settings(root, manifest), auth_verifier=lambda request: True)
    )
    api = client.get("/api/predictions").json()
    assert api["row_count"] == 1
    assert api["rows"][0]["snapshot_sha256"] == api["snapshot_sha256"]

    csv_response = client.get("/exports/predictions.csv")
    assert csv_response.status_code == 200
    assert "snapshot_sha256" in csv_response.text
    assert api["snapshot_sha256"] in csv_response.text

    snapshot_response = client.get("/exports/predictions.json")
    assert snapshot_response.content == (root / "snapshot.json").read_bytes()
    manifest_response = client.get("/exports/manifest.json")
    assert manifest_response.content == manifest.read_bytes()


def test_security_headers_cover_health_and_authenticated_routes(published_snapshot) -> None:
    root, manifest = published_snapshot
    client = TestClient(
        create_app(settings=settings(root, manifest), auth_verifier=lambda request: True)
    )
    for path in ("/healthz", "/", "/api/predictions"):
        response = client.get(path)
        assert response.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"


def test_dashboard_source_has_no_prediction_or_collector_imports() -> None:
    dashboard_root = Path(__file__).resolve().parents[2] / "dashboard"
    prohibited_roots = {"src", "run_slate", "run_daily", "gui"}
    for path in dashboard_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0] for alias in node.names}
                assert not roots.intersection(prohibited_roots), (path, roots)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".", 1)[0] not in prohibited_roots, (
                    path,
                    node.module,
                )
