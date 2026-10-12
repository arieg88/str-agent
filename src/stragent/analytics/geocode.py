"""Address -> coordinates.

Uses OpenStreetMap's Nominatim. Its public service allows at most one request per second and
requires a descriptive User-Agent (https://operations.osmfoundation.org/policies/nominatim/),
so results are cached on disk and requests are throttled.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import httpx

from stragent.config import CityConfig, GeocodingConfig

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$")


class GeocodingError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    display_name: str | None
    source: str


class Geocoder(Protocol):
    def geocode(self, query: str) -> GeocodeResult: ...


def parse_latlon(query: str) -> GeocodeResult | None:
    """Accept '40.6326, 22.9416' as a location without calling any service."""
    match = _LATLON_RE.match(query)
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return GeocodeResult(lat, lon, None, "coordinates")


class NominatimGeocoder:
    def __init__(
        self,
        city: CityConfig,
        cfg: GeocodingConfig,
        cache_path: Path,
        client: httpx.Client | None = None,
    ) -> None:
        self.city = city
        self.cfg = cfg
        self.cache_path = cache_path
        self._client = client
        self._last_request = 0.0
        self._cache: dict[str, dict] = {}
        if cache_path.exists():
            self._cache = json.loads(cache_path.read_text(encoding="utf-8"))

    def _user_agent(self) -> str:
        contact = os.environ.get("STRAGENT_CONTACT_EMAIL")
        return f"{self.cfg.user_agent} {contact}" if contact else self.cfg.user_agent

    def _save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def geocode(self, query: str) -> GeocodeResult:
        direct = parse_latlon(query)
        if direct:
            return direct

        key = f"{self.city.display_name}|{query.strip().lower()}"
        if key in self._cache:
            return GeocodeResult(**{**self._cache[key], "source": "nominatim (cached)"})

        wait = self.cfg.min_interval_seconds - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

        west, south, east, north = self.city.viewbox
        params: dict[str, str | int] = {
            "q": query
            if self.city.display_name.lower() in query.lower()
            else f"{query}, {self.city.display_name}",
            "format": "jsonv2",
            "limit": 1,
            "countrycodes": self.city.country_code,
            "viewbox": f"{west},{north},{east},{south}",
            "bounded": 1,
        }
        client = self._client or httpx.Client(timeout=20)
        try:
            resp = client.get(
                NOMINATIM_URL, params=params, headers={"User-Agent": self._user_agent()}
            )
            self._last_request = time.monotonic()
            resp.raise_for_status()
            results = resp.json()
        except httpx.HTTPError as exc:
            raise GeocodingError(
                f"Geocoding failed ({exc}). "
                "You can pass coordinates instead, e.g. '40.6326, 22.9416'."
            ) from exc
        finally:
            if self._client is None:
                client.close()

        if not results:
            raise GeocodingError(f"No match for '{query}' in {self.city.display_name}.")
        hit = results[0]
        result = GeocodeResult(
            latitude=float(hit["lat"]),
            longitude=float(hit["lon"]),
            display_name=hit.get("display_name"),
            source="nominatim",
        )
        self._cache[key] = {k: v for k, v in asdict(result).items() if k != "source"}
        self._save()
        return result
