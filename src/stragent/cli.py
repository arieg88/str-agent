"""Command-line interface.

stragent fixtures                      # build a synthetic snapshot for offline use
stragent ingest --snapshot 2026-09-25  # download + load the real Thessaloniki data
stragent snapshots                     # what's loaded
stragent analyze "Egnatia 120" --bedrooms 2
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from stragent.analysis import NoDataError, OutOfAreaError, analyze
from stragent.analytics.comps import CompQuality, RadiusError
from stragent.analytics.geocode import GeocodingError, NominatimGeocoder
from stragent.config import Settings, get_settings
from stragent.data.fixtures import build_fixture
from stragent.data.ingest import IngestReport
from stragent.data.ingest import ingest as run_ingest
from stragent.db import connect, delete_snapshot, loaded_snapshots
from stragent.models import Analysis, ValueRange

app = typer.Typer(
    help="Short-term-rental analyst built on Inside Airbnb data.", no_args_is_help=True
)
console = Console()

DbOption = Annotated[Path | None, typer.Option("--db", help="DuckDB file (default from config)")]
CityOption = Annotated[str, typer.Option("--city", help="City key from config/settings.toml")]


def _db_path(settings: Settings, db: Path | None) -> Path:
    return db or settings.resolve(settings.paths.database)


@app.command()
def fixtures(
    out: Annotated[Path, typer.Option(help="Output directory")] = Path(
        "data/fixtures/thessaloniki"
    ),
    listings: Annotated[int, typer.Option(help="Number of synthetic listings")] = 1500,
    snapshot: Annotated[str, typer.Option(help="Snapshot date to stamp")] = "2026-09-25",
) -> None:
    """Generate a synthetic snapshot in Inside Airbnb's file format."""
    path = build_fixture(out, n_listings=listings, snapshot=snapshot)
    console.print(f"[green]Synthetic snapshot written to {path}[/green]")
    console.print(f"Load it with: stragent ingest --from-dir {path} --snapshot {snapshot}")


@app.command()
def ingest(
    snapshot: Annotated[
        list[str],
        typer.Option(help="Snapshot date, e.g. 2026-09-22. Repeat to load several."),
    ],
    city: CityOption = "thessaloniki",
    from_dir: Annotated[
        Path | None, typer.Option(help="Load files from a local directory instead of downloading")
    ] = None,
    db: DbOption = None,
) -> None:
    """Download, clean and load one or more Inside Airbnb snapshots."""
    settings = get_settings()
    city_cfg = settings.city(city)
    if from_dir is not None and len(snapshot) > 1:
        console.print("[red]--from-dir loads one snapshot at a time.[/red]")
        raise typer.Exit(1)

    failures: list[str] = []
    con = connect(_db_path(settings, db))
    try:
        for snap in snapshot:
            try:
                with console.status(f"Loading {city_cfg.display_name} {snap}…"):
                    report = run_ingest(
                        con,
                        city.lower(),
                        city_cfg,
                        snap,
                        settings.resolve(settings.paths.raw_dir),
                        from_dir=from_dir,
                    )
            except Exception as exc:  # keep going with the other snapshots
                console.print(f"[red]{snap}: {exc}[/red]")
                failures.append(snap)
                continue
            _print_report(city_cfg.display_name, report)
    finally:
        con.close()

    if failures:
        console.print(f"[red]Failed: {', '.join(failures)}[/red]")
        raise typer.Exit(1)


def _print_report(city_name: str, report: IngestReport) -> None:
    table = Table(title=f"Loaded {city_name} · {report.snapshot_date}")
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    for name, n in report.rows.items():
        table.add_row(name, f"{n:,}")
    console.print(table)
    if report.dropped_listings:
        console.print(
            f"[yellow]Dropped {report.dropped_listings} listings with no coordinates.[/yellow]"
        )
    console.print(f"Source: {report.source}")


@app.command()
def snapshots(db: DbOption = None) -> None:
    """List loaded snapshots."""
    settings = get_settings()
    path = _db_path(settings, db)
    if not path.exists():
        console.print("[yellow]No database yet. Run `stragent ingest` first.[/yellow]")
        raise typer.Exit(1)
    con = connect(path, read_only=True)
    rows = con.execute(
        "SELECT city, snapshot_date, listings_rows, calendar_rows, reviews_rows, source "
        "FROM snapshots ORDER BY city, snapshot_date DESC"
    ).fetchall()
    con.close()
    table = Table(title="Loaded snapshots")
    for col in ("City", "Snapshot", "Listings", "Calendar", "Reviews", "Source"):
        table.add_column(col)
    for city, snap, n_l, n_c, n_r, source in rows:
        label = f"{source} [yellow](synthetic)[/yellow]" if "fixtures" in source else source
        table.add_row(city, str(snap), f"{n_l:,}", f"{n_c:,}", f"{n_r:,}", label)
    console.print(table)


