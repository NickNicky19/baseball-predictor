from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dashboard.app import DashboardSettings, create_app
from tests.omega_dashboard.conftest import NOW, bind_row_hash, publish_fixture


def settings(
    root: Path,
    manifest: Path | None,
    digest: str | None,
    *,
    secret: str | None = None,
) -> DashboardSettings:
    return DashboardSettings(
        snapshot_root=root,
        manifest_path=manifest,
        allowed_producers=frozenset({"authorized-runner-v1"}),
        trusted_manifest_sha256=digest,
        maximum_snapshot_age_seconds=600,
        maximum_manifest_bytes=1024 * 1024,
        maximum_snapshot_bytes=10 * 1024 * 1024,
        immutability_anchor=root,
        auth_secret=secret,
        source_commit="67efa1427987517d2c283cd502c1898b97c6bb2b",
    )


def app_for(root: Path, manifest: Path | None, digest: str | None, **kwargs):
    return create_app(
        settings=settings(root, manifest, digest, secret=kwargs.pop("secret", None)),
        clock=lambda: NOW,
        **kwargs,
    )


def test_only_health_is_public_and_missing_auth_fails_closed(
    published_snapshot, synthetic_source_permission
) -> None:
    root, manifest, digest = published_snapshot
    client = TestClient(app_for(root, manifest, digest))
    assert client.get("/healthz").status_code == 200
    for path in (
        "/",
        "/version",
        "/static/style.css",
        "/api/predictions",
        "/exports/predictions.csv",
    ):
        assert client.get(path).status_code == 503


def test_bearer_and_session_authentication(
    published_snapshot, synthetic_source_permission
) -> None:
    root, manifest, digest = published_snapshot
    client = TestClient(app_for(root, manifest, digest, secret="test-secret"))
    assert client.get("/").status_code == 401
    assert client.get("/", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert (
        client.get("/", headers={"Authorization": "Bearer test-secret"}).status_code
        == 200
    )
    client.cookies.set("dashboard_session", "test-secret")
    assert client.get("/version").status_code == 200


def test_truthful_no_data_when_trust_configuration_is_absent(tmp_path: Path) -> None:
    app = create_app(
        settings=settings(tmp_path, None, None),
        auth_verifier=lambda request: request.headers.get("x-test-auth") == "yes",
        clock=lambda: NOW,
    )
    client = TestClient(app)
    assert client.get("/").status_code == 401
    response = client.get("/", headers={"x-test-auth": "yes"})
    assert response.status_code == 200
    assert "No legitimate prediction snapshot is available" in response.text
    assert "placeholder" not in response.text.casefold()
    assert client.get("/healthz").json()["snapshot_state"] == "unavailable"


def test_filters_and_market_tabs(
    published_snapshot, synthetic_source_permission
) -> None:
    root, manifest, digest = published_snapshot
    client = TestClient(
        app_for(root, manifest, digest, auth_verifier=lambda request: True)
    )
    response = client.get("/?market=hits&team=Alpha%20Club&show_abstentions=true")
    assert response.status_code == 200
    assert "Synthetic Batter" in response.text
    assert "Home Runs" in response.text
    assert "Pitcher Strikeouts" not in response.text
    assert "Betting authorization" in response.text
    assert ">No<" in response.text
    assert client.get("/api/predictions?market=rbi").status_code == 400
    assert client.get("/api/predictions?team=Unknown%20Club").json()["row_count"] == 0


def test_probability_side_line_and_receipt_bound_export(
    tmp_path: Path, snapshot_document, synthetic_source_permission
) -> None:
    row = snapshot_document["rows"][0]
    row["point_prediction_kind"] = "probability"
    row["point_prediction"] = 0.6
    row["product_id"] = "hits-over-half-research"
    row["market_side"] = "over"
    row["market_line"] = 0.5
    row["uncertainty"] = {"method": "display_bound", "lower": 0.5, "upper": 0.7}
    row["comparison"]["current_live_prediction"] = 0.6
    bind_row_hash(row)
    manifest, digest = publish_fixture(tmp_path, snapshot=snapshot_document)
    client = TestClient(
        app_for(tmp_path, manifest, digest, auth_verifier=lambda request: True)
    )
    response = client.get("/")
    assert response.status_code == 200
    assert "hits-over-half-research" in response.text
    assert "over 0.5" in response.text
    api = client.get("/api/predictions").json()
    assert api["row_count"] == 1
    assert api["rows"][0]["identity_receipt_sha256"] == "c" * 64
    assert api["rows"][0]["snapshot_sha256"] == api["snapshot_sha256"]


def test_exports_are_exact_and_security_headers_apply(
    published_snapshot, synthetic_source_permission
) -> None:
    root, manifest, digest = published_snapshot
    client = TestClient(
        app_for(root, manifest, digest, auth_verifier=lambda request: True)
    )
    assert (
        client.get("/exports/predictions.json").content
        == (root / "snapshot.json").read_bytes()
    )
    assert (
        client.get("/exports/manifest.json").content
        == (root / "manifest.json").read_bytes()
    )
    csv_response = client.get("/exports/predictions.csv")
    assert digest not in csv_response.text
    assert "snapshot_sha256" in csv_response.text
    for path in ("/healthz", "/", "/api/predictions"):
        response = client.get(path)
        assert response.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"


def test_one_invalid_row_rejects_the_entire_snapshot(
    tmp_path: Path, snapshot_document, synthetic_source_permission
) -> None:
    invalid = snapshot_document["rows"][0].copy()
    invalid["row_id"] = "second-row"
    invalid["mlb_player_id"] = 999
    bind_row_hash(invalid)
    snapshot_document["rows"].append(invalid)
    manifest, digest = publish_fixture(tmp_path, snapshot=snapshot_document)
    client = TestClient(
        app_for(tmp_path, manifest, digest, auth_verifier=lambda request: True)
    )
    response = client.get("/")
    assert response.status_code == 200
    assert "No legitimate prediction snapshot is available" in response.text
    assert "Synthetic Batter" not in response.text
    assert client.get("/api/predictions").status_code == 503
