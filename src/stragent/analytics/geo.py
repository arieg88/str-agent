"""Small geographic helpers. No GIS server needed at this stage."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from shapely.geometry import Point, shape
from shapely.prepared import prep

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bounding_box(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """(min_lat, max_lat, min_lon, max_lon) enclosing a circle; a cheap pre-filter."""
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    dlon = math.degrees(radius_m / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


# SQL version of haversine_m, for use inside DuckDB queries.
HAVERSINE_SQL = (
    "2 * 6371008.8 * asin(sqrt("
    "pow(sin(radians(latitude - $lat) / 2), 2) + "
    "cos(radians($lat)) * cos(radians(latitude)) * "
    "pow(sin(radians(longitude - $lon) / 2), 2)))"
)


@dataclass
class NeighbourhoodIndex:
    """Point-in-polygon lookup over the city's neighbourhood polygons."""

    names: list[str]
    _shapes: list

    @classmethod
    def from_rows(cls, rows: list[tuple[str, str]]) -> NeighbourhoodIndex:
        names, shapes = [], []
        for name, geojson in rows:
            names.append(name)
            shapes.append(prep(shape(json.loads(geojson))))
        return cls(names, shapes)

    def lookup(self, lat: float, lon: float) -> str | None:
        point = Point(lon, lat)  # GeoJSON order is (lon, lat)
        for name, poly in zip(self.names, self._shapes, strict=True):
            if poly.covers(point):
                return name
        return None
