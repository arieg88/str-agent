# Data notes

Source: [Inside Airbnb](https://insideairbnb.com/get-the-data/), licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Credit Inside Airbnb wherever results
are shown.

## Files loaded per snapshot

| File | Table | Notes |
| --- | --- | --- |
| `data/listings.csv.gz` | `listings` | One row per listing. ~25 of ~75 columns kept. |
| `data/calendar.csv.gz` | `calendar` | 365 days per listing. `available = f` means booked *or* blocked by the host. |
| `data/reviews.csv.gz` | `reviews` | Review text + date. Reviewer names are dropped on load. |
| `visualisations/neighbourhoods.geojson` | `neighbourhoods` | Polygons, stored as GeoJSON text. |

Every row carries `city` and `snapshot_date`, so several quarterly snapshots can live side by side.
Re-loading a snapshot replaces its rows.

## Cleaning rules

- `price` arrives as text like `$1,234.00`; it is parsed to a number. The values are in the local
  currency (EUR for Greece) despite the `$` sign — verify against a few live listings.
- `t` / `f` columns become booleans.
- Listings without coordinates are dropped and counted in the ingest report.
- Duplicate listing ids keep the last row.
- Columns missing from older snapshots are created as empty rather than failing.
- `neighbourhood` comes from `neighbourhood_cleansed`.

## Known quirks

- **No booking data.** Occupancy is estimated from reviews (see `analytics/revenue.py`).
- **Calendar prices are often blank** in recent snapshots; the listing `price` is used instead.
- **`bedrooms` is sometimes missing.** Such listings are excluded when a bedroom filter is set.
- **Newer snapshots include `estimated_occupancy_l365d` and `estimated_revenue_l365d`.** When
  present, they are shown next to our own estimate as a cross-check.
- **Snapshot staleness.** Every result shows the snapshot date.

## Synthetic fixture

`stragent fixtures` writes a fake snapshot in the same format for tests and offline demos.
All its neighbourhood names end in `(synthetic)` and the CLI prints a warning whenever results
come from it.
