.PHONY: install demo ingest analyze snapshots serve serve-demo docker test lint lint-js typecheck check clean

SNAPSHOT ?=
CITY ?= thessaloniki
ADDR ?= 40.6326, 22.9416
BEDROOMS ?= 2
FIXTURE_DIR := data/fixtures/thessaloniki
FIXTURE_SNAPSHOT := 2026-09-25
# Run through npx at a pinned version: needs Node on the machine, adds nothing to the project
BIOME := npx --yes @biomejs/biome@2.5.14
# The demo uses its own database so synthetic data never mixes with real snapshots
DEMO_DB := data/demo.duckdb

# Copy instead of hardlink: avoids uv warnings on WSL drives like /mnt/c
export UV_LINK_MODE := copy

install:  ## Create .venv and install the project with dev tools
	uv sync
	uv run pre-commit install

demo:  ## Build synthetic data into data/demo.duckdb and analyse one point (no internet needed)
	uv run stragent fixtures --out $(FIXTURE_DIR) --snapshot $(FIXTURE_SNAPSHOT)
	uv run stragent ingest --from-dir $(FIXTURE_DIR) --snapshot $(FIXTURE_SNAPSHOT) --db $(DEMO_DB)
	uv run stragent analyze "$(ADDR)" --bedrooms $(BEDROOMS) --db $(DEMO_DB) --no-save

ingest:  ## Load real data: make ingest SNAPSHOT="2026-09-22 2026-06-20" (one or more dates)
	@test -n "$(SNAPSHOT)" || (echo "Set SNAPSHOT=YYYY-MM-DD (see insideairbnb.com/get-the-data)"; exit 1)
	uv run stragent ingest --city $(CITY) $(foreach s,$(SNAPSHOT),--snapshot $(s))

analyze:  ## make analyze ADDR="Egnatia 120" BEDROOMS=2 ARGS="--min-reviews 5"
	uv run stragent analyze "$(ADDR)" --bedrooms $(BEDROOMS) --city $(CITY) $(ARGS)

snapshots:
	uv run stragent snapshots

serve:  ## API + map page on http://127.0.0.1:8000 (docs at /docs)
	uv run stragent serve --reload

serve-demo:  ## Same, on the synthetic demo database
	uv run stragent serve --reload --db $(DEMO_DB)

docker:  ## Same, in Docker (mounts ./data)
	docker compose up --build

test:
	uv run pytest

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

lint-js:  ## Optional: lint the map page's JS and CSS with Biome (not part of check or CI)
	$(BIOME) lint src/stragent/api/static

typecheck:
	uv run mypy src

check: lint typecheck test

clean:
	rm -rf $(DEMO_DB) outputs .pytest_cache .mypy_cache .ruff_cache
