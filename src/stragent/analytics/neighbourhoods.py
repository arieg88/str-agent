"""Neighbourhood-level summaries for one snapshot."""

from __future__ import annotations

import json
from typing import Any

import duckdb

_SUMMARY_SQL = """
    SELECT n.neighbourhood AS name,
           any_value(n.neighbourhood_group) AS grp,
           count(l.id) AS listings,
           avg(CASE WHEN l.room_type = 'Entire home/apt' THEN 1.0 ELSE 0.0 END)
               FILTER (WHERE l.id IS NOT NULL) AS entire_home_share,
           median(l.price) FILTER (WHERE l.room_type = 'Entire home/apt'
                                   AND l.price BETWEEN $min_price AND $max_price)
               AS median_price_entire_home,
           median(l.number_of_reviews_ltm) AS median_reviews_ltm,
           avg(CASE WHEN coalesce(l.calculated_host_listings_count, 1) >= $multi
                    THEN 1.0 ELSE 0.0 END) FILTER (WHERE l.id IS NOT NULL)
               AS multi_listing_host_share
    FROM neighbourhoods n
    LEFT JOIN listings l
      ON l.city = n.city AND l.snapshot_date = n.snapshot_date
     AND l.neighbourhood = n.neighbourhood
    WHERE n.city = $city AND n.snapshot_date = CAST($snapshot AS DATE)
      {name_filter}
    GROUP BY n.neighbourhood
    ORDER BY listings DESC, name
"""


def _row(r: tuple[Any, ...]) -> dict[str, Any]:
    keys = (
        "name",
        "group",
        "listings",
        "entire_home_share",
        "median_price_entire_home",
        "median_reviews_ltm",
        "multi_listing_host_share",
    )
    out = dict(zip(keys, r, strict=True))
    for k in (
        "entire_home_share",
        "median_price_entire_home",
        "median_reviews_ltm",
        "multi_listing_host_share",
    ):
        out[k] = None if out[k] is None else round(float(out[k]), 4)
    out["listings"] = int(out["listings"])
    return out


def summaries(
    con: duckdb.DuckDBPyConnection,
    city: str,
    snapshot: str,
    min_price: float,
    max_price: float,
    multi_listing_threshold: int,
) -> list[dict[str, Any]]:
    params = {
        "city": city,
        "snapshot": snapshot,
        "min_price": min_price,
        "max_price": max_price,
        "multi": multi_listing_threshold,
    }
    rows = con.execute(_SUMMARY_SQL.format(name_filter=""), params).fetchall()
    return [_row(r) for r in rows]


def detail(
    con: duckdb.DuckDBPyConnection,
    city: str,
    snapshot: str,
    name: str,
    min_price: float,
    max_price: float,
    multi_listing_threshold: int,
) -> dict[str, Any] | None:
    params = {
        "city": city,
        "snapshot": snapshot,
        "min_price": min_price,
        "max_price": max_price,
        "multi": multi_listing_threshold,
        "name": name,
    }
    sql = _SUMMARY_SQL.format(name_filter="AND lower(n.neighbourhood) = lower($name)")
    row = con.execute(sql, params).fetchone()
    if row is None:
        return None
    geometry = con.execute(
        "SELECT geometry_geojson FROM neighbourhoods "
        "WHERE city = $city AND snapshot_date = CAST($snapshot AS DATE) "
        "AND lower(neighbourhood) = lower($name)",
        {"city": city, "snapshot": snapshot, "name": name},
    ).fetchone()
    out = _row(row)
    out["geometry"] = json.loads(geometry[0]) if geometry else {}
    out["snapshot_date"] = snapshot
    return out


def all_geometries(con: duckdb.DuckDBPyConnection, city: str, snapshot: str) -> dict[str, Any]:
    """A GeoJSON FeatureCollection of every neighbourhood, for the map."""
    rows = con.execute(
        "SELECT neighbourhood, geometry_geojson FROM neighbourhoods "
        "WHERE city = ? AND snapshot_date = CAST(? AS DATE)",
        [city, snapshot],
    ).fetchall()
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": n}, "geometry": json.loads(g)}
            for n, g in rows
        ],
    }
