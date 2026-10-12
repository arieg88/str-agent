import pandas as pd
import pytest

from stragent.data.clean import (
    clean_calendar,
    clean_listings,
    clean_neighbourhoods,
    clean_reviews,
    parse_bool,
    parse_price,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$85.00", 85.0),
        ("$1,234.50", 1234.5),
        ("€70", 70.0),
        (42, 42.0),
        ("", None),
        (None, None),
        (float("nan"), None),
        ("n/a", None),
    ],
)
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("t", True), ("f", False), ("TRUE", True), ("", None), (None, None)],
)
def test_parse_bool(raw, expected):
    assert parse_bool(raw) == expected


def _raw_listings() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [1, 2, 3, 3],
            "name": [" Flat A ", "Flat B", "Flat C", "Flat C v2"],
            "neighbourhood_cleansed": ["Kentro", "Kentro", "Toumpa", "Toumpa"],
            "latitude": [40.63, None, 40.62, 40.62],
            "longitude": [22.94, 22.95, 22.97, 22.97],
            "room_type": ["Entire home/apt"] * 4,
            "bedrooms": [2, 1, None, 1],
            "price": ["$85.00", "$60.00", "", "$1,200.00"],
            "host_is_superhost": ["t", "f", None, "f"],
            "number_of_reviews_ltm": [10, 2, 0, 0],
            "listing_url": ["x"] * 4,  # extra columns are ignored
        }
    )


def test_clean_listings_types_and_drops():
    df = clean_listings(_raw_listings())
    # id 2 has no latitude; id 3 is duplicated -> last row kept
    assert df["id"].tolist() == [1, 3]
    assert df.loc[0, "name"] == "Flat A"
    assert df.loc[0, "neighbourhood"] == "Kentro"
    assert df.loc[0, "price"] == 85.0
    assert df.loc[1, "price"] == 1200.0
    assert bool(df.loc[0, "host_is_superhost"]) is True
    # columns absent from the raw file are created as missing, not an error
    assert pd.isna(df.loc[0, "license"])
    assert pd.isna(df.loc[0, "estimated_revenue_l365d"])


def test_clean_calendar_blank_prices():
    raw = pd.DataFrame(
        {
            "listing_id": [1, 1],
            "date": ["2026-09-25", "2026-09-26"],
            "available": ["t", "f"],
            "price": ["", "$90.00"],
            "minimum_nights": [2, 2],
        }
    )
    df = clean_calendar(raw)
    assert df["available"].tolist() == [True, False]
    assert pd.isna(df.loc[0, "price"]) and df.loc[1, "price"] == 90.0


def test_clean_reviews_drops_reviewer_name():
    raw = pd.DataFrame(
        {
            "listing_id": [1],
            "id": [100],
            "date": ["2026-01-01"],
            "reviewer_id": [5],
            "reviewer_name": ["Someone"],
            "comments": [" Lovely "],
        }
    )
    df = clean_reviews(raw)
    assert "reviewer_name" not in df.columns
    assert df.loc[0, "comments"] == "Lovely"


def test_clean_neighbourhoods_skips_bad_features():
    geojson = {
        "features": [
            {
                "properties": {"neighbourhood": "A"},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            },
            {
                "properties": {"neighbourhood": None},
                "geometry": {"type": "Point", "coordinates": [0, 0]},
            },
            {"properties": {"neighbourhood": "B"}, "geometry": None},
        ]
    }
    df = clean_neighbourhoods(geojson)
    assert df["neighbourhood"].tolist() == ["A"]
