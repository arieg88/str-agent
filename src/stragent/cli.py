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
from stragent.analytics.geocode import GeocodingError, NominatimGeocoder
from stragent.config import Settings, get_settings
from stragent.data.fixtures import build_fixture
from stragent.data.ingest import ingest as run_ingest
from stragent.db import connect
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
    snapshot: Annotated[str, typer.Option(help="Snapshot date, e.g. 2026-09-25")],
    city: CityOption = "thessaloniki",
    from_dir: Annotated[
        Path | None, typer.Option(help="Load files from a local directory instead of downloading")
    ] = None,
    db: DbOption = None,
) -> None:
    """Download, clean and load one Inside Airbnb snapshot."""
    settings = get_settings()
    city_cfg = settings.city(city)
    with console.status(f"Loading {city_cfg.display_name} {snapshot}…"):
        con = connect(_db_path(settings, db))
        try:
            report = run_ingest(
                con,
                city.lower(),
                city_cfg,
                snapshot,
                settings.resolve(settings.paths.raw_dir),
                from_dir=from_dir,
            )
        finally:
            con.close()

    table = Table(title=f"Loaded {city_cfg.display_name} · {report.snapshot_date}")
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
        table.add_row(city, str(snap), f"{n_l:,}", f"{n_c:,}", f"{n_r:,}", source)
    console.print(table)


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
        f"within {f.radius_m} m ({result.comp_stats.active_count} active)",
    )
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
    console.print(market)

    comps = Table(title=f"Nearest comps (showing {len(result.comps)})")
    for col, justify in [
        ("Distance", "right"),
        ("Beds", "right"),
        ("Price", "right"),
        ("Reviews 12m", "right"),
        ("Rating", "right"),
        ("Est. revenue", "right"),
        ("Listing", "left"),
    ]:
        comps.add_column(col, justify=justify)  # type: ignore[arg-type]
    for c in result.comps:
        comps.add_row(
            f"{c.distance_m:.0f} m",
            "-" if c.bedrooms is None else str(c.bedrooms),
            _money(c.price, cur),
            "-" if c.number_of_reviews_ltm is None else str(c.number_of_reviews_ltm),
            "-" if c.review_scores_rating is None else f"{c.review_scores_rating:.2f}",
            "-" if c.est_revenue is None else _money(c.est_revenue, cur),
            c.url,
        )
    console.print(comps)
    a = result.revenue.assumptions
    console.print(
        f"[dim]Occupancy model: reviews ÷ {a['review_rate']:.0%} review rate × "
        f"max({a['average_stay_nights']:.0f}, minimum nights), capped at "
        f"{a['max_occupancy']:.0%}. Estimates, not bookings. {result.data_source}.[/dim]"
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
    city: CityOption = "thessaloniki",
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of tables")] = False,
    save: Annotated[bool, typer.Option(help="Save the JSON result under outputs/")] = True,
    db: DbOption = None,
) -> None:
    """Comparable listings, market stats and a revenue range for an address."""
    settings = get_settings()
    path = _db_path(settings, db)
    if not path.exists():
        console.print("[red]No database yet. Run `stragent ingest` (or `make demo`) first.[/red]")
        raise typer.Exit(1)

    city_key = city.lower()
    geocoder = NominatimGeocoder(
        settings.city(city_key),
        settings.geocoding,
        settings.resolve(settings.paths.cache_dir) / "geocode.json",
    )
    con = connect(path, read_only=True)
    try:
        result = analyze(con, settings, city_key, address, geocoder, bedrooms, room_type)
    except (GeocodingError, OutOfAreaError, NoDataError) as exc:
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


if __name__ == "__main__":
    app()
