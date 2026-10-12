"""DuckDB connection and schema.

Stages 0-1 use DuckDB (a single local file, no server). Stage 4 moves to Postgres with
PostGIS and pgvector; keep SQL here and in the analytics modules simple so it ports easily.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    city            VARCHAR NOT NULL,
    snapshot_date   DATE    NOT NULL,
    loaded_at       TIMESTAMP NOT NULL,
    source          VARCHAR NOT NULL,
    listings_rows   INTEGER,
    calendar_rows   BIGINT,
    reviews_rows    BIGINT,
    neighbourhoods_rows INTEGER,
    PRIMARY KEY (city, snapshot_date)
);

CREATE TABLE IF NOT EXISTS listings (
    city            VARCHAR NOT NULL,
    snapshot_date   DATE    NOT NULL,
    id              BIGINT  NOT NULL,
    name            VARCHAR,
    host_id         BIGINT,
    host_is_superhost BOOLEAN,
    calculated_host_listings_count INTEGER,
    neighbourhood   VARCHAR,
    latitude        DOUBLE  NOT NULL,
    longitude       DOUBLE  NOT NULL,
    property_type   VARCHAR,
    room_type       VARCHAR,
    accommodates    INTEGER,
    bedrooms        INTEGER,
    beds            INTEGER,
    price           DOUBLE,
    minimum_nights  INTEGER,
    availability_365 INTEGER,
    number_of_reviews INTEGER,
    number_of_reviews_ltm INTEGER,
    reviews_per_month DOUBLE,
    review_scores_rating DOUBLE,
    first_review    DATE,
    last_review     DATE,
    host_since      DATE,
    license         VARCHAR,
    instant_bookable BOOLEAN,
    estimated_occupancy_l365d INTEGER,
    estimated_revenue_l365d DOUBLE,
    PRIMARY KEY (city, snapshot_date, id)
);

CREATE TABLE IF NOT EXISTS calendar (
    city            VARCHAR NOT NULL,
    snapshot_date   DATE    NOT NULL,
    listing_id      BIGINT  NOT NULL,
    date            DATE    NOT NULL,
    available       BOOLEAN,
    price           DOUBLE,
    minimum_nights  INTEGER
);

CREATE TABLE IF NOT EXISTS reviews (
    city            VARCHAR NOT NULL,
    snapshot_date   DATE    NOT NULL,
    listing_id      BIGINT  NOT NULL,
    id              BIGINT  NOT NULL,
    date            DATE,
    reviewer_id     BIGINT,
    comments        VARCHAR
);

CREATE TABLE IF NOT EXISTS neighbourhoods (
    city            VARCHAR NOT NULL,
    snapshot_date   DATE    NOT NULL,
    neighbourhood   VARCHAR NOT NULL,
    neighbourhood_group VARCHAR,
    geometry_geojson VARCHAR NOT NULL
);
"""

# Columns added after the first release. CREATE TABLE IF NOT EXISTS does not add columns to an
# existing table, so these run on every writable connect. Re-ingest a snapshot to fill them
# (raw files are cached, so nothing is downloaded again).
# Read-only connections (analyze, the API) can't run these, so readers must also tolerate the
# columns being absent: see LATER_LISTING_COLUMNS.
LATER_LISTING_COLUMNS = ("first_review", "host_since")
MIGRATIONS = tuple(
    f"ALTER TABLE listings ADD COLUMN IF NOT EXISTS {col} DATE" for col in LATER_LISTING_COLUMNS
)

TABLES = ("listings", "calendar", "reviews", "neighbourhoods")


def connect(path: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
        for statement in MIGRATIONS:
            con.execute(statement)
    return con


def latest_snapshot(con: duckdb.DuckDBPyConnection, city: str) -> str | None:
    row = con.execute("SELECT max(snapshot_date) FROM snapshots WHERE city = ?", [city]).fetchone()
    return None if row is None or row[0] is None else row[0].isoformat()


def loaded_snapshots(con: duckdb.DuckDBPyConnection, city: str) -> list[str]:
    rows = con.execute(
        "SELECT snapshot_date FROM snapshots WHERE city = ? ORDER BY snapshot_date DESC", [city]
    ).fetchall()
    return [r[0].isoformat() for r in rows]


def delete_snapshot(con: duckdb.DuckDBPyConnection, city: str, snapshot: str) -> dict[str, int]:
    """Remove one snapshot from every table. Returns rows deleted per table."""
    deleted: dict[str, int] = {}
    con.execute("BEGIN TRANSACTION")
    try:
        for table in (*TABLES, "snapshots"):
            row = con.execute(
                f"SELECT count(*) FROM {table} WHERE city = ? AND snapshot_date = CAST(? AS DATE)",
                [city, snapshot],
            ).fetchone()
            deleted[table] = int(row[0]) if row else 0
            con.execute(
                f"DELETE FROM {table} WHERE city = ? AND snapshot_date = CAST(? AS DATE)",
                [city, snapshot],
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return deleted