@app.command()
def drop(
    snapshot: Annotated[str, typer.Option(help="Snapshot date to remove, e.g. 2026-09-25")],
    city: CityOption = "thessaloniki",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation")] = False,
    db: DbOption = None,
) -> None:
    """Remove one snapshot from the database (raw downloaded files are kept)."""
    settings = get_settings()
    path = _db_path(settings, db)
    if not path.exists():
        console.print("[yellow]No database yet.[/yellow]")
        raise typer.Exit(1)
    con = connect(path)
    try:
        loaded = loaded_snapshots(con, city.lower())
        if snapshot not in loaded:
            listed = ", ".join(loaded) or "none"
            console.print(f"[red]Snapshot {snapshot} is not loaded. Loaded: {listed}[/red]")
            raise typer.Exit(1)
        if not yes:
            typer.confirm(f"Remove {city} snapshot {snapshot} from {path}?", abort=True)
        deleted = delete_snapshot(con, city.lower(), snapshot)
    finally:
        con.close()
    console.print(
        f"Removed {snapshot}: {deleted['listings']:,} listings, "
        f"{deleted['calendar']:,} calendar rows, {deleted['reviews']:,} reviews."
    )


def _money(value: float, currency: str) -> str:
    symbol = "€" if currency == "EUR" else f"{currency} "
    return f"{symbol}{value:,.0f}"


