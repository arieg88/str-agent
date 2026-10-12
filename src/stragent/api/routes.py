"""HTTP endpoints. Thin wrappers: all logic lives in stragent.analysis and stragent.analytics.

Endpoints are plain `def` (not `async def`): DuckDB and the geocoder are blocking, and FastAPI
runs sync endpoints in a thread pool so they don't stall the event loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from stragent.analysis import analyze, resolve_snapshot, seasonality
from stragent.analytics import neighbourhoods as hoods
from stragent.api.deps import (
    GeocoderFactory,
    get_db,
    get_db_path,
    get_geocoder_factory,
    get_settings,
)
from stragent.api.schemas import (
    AnalyzeRequest,
    CompsRequest,
    ErrorResponse,
    Health,
    NeighbourhoodDetail,
    NeighbourhoodSummary,
    SeasonalityRequest,
    SnapshotInfo,
)
from stragent.config import Settings
from stragent.db import connect
from stragent.models import Analysis, Comp, CompFilters, Location, Seasonality

router = APIRouter(prefix="/api")

Db = Annotated[duckdb.DuckDBPyConnection, Depends(get_db)]
Cfg = Annotated[Settings, Depends(get_settings)]
Geo = Annotated[GeocoderFactory, Depends(get_geocoder_factory)]
ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


def _snapshots(con: duckdb.DuckDBPyConnection, city: str | None = None) -> list[SnapshotInfo]:
    sql = (
        "SELECT city, snapshot_date, listings_rows, calendar_rows, reviews_rows, loaded_at, source "
        "FROM snapshots {where} ORDER BY city, snapshot_date DESC"
    )
    rows = (
        con.execute(sql.format(where="WHERE city = ?"), [city]).fetchall()
        if city
        else con.execute(sql.format(where="")).fetchall()
    )
    return [
        SnapshotInfo(
            city=c,
            snapshot_date=d.isoformat(),
            listings=int(nl or 0),
            calendar_rows=int(nc or 0),
            reviews=int(nr or 0),
            loaded_at=t.isoformat(timespec="seconds"),
            source=src,
        )
        for c, d, nl, nc, nr, t, src in rows
    ]


@router.get("/health", response_model=Health, tags=["meta"])
def health(db_path: Annotated[Path, Depends(get_db_path)]) -> Health:
    """Is the service up, and what data does it have?"""
    if not db_path.exists():
        return Health(status="no_data", database=False, database_path=str(db_path), snapshots=[])
    con = connect(db_path, read_only=True)
    try:
        snaps = _snapshots(con)
    finally:
        con.close()
    return Health(
        status="ok" if snaps else "no_data",
        database=True,
        database_path=str(db_path),
        snapshots=snaps,
    )


@router.get("/snapshots", response_model=list[SnapshotInfo], tags=["meta"])
def snapshots(con: Db, city: str | None = None) -> list[SnapshotInfo]:
    return _snapshots(con, city.lower() if city else None)


def _run_analyze(
    req: AnalyzeRequest, con: duckdb.DuckDBPyConnection, settings: Settings, geo: GeocoderFactory
) -> Analysis:
    city = req.city.lower()
    return analyze(
        con,
        settings,
        city,
        req.query(),
        geo(city),
        bedrooms=req.bedrooms,
        room_type=req.room_type,
        max_comps_listed=req.show,
        quality=req.quality(),
        snapshot=req.snapshot,
        radius_m=req.radius_m,
    )


@router.post("/analyze", response_model=Analysis, responses=ERRORS, tags=["analysis"])
def analyze_endpoint(req: AnalyzeRequest, con: Db, settings: Cfg, geo: Geo) -> Analysis:
    """Comps, market stats and a revenue range for an address or coordinates."""
    return _run_analyze(req, con, settings, geo)


class CompsResponse(BaseModel):
    """Every comp (not just the nearest few) with coordinates, for the map."""

    snapshot_date: str
    currency: str
    location: Location
    filters: CompFilters
    comps: list[Comp]


@router.get("/comps", response_model=CompsResponse, responses=ERRORS, tags=["analysis"])
def comps_endpoint(
    con: Db,
    settings: Cfg,
    geo: Geo,
    q: Annotated[CompsRequest, Query()],
) -> CompsResponse:
    """All comps for a location (up to `show`, default 500), nearest first."""
    result = _run_analyze(q, con, settings, geo)
    return CompsResponse(
        snapshot_date=result.snapshot_date,
        currency=result.currency,
        location=result.location,
        filters=result.filters,
        comps=result.comps,
    )


@router.get("/seasonality", response_model=Seasonality, responses=ERRORS, tags=["analysis"])
def seasonality_endpoint(
    con: Db,
    settings: Cfg,
    geo: Geo,
    req: Annotated[SeasonalityRequest, Query()],
) -> Seasonality:
    """Forward calendar and review history by month for the same comps as /analyze."""
    city = req.city.lower()
    return seasonality(
        con,
        settings,
        city,
        req.query(),
        geo(city),
        bedrooms=req.bedrooms,
        room_type=req.room_type,
        quality=req.quality(),
        snapshot=req.snapshot,
        review_months=req.months,
        radius_m=req.radius_m,
    )


@router.get(
    "/neighbourhoods",
    response_model=list[NeighbourhoodSummary],
    responses=ERRORS,
    tags=["neighbourhoods"],
)
def neighbourhoods_endpoint(
    con: Db, settings: Cfg, city: str = "thessaloniki", snapshot: str | None = None
) -> list[NeighbourhoodSummary]:
    """Every neighbourhood with listing counts and typical prices."""
    city = city.lower()
    snap = resolve_snapshot(con, settings, city, snapshot)
    rows = hoods.summaries(
        con,
        city,
        snap,
        settings.comps.min_price,
        settings.comps.max_price,
        settings.market.multi_listing_threshold,
    )
    return [NeighbourhoodSummary(**r) for r in rows]


@router.get("/neighbourhoods.geojson", tags=["neighbourhoods"], responses=ERRORS)
def neighbourhood_shapes(
    con: Db, settings: Cfg, city: str = "thessaloniki", snapshot: str | None = None
) -> dict[str, Any]:
    """All neighbourhood polygons as one GeoJSON FeatureCollection."""
    city = city.lower()
    snap = resolve_snapshot(con, settings, city, snapshot)
    return hoods.all_geometries(con, city, snap)


@router.get(
    "/neighbourhoods/{name}",
    response_model=NeighbourhoodDetail,
    responses=ERRORS,
    tags=["neighbourhoods"],
)
def neighbourhood_detail(
    name: str, con: Db, settings: Cfg, city: str = "thessaloniki", snapshot: str | None = None
) -> NeighbourhoodDetail:
    """One neighbourhood's stats and its polygon (name is case-insensitive)."""
    city = city.lower()
    snap = resolve_snapshot(con, settings, city, snapshot)
    row = hoods.detail(
        con,
        city,
        snap,
        name,
        settings.comps.min_price,
        settings.comps.max_price,
        settings.market.multi_listing_threshold,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No neighbourhood called '{name}'.")
    return NeighbourhoodDetail(**row)
