"""Address in, comps + market stats + revenue estimate (and seasonality) out.

Everything here is deterministic. No LLM is involved; later stages explain these numbers
but never compute them. The CLI and the API both call these functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb
import pandas as pd

from stragent.analytics.comps import (
    CompQuality,
    CompSelection,
    comp_stats,
    market_stats,
    select_comps,
)
from stragent.analytics.geo import NeighbourhoodIndex
from stragent.analytics.geocode import Geocoder, GeocodeResult
from stragent.analytics.revenue import (
    ListingEstimate,
    Range,
    area_estimates,
    estimate_listing,
    percentile_range,
)
from stragent.analytics.seasonality import forward_calendar, review_history
from stragent.config import CityConfig, OccupancyConfig, Settings
from stragent.db import latest_snapshot, loaded_snapshots
from stragent.models import (
    Analysis,
    Comp,
    Location,
    RevenueEstimate,
    Seasonality,
    ValueRange,
)

LISTING_URL = "https://www.airbnb.com/rooms/{id}"


class NoDataError(RuntimeError):
    pass


class OutOfAreaError(ValueError):
    pass


def _vr(r: Range | None) -> ValueRange | None:
    return None if r is None else ValueRange(p25=r.p25, median=r.median, p75=r.p75, n=r.n)


def _missing(value: Any) -> bool:
    return value is None or bool(pd.isna(value))


def _opt_int(value: Any) -> int | None:
    return None if _missing(value) else int(value)


def _opt_str(value: Any) -> str | None:
    return None if _missing(value) else str(value)


def _opt_float(value: Any) -> float | None:
    return None if _missing(value) else float(value)


def _opt_date(value: Any) -> Any:
    return None if _missing(value) else pd.Timestamp(value).date()


def _opt_estimate(ltm: Any, min_nights: Any, price: Any, cfg: OccupancyConfig) -> ListingEstimate:
    return estimate_listing(_opt_int(ltm), _opt_int(min_nights), _opt_float(price), cfg)


def _neighbourhoods(con: duckdb.DuckDBPyConnection, city: str, snapshot: str) -> NeighbourhoodIndex:
    rows = con.execute(
        "SELECT neighbourhood, geometry_geojson FROM neighbourhoods "
        "WHERE city = ? AND snapshot_date = CAST(? AS DATE)",
        [city, snapshot],
    ).fetchall()
    return NeighbourhoodIndex.from_rows(rows)


def resolve_snapshot(
    con: duckdb.DuckDBPyConnection, settings: Settings, city_key: str, snapshot: str | None
) -> str:
    """The requested snapshot if loaded, otherwise the latest one."""
    city = settings.city(city_key)
    if snapshot is None:
        latest = latest_snapshot(con, city_key)
        if latest is None:
            raise NoDataError(
                f"No data loaded for {city.display_name}. Run `stragent ingest` first."
            )
        return latest
    loaded = loaded_snapshots(con, city_key)
    if snapshot not in loaded:
        raise NoDataError(
            f"Snapshot {snapshot} is not loaded. Loaded: {', '.join(loaded) or 'none'}."
        )
    return snapshot


@dataclass
class Context:
    """Everything shared by analyze() and seasonality(): where, when and which comps."""

    city: CityConfig
    snapshot: str
    geo: GeocodeResult
    neighbourhood: str | None
    selection: CompSelection
    query: str

    @property
    def location(self) -> Location:
        return Location(
            query=self.query,
            latitude=self.geo.latitude,
            longitude=self.geo.longitude,
            display_name=self.geo.display_name,
            neighbourhood=self.neighbourhood,
            geocoder=self.geo.source,
        )


def build_context(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    city_key: str,
    query: str,
    geocoder: Geocoder,
    bedrooms: int | None = None,
    room_type: str | None = None,
    quality: CompQuality | None = None,
    snapshot: str | None = None,
) -> Context:
    city = settings.city(city_key)
    snapshot = resolve_snapshot(con, settings, city_key, snapshot)
    geo = geocoder.geocode(query)
    hood = _neighbourhoods(con, city_key, snapshot).lookup(geo.latitude, geo.longitude)
    selection = select_comps(
        con,
        city_key,
        snapshot,
        geo.latitude,
        geo.longitude,
        room_type or settings.comps.default_room_type,
        bedrooms,
        settings.comps,
        quality,
    )
    if hood is None and selection.area.empty:
        raise OutOfAreaError(
            f"{geo.latitude:.5f}, {geo.longitude:.5f} is outside {city.display_name}'s "
            f"neighbourhoods and has no listings within {selection.filters.radius_m} m."
        )
    return Context(city, snapshot, geo, hood, selection, query)


def analyze(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    city_key: str,
    query: str,
    geocoder: Geocoder,
    bedrooms: int | None = None,
    room_type: str | None = None,
    max_comps_listed: int = 15,
    quality: CompQuality | None = None,
    snapshot: str | None = None,
) -> Analysis:
    ctx = build_context(
        con, settings, city_key, query, geocoder, bedrooms, room_type, quality, snapshot
    )
    city, snapshot, selection, hood = ctx.city, ctx.snapshot, ctx.selection, ctx.neighbourhood
    comps = selection.comps
    occ_cfg = settings.occupancy
    estimates = [
        _opt_estimate(r.number_of_reviews_ltm, r.minimum_nights, r.price, occ_cfg)
        for r in comps.itertuples()
    ]
    active = [
        est
        for est, ltm in zip(estimates, comps["number_of_reviews_ltm"].fillna(0), strict=True)
        if ltm > 0
    ]
    occ_range, nights_range, revenue_range = area_estimates(active)

    ia_revenue = None
    if "estimated_revenue_l365d" in comps and comps["estimated_revenue_l365d"].notna().any():
        ia_revenue = _vr(
            percentile_range(comps["estimated_revenue_l365d"].dropna().astype(float).tolist())
        )

    stats = comp_stats(comps)
    warnings: list[str] = []
    target = settings.comps.min_comps
    if stats.count < target:
        warnings.append(
            f"Only {stats.count} comparable listings within {selection.filters.radius_m} m "
            f"(target {target}); treat the ranges as rough."
        )
    elif selection.filters.radius_widened:
        warnings.append(
            f"Search radius widened to {selection.filters.radius_m} m to find enough comps."
        )
    if stats.count and stats.active_count < max(3, stats.count // 2):
        warnings.append(
            f"Only {stats.active_count} of {stats.count} comps had reviews in the last 12 months."
        )
    capped_share = (sum(e.capped for e in active) / len(active)) if active else None
    if capped_share and capped_share > 0.3:
        warnings.append(
            f"{capped_share:.0%} of active comps hit the {occ_cfg.max_occupancy:.0%} occupancy "
            "cap, so the top of the revenue range may be understated."
        )
    if hood is None:
        warnings.append("The location is outside the neighbourhood polygons in this snapshot.")
    if any("(synthetic)" in str(n) for n in selection.area["neighbourhood"].dropna().unique()):
        warnings.append("SYNTHETIC FIXTURE DATA: these numbers are not real market data.")
    if (
        not comps.empty
        and comps["number_of_reviews"].fillna(0).gt(0).any()
        and comps["first_review"].isna().all()
    ):
        warnings.append(
            "'Active since' is empty: this snapshot was loaded before that column existed. "
            f"Re-run `stragent ingest --snapshot {snapshot}` (files are cached)."
        )

    listed = []
    records: list[dict[str, Any]] = comps.to_dict("records")  # type: ignore[assignment]
    for row, est in list(zip(records, estimates, strict=True))[:max_comps_listed]:
        listed.append(
            Comp(
                id=int(row["id"]),
                url=LISTING_URL.format(id=int(row["id"])),
                name=_opt_str(row["name"]),
                neighbourhood=_opt_str(row["neighbourhood"]),
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                distance_m=round(float(row["distance_m"]), 1),
                room_type=_opt_str(row["room_type"]),
                bedrooms=_opt_int(row["bedrooms"]),
                price=float(row["price"]),
                minimum_nights=_opt_int(row["minimum_nights"]),
                number_of_reviews_ltm=_opt_int(row["number_of_reviews_ltm"]),
                review_scores_rating=_opt_float(row["review_scores_rating"]),
                active_since=_opt_date(row["first_review"]),
                host_since=_opt_date(row["host_since"]),
                est_occupancy=round(est.occupancy, 4),
                est_revenue=None if est.revenue is None else round(est.revenue, 2),
            )
        )

    return Analysis(
        city=city.display_name,
        snapshot_date=snapshot,
        currency=city.currency,
        location=ctx.location,
        filters=selection.filters,
        comp_stats=stats,
        revenue=RevenueEstimate(
            method="review-based occupancy model (Inside Airbnb data assumptions)",
            assumptions={
                "review_rate": occ_cfg.review_rate,
                "average_stay_nights": occ_cfg.average_stay_nights,
                "max_occupancy": occ_cfg.max_occupancy,
            },
            occupancy=_vr(occ_range),
            occupied_nights=_vr(nights_range),
            annual_revenue=_vr(revenue_range),
            capped_share=capped_share,
            inside_airbnb_revenue=ia_revenue,
        ),
        market=market_stats(selection.area, selection.filters.radius_m, settings.market, snapshot),
        comps=listed,
        warnings=warnings,
    )


def seasonality(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    city_key: str,
    query: str,
    geocoder: Geocoder,
    bedrooms: int | None = None,
    room_type: str | None = None,
    quality: CompQuality | None = None,
    snapshot: str | None = None,
    review_months: int = 24,
) -> Seasonality:
    """Month-by-month demand signals for the same comps analyze() would use."""
    ctx = build_context(
        con, settings, city_key, query, geocoder, bedrooms, room_type, quality, snapshot
    )
    ids = [int(i) for i in ctx.selection.comps["id"].tolist()]
    calendar, calendar_listings = forward_calendar(con, city_key, ctx.snapshot, ids)
    history = review_history(con, city_key, ctx.snapshot, ids, review_months)
    notes = [
        "Calendar: share of nights not open for booking (booked OR blocked by the host), "
        "so it overstates occupancy; compare months, not absolute levels.",
        "Reviews: posted per month before the snapshot; they follow stays by a few days.",
    ]
    if len(ids) - calendar_listings:
        notes.append(
            f"{len(ids) - calendar_listings} comps with fully blocked calendars were left out "
            "of the calendar series."
        )
    return Seasonality(
        city=ctx.city.display_name,
        snapshot_date=ctx.snapshot,
        location=ctx.location,
        filters=ctx.selection.filters,
        listing_count=len(ids),
        calendar_listing_count=calendar_listings,
        forward_calendar=calendar,
        review_history=history,
        notes=notes,
    )
