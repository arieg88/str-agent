"""Generate a small synthetic snapshot in the exact Inside Airbnb file format.

Used for tests and for trying the tool without downloading real data. Every neighbourhood
name ends in "(synthetic)" so fixture output can never be mistaken for real market data.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# name: (west, south, east, north, share of listings, price factor)
NEIGHBOURHOODS: dict[str, tuple[float, float, float, float, float, float]] = {
    "Kentro (synthetic)": (22.925, 40.620, 22.965, 40.645, 0.55, 1.15),
    "Ano Poli (synthetic)": (22.925, 40.645, 22.975, 40.665, 0.15, 0.95),
    "Faliro (synthetic)": (22.935, 40.600, 22.965, 40.620, 0.15, 1.05),
    "Toumpa (synthetic)": (22.965, 40.600, 23.000, 40.645, 0.12, 0.85),
    # Deliberately sparse so the comps search has to widen its radius here.
    "Kalamaria (synthetic)": (22.935, 40.570, 22.985, 40.600, 0.03, 1.00),
}

ROOM_TYPES = ["Entire home/apt", "Private room", "Hotel room", "Shared room"]
ROOM_WEIGHTS = [0.82, 0.13, 0.03, 0.02]

REVIEW_TEMPLATES = [
    "Great location, walking distance to the waterfront. Clean flat.",
    "The apartment was spotless and the host was very helpful.",
    "A bit noisy at night because of the bars nearby, otherwise perfect.",
    "Easy to reach from the airport by bus. Would stay again.",
    "Πολύ καθαρό διαμέρισμα, εξαιρετική τοποθεσία!",
    "Ο οικοδεσπότης ήταν πολύ εξυπηρετικός. Το προτείνω.",
    "Parking was hard to find but the flat itself was lovely.",
    "Nice view of the sea from the balcony.",
]


def _polygon(w: float, s: float, e: float, n: float) -> dict:
    return {
        "type": "MultiPolygon",
        "coordinates": [[[[w, s], [e, s], [e, n], [w, n], [w, s]]]],
    }


def build_fixture(
    out_dir: Path,
    n_listings: int = 1500,
    snapshot: str = "2026-09-25",
    seed: int = 42,
) -> Path:
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    snap = date.fromisoformat(snapshot)

    names = list(NEIGHBOURHOODS)
    shares = np.array([NEIGHBOURHOODS[n][4] for n in names])
    hood_idx = rng.choice(len(names), size=n_listings, p=shares / shares.sum())

    # ~30% of listings belong to multi-listing hosts
    n_hosts = int(n_listings * 0.75)
    host_ids = rng.integers(1_000, 1_000 + n_hosts, size=n_listings)
    pro_hosts = rng.choice(n_hosts, size=n_hosts // 25, replace=False) + 1_000
    pro_mask = rng.random(n_listings) < 0.3
    host_ids[pro_mask] = rng.choice(pro_hosts, size=pro_mask.sum())
    host_counts = pd.Series(host_ids).map(pd.Series(host_ids).value_counts())

    rows = []
    for i in range(n_listings):
        name = names[hood_idx[i]]
        w, s, e, n, _, factor = NEIGHBOURHOODS[name]
        lat = rng.uniform(s, n)
        lon = rng.uniform(w, e)
        room = rng.choice(ROOM_TYPES, p=ROOM_WEIGHTS)
        bedrooms = int(rng.choice([0, 1, 1, 1, 2, 2, 3, 4])) if room == "Entire home/apt" else 1
        base = 40 + 20 * bedrooms if room == "Entire home/apt" else 30
        price = round(base * factor * rng.lognormal(0, 0.25), 0)
        min_nights = int(rng.choice([1, 1, 2, 2, 2, 3, 5, 30]))
        reviews_ltm = int(rng.poisson(14 if room == "Entire home/apt" else 8))
        total_reviews = reviews_ltm + int(rng.poisson(25))
        est_nights = min(reviews_ltm * 2 * max(3, min_nights), 255)
        rows.append(
            {
                "id": 10_000_000 + i,
                "listing_url": f"https://www.airbnb.com/rooms/{10_000_000 + i}",
                "name": f"Synthetic {room.lower()} #{i}",
                "host_id": int(host_ids[i]),
                "host_name": "Synthetic host",
                "host_is_superhost": "t" if rng.random() < 0.35 else "f",
                "calculated_host_listings_count": int(host_counts[i]),
                "neighbourhood_cleansed": name,
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "property_type": "Entire rental unit" if room == "Entire home/apt" else room,
                "room_type": room,
                "accommodates": bedrooms * 2 + 2,
                "bathrooms_text": "1 bath",
                "bedrooms": bedrooms,
                "beds": max(1, bedrooms),
                "price": f"${price:,.2f}",
                "minimum_nights": min_nights,
                "maximum_nights": 365,
                "availability_365": int(rng.integers(0, 366)),
                "number_of_reviews": total_reviews,
                "number_of_reviews_ltm": reviews_ltm,
                "first_review": (snap - timedelta(days=int(rng.integers(30, 3000)))).isoformat(),
                "host_since": (snap - timedelta(days=int(rng.integers(3000, 4500)))).isoformat(),
                "last_review": (snap - timedelta(days=int(rng.integers(1, 200)))).isoformat(),
                "review_scores_rating": round(float(np.clip(rng.normal(4.75, 0.2), 3.0, 5.0)), 2),
                "license": f"{int(rng.integers(10**7, 10**8))}" if rng.random() < 0.9 else "",
                "instant_bookable": "t" if rng.random() < 0.5 else "f",
                "reviews_per_month": round(reviews_ltm / 12, 2),
                "estimated_occupancy_l365d": est_nights,
                "estimated_revenue_l365d": est_nights * price,
            }
        )

    listings = pd.DataFrame(rows)
    # A few realistic defects for the cleaner to handle
    listings.loc[3, "latitude"] = np.nan
    listings.loc[7, "price"] = ""
    listings.loc[11, "price"] = "$12,500.00"
    listings.loc[15, "bedrooms"] = np.nan
    listings.to_csv(out_dir / "listings.csv.gz", index=False, compression="gzip")

    # Calendar: 365 days per listing, availability loosely tied to occupancy
    days = pd.date_range(snap, periods=365, freq="D")
    cal_parts = []
    for row in rows:
        p_booked = min(0.85, row["number_of_reviews_ltm"] / 30)
        booked = rng.random(len(days)) < p_booked
        cal_parts.append(
            pd.DataFrame(
                {
                    "listing_id": row["id"],
                    "date": days.date,
                    "available": np.where(booked, "f", "t"),
                    "price": "",
                    "adjusted_price": "",
                    "minimum_nights": row["minimum_nights"],
                    "maximum_nights": 365,
                }
            )
        )
    pd.concat(cal_parts).to_csv(out_dir / "calendar.csv.gz", index=False, compression="gzip")

    # Reviews: up to 10 per listing to keep the file small
    rev_rows = []
    rid = 1
    for row in rows:
        for _ in range(min(row["number_of_reviews"], 10)):
            rev_rows.append(
                {
                    "listing_id": row["id"],
                    "id": rid,
                    "date": (snap - timedelta(days=int(rng.integers(1, 700)))).isoformat(),
                    "reviewer_id": int(rng.integers(1, 10**7)),
                    "reviewer_name": "Synthetic guest",
                    "comments": rng.choice(REVIEW_TEMPLATES),
                }
            )
            rid += 1
    pd.DataFrame(rev_rows).to_csv(out_dir / "reviews.csv.gz", index=False, compression="gzip")

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"neighbourhood": name, "neighbourhood_group": None},
                "geometry": _polygon(*NEIGHBOURHOODS[name][:4]),
            }
            for name in names
        ],
    }
    (out_dir / "neighbourhoods.geojson").write_text(json.dumps(geojson), encoding="utf-8")
    return out_dir
