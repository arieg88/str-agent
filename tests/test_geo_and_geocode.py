import json

import httpx
import pytest

from stragent.analytics.geo import NeighbourhoodIndex, bounding_box, haversine_m
from stragent.analytics.geocode import GeocodingError, NominatimGeocoder, parse_latlon
from stragent.config import load_settings


def test_haversine_one_degree_latitude():
    assert haversine_m(40.0, 22.9, 41.0, 22.9) == pytest.approx(111_195, rel=1e-3)


def test_haversine_zero():
    assert haversine_m(40.63, 22.94, 40.63, 22.94) == 0


def test_bounding_box_contains_circle():
    lat, lon, r = 40.63, 22.94, 1000
    min_lat, max_lat, min_lon, max_lon = bounding_box(lat, lon, r)
    assert haversine_m(lat, lon, max_lat, lon) == pytest.approx(r, rel=1e-3)
    assert haversine_m(lat, lon, lat, max_lon) == pytest.approx(r, rel=1e-3)
    assert min_lat < lat < max_lat and min_lon < lon < max_lon


def test_neighbourhood_lookup():
    square = {
        "type": "Polygon",
        "coordinates": [[[22.9, 40.6], [23.0, 40.6], [23.0, 40.7], [22.9, 40.7], [22.9, 40.6]]],
    }
    index = NeighbourhoodIndex.from_rows([("Centre", json.dumps(square))])
    assert index.lookup(40.65, 22.95) == "Centre"
    assert index.lookup(37.98, 23.72) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("40.6326, 22.9416", (40.6326, 22.9416)),
        ("40.6 22.9", (40.6, 22.9)),
        ("Egnatia 120", None),
        ("95, 10", None),
    ],
)
def test_parse_latlon(text, expected):
    result = parse_latlon(text)
    assert (None if result is None else (result.latitude, result.longitude)) == expected


def _geocoder(tmp_path, handler):
    settings = load_settings()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = settings.geocoding.model_copy(update={"min_interval_seconds": 0})
    return NominatimGeocoder(
        settings.city("thessaloniki"), cfg, tmp_path / "geo.json", client=client
    )


def test_nominatim_request_and_cache(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200, json=[{"lat": "40.6401", "lon": "22.9444", "display_name": "Egnatia 120"}]
        )

    geocoder = _geocoder(tmp_path, handler)
    first = geocoder.geocode("Egnatia 120")
    second = geocoder.geocode("egnatia 120 ")

    assert (first.latitude, first.longitude) == (40.6401, 22.9444)
    assert second.source == "nominatim (cached)"
    assert len(calls) == 1
    params = calls[0].url.params
    assert params["q"] == "Egnatia 120, Thessaloniki"
    assert params["countrycodes"] == "gr" and params["bounded"] == "1"
    assert calls[0].headers["User-Agent"].startswith("stragent/")
    # cache survives a restart
    assert _geocoder(tmp_path, handler).geocode("Egnatia 120").source == "nominatim (cached)"


def test_nominatim_no_result(tmp_path):
    geocoder = _geocoder(tmp_path, lambda r: httpx.Response(200, json=[]))
    with pytest.raises(GeocodingError, match="No match"):
        geocoder.geocode("Nowhere street")


def test_nominatim_http_error(tmp_path):
    geocoder = _geocoder(tmp_path, lambda r: httpx.Response(503))
    with pytest.raises(GeocodingError, match="coordinates instead"):
        geocoder.geocode("Egnatia 120")


def test_coordinates_skip_network(tmp_path):
    def handler(request):
        raise AssertionError("should not be called")

    assert _geocoder(tmp_path, handler).geocode("40.63, 22.94").source == "coordinates"
