# CLAUDE.md

Context for AI coding sessions on this repo. Keep it short and current.

## What this is
"Should I Airbnb this flat?" for Thessaloniki. An address goes in; comparable Airbnb listings,
a nightly price range, estimated occupancy and revenue ranges, market stats and seasonality
come out. Data: Inside Airbnb quarterly snapshots (CC BY 4.0, credit them).

It is a portfolio project for an "AI agent engineer" role (Python, FastAPI, LLMs, RAG, agents,
evals, cloud). The full staged plan is in `docs/PLAN.md`.

## Progress
Progress and what's next live in the Status section at the top of `docs/PLAN.md`, not here.

## Core principle
Numbers come from deterministic, tested Python. LLMs only explain, rank and summarise them
and must cite them. Never let an LLM compute a figure.

## Commands
```bash
make install            # uv sync + pre-commit hook
make check              # ruff + mypy + pytest (run before every commit)
make demo               # synthetic data into data/demo.duckdb (never the real DB)
make serve              # API + map page, http://127.0.0.1:8000 (docs at /docs)
make serve-demo         # same, on the demo DB
make lint-js            # optional: Biome on the map page via npx (not in check or CI)
make ingest SNAPSHOT="2026-09-22 2026-06-20"
uv run stragent analyze "Egnatia 120" --bedrooms 2 --radius 800
uv run stragent snapshots / drop --snapshot DATE
```

## Layout
- `src/stragent/analysis.py`: shared entry point for CLI and API (`build_context`, `analyze`,
  `seasonality`). Add features here, not in the CLI or routes.
- `analytics/`: comps, revenue model, seasonality, geocoding, neighbourhoods (pure logic).
- `data/`: cleaning, ingest, synthetic fixtures. `db.py`: DuckDB schema and migrations.
- `models.py`: the Pydantic result contract (CLI JSON == API JSON). Extend, don't break.
- `api/`: FastAPI app factory, thin routes, static map page (`api/static/`, see "Frontend structure").
- `config/settings.toml`: every tunable assumption (radius, review rate, caps). No magic
  numbers in code.

## Conventions
- Python 3.11+, uv, ruff (line length 100), mypy clean, pytest. Tests use a synthetic DB in a
  temp dir and a fake geocoder; they never touch `data/` or the network.
- Every new feature gets tests; `make check` must pass. CI runs the same.
- Endpoints are sync `def` (DuckDB is blocking). One error shape:
  `{"error", "detail", "request_id"}`.
- Map page: build DOM with `textContent`, never `innerHTML` with data.
- Map page modules import one way: `main` -> `api`, `map`, `render`; `render` -> `charts`; all ->
  `dom`. `render.js` must not import `map.js` (pass callbacks from `main.js`). `L` is the only
  global and only `map.js` uses it. Asset URLs are absolute (`/static/...`).
- Map page links built from data go through `httpsUrl()` in `dom.js` (`el()` drops any other
  `href`). CDN tags are version-pinned with `integrity` hashes; update the hash with the version.
- Map page unit tests live in `tests/js/*.test.mjs` (Node's built-in runner, no npm packages).
  `tests/test_frontend.py` runs them inside `make check`; without Node they are skipped locally
  and fail in CI (`STRAGENT_REQUIRE_NODE=1`, with Node installed by `actions/setup-node`).

## Frontend structure
Plain ES modules served by FastAPI from `src/stragent/api/static/`, no framework and no build
step (revisit with Vite + TypeScript in a `web/` app at Stage 5, when the agent chat needs
streaming and state):
```
static/index.html      layout only; loads Leaflet, css/app.css and js/main.js (type="module")
static/css/app.css     all styles; colours as CSS variables with light and dark values
static/js/main.js      startup: wire form, slider, map clicks; call api, then render
static/js/api.js       fetch wrapper, error shape, query building, snapshot list
static/js/map.js       Leaflet map, neighbourhood layer, pin, radius circle, comp markers
static/js/charts.js    the bar chart (hover tooltip + table view)
static/js/render.js    summary, seasonality, market and comps panels
static/js/dom.js       el() helper (textContent only), formatters (eur, pct, month names)
```

## Gotchas already hit (don't reintroduce)
- `.gitignore` patterns for data must be anchored (`/data/`), or `src/stragent/data/` is ignored.
- The API and `analyze` open DuckDB read-only, so migrations don't run there. Readers must
  tolerate missing new columns (see `LATER_LISTING_COLUMNS` in `db.py`).
- Synthetic data must never go into `data/stragent.duckdb`: its date is newer than real
  snapshots and becomes the default.
- Occupancy is inferred from reviews (review rate 50% in config). Evidence suggests 60-70% is
  more realistic, so treat revenue as optimistic. Ranges, never single numbers.
- Calendar "unavailable" means booked OR blocked by the host.
- Nominatim: max 1 request/s, real User-Agent, cache results.

## Environment
Developed on WSL with the repo under `/mnt/c/...` (`UV_LINK_MODE=copy` is set in the Makefile).

## When you finish a task
- Run `make check`.
- Update the Status section of `docs/PLAN.md` (what's done, what changed from the plan).
- If something new went wrong or a decision was made that future work must respect, add it to
  "Gotchas" or "Conventions" here. Keep this file about lasting facts, not to-dos.

## Git
- Commit messages: short subject, bullet body.
- Never commit `data/`, `outputs/` or `.duckdb` files.