def _range(r: ValueRange | None, fmt: Callable[[float], str]) -> str:
    if r is None:
        return "n/a"
    return f"{fmt(r.median)}  ({fmt(r.p25)} – {fmt(r.p75)})"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render(result: Analysis) -> None:
    cur = result.currency
    loc = result.location
    header = f"[bold]{loc.query}[/bold]\n{loc.latitude:.5f}, {loc.longitude:.5f}"
    if loc.display_name:
        header += f"\n{loc.display_name}"
    header += f"\nNeighbourhood: {loc.neighbourhood or 'unknown'}"
    header += f"\nData: Inside Airbnb {result.city} snapshot {result.snapshot_date}"
    console.print(Panel(header, title="Location", expand=False))

    for warning in result.warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")

    f = result.filters
    beds = "any" if f.bedrooms is None else f"{f.bedrooms} ±{f.bedroom_tolerance}"
    summary = Table(show_header=False, title="Estimate (median, with 25th–75th percentile)")
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row(
        "Comps",
        f"{result.comp_stats.count} × {f.room_type}, bedrooms {beds}, "
        f"within {f.radius_m} m{' (auto)' if f.radius_mode == 'auto' else ''} "
        f"({result.comp_stats.active_count} active)",
    )
    extra = CompQuality(
        min_reviews_ltm=f.min_reviews_ltm,
        min_rating=f.min_rating,
        max_minimum_nights=f.max_minimum_nights,
        superhost_only=f.superhost_only,
    ).describe()
    if extra:
        summary.add_row("Comp filters", ", ".join(extra))
    summary.add_row(
        "Nightly price", _range(result.comp_stats.nightly_price, lambda v: _money(v, cur))
    )
    summary.add_row("Occupancy", _range(result.revenue.occupancy, lambda v: f"{v:.0%}"))
    summary.add_row("Nights / year", _range(result.revenue.occupied_nights, lambda v: f"{v:.0f}"))
    summary.add_row(
        "Revenue / year", _range(result.revenue.annual_revenue, lambda v: _money(v, cur))
    )
    if result.revenue.inside_airbnb_revenue:
        summary.add_row(
            "Inside Airbnb's estimate",
            _range(result.revenue.inside_airbnb_revenue, lambda v: _money(v, cur)),
        )
    rating = result.comp_stats.review_score_median
    summary.add_row("Review score", "n/a" if rating is None else f"{rating:.2f}")
    console.print(summary)

    m = result.market
    market = Table(show_header=False, title=f"Market within {m.radius_m} m (all listing types)")
    market.add_column(style="bold")
    market.add_column()
    market.add_row("Listings", str(m.total_listings))
    market.add_row("Entire homes", _pct(m.entire_home_share))
    market.add_row("Run by multi-listing hosts", _pct(m.multi_listing_host_share))
    market.add_row("Held by top 3 hosts", _pct(m.top3_hosts_share))
    market.add_row("Show a licence number", _pct(m.licensed_share))
    market.add_row("First reviewed in last 12 months", _pct(m.new_listings_share))
    console.print(market)

    comps = Table(title=f"Nearest comps (showing {len(result.comps)})")
    for col, justify in [
        ("Distance", "right"),
        ("Beds", "right"),
        ("Price", "right"),
        ("Reviews 12m", "right"),
        ("Reviews total", "right"),
        ("Rating", "right"),
        ("Est. revenue", "right"),
        ("Active since", "right"),
        ("Listing", "left"),
    ]:
        comps.add_column(col, justify=justify)  # type: ignore[arg-type]
    for c in result.comps:
        comps.add_row(
            f"{c.distance_m:.0f} m",
            "-" if c.bedrooms is None else str(c.bedrooms),
            _money(c.price, cur),
            "-" if c.number_of_reviews_ltm is None else str(c.number_of_reviews_ltm),
            "-" if c.number_of_reviews is None else str(c.number_of_reviews),
            "-" if c.review_scores_rating is None else f"{c.review_scores_rating:.2f}",
            "-" if c.est_revenue is None else _money(c.est_revenue, cur),
            "-" if c.active_since is None else c.active_since.strftime("%Y-%m"),
            c.url,
        )
    console.print(comps)
    a = result.revenue.assumptions
    console.print(
        f"[dim]Occupancy model: reviews ÷ {a['review_rate']:.0%} review rate × "
        f"max({a['average_stay_nights']:.0f}, minimum nights), capped at "
        f"{a['max_occupancy']:.0%}. Estimates, not bookings. "
        f"'Active since' = first review date. {result.data_source}.[/dim]"
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "location"


@app.command(name="analyze")
def analyze_cmd(
    address: Annotated[str, typer.Argument(help="Street address, or 'lat, lon'")],
    bedrooms: Annotated[int | None, typer.Option(help="Bedrooms of the flat")] = None,
    room_type: Annotated[
        str | None, typer.Option(help="Room type to compare against (default: Entire home/apt)")
    ] = None,
    min_reviews: Annotated[
        int, typer.Option(help="Only comps with at least this many reviews in the last 12 months")
    ] = 0,
    min_rating: Annotated[
        float | None, typer.Option(help="Only comps with at least this review score (0-5)")
    ] = None,
    max_min_nights: Annotated[
        int | None,
        typer.Option(help="Only comps whose minimum stay is at most this (drops monthly rentals)"),
    ] = None,
    superhost_only: Annotated[bool, typer.Option(help="Only comps run by superhosts")] = False,
    radius: Annotated[
        int | None,
        typer.Option(help="Search radius in metres (default: auto, 500 m widened to 1.5 km)"),
    ] = None,
    show: Annotated[int, typer.Option(help="How many comps to list in the table")] = 15,
    snapshot: Annotated[
        str | None, typer.Option(help="Snapshot date to use (default: latest loaded)")
    ] = None,
    city: CityOption = "thessaloniki",
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of tables")] = False,
    save: Annotated[bool, typer.Option(help="Save the JSON result under outputs/")] = True,
    db: DbOption = None,
) -> None:
    """Comparable listings, market stats and a revenue range for an address."""
    quality = CompQuality(
        min_reviews_ltm=min_reviews,
        min_rating=min_rating,
        max_minimum_nights=max_min_nights,
        superhost_only=superhost_only,
    )
    settings = get_settings()
    path = _db_path(settings, db)
    if not path.exists():
        console.print("[red]No database yet. Run `stragent ingest` (or `make demo`) first.[/red]")
        raise typer.Exit(1)

    city_key = city.lower()
    geocoder = NominatimGeocoder(
        settings.city(city_key),
        settings.geocoding,
        settings.resolve(settings.paths.cache_dir) / f"geocode-{city_key}.json",
    )
    con = connect(path, read_only=True)
    try:
        result = analyze(
            con,
            settings,
            city_key,
            address,
            geocoder,
            bedrooms,
            room_type,
            max_comps_listed=show,
            quality=quality,
            snapshot=snapshot,
            radius_m=radius,
        )
    except (GeocodingError, OutOfAreaError, NoDataError, RadiusError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    finally:
        con.close()

    payload = result.model_dump_json(indent=2)
    if save:
        out_dir = settings.resolve(settings.paths.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{date.today().isoformat()}-{_slug(address)}.json"
        out_file.write_text(payload, encoding="utf-8")
    if as_json:
        print(payload)
    else:
        render(result)
        if save:
            console.print(f"[dim]Saved {out_file}[/dim]")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to bind; 0.0.0.0 inside Docker")] = (
        "127.0.0.1"
    ),
    port: Annotated[int, typer.Option(help="Port")] = 8000,
    reload: Annotated[bool, typer.Option(help="Restart on code changes (development)")] = False,
    db: DbOption = None,
) -> None:
    """Run the HTTP API and map page (http://HOST:PORT/, docs at /docs)."""
    import logging
    import os

    import uvicorn

    if db is not None:
        os.environ["STRAGENT_DB_PATH"] = str(db.resolve())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    console.print(f"Map page: http://{host}:{port}/   API docs: http://{host}:{port}/docs")
    uvicorn.run("stragent.api.app:create_app", factory=True, host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
