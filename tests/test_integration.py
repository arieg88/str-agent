"""End to end on a synthetic snapshot: fixture files -> ingest -> analyze -> CLI."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stragent.analysis import NoDataError, OutOfAreaError, analyze
from stragent.analytics.geocode import parse_latlon
from stragent.cli import app
from stragent.config import load_settings
from stragent.data.fixtures import build_fixture
from stragent.data.ingest import ingest
from stragent.db import connect
from stragent.models import Analysis

pytestmark = pytest.mark.integration

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


@pytest.fixture(scope="module")
def settings():
    return load_settings()


@pytest.fixture(scope="module")
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


def _run(db: Path, settings, query: str, bedrooms=2) -> Analysis:
    con = connect(db, read_only=True)
    try:
        return analyze(con, settings, "thessaloniki", query, CoordsOnly(), bedrooms=bedrooms)
    finally:
        con.close()


def test_dense_area_uses_smallest_radius(db, settings):
    result = _run(db, settings, CENTRE)
    assert result.filters.radius_m == 500 and not result.filters.radius_widened
    assert result.comp_stats.count >= settings.comps.min_comps
    assert result.location.neighbourhood == "Kentro (synthetic)"
    rev = result.revenue.annual_revenue
    assert rev is not None and rev.p25 <= rev.median <= rev.p75
    assert all(c.distance_m <= 500 for c in result.comps)
    assert [c.distance_m for c in result.comps] == sorted(c.distance_m for c in result.comps)
    assert all(1 <= (c.bedrooms or 0) <= 3 for c in result.comps)
    assert any("SYNTHETIC" in w for w in result.warnings)


def test_sparse_area_widens_radius(db, settings):
    result = _run(db, settings, KALAMARIA)
    assert result.filters.radius_widened
    assert result.filters.radius_m > 500
    assert any("widened" in w or "Only" in w for w in result.warnings)


def test_outside_city_is_rejected(db, settings):
    with pytest.raises(OutOfAreaError):
        _run(db, settings, ATHENS)


def test_no_data_loaded(tmp_path, settings):
    con = connect(tmp_path / "empty.duckdb")
    with pytest.raises(NoDataError):
        analyze(con, settings, "thessaloniki", CENTRE, CoordsOnly())
    con.close()


def test_reingest_is_idempotent(db, settings, tmp_path):
    con = connect(db)
    before = con.execute("SELECT count(*) FROM listings").fetchone()[0]
    raw = Path(tmp_path)
    src = build_fixture(raw / "again", n_listings=800, snapshot=SNAPSHOT)
    ingest(con, "thessaloniki", settings.city("thessaloniki"), SNAPSHOT, raw / "raw", from_dir=src)
    after = con.execute("SELECT count(*) FROM listings").fetchone()[0]
    snaps = con.execute("SELECT count(*) FROM snapshots").fetchone()[0]
    con.close()
    assert before == after and snaps == 1


def test_cli_analyze_json(db):
    result = CliRunner().invoke(
        app, ["analyze", CENTRE, "--bedrooms", "2", "--json", "--no-save", "--db", str(db)]
    )
    assert result.exit_code == 0, result.output
    parsed = Analysis.model_validate(json.loads(result.output))
    assert parsed.comp_stats.count >= 10


def test_cli_outside_city_exit_code(db):
    result = CliRunner().invoke(app, ["analyze", ATHENS, "--no-save", "--db", str(db)])
    assert result.exit_code == 2
    assert "outside" in result.output


def test_quality_filter_shrinks_comps(db, settings):
    from stragent.analytics.comps import CompQuality

    con = connect(db, read_only=True)
    base = analyze(con, settings, "thessaloniki", CENTRE, CoordsOnly(), bedrooms=2)
    strict = analyze(
        con,
        settings,
        "thessaloniki",
        CENTRE,
        CoordsOnly(),
        bedrooms=2,
        quality=CompQuality(min_reviews_ltm=15),
    )
    con.close()
    assert strict.filters.min_reviews_ltm == 15
    assert all((c.number_of_reviews_ltm or 0) >= 15 for c in strict.comps)
    # a stricter filter may need a wider radius to reach the target
    assert strict.filters.radius_m >= base.filters.radius_m


def test_multiple_snapshots_and_selection(tmp_path, settings):
    src = build_fixture(tmp_path / "fx", n_listings=300, snapshot="2026-06-20")
    path = tmp_path / "multi.duckdb"
    for snap in ("2026-06-20", "2026-09-22"):
        result = CliRunner().invoke(
            app, ["ingest", "--snapshot", snap, "--from-dir", str(src), "--db", str(path)]
        )
        assert result.exit_code == 0, result.output
    con = connect(path, read_only=True)
    latest = analyze(con, settings, "thessaloniki", CENTRE, CoordsOnly())
    older = analyze(con, settings, "thessaloniki", CENTRE, CoordsOnly(), snapshot="2026-06-20")
    with pytest.raises(NoDataError, match="not loaded"):
        analyze(con, settings, "thessaloniki", CENTRE, CoordsOnly(), snapshot="2025-01-01")
    con.close()
    assert latest.snapshot_date == "2026-09-22"
    assert older.snapshot_date == "2026-06-20"


def test_cli_rejects_from_dir_with_several_snapshots(tmp_path):
    result = CliRunner().invoke(
        app,
        [
            "ingest",
            "--snapshot",
            "2026-06-20",
            "--snapshot",
            "2026-09-22",
            "--from-dir",
            "x",
            "--db",
            str(tmp_path / "x.duckdb"),
        ],
    )
    assert result.exit_code == 1


def test_cli_filters_and_show(db):
    result = CliRunner().invoke(
        app,
        [
            "analyze",
            CENTRE,
            "--bedrooms",
            "2",
            "--min-reviews",
            "10",
            "--max-min-nights",
            "3",
            "--show",
            "5",
            "--json",
            "--no-save",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = Analysis.model_validate(json.loads(result.output))
    assert len(parsed.comps) <= 5
    assert parsed.filters.max_minimum_nights == 3
    assert all((c.minimum_nights or 1) <= 3 for c in parsed.comps)
