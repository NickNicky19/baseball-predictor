"""FastAPI application for the read-only prediction display boundary."""

from __future__ import annotations

import csv
import inspect
import io
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from .contracts import SUPPORTED_MARKETS, PredictionDisplayRow
from .snapshot_store import LoadedSnapshot, SnapshotStore, SnapshotUnavailable


DASHBOARD_VERSION = "omega-readonly-dashboard-v1"
PACKAGE_ROOT = Path(__file__).resolve().parent
MARKET_LABELS = {
    "hits": "Hits",
    "home_runs": "Home Runs",
    "total_bases": "Total Bases",
    "hrr": "HRR",
    "pitcher_strikeouts": "Pitcher Strikeouts",
}
HEALTH_ORDER = {"healthy": 0, "degraded": 1, "unknown": 2, "quarantined": 3}

AuthVerifier = Callable[[Request], bool | Awaitable[bool]]


@dataclass(frozen=True)
class DashboardSettings:
    snapshot_root: Path
    manifest_path: Path | None
    allowed_producers: frozenset[str]
    auth_secret: str | None
    source_commit: str

    @classmethod
    def from_environment(cls) -> "DashboardSettings":
        root = Path(os.environ.get("DASHBOARD_SNAPSHOT_ROOT", "/var/lib/baseball-dashboard/snapshots"))
        manifest_value = os.environ.get("DASHBOARD_MANIFEST_PATH")
        producers = frozenset(
            item.strip()
            for item in os.environ.get("DASHBOARD_ALLOWED_PRODUCERS", "").split(",")
            if item.strip()
        )
        return cls(
            snapshot_root=root,
            manifest_path=Path(manifest_value) if manifest_value else None,
            allowed_producers=producers,
            auth_secret=os.environ.get("DASHBOARD_AUTH_SECRET"),
            source_commit=os.environ.get("DASHBOARD_SOURCE_COMMIT", "unavailable"),
        )


@dataclass(frozen=True)
class RowFilters:
    game_date: str | None
    search: str | None
    team: str | None
    opponent: str | None
    market: str | None
    lineup_status: str | None
    data_health_tier: str | None
    max_source_age_seconds: int | None
    show_abstentions: bool
    sort_by: Literal["projection", "uncertainty", "data_quality", "player"]
    sort_direction: Literal["asc", "desc"]


def _uncertainty_width(row: PredictionDisplayRow) -> float | None:
    if row.uncertainty is None:
        return None
    return row.uncertainty.upper - row.uncertainty.lower


def filter_and_sort_rows(
    loaded: LoadedSnapshot, filters: RowFilters
) -> list[PredictionDisplayRow]:
    rows = list(loaded.snapshot.rows)
    if filters.game_date and filters.game_date != loaded.snapshot.official_slate_date:
        rows = []
    if filters.search:
        needle = filters.search.casefold()
        rows = [row for row in rows if needle in row.player_name.casefold()]
    if filters.team:
        rows = [row for row in rows if row.team == filters.team]
    if filters.opponent:
        rows = [row for row in rows if row.opponent == filters.opponent]
    if filters.market:
        rows = [row for row in rows if row.market == filters.market]
    if filters.lineup_status:
        rows = [row for row in rows if row.lineup_status == filters.lineup_status]
    if filters.data_health_tier:
        rows = [row for row in rows if row.data_health_tier == filters.data_health_tier]
    if filters.max_source_age_seconds is not None:
        rows = [
            row
            for row in rows
            if row.source_freshness_seconds is not None
            and row.source_freshness_seconds <= filters.max_source_age_seconds
        ]
    if not filters.show_abstentions:
        rows = [row for row in rows if row.abstention_reason is None]

    def key(row: PredictionDisplayRow):
        if filters.sort_by == "projection":
            return (row.point_prediction is None, row.point_prediction or 0.0, row.player_name.casefold())
        if filters.sort_by == "uncertainty":
            width = _uncertainty_width(row)
            return (width is None, width or 0.0, row.player_name.casefold())
        if filters.sort_by == "data_quality":
            return (HEALTH_ORDER[row.data_health_tier], row.player_name.casefold())
        return (row.player_name.casefold(),)

    rows.sort(key=key, reverse=filters.sort_direction == "desc")
    return rows


