# stragent — "Should I Airbnb this flat?"

Enter an address in Thessaloniki and get comparable Airbnb listings nearby, a nightly price range,
an estimated occupancy and annual revenue range, and a picture of the local market — all computed
from [Inside Airbnb](https://insideairbnb.com/get-the-data/) open data.

This is **Stages 0–1** of a staged build toward an LLM agent. Everything here is deterministic,
tested Python; later stages add an API, an LLM-written memo that must cite these numbers, review
RAG, a LangGraph agent, evals and cloud deployment.

## Quick start (no internet needed)

```bash
make install   # uv sync: .venv + dev tools + pre-commit hook
make demo      # build a synthetic snapshot, load it, analyse the city centre
```

The demo uses **synthetic** data and says so in its output.

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

Already downloaded the files? `uv run stragent ingest --from-dir path/to/files --snapshot 2026-09-25`.

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
   multi-listing hosts, top-3-host concentration, share showing a licence number.

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
  analysis.py        address → Analysis (the JSON contract)
  cli.py             `stragent` command
tests/               52 tests: maths, cleaning, geocoding (mocked HTTP), end to end
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
