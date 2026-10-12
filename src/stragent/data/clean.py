"""Pure cleaning functions for Inside Airbnb files.

Each function takes the raw DataFrame as read from the CSV and returns a frame whose columns
match the tables in ``stragent.db``. No I/O here, so everything is easy to unit test.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

LISTING_COLUMNS = [
    "id",
    "name",
    "host_id",
    "host_is_superhost",
    "calculated_host_listings_count",
    "neighbourhood",
    "latitude",
    "longitude",
    "property_type",
    "room_type",
    "accommodates",
    "bedrooms",
    "beds",
    "price",
    "minimum_nights",
    "availability_365",
    "number_of_reviews",
    "number_of_reviews_ltm",
    "reviews_per_month",
    "review_scores_rating",
    "first_review",
    "last_review",
    "host_since",
    "license",
    "instant_bookable",
    "estimated_occupancy_l365d",
    "estimated_revenue_l365d",
]

INT_COLUMNS = [
    "host_id",
    "calculated_host_listings_count",
    "accommodates",
    "bedrooms",
    "beds",
    "minimum_nights",
    "availability_365",
    "number_of_reviews",
    "number_of_reviews_ltm",
    "estimated_occupancy_l365d",
]

FLOAT_COLUMNS = [
    "latitude",
    "longitude",
    "reviews_per_month",
    "review_scores_rating",
    "estimated_revenue_l365d",
]

_PRICE_RE = re.compile(r"[^0-9.\-]")


def parse_price(value: Any) -> float | None:
    """'$1,234.00' -> 1234.0; blanks and garbage -> None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = _PRICE_RE.sub("", str(value))
    if text in {"", ".", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_bool(value: Any) -> bool | None:
    """Inside Airbnb uses 't' / 'f'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip().lower()
    if text in {"t", "true", "1", "yes"}:
        return True
    if text in {"f", "false", "0", "no"}:
        return False
    return None


def _to_int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").round().astype("Int64")


def clean_listings(raw: pd.DataFrame) -> pd.DataFrame:
    """Keep the columns we use, fix types, drop rows without coordinates."""
    df = raw.copy()

    # Older snapshots call it neighbourhood_cleansed; newer ones too. Fall back if missing.
    if "neighbourhood_cleansed" in df.columns:
        df["neighbourhood"] = df["neighbourhood_cleansed"]

    # Some snapshots have no bedrooms value but describe it in the name or bathrooms_text;
    # we keep it simple and leave those as missing.
    for col in LISTING_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    df = df[LISTING_COLUMNS].copy()
    df["id"] = pd.to_numeric(df["id"], errors="coerce").astype("Int64")
    df["price"] = df["price"].map(parse_price).astype("Float64")
    for col in ("host_is_superhost", "instant_bookable"):
        df[col] = df[col].map(parse_bool).astype("boolean")
    for col in INT_COLUMNS:
        df[col] = _to_int(df[col])
    for col in FLOAT_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Float64")
    df["estimated_revenue_l365d"] = df["estimated_revenue_l365d"].astype("Float64")
    for col in ("first_review", "last_review", "host_since"):
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    for col in ("name", "neighbourhood", "property_type", "room_type", "license"):
        df[col] = df[col].astype("string").str.strip()

    df = df.dropna(subset=["id", "latitude", "longitude"])
    df = df.drop_duplicates(subset=["id"], keep="last")
    return df.reset_index(drop=True)


def clean_calendar(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "listing_id": pd.to_numeric(raw["listing_id"], errors="coerce").astype("Int64"),
            "date": pd.to_datetime(raw["date"], errors="coerce").dt.date,
            "available": raw["available"].map(parse_bool).astype("boolean"),
            # Recent snapshots often leave calendar prices blank; that's expected.
            "price": (raw["price"].map(parse_price) if "price" in raw else pd.NA),
            "minimum_nights": _to_int(raw.get("minimum_nights", pd.Series(pd.NA, index=raw.index))),
        }
    )
    df["price"] = df["price"].astype("Float64")
    return df.dropna(subset=["listing_id", "date"]).reset_index(drop=True)


def clean_reviews(raw: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "listing_id": pd.to_numeric(raw["listing_id"], errors="coerce").astype("Int64"),
            "id": pd.to_numeric(raw["id"], errors="coerce").astype("Int64"),
            "date": pd.to_datetime(raw["date"], errors="coerce").dt.date,
            "reviewer_id": pd.to_numeric(
                raw.get("reviewer_id", pd.Series(pd.NA, index=raw.index)), errors="coerce"
            ).astype("Int64"),
            "comments": raw["comments"].astype("string").str.strip(),
        }
    )
    # Reviewer names are personal data we don't need, so they are never loaded.
    return df.dropna(subset=["listing_id", "id"]).reset_index(drop=True)


def clean_neighbourhoods(geojson: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for feature in geojson.get("features", []):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry")
        name = props.get("neighbourhood")
        if not name or not geometry:
            continue
        rows.append(
            {
                "neighbourhood": str(name).strip(),
                "neighbourhood_group": props.get("neighbourhood_group"),
                "geometry_geojson": json.dumps(geometry),
            }
        )
    return pd.DataFrame(rows, columns=["neighbourhood", "neighbourhood_group", "geometry_geojson"])