def _row_for_export(row: PredictionDisplayRow, *, snapshot_sha256: str) -> dict[str, object]:
    value = row.model_dump(mode="json")
    value["snapshot_sha256"] = snapshot_sha256
    return value


def _filters_from_query(
    *,
    game_date: str | None,
    search: str | None,
    team: str | None,
    opponent: str | None,
    market: str | None,
    lineup_status: str | None,
    data_health_tier: str | None,
    max_source_age_seconds: int | None,
    show_abstentions: bool,
    sort_by: str,
    sort_direction: str,
) -> RowFilters:
    if market is not None and market not in SUPPORTED_MARKETS:
        raise HTTPException(status_code=400, detail="unsupported market filter")
    if lineup_status is not None and lineup_status not in {
        "projected",
        "confirmed",
        "unknown",
        "not_applicable",
    }:
        raise HTTPException(status_code=400, detail="unsupported lineup-status filter")
    if data_health_tier is not None and data_health_tier not in HEALTH_ORDER:
        raise HTTPException(status_code=400, detail="unsupported data-health filter")
    if sort_by not in {"projection", "uncertainty", "data_quality", "player"}:
        raise HTTPException(status_code=400, detail="unsupported sort field")
    if sort_direction not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="unsupported sort direction")
    return RowFilters(
        game_date=game_date,
        search=search,
        team=team,
        opponent=opponent,
        market=market,
        lineup_status=lineup_status,
        data_health_tier=data_health_tier,
        max_source_age_seconds=max_source_age_seconds,
        show_abstentions=show_abstentions,
        sort_by=sort_by,  # type: ignore[arg-type]
        sort_direction=sort_direction,  # type: ignore[arg-type]
    )


