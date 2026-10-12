"""Download an Inside Airbnb snapshot and load it into DuckDB.

Idempotent: loading the same city + snapshot twice replaces the earlier rows.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import httpx
import pandas as pd

from stragent.config import CityConfig
from stragent.data import clean

# remote path (relative to the snapshot base URL) -> local file name
FILES = {
    "data/listings.csv.gz": "listings.csv.gz",
    "data/calendar.csv.gz": "calendar.csv.gz",
    "data/reviews.csv.gz": "reviews.csv.gz",
    "visualisations/neighbourhoods.geojson": "neighbourhoods.geojson",
}


@dataclass
class IngestReport:
    city: str
    snapshot_date: str
    source: str
    rows: dict[str, int]
    dropped_listings: int


def download_snapshot(
    city: CityConfig, snapshot: str, dest: Path, client: httpx.Client | None = None
) -> Path:
    """Download the four files for one snapshot into ``dest``. Skips files already present."""
    dest.mkdir(parents=True, exist_ok=True)
    base = city.snapshot_url(snapshot).rstrip("/")
    owns_client = client is None
    client = client or httpx.Client(timeout=120, follow_redirects=True)
    try:
        for remote, local in FILES.items():
            target = dest / local
            if target.exists() and target.stat().st_size > 0:
                continue
            tmp = target.with_suffix(target.suffix + ".part")
            url = f"{base}/{remote}"
            with client.stream("GET", url) as resp:
                if resp.status_code == 404:
                    raise FileNotFoundError(
                        f"{url} not found. Check the snapshot date and base_url on "
                        "https://insideairbnb.com/get-the-data/"
                    )
                resp.raise_for_status()
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
            tmp.replace(target)
    finally:
        if owns_client:
            client.close()
    return dest


def read_snapshot_dir(src: Path) -> dict[str, pd.DataFrame]:
    """Read and clean the four files from a local directory."""
    missing = [name for name in FILES.values() if not (src / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing files in {src}: {', '.join(missing)}")

    raw_listings = pd.read_csv(src / "listings.csv.gz", low_memory=False)
    raw_calendar = pd.read_csv(src / "calendar.csv.gz", low_memory=False)
    raw_reviews = pd.read_csv(src / "reviews.csv.gz", low_memory=False)
    geojson = json.loads((src / "neighbourhoods.geojson").read_text(encoding="utf-8"))

    listings = clean.clean_listings(raw_listings)
    frames = {
        "listings": listings,
        "calendar": clean.clean_calendar(raw_calendar),
        "reviews": clean.clean_reviews(raw_reviews),
        "neighbourhoods": clean.clean_neighbourhoods(geojson),
    }
    frames["listings"].attrs["dropped"] = len(raw_listings) - len(listings)
    return frames


def load_frames(
    con: duckdb.DuckDBPyConnection,
    city: str,
    snapshot: str,
    frames: dict[str, pd.DataFrame],
    source: str,
) -> IngestReport:
    snap = date.fromisoformat(snapshot)
    con.execute("BEGIN TRANSACTION")
    try:
        rows: dict[str, int] = {}
        for table, df in frames.items():
            con.execute(f"DELETE FROM {table} WHERE city = ? AND snapshot_date = ?", [city, snap])
            staged = df.copy()
            staged.insert(0, "snapshot_date", snap)
            staged.insert(0, "city", city)
            con.register("staged", staged)
            cols = ", ".join(staged.columns)
            con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM staged")
            con.unregister("staged")
            rows[table] = len(staged)

        con.execute("DELETE FROM snapshots WHERE city = ? AND snapshot_date = ?", [city, snap])
        con.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                city,
                snap,
                datetime.now(UTC).replace(tzinfo=None),
                source,
                rows["listings"],
                rows["calendar"],
                rows["reviews"],
                rows["neighbourhoods"],
            ],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    return IngestReport(
        city=city,
        snapshot_date=snapshot,
        source=source,
        rows=rows,
        dropped_listings=int(frames["listings"].attrs.get("dropped", 0)),
    )


def ingest(
    con: duckdb.DuckDBPyConnection,
    city_key: str,
    city: CityConfig,
    snapshot: str,
    raw_root: Path,
    from_dir: Path | None = None,
) -> IngestReport:
    """Download (unless ``from_dir`` is given), clean and load one snapshot."""
    date.fromisoformat(snapshot)  # fail fast on a bad date
    target = raw_root / city_key / snapshot
    if from_dir is not None:
        if from_dir.resolve() != target.resolve():
            target.mkdir(parents=True, exist_ok=True)
            for name in FILES.values():
                if (from_dir / name).exists():
                    shutil.copy2(from_dir / name, target / name)
        source = f"local:{from_dir}"
    else:
        download_snapshot(city, snapshot, target)
        source = city.snapshot_url(snapshot)

    frames = read_snapshot_dir(target)
    return load_frames(con, city_key, snapshot, frames, source)
