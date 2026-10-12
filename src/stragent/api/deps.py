"""FastAPI dependencies: settings, a database connection per request, the geocoder."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import duckdb
from fastapi import Request

from stragent.analysis import NoDataError
from stragent.analytics.geocode import Geocoder
from stragent.config import Settings
from stragent.db import connect


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db_path(request: Request) -> Path:
    path: Path = request.app.state.db_path
    return path


def get_db(request: Request) -> Iterator[duckdb.DuckDBPyConnection]:
    """A read-only connection opened for this request and closed after it.

    Opening per request (a few milliseconds) means the server never holds the file between
    requests, so `stragent ingest` can write to it while the API is running, and new
    snapshots show up without a restart.
    """
    path = get_db_path(request)
    if not path.exists():
        raise NoDataError(f"No database at {path}. Run `stragent ingest` first.")
    con = connect(path, read_only=True)
    try:
        yield con
    finally:
        con.close()


GeocoderFactory = Callable[[str], Geocoder]


def get_geocoder_factory(request: Request) -> GeocoderFactory:
    """Returns city_key -> Geocoder. One geocoder (and cache) per city, created on first use."""
    factory: GeocoderFactory = request.app.state.geocoder_for
    return factory
