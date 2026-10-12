"""Shared test fixtures: one synthetic snapshot database for the whole session."""

from pathlib import Path

import pytest

from stragent.analytics.geocode import parse_latlon
from stragent.config import load_settings
from stragent.data.fixtures import build_fixture
from stragent.data.ingest import ingest
from stragent.db import connect

SNAPSHOT = "2026-09-25"
CENTRE = "40.6326, 22.9416"  # dense synthetic "Kentro"
KALAMARIA = "40.585, 22.96"  # deliberately sparse
ATHENS = "37.9838, 23.7275"


class CoordsOnly:
    """Offline geocoder for tests: accepts 'lat, lon' strings only."""

    def geocode(self, query):
        result = parse_latlon(query)
        assert result is not None
        return result


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def db(tmp_path_factory, settings) -> Path:
    root = tmp_path_factory.mktemp("stragent")
    src = build_fixture(root / "fixture", n_listings=800, snapshot=SNAPSHOT)
    path = root / "test.duckdb"
    con = connect(path)
    report = ingest(
        con, "thessaloniki", settings.city("thessaloniki"), SNAPSHOT, root / "raw", from_dir=src
    )
    con.close()
    assert report.rows["listings"] == 799  # one fixture row has no latitude
    assert report.rows["neighbourhoods"] == 5
    return path
