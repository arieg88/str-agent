"""FastAPI application factory.

    uv run stragent serve            # http://127.0.0.1:8000  (map page at /, docs at /docs)

create_app() takes everything it needs as arguments so tests can pass a temporary database and
an offline geocoder; with no arguments it reads config/settings.toml and STRAGENT_DB_PATH.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from stragent import __version__
from stragent.analysis import NoDataError, OutOfAreaError
from stragent.analytics.geocode import Geocoder, GeocodingError, NominatimGeocoder
from stragent.api.deps import GeocoderFactory
from stragent.api.routes import router
from stragent.config import Settings, UnknownCityError, get_settings

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("stragent.api")


def _nominatim_factory(settings: Settings) -> GeocoderFactory:
    cache: dict[str, Geocoder] = {}

    def geocoder_for(city_key: str) -> Geocoder:
        if city_key not in cache:
            cache[city_key] = NominatimGeocoder(
                settings.city(city_key),
                settings.geocoding,
                settings.resolve(settings.paths.cache_dir) / f"geocode-{city_key}.json",
            )
        return cache[city_key]

    return geocoder_for


def _error(status: int, code: str, detail: str, request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": code,
            "detail": detail,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


def create_app(
    settings: Settings | None = None,
    db_path: Path | None = None,
    geocoder_for: GeocoderFactory | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    env_db = os.environ.get("STRAGENT_DB_PATH")
    db_path = db_path or (Path(env_db) if env_db else settings.resolve(settings.paths.database))

    app = FastAPI(
        title="stragent",
        version=__version__,
        description=(
            "Short-term-rental analysis for Thessaloniki on Inside Airbnb data "
            "(insideairbnb.com, CC BY 4.0). Estimates, not bookings."
        ),
    )
    app.state.settings = settings
    app.state.db_path = db_path
    app.state.geocoder_for = geocoder_for or _nominatim_factory(settings)

    @app.middleware("http")
    async def request_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["x-request-id"] = request_id
        log.info(
            "%s %s -> %s in %.0f ms [%s]",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response

    # One error shape everywhere: {"error": code, "detail": message, "request_id": id}
    @app.exception_handler(NoDataError)
    async def _no_data(request: Request, exc: NoDataError) -> JSONResponse:
        return _error(404, "no_data", str(exc), request)

    @app.exception_handler(OutOfAreaError)
    async def _out_of_area(request: Request, exc: OutOfAreaError) -> JSONResponse:
        return _error(422, "out_of_area", str(exc), request)

    @app.exception_handler(GeocodingError)
    async def _geocoding(request: Request, exc: GeocodingError) -> JSONResponse:
        return _error(422, "geocoding_failed", str(exc), request)

    @app.exception_handler(UnknownCityError)
    async def _unknown_city(request: Request, exc: UnknownCityError) -> JSONResponse:
        return _error(404, "unknown_city", str(exc), request)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        parts = []
        for err in exc.errors():
            where = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query"))
            parts.append(f"{where}: {err.get('msg')}" if where else str(err.get("msg")))
        return _error(422, "invalid_request", "; ".join(parts), request)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 422: "invalid_request"}.get(
            exc.status_code, "http_error"
        )
        return _error(exc.status_code, code, str(exc.detail), request)

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
