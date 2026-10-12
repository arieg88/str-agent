# stragent — "Should I Airbnb this flat?"

Enter an address in Thessaloniki and get comparable Airbnb listings nearby, a nightly price range,
an estimated occupancy and annual revenue range, and a picture of the local market — all computed
from [Inside Airbnb](https://insideairbnb.com/get-the-data/) open data.

This is **Stages 0–2** of a staged build toward an LLM agent: data pipeline, analysis CLI, and an
HTTP API with a map page. Everything here is deterministic, tested Python; later stages add an
LLM-written memo that must cite these numbers, review RAG, a LangGraph agent, evals and cloud
deployment.

## Quick start (no internet needed)

```bash
make install   # uv sync: .venv + dev tools + pre-commit hook
make demo        # build a synthetic snapshot into data/demo.duckdb, analyse the city centre
make serve-demo  # API + map page on the demo data: http://127.0.0.1:8000, API docs at /docs
```

The demo uses **synthetic** data in its own database (`data/demo.duckdb`), so it never mixes
with real snapshots, and says so in its output.

## With real data

1. On [insideairbnb.com/get-the-data](https://insideairbnb.com/get-the-data/), find Thessaloniki
   and note the snapshot date. Check `base_url` in `config/settings.toml` matches the file links.
2. Load it and analyse:

```bash
make ingest SNAPSHOT=2026-09-25
export STRAGENT_CONTACT_EMAIL=you@example.com   # recommended by Nominatim's usage policy
uv run stragent analyze "Egnatia 120" --bedrooms 2
uv run stragent analyze "40.6326, 22.9416" --bedrooms 1 --json   # coordinates skip geocoding
```

Load several quarters at once (find older dates under "show archived data"):

```bash
make ingest SNAPSHOT="2026-09-22 2026-06-20"
uv run stragent snapshots                      # what's loaded
uv run stragent analyze "Egnatia 120" --snapshot 2026-06-20   # default: latest
```

Narrow the comps (market stats always use every listing in the radius):

```bash
uv run stragent analyze "Egnatia 120" --bedrooms 2 \
    --min-reviews 5 --min-rating 4.5 --max-min-nights 7 --superhost-only --show 30
```

Remove a snapshot you no longer want: `uv run stragent drop --snapshot 2026-06-20`.

Already downloaded the files? `uv run stragent ingest --from-dir path/to/files --snapshot 2026-09-25`.

## API and map page (Stage 2)

```bash
make serve                   # uv run stragent serve --reload
# or, in Docker (data/ is mounted, so load data first on the host or with the second line)
docker compose up --build
docker compose run --rm api stragent ingest --snapshot 2026-09-22
```

Open http://127.0.0.1:8000: type an address or click the map. The page shows the comps coloured
by price, the search radius, the estimate, the local market and two seasonality charts.
Interactive API docs are at `/docs`.

| Endpoint | Returns |
| --- | --- |
| `POST /api/analyze` | Same result as `stragent analyze` (address or `lat`/`lon`, plus filters) |
| `GET /api/comps` | Every comp with coordinates, nearest first, for maps |
| `GET /api/seasonality` | Next 12 months' calendar (booked or blocked share) and past reviews per month |
| `GET /api/neighbourhoods` | Listings, typical prices and host concentration per neighbourhood |
| `GET /api/neighbourhoods/{name}` | One neighbourhood's stats and polygon |
| `GET /api/neighbourhoods.geojson` | All polygons, for the map |
| `GET /api/snapshots`, `GET /api/health` | What's loaded; service status |

Errors always look like `{"error": "out_of_area", "detail": "...", "request_id": "..."}`, and every
response carries an `x-request-id` header that also appears in the server log.

Design notes: endpoints are thin wrappers around the same functions the CLI uses
(`analysis.py`); each request opens its own read-only DuckDB connection, so `stragent ingest`
can run while the server is up and new snapshots appear without a restart.

## Example output (synthetic data)

```
Comps                    36 × Entire home/apt, bedrooms 2 ±1, within 500 m (36 active)
Nightly price            €83  (€70 – €100)
Occupancy                28%  (23% – 38%)
Nights / year            101  (82 – 140)
Revenue / year           €8,796  (€6,582 – €14,691)
Review score             4.77

Market within 500 m      69 listings · 77% entire homes · 72% run by multi-listing hosts
```

Each run also saves the full result as JSON under `outputs/`. That JSON (`src/stragent/models.py`)
is the contract later stages build on.

## How the estimate works

1. **Geocode** the address with OpenStreetMap Nominatim, biased to the city, cached on disk and
   throttled to one request per second. `lat, lon` input skips this.
2. **Find comps**: same room type, bedrooms ±1, valid price, within 500 m. Fewer than 10? Widen to
   1 km, then 1.5 km, and say so.
3. **Estimate occupancy per comp** from reviews, following Inside Airbnb's data assumptions:
   `bookings = reviews in last 12 months ÷ 50% review rate`,
   `nights = bookings × max(3, minimum nights)`, capped at 70% of the year.
4. **Report ranges** (25th / median / 75th percentile) across active comps, never one number.
5. **Market stats** across *all* listings in the radius: share of entire homes, share run by
   multi-listing hosts, top-3-host concentration, share showing a licence number, and share first
   reviewed in the last 12 months (new supply).
6. **Active since** for each comp is its first review date: the listing was taking bookings by
   then (it may have been listed a little earlier).
7. **Seasonality** (API and map page): reviews per comp per month over the last 24 months (past
   demand), and the share of the next 12 months' nights not open for booking (booked *or* blocked,
   so compare months rather than reading it as occupancy).

Every assumption lives in `config/settings.toml`.

## Project layout

```
src/stragent/
  config.py          typed settings from config/settings.toml
  db.py              DuckDB schema (moves to Postgres + PostGIS at Stage 4)
  data/clean.py      pure cleaning functions
  data/ingest.py     download → clean → load, idempotent per snapshot
  data/fixtures.py   synthetic snapshot in Inside Airbnb's format
  analytics/geo.py   haversine, bounding box, point-in-neighbourhood
  analytics/geocode.py  Nominatim client with cache and throttling
  analytics/comps.py    comps search with radius widening, market stats
  analytics/revenue.py  occupancy and revenue model
  analytics/seasonality.py  calendar and review series by month
  analytics/neighbourhoods.py  per-neighbourhood summaries
  analysis.py        address → Analysis / Seasonality (shared by CLI and API)
  cli.py             `stragent` command (ingest, analyze, serve, …)
  api/app.py         FastAPI factory: middleware, error handlers, static page
  api/routes.py      endpoints (thin wrappers around analysis.py)
  api/static/index.html  map page (Leaflet + OpenStreetMap tiles)
tests/               72 tests: maths, cleaning, geocoding (mocked HTTP), end to end, API
```

## Development

```bash
make check     # ruff + mypy + pytest
```

CI runs the same checks on every push (`.github/workflows/ci.yml`).

## Limitations

- Occupancy and revenue are **estimates** from reviews, not bookings.
- A blocked calendar day may be booked or simply closed by the host.
- Inside Airbnb snapshots are quarterly; the snapshot date is shown on every result.
- Not investment, tax or legal advice. Short-term-rental rules in Greece change; check them
  separately.

## Data credit

Data from [Inside Airbnb](https://insideairbnb.com), licensed under CC BY 4.0. See
[DATA_NOTES.md](DATA_NOTES.md).
