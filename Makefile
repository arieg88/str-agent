.PHONY: install demo ingest analyze snapshots test lint typecheck check clean

SNAPSHOT ?=
CITY ?= thessaloniki
ADDR ?= 40.6326, 22.9416
BEDROOMS ?= 2
FIXTURE_DIR := data/fixtures/thessaloniki
FIXTURE_SNAPSHOT := 2026-09-25

# Copy instead of hardlink: avoids uv warnings on WSL drives like /mnt/c
export UV_LINK_MODE := copy

install:  ## Create .venv and install the project with dev tools
	uv sync
	uv run pre-commit install

demo:  ## Build synthetic data, load it, run one analysis (no internet needed)
	uv run stragent fixtures --out $(FIXTURE_DIR) --snapshot $(FIXTURE_SNAPSHOT)
	uv run stragent ingest --from-dir $(FIXTURE_DIR) --snapshot $(FIXTURE_SNAPSHOT)
	uv run stragent analyze "$(ADDR)" --bedrooms $(BEDROOMS)

ingest:  ## Load real data: make ingest SNAPSHOT="2026-09-22 2026-06-20" (one or more dates)
	@test -n "$(SNAPSHOT)" || (echo "Set SNAPSHOT=YYYY-MM-DD (see insideairbnb.com/get-the-data)"; exit 1)
	uv run stragent ingest --city $(CITY) $(foreach s,$(SNAPSHOT),--snapshot $(s))

analyze:  ## make analyze ADDR="Egnatia 120" BEDROOMS=2 ARGS="--min-reviews 5"
	uv run stragent analyze "$(ADDR)" --bedrooms $(BEDROOMS) --city $(CITY) $(ARGS)

snapshots:
	uv run stragent snapshots

test:
	uv run pytest

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

typecheck:
	uv run mypy src

check: lint typecheck test

clean:
	rm -rf data/stragent.duckdb outputs .pytest_cache .mypy_cache .ruff_cache
