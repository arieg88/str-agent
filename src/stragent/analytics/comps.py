"""Find comparable listings and compute market statistics around a point."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import pandas as pd

from stragent.analytics.geo import HAVERSINE_SQL, bounding_box
from stragent.analytics.revenue import percentile_range
from stragent.config import CompsConfig, MarketConfig
from stragent.models import CompFilters, CompStats, MarketStats, ValueRange


@dataclass
class CompSelection:
    comps: pd.DataFrame
    filters: CompFilters
    area: pd.DataFrame  # every listing (any type) within the radius used


def listings_within(
    con: duckdb.DuckDBPyConnection,
    city: str,
    snapshot: str,
    lat: float,
    lon: float,
    radius_m: float,
) -> pd.DataFrame:
    """All listings within ``radius_m`` metres, nearest first, with a distance_m column."""
    min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lon, radius_m)
    sql = f"""
        SELECT * FROM (
            SELECT *, {HAVERSINE_SQL} AS distance_m
            FROM listings
            WHERE city = $city AND snapshot_date = CAST($snapshot AS DATE)
              AND latitude BETWEEN $min_lat AND $max_lat
              AND longitude BETWEEN $min_lon AND $max_lon
        )
        WHERE distance_m <= $radius
        ORDER BY distance_m
    """
    params = {
        "city": city,
        "snapshot": snapshot,
        "lat": lat,
        "lon": lon,
        "min_lat": min_lat,
        "max_lat": max_lat,
        "min_lon": min_lon,
        "max_lon": max_lon,
        "radius": radius_m,
    }
    return con.execute(sql, params).df()


def filter_comps(
    area: pd.DataFrame,
    room_type: str,
    bedrooms: int | None,
    cfg: CompsConfig,
) -> pd.DataFrame:
    df = area[
        (area["room_type"] == room_type)
        & area["price"].notna()
        & area["price"].between(cfg.min_price, cfg.max_price)
    ]
    if bedrooms is not None:
        df = df[
            df["bedrooms"].notna() & (df["bedrooms"] - bedrooms).abs().le(cfg.bedroom_tolerance)
        ]
    return df


def select_comps(
    con: duckdb.DuckDBPyConnection,
    city: str,
    snapshot: str,
    lat: float,
    lon: float,
    room_type: str,
    bedrooms: int | None,
    cfg: CompsConfig,
) -> CompSelection:
    """Use the smallest radius step that yields ``min_comps``; otherwise the largest step."""
    steps = sorted(cfg.radius_steps_m)
    widest = listings_within(con, city, snapshot, lat, lon, steps[-1])

    chosen_radius = steps[-1]
    for radius in steps:
        candidates = filter_comps(widest[widest["distance_m"] <= radius], room_type, bedrooms, cfg)
        if len(candidates) >= cfg.min_comps:
            chosen_radius = radius
            break

    area = widest[widest["distance_m"] <= chosen_radius].reset_index(drop=True)
    comps = filter_comps(area, room_type, bedrooms, cfg).reset_index(drop=True)
    filters = CompFilters(
        room_type=room_type,
        bedrooms=bedrooms,
        bedroom_tolerance=cfg.bedroom_tolerance,
        radius_m=chosen_radius,
        radius_widened=chosen_radius != steps[0],
        min_comps_target=cfg.min_comps,
    )
    return CompSelection(comps=comps, filters=filters, area=area)


def _share(mask: pd.Series) -> float | None:
    return None if len(mask) == 0 else float(mask.mean())


def _to_range(values: pd.Series) -> ValueRange | None:
    r = percentile_range(values.dropna().astype(float).tolist())
    return None if r is None else ValueRange(p25=r.p25, median=r.median, p75=r.p75, n=r.n)


def comp_stats(comps: pd.DataFrame) -> CompStats:
    active = comps["number_of_reviews_ltm"].fillna(0) > 0
    ratings = comps["review_scores_rating"].dropna()
    superhost = comps["host_is_superhost"].dropna()
    min_nights = comps["minimum_nights"].dropna()
    return CompStats(
        count=len(comps),
        active_count=int(active.sum()),
        nightly_price=_to_range(comps["price"]),
        review_score_median=None if ratings.empty else float(ratings.median()),
        superhost_share=None if superhost.empty else float(superhost.astype(bool).mean()),
        minimum_nights_median=None if min_nights.empty else float(min_nights.median()),
    )


def market_stats(area: pd.DataFrame, radius_m: int, cfg: MarketConfig) -> MarketStats:
    total = len(area)
    if total == 0:
        return MarketStats(
            radius_m=radius_m,
            total_listings=0,
            entire_home_share=None,
            multi_listing_host_share=None,
            top3_hosts_share=None,
            licensed_share=None,
        )
    host_counts = area["calculated_host_listings_count"].fillna(1)
    top3 = area["host_id"].value_counts().head(3).sum()
    licensed = area["license"].fillna("").astype(str).str.strip().ne("")
    return MarketStats(
        radius_m=radius_m,
        total_listings=total,
        entire_home_share=_share(area["room_type"] == "Entire home/apt"),
        multi_listing_host_share=_share(host_counts >= cfg.multi_listing_threshold),
        top3_hosts_share=float(top3 / total),
        licensed_share=_share(licensed),
    )