def create_app(
    *,
    settings: DashboardSettings | None = None,
    auth_verifier: AuthVerifier | None = None,
) -> FastAPI:
    settings = settings or DashboardSettings.from_environment()
    store = SnapshotStore(
        root=settings.snapshot_root,
        manifest_path=settings.manifest_path,
        allowed_producers=settings.allowed_producers,
    )
    templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))
    app = FastAPI(
        title="Baseball Prediction Snapshot Viewer",
        version=DASHBOARD_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    async def require_auth(request: Request) -> None:
        if auth_verifier is not None:
            result = auth_verifier(request)
            if inspect.isawaitable(result):
                result = await result
            if result:
                return
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
        if not settings.auth_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="dashboard authentication is not configured",
            )
        authorization = request.headers.get("authorization", "")
        bearer = authorization[7:] if authorization.startswith("Bearer ") else ""
        session = request.cookies.get("dashboard_session", "")
        if not (
            secrets.compare_digest(bearer, settings.auth_secret)
            or secrets.compare_digest(session, settings.auth_secret)
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    def require_snapshot() -> LoadedSnapshot:
        try:
            return store.load()
        except SnapshotUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="prediction snapshot is unavailable or invalid",
            ) from exc

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/healthz")
    def health() -> dict[str, str]:
        try:
            store.load()
            snapshot_state = "ready"
        except SnapshotUnavailable:
            snapshot_state = "unavailable"
        return {
            "service": "healthy",
            "snapshot_state": snapshot_state,
            "dashboard_version": DASHBOARD_VERSION,
            "source_commit": settings.source_commit,
        }

    @app.get("/version", dependencies=[Depends(require_auth)])
    def version() -> dict[str, str]:
        return {
            "dashboard_version": DASHBOARD_VERSION,
            "source_commit": settings.source_commit,
        }

    @app.get("/static/{filename}", name="dashboard_static", dependencies=[Depends(require_auth)])
    def dashboard_static(filename: str) -> FileResponse:
        if filename != "style.css":
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(PACKAGE_ROOT / "static" / "style.css", media_type="text/css")

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
    def index(
        request: Request,
        game_date: str | None = None,
        search: str | None = None,
        team: str | None = None,
        opponent: str | None = None,
        market: str | None = None,
        lineup_status: str | None = None,
        data_health_tier: str | None = None,
        max_source_age_seconds: int | None = Query(default=None, ge=0),
        show_abstentions: bool = True,
        sort_by: str = "player",
        sort_direction: str = "asc",
    ) -> HTMLResponse:
        filters = _filters_from_query(
            game_date=game_date,
            search=search,
            team=team,
            opponent=opponent,
            market=market,
            lineup_status=lineup_status,
            data_health_tier=data_health_tier,
            max_source_age_seconds=max_source_age_seconds,
            show_abstentions=show_abstentions,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )
        try:
            loaded = store.load()
        except SnapshotUnavailable:
            return templates.TemplateResponse(
                request=request,
                name="index.html",
                context={
                    "available": False,
                    "dashboard_version": DASHBOARD_VERSION,
                    "source_commit": settings.source_commit,
                    "market_labels": MARKET_LABELS,
                    "filters": filters,
                },
                status_code=200,
            )
        rows = filter_and_sort_rows(loaded, filters)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "available": True,
                "loaded": loaded,
                # Plain JSON-compatible values keep templates display-only and
                # prevent Jinja from invoking model-specific behavior.
                "rows": [row.model_dump(mode="json") for row in rows],
                "teams": sorted({row.team for row in loaded.snapshot.rows}),
                "opponents": sorted({row.opponent for row in loaded.snapshot.rows}),
                "dashboard_version": DASHBOARD_VERSION,
                "source_commit": settings.source_commit,
                "market_labels": MARKET_LABELS,
                "filters": filters,
            },
        )

    @app.get("/api/predictions", dependencies=[Depends(require_auth)])
    def predictions_api(
        loaded: LoadedSnapshot = Depends(require_snapshot),
        game_date: str | None = None,
        search: str | None = None,
        team: str | None = None,
        opponent: str | None = None,
        market: str | None = None,
        lineup_status: str | None = None,
        data_health_tier: str | None = None,
        max_source_age_seconds: int | None = Query(default=None, ge=0),
        show_abstentions: bool = True,
        sort_by: str = "player",
        sort_direction: str = "asc",
    ) -> JSONResponse:
        filters = _filters_from_query(
            game_date=game_date,
            search=search,
            team=team,
            opponent=opponent,
            market=market,
            lineup_status=lineup_status,
            data_health_tier=data_health_tier,
            max_source_age_seconds=max_source_age_seconds,
            show_abstentions=show_abstentions,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )
        rows = filter_and_sort_rows(loaded, filters)
        return JSONResponse(
            {
                "schema_version": "prediction-display-api-v1",
                "official_slate_date": loaded.snapshot.official_slate_date,
                "snapshot_sha256": loaded.snapshot_sha256,
                "row_count": len(rows),
                "rows": [
                    _row_for_export(row, snapshot_sha256=loaded.snapshot_sha256)
                    for row in rows
                ],
            }
        )

    @app.get("/exports/predictions.csv", dependencies=[Depends(require_auth)])
    def predictions_csv(
        loaded: LoadedSnapshot = Depends(require_snapshot),
        market: str | None = None,
        show_abstentions: bool = True,
        sort_by: str = "player",
        sort_direction: str = "asc",
    ) -> Response:
        filters = _filters_from_query(
            game_date=None,
            search=None,
            team=None,
            opponent=None,
            market=market,
            lineup_status=None,
            data_health_tier=None,
            max_source_age_seconds=None,
            show_abstentions=show_abstentions,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )
        rows = [
            _row_for_export(row, snapshot_sha256=loaded.snapshot_sha256)
            for row in filter_and_sort_rows(loaded, filters)
        ]
        fields = list(rows[0]) if rows else ["snapshot_sha256"]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, sort_keys=True, separators=(",", ":"))
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )
        return Response(
            stream.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=predictions.csv"},
        )

    @app.get("/exports/predictions.json", dependencies=[Depends(require_auth)])
    def predictions_json(loaded: LoadedSnapshot = Depends(require_snapshot)) -> Response:
        return Response(
            loaded.snapshot_bytes,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=prediction-snapshot.json"},
        )

    @app.get("/exports/manifest.json", dependencies=[Depends(require_auth)])
    def manifest_json(loaded: LoadedSnapshot = Depends(require_snapshot)) -> Response:
        return Response(
            loaded.manifest_bytes,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=prediction-manifest.json"},
        )

    return app
