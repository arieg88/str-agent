"""HTTP API tests: the FastAPI app against the shared synthetic database."""

import pytest
from fastapi.testclient import TestClient

from conftest import CENTRE, SNAPSHOT, CoordsOnly
from stragent.api.app import STATIC_DIR, create_app
from stragent.models import Analysis, Seasonality

pytestmark = pytest.mark.integration
LAT, LON = (float(x) for x in CENTRE.split(","))


@pytest.fixture(scope="module")
def client(db, settings):
    app = create_app(settings=settings, db_path=db, geocoder_for=lambda city: CoordsOnly())
    return TestClient(app)


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["snapshots"][0]["snapshot_date"] == SNAPSHOT


def test_health_without_database(settings, tmp_path):
    app = create_app(settings=settings, db_path=tmp_path / "missing.duckdb")
    body = TestClient(app).get("/api/health").json()
    assert body == {
        "status": "no_data",
        "database": False,
        "database_path": str(tmp_path / "missing.duckdb"),
        "snapshots": [],
    }


def test_snapshots_without_database_is_no_data(settings, tmp_path):
    # the map page shows its "run ingest first" hint on this code, and only on this code
    app = create_app(settings=settings, db_path=tmp_path / "missing.duckdb")
    r = TestClient(app).get("/api/snapshots")
    assert r.status_code == 404
    assert r.json()["error"] == "no_data" and r.json()["request_id"]


def test_analyze_by_coordinates(client):
    r = client.post("/api/analyze", json={"lat": LAT, "lon": LON, "bedrooms": 2, "show": 5})
    assert r.status_code == 200
    result = Analysis.model_validate(r.json())
    assert result.comp_stats.count >= 10
    assert len(result.comps) == 5
    assert all(c.latitude and c.longitude for c in result.comps)
    assert r.headers["x-request-id"]


def test_analyze_by_address_uses_geocoder(client):
    # CoordsOnly geocoder: an address string that is really coordinates goes through it
    r = client.post("/api/analyze", json={"address": CENTRE})
    assert r.status_code == 200
    assert r.json()["location"]["query"] == CENTRE


def test_analyze_filters_match_cli_semantics(client):
    r = client.post(
        "/api/analyze",
        json={"lat": LAT, "lon": LON, "bedrooms": 2, "min_reviews": 15, "max_min_nights": 3},
    )
    body = r.json()
    assert body["filters"]["min_reviews_ltm"] == 15
    assert all(c["number_of_reviews_ltm"] >= 15 for c in body["comps"])
    assert all((c["minimum_nights"] or 1) <= 3 for c in body["comps"])


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"bedrooms": 2}, 422, "invalid_request"),
        ({"lat": 37.98, "lon": 23.72}, 422, "out_of_area"),
        ({"lat": LAT, "lon": LON, "snapshot": "2020-01-01"}, 404, "no_data"),
        ({"lat": LAT, "lon": LON, "city": "athens"}, 404, "unknown_city"),
        ({"lat": 200, "lon": LON}, 422, "invalid_request"),
    ],
)
def test_analyze_errors_share_one_shape(client, payload, status, code):
    r = client.post("/api/analyze", json=payload)
    assert r.status_code == status
    body = r.json()
    assert body["error"] == code
    assert body["detail"] and body["request_id"]


def test_comps_returns_all_with_coordinates(client):
    few = client.post("/api/analyze", json={"lat": LAT, "lon": LON, "show": 3}).json()
    r = client.get("/api/comps", params={"lat": LAT, "lon": LON})
    assert r.status_code == 200
    comps = r.json()["comps"]
    assert len(comps) == few["comp_stats"]["count"] > 3
    distances = [c["distance_m"] for c in comps]
    assert distances == sorted(distances)


def test_seasonality(client):
    r = client.get("/api/seasonality", params={"lat": LAT, "lon": LON, "bedrooms": 2, "months": 12})
    assert r.status_code == 200
    s = Seasonality.model_validate(r.json())
    assert len(s.review_history) == 12
    assert s.review_history[-1].month == "2026-08"  # last complete month before the snapshot
    assert 12 <= len(s.forward_calendar) <= 13  # partial first/last months
    assert s.forward_calendar[0].month == "2026-09"
    assert all(0 <= m.unavailable_share <= 1 for m in s.forward_calendar)
    assert s.calendar_listing_count <= s.listing_count


def test_neighbourhoods(client):
    rows = client.get("/api/neighbourhoods").json()
    assert {r["name"] for r in rows} >= {"Kentro (synthetic)", "Kalamaria (synthetic)"}
    assert rows[0]["listings"] >= rows[-1]["listings"]
    detail = client.get("/api/neighbourhoods/kentro (synthetic)").json()
    assert detail["geometry"]["type"] == "MultiPolygon"
    assert client.get("/api/neighbourhoods/nowhere").json()["error"] == "not_found"
    shapes = client.get("/api/neighbourhoods.geojson").json()
    assert len(shapes["features"]) == 5


def test_map_page_and_docs(client):
    page = client.get("/")
    assert page.status_code == 200 and "leaflet" in page.text
    assert client.get("/openapi.json").json()["info"]["title"] == "stragent"


def test_map_page_assets_are_served(client):
    scripts = sorted(p.name for p in (STATIC_DIR / "js").glob("*.js"))
    assert "main.js" in scripts
    expected = {"/": "text/html", "/static/css/app.css": "text/css"}
    # text/javascript or application/javascript, depending on the Python version
    expected |= {f"/static/js/{name}": "javascript" for name in scripts}
    for path, content_type in expected.items():
        r = client.get(path)
        assert r.status_code == 200, path
        assert content_type in r.headers["content-type"], path
    page = client.get("/").text
    assert 'href="/static/css/app.css"' in page
    assert '<script type="module" src="/static/js/main.js">' in page


def test_radius_via_api(client):
    r = client.post("/api/analyze", json={"lat": LAT, "lon": LON, "radius_m": 800})
    assert r.status_code == 200 and r.json()["filters"]["radius_m"] == 800
    s = client.get("/api/seasonality", params={"lat": LAT, "lon": LON, "radius_m": 800}).json()
    assert s["filters"]["radius_m"] == 800
    bad = client.post("/api/analyze", json={"lat": LAT, "lon": LON, "radius_m": 99_999})
    assert bad.status_code == 422 and bad.json()["error"] == "invalid_radius"
