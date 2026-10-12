"""End to end on a synthetic snapshot: fixture files -> ingest -> analyze -> CLI."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conftest import ATHENS, CENTRE, KALAMARIA, SNAPSHOT, CoordsOnly
from stragent.analysis import NoDataError, OutOfAreaError, analyze
from stragent.cli import app
from stragent.data.fixtures import build_fixture
from stragent.data.ingest import ingest
from stragent.db import connect
from stragent.models import Analysis

pytestmark = pytest.mark.integration


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


def test_old_database_gets_new_columns(tmp_path):
    """A database created before first_review/host_since existed is migrated on connect."""
    path = tmp_path / "old.duckdb"
    con = connect(path)
    con.execute("ALTER TABLE listings DROP COLUMN first_review")
    con.execute("ALTER TABLE listings DROP COLUMN host_since")
    con.close()
    con = connect(path)
    cols = {r[0] for r in con.execute("DESCRIBE listings").fetchall()}
    con.close()
    assert {"first_review", "host_since"} <= cols


def test_old_database_read_only_still_analyzes(tmp_path, settings):
    """analyze and the API open the database read-only, so they can't migrate it."""
    src = build_fixture(tmp_path / "fx", n_listings=300, snapshot=SNAPSHOT)
    path = tmp_path / "old.duckdb"
    con = connect(path)
    ingest(
        con, "thessaloniki", settings.city("thessaloniki"), SNAPSHOT, tmp_path / "raw", from_dir=src
    )
    con.execute("ALTER TABLE listings DROP COLUMN first_review")
    con.execute("ALTER TABLE listings DROP COLUMN host_since")
    con.close()

    con = connect(path, read_only=True)
    result = analyze(con, settings, "thessaloniki", CENTRE, CoordsOnly(), bedrooms=2)
    con.close()
    assert result.comps and all(c.active_since is None for c in result.comps)
    assert any("Active since" in w for w in result.warnings)
    assert result.market.new_listings_share is None


def test_drop_snapshot(tmp_path):
    src = build_fixture(tmp_path / "fx", n_listings=200, snapshot="2026-06-20")
    path = tmp_path / "drop.duckdb"
    runner = CliRunner()
    for snap in ("2026-06-20", "2026-09-22"):
        args = ["ingest", "--snapshot", snap, "--from-dir", str(src), "--db", str(path)]
        assert runner.invoke(app, args).exit_code == 0
    result = runner.invoke(app, ["drop", "--snapshot", "2026-09-22", "--yes", "--db", str(path)])
    assert result.exit_code == 0, result.output
    con = connect(path, read_only=True)
    left = con.execute("SELECT DISTINCT snapshot_date::VARCHAR FROM listings").fetchall()
    snaps = con.execute("SELECT snapshot_date::VARCHAR FROM snapshots").fetchall()
    cal = con.execute(
        "SELECT count(*) FROM calendar WHERE snapshot_date = DATE '2026-09-22'"
    ).fetchone()
    con.close()
    assert left == [("2026-06-20",)] and snaps == [("2026-06-20",)] and cal == (0,)
    missing = runner.invoke(app, ["drop", "--snapshot", "2020-01-01", "--yes", "--db", str(path)])
    assert missing.exit_code == 1


def test_fixed_radius(db, settings):
    con = connect(db, read_only=True)
    small = analyze(con, settings, "thessaloniki", CENTRE, CoordsOnly(), bedrooms=2, radius_m=200)
    large = analyze(con, settings, "thessaloniki", CENTRE, CoordsOnly(), bedrooms=2, radius_m=2000)
    con.close()
    assert small.filters.radius_m == 200 and small.filters.radius_mode == "fixed"
    assert not small.filters.radius_widened  # a fixed radius is never widened
    assert all(c.distance_m <= 200 for c in small.comps)
    assert large.comp_stats.count > small.comp_stats.count
    assert large.market.total_listings > small.market.total_listings
    if small.comp_stats.count < settings.comps.min_comps:
        assert any("larger radius" in w for w in small.warnings)


def test_radius_out_of_bounds(db, settings):
    from stragent.analytics.comps import RadiusError

    con = connect(db, read_only=True)
    with pytest.raises(RadiusError):
        analyze(con, settings, "thessaloniki", CENTRE, CoordsOnly(), radius_m=50_000)
    con.close()


def test_total_reviews_reported(db, settings):
    result = _run(db, settings, CENTRE)
    assert all(c.number_of_reviews is not None for c in result.comps)
    assert all((c.number_of_reviews or 0) >= (c.number_of_reviews_ltm or 0) for c in result.comps)


def test_cli_radius(db):
    result = CliRunner().invoke(
        app, ["analyze", CENTRE, "--radius", "300", "--json", "--no-save", "--db", str(db)]
    )
    assert result.exit_code == 0, result.output
    parsed = Analysis.model_validate(json.loads(result.output))
    assert parsed.filters.radius_m == 300 and parsed.filters.radius_mode == "fixed"
    bad = CliRunner().invoke(
        app, ["analyze", CENTRE, "--radius", "10", "--no-save", "--db", str(db)]
    )
    assert bad.exit_code == 2 and "between" in bad.output
