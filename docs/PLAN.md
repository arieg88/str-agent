# Thessaloniki STR Analyst Agent — Build Plan

## Status (as built, 2026-10-12)

This is the original plan. Where the build differs, this section wins.

- **Done: Stages 0-2**, plus comp filters, adjustable radius, "Active since", total reviews,
  seasonality charts, multi-snapshot loading and `stragent drop`. After Stage 2: the map page
  split into ES modules, the map page hardening commit, and the CI Node requirement (CI installs
  Node and sets `STRAGENT_REQUIRE_NODE=1`, so the JS tests cannot be skipped there).
- **DuckDB instead of Postgres/PostGIS** for Stages 0-3 (distances via haversine SQL,
  neighbourhoods via shapely). Revisit at Stage 4 (pgvector) or keep DuckDB with its vector
  extension for a cheaper demo.
- **Map page** is served by FastAPI from `src/stragent/api/static/` (no separate `web/` yet). It is
  split into `index.html` (layout), `css/app.css` and ES modules in `js/` (see CLAUDE.md,
  "Frontend structure"). `make lint-js` runs Biome on it through npx (optional, not in `make check`
  or CI).
  Hardened after the split: links from data must be `https://`, a newer search always wins over
  an older one still in flight, errors show the request id, a failed search clears the old
  result, startup errors say what actually failed, no price legend without comps, and the
  Leaflet CDN tags carry integrity hashes.
- **Stage 3 in progress** (memo with verified citations), in four reviewed steps:
  - **3a done:** `llm/facts.py` turns an Analysis into numbered facts with fixed ids
    (`[F17]` is always the median nightly price; inputs and a derived monthly revenue included),
    and `llm/validate.py` checks that every number in a memo is a correctly cited fact. The
    validator's rules are the docstring of `validate.py`. No LLM yet.
  - **3b done:** provider layer (`anthropic`, `openai_compatible` for OpenAI/Ollama/vLLM, and a
    scripted fake), `memo.py` (generate, validate, regenerate once, overall deadline), prompt
    file `prompts/memo/v1.md`, disk cache of verified memos (re-validated on every hit), and
    `stragent memo`. Cost is estimated from `[llm.prices]` in settings.toml (null if unpriced).
    Anthropic is called with `messages.create` + `output_config.format`, not `messages.parse`,
    because parse validates the JSON before the stop reason (refusal, max_tokens) can be read.
  - **Next: 3c** `POST /api/memo` and the map page memo panel, then 3d (golden set,
    `stragent eval-memo`).
  - Decided for Stage 3: default model `claude-sonnet-5-5` (Haiku for development, Opus kept
    for the Stage 6 comparison); no refusal-fallback beta (a refusal is `llm_error`); no streaming (the memo is validated
    whole before it is shown as verified; revisit with the Stage 5 chat); seasonality is not in
    the memo facts yet; the validator checks numbers, not wording ("higher than" is unchecked
    until the Stage 6 judge).
- **Known follow-ups on the map page:**
  - Dark mode gaps: `.error` is a hard-coded `#b3261e`, and map and chart colours are read once
    when drawn, so they do not follow a theme switch until the next search.
  - Keyboard access: comp rows are click-only; chart hit areas are focusable but have no label.
  - Static files are served without `Cache-Control`, so a browser can keep an old module after an
    edit during development (hard-reload, or add a no-cache header in dev).
- **Cloud: AWS** (Stage 7 below).
- **Possible addition:** long-term rent and sale €/m² per area (curated CSV from published
  Spitogatos SPI figures) for a short-term vs long-term yield comparison (fits Stage 8 ROI).

## Overview

You enter an address in Thessaloniki and the app tells you whether a flat there is worth running as an Airbnb: comparable listings, likely nightly rate, occupancy and revenue range, seasonality, competition, and what guests say about the area. It is built in nine stages; each one ends with something that runs and can be demoed.

**Guiding principles**

- **Numbers come from code, words come from the LLM.** Every figure is computed by deterministic, tested Python. The LLM only explains, ranks and summarises those figures, and must cite them.
- **Every stage ships.** Each stage ends in a working command, endpoint or page, plus tests. You can stop after any stage and still have a portfolio piece.
- **Evaluate early.** A tiny golden set starts at Stage 3 and grows; Stage 6 turns it into a full harness.
- **Build for swapping.** Model provider, embedding model and city are configuration, not code changes.

**How the stages build up**

1. Stage 0 — data and project setup
2. Stage 1 — CLI: address to comps and revenue estimate (no LLM yet)
3. Stage 2 — FastAPI service and map page
4. Stage 3 — LLM-written memo with verified citations
5. Stage 4 — review RAG and aspect extraction
6. Stage 5 — LangGraph agent with tools and chat
7. Stage 6 — evaluation harness and model comparison
8. Stage 7 — cloud deployment and observability
9. Stage 8+ — extensions (more cities, trends, ROI simulator, MCP server)



## Stage 0 — Data and project setup (1–2 days)

Goal: one command loads the latest Thessaloniki snapshot into a spatial database.

**Data to pull** from the [Inside Airbnb downloads page](https://insideairbnb.com/get-the-data/) (copy the current Thessaloniki snapshot URLs from there):

- `listings.csv.gz` — detailed listings: location, room type, bedrooms, price, host, review scores, `reviews_per_month`, `minimum_nights`, license
- `calendar.csv.gz` — 365 days of availability and price per listing
- `reviews.csv.gz` — every review text with date and listing id
- `neighbourhoods.geojson` — neighbourhood polygons

**Build**

- Repo with `uv` for dependencies, `ruff`, `mypy`, `pytest`, `pre-commit`
- `docker compose` running Postgres with PostGIS (pgvector added in Stage 4)
- `scripts/ingest.py --city thessaloniki --snapshot <date>`: download, clean, load
- Cleaning: parse price strings to numbers, cast booleans, drop listings with no coordinates, add a `geom` point column and a spatial index
- Store `snapshot_date` on every row so later stages can compare quarters
- A short `DATA_NOTES.md`: column meanings, quirks, the CC BY 4.0 attribution

**Done when:** `make ingest` finishes and prints row counts per table; a notebook shows listings on a map.

**Cheaper alternative:** DuckDB with its spatial extension instead of Postgres. Fine for Stages 1–3, but you will want Postgres by Stage 4.

## Stage 1 — MVP: address in, comps and revenue out (3–5 days)

Goal: a CLI that answers "what would a 2-bedroom here earn?" in under 2 seconds, with no LLM involved.

```
python -m stragent analyze "Egnatia 120, Thessaloniki" --bedrooms 2
```

**Features**

1. **Geocode** the address with Nominatim (OpenStreetMap). Cache results; respect its usage policy (1 request per second, a real user agent).
2. **Find comps** with a PostGIS query: same room type, bedrooms ±1, within 500 m. If fewer than 10 comps, widen to 1 km and say so.
3. **Market stats** for the comps: count, median nightly price with 25th–75th percentile, median review score, share of entire homes, share run by multi-listing hosts.
4. **Occupancy and revenue estimate** using the review-based approach Inside Airbnb describes on its Data Assumptions page: estimated bookings from `reviews_per_month` and an assumed review rate, times average stay length (or `minimum_nights` if higher), capped at a maximum occupancy. All assumptions live in one config file.
5. **Output** a clean terminal table plus a JSON file. The JSON schema becomes the contract every later stage builds on.

**Tests:** unit tests for the occupancy and revenue maths with hand-calculated fixtures; an integration test on 5 known addresses.

**Done when:** any Thessaloniki address returns comps, a price range and a revenue range; addresses outside the city return a clear error.

**What it shows recruiters:** clean Python, data engineering, testing, and judgement about what should *not* be done by an LLM.

## Stage 2 — FastAPI service and map page (4–6 days)

Goal: the Stage 1 logic becomes an API, and a browser page shows the results on a map.

**API endpoints** (Pydantic request and response models, auto-generated OpenAPI docs):

| Endpoint | Returns |
| --- | --- |
| `POST /analyze` | Comps, market stats, revenue estimate for an address |
| `GET /comps` | Comps for a lat/lon, radius and filters |
| `GET /seasonality` | Monthly average price and availability for the comps, from the calendar file |
| `GET /neighbourhoods/{name}` | Neighbourhood-level stats and its polygon |
| `GET /health` | Service and database status |

**Map page** (Leaflet in a single HTML page, or Streamlit if you prefer pure Python): address search box, pin, radius circle, comp markers coloured by price, a side panel with stats and a seasonality chart.

**Engineering:** async database access, dependency injection for the DB session, consistent error responses, request logging, API tests with `httpx`.

**Done when:** `docker compose up` gives a working map at localhost, and the API has tests in CI (GitHub Actions).

## Stage 3 — LLM-written memo with verified citations (4–6 days)

Goal: `POST /memo` turns the Stage 1 numbers into a readable investment memo where every figure is traceable.

**How it works**

1. The analysis JSON is converted into a numbered list of facts, e.g. `[F3] median nightly price of 14 comps: 62`.
2. The LLM writes the memo using a fixed structure: summary verdict, pricing, occupancy and revenue, competition, risks, and what to check before buying.
3. Every number in the memo must carry a fact tag like `[F3]`.
4. A validator extracts all numbers from the memo and checks each one matches its cited fact. If any fail, the memo is regenerated once with the errors fed back; if it fails again, the response flags it.

**Engineering**

- A thin provider layer so you can switch between Claude, OpenAI and an open model via config
- Prompts stored as versioned files (`prompts/memo/v3.md`), never inline strings
- Structured output for the memo sections (JSON schema), rendered to Markdown
- Streaming responses (server-sent events) to the map page
- Token and cost logging per request

**Start the golden set now:** 15–20 addresses with their computed facts, plus a script that checks memos for unverified numbers.

**Done when:** the memo appears in the map page, and the golden-set run shows zero unverified numbers.

**What it shows:** prompt engineering, structured output, guardrails against hallucinated numbers.

## Stage 4 — Review RAG and aspect extraction (1–2 weeks)

Goal: the memo gains a "What guests say about this area" section, grounded in real reviews, and users can ask free-text questions about an area.

**Part A — retrieval over reviews**

1. Detect language per review (many are Greek, many English); keep the original text.
2. Embed with a multilingual model; store vectors in pgvector alongside `listing_id`, review date and the listing's location.
3. Hybrid search: Postgres full-text (keyword) plus vector similarity, merged with reciprocal rank fusion, then a reranker on the top 30.
4. Always filter by geography first (comps' listing ids or a radius), so answers are about *this* area.
5. `GET /reviews/search?q=noise at night&lat=…&lon=…` returns ranked snippets with review ids and dates.

**Part B — aspect extraction (offline batch job)**

1. Define aspects: noise, cleanliness, transport, parking, safety, host communication, view, value.
2. An LLM with a JSON schema extracts, per review, each aspect mentioned, its sentiment and the supporting span.
3. Run it as a resumable batch job (use a batch API if your provider has one, to cut cost).
4. Aggregate scores per listing and per neighbourhood; the memo cites these aggregates as facts, with 1–2 example review snippets.

**Measure it**

- Hand-label 100–150 reviews for aspects; report precision and recall per aspect, and compare a cheap model against a strong one.
- Write 30 test queries with known relevant reviews; report recall@10 for keyword-only, vector-only and hybrid.

**Done when:** the memo includes grounded guest sentiment, and your README has the retrieval and extraction metrics tables.

**What it shows:** RAG beyond the tutorial version — hybrid search, reranking, metadata filtering, multilingual data, measured quality.

## Stage 5 — LangGraph agent with tools and chat (1–2 weeks)

Goal: replace the fixed pipeline with an agent that decides which tools to call, asks clarifying questions, and holds a conversation ("what if it were 3 bedrooms?", "compare with Kalamaria").

**Tools** (each a typed Python function with a docstring the model reads):

| Tool | Wraps |
| --- | --- |
| `geocode` | Nominatim, with cache |
| `find_comps` | Stage 1 spatial query |
| `estimate_revenue` | Stage 1 maths |
| `seasonality` | Calendar aggregation |
| `search_reviews` | Stage 4 hybrid retrieval |
| `area_sentiment` | Stage 4 aspect aggregates |
| `nearby_places` | OpenStreetMap Overpass API: metro stations, waterfront, university, supermarkets |
| `compare_locations` | Runs the above for 2–3 addresses side by side |

**Graph**

1. **Understand** the request; extract address, bedrooms, budget, question type.
2. **Clarify** when needed (address ambiguous, outside Thessaloniki, bedrooms missing) using a LangGraph interrupt, then resume.
3. **Plan and call tools**, independent ones in parallel.
4. **Validate** tool outputs (empty comps, too few reviews) and adjust (widen radius, say data is thin).
5. **Write** the answer or memo with fact citations.
6. **Check** citations with the Stage 3 validator; loop back once on failure.

**Conversation:** thread-based memory using LangGraph's Postgres checkpointer; a `/chat` endpoint with streaming of both tokens and tool-call events, so the map page can show "Finding comps…" as it works.

**Done when:** the agent correctly handles five scenario types: single address memo, follow-up what-if, comparison, review question, and an out-of-scope request it politely declines.

**What it shows:** agent orchestration, tool calling, human-in-the-loop, state management — the core of the job ad.

## Stage 6 — Evaluation harness and model comparison (1 week)

Goal: one command scores the whole system, and CI blocks changes that make it worse.

**Test sets**

- **Golden addresses** (grow to \~50): expected comps count, price range and revenue range, computed deterministically.
- **Agent scenarios** (\~40 chat transcripts): expected tools, expected arguments, and a rubric for the answer.
- **Adversarial cases:** addresses in other cities, nonsense input, prompt-injection text hidden in a review.

**Metrics**

| Metric | How it's measured |
| --- | --- |
| Tool selection accuracy | Called the expected tools, no unnecessary ones |
| Argument correctness | Radius, bedrooms, location passed correctly |
| Numeric faithfulness | Share of memo numbers that match a cited fact |
| Citation coverage | Share of claims that carry a citation |
| Answer quality | LLM-as-judge with a rubric, calibrated against your own labels on \~30 answers |
| Correct refusals | Out-of-scope and injection cases handled safely |
| Latency and cost | p50/p95 latency and cost per request |

**Experiments to publish in the README**

- 3 models compared on all metrics (quality, cost, latency)
- 2–3 prompt versions compared on the memo
- Retrieval settings compared (with and without reranker)

**Model comparison (decided in Stage 3):** run the Stage 3 golden set across providers and models
through the same provider layer, and report per model: unverified-number rate, retries, latency
and cost.

**Engineering:** pytest-style eval runner, results saved as JSON per run, a small HTML report, and a GitHub Actions job that fails if key metrics drop below thresholds.

**Done when:** `make eval` produces a report, and the README shows a model comparison table.

**What it shows:** the "evaluate model performance and improve quality through experimentation" line of the job ad, done properly.

## Stage 7 — Cloud deployment and observability (4–6 days)

Goal: a public demo URL that stays up cheaply, with tracing you can show in an interview.

**Default plan on AWS** (GCP equivalents in brackets):

- API container on AWS App Runner (Cloud Run); ECS Fargate behind a load balancer if you later need more control
- Stages 2–3: the ingest job builds the DuckDB file, stores it in S3, and the container downloads it at start-up (App Runner has no persistent disk)
- From Stage 4: Postgres with PostGIS and pgvector on Amazon RDS (Cloud SQL)
- Quarterly ingestion as an ECS Fargate task started by EventBridge Scheduler (Cloud Run Job plus Cloud Scheduler)
- Images in Amazon ECR, built and deployed by GitHub Actions using OIDC, so no long-lived AWS keys
- API keys in AWS Secrets Manager (Secret Manager)
- Optional: infrastructure as code with Terraform or AWS CDK

**Estimated monthly cost** (approximate, Europe (Ireland), on-demand prices as of October 2026):

| Item | Monthly cost (USD) |
| --- | --- |
| App Runner, 0.25 vCPU / 0.5 GB, idle most of the day, about 1 h/day active | \~$3–5 |
| App Runner, 1 vCPU / 2 GB, same usage | \~$12–13 |
| RDS db.t4g.micro Postgres plus 20 GB storage (from Stage 4) | \~$14–15 |
| S3, ECR, Secrets Manager, CloudWatch logs, EventBridge | \~$1–2 |
| LLM API calls (Stage 3 onwards) | depends on traffic; capped by the daily spend limit below |

That is roughly $5–15 a month for Stages 2–3 and $20–30 from Stage 4. New AWS accounts on the 6-month free plan get up to $200 in credits, which covers this. Sources: [App Runner pricing](https://aws.amazon.com/apprunner/pricing/), [RDS db.t4g.micro pricing](https://www.bytebase.com/dbcost/rds/instance/db.t4g.micro/), [AWS Free Tier credits](https://aws.amazon.com/about-aws/whats-new/2025/07/aws-free-tier-credits-month-free-plan/).

**Production hardening**

- Tracing of every LLM and tool call with Langfuse or LangSmith, linked to request ids
- Structured JSON logs; dashboards for latency, errors, tokens and cost
- Rate limiting and a simple API key for the public demo; a per-day spend cap
- Caching: geocodes, comps and memos for repeated addresses
- Model fallback: if the primary provider errors, retry on a secondary
- A monthly budget alert on the cloud account
- A cost guard for the public `/api/memo` (decided in Stage 3): a daily cap on memo generations,
  or serve only cached golden-set memos on the demo

**Done when:** the demo URL works from your phone, a trace of a full agent run can be opened and explained, and ingestion of a new snapshot runs without you.

**What it shows:** "deploy and maintain scalable AI systems in production" and cloud platform experience.

## Stage 8+ — Extensions (pick any, in any order)

Each extension is independent and adds one clear talking point.

- **More cities:** add Athens and Crete via config. Proves the pipeline is city-agnostic and adds a "compare across cities" question.
- **Trends over time:** keep every quarterly snapshot; show price, supply and professional-host share changing per neighbourhood. A "what changed this quarter" brief can run as a scheduled agent.
- **ROI simulator:** user enters purchase price, renovation and furnishing costs, fees and cleaning costs; the agent computes payback period and yield ranges from the revenue estimate. All maths in code, explained by the LLM.
- **Explainable price model:** gradient-boosted model for nightly price with SHAP values; the LLM turns them into advice ("a balcony adds roughly X here").
- **Listing optimiser:** paste a listing; get a rewrite and missing-amenity suggestions benchmarked against top comps, evaluated with pairwise LLM-as-judge.
- **MCP server:** expose the tools as an MCP server so any agent client can use them. Small, current, and an easy open-source release.
- **Regulation notes tool:** short-term-rental rules sourced from official Greek pages, stored with retrieval dates, and cited when relevant. Treat as informational, not legal advice.
- **Greek-language UI and answers:** the multilingual work from Stage 4 makes this cheap.

## Repo structure, timeline, risks and presentation

**Repo structure**

```
str-agent/
  src/stragent/
    data/          # ingestion, cleaning, schema
    analytics/     # comps, revenue, seasonality (pure functions)
    rag/           # embeddings, hybrid search, reranker, aspects
    llm/           # provider layer, prompts loader, validators
    agent/         # LangGraph graph, tools, state
    api/           # FastAPI app, routers, schemas
  prompts/         # versioned prompt files
  web/             # map page
  evals/           # datasets, runners, reports
  infra/           # Dockerfiles, Terraform, CI
  tests/
```

**Timeline:** roughly 8–10 weeks of evenings and weekends for Stages 0–7. Stages 0–3 (about 3 weeks) already make a solid CV entry; add the rest as you go.

**Risks and how to handle them**

- **Occupancy is estimated, not observed.** State the assumptions in the UI and memo; show ranges, never single numbers.
- **Calendar availability ≠ bookings.** A blocked day may be booked or simply closed by the host; treat it as a weak signal.
- **Thin data in some streets.** Widen the radius automatically and tell the user how many comps were used.
- **Snapshot staleness.** Show the snapshot date on every result.
- **LLM cost.** Cache memos, batch the aspect extraction, and use a cheaper model where evals show it is good enough.
- **Geocoding limits.** Cache aggressively; Nominatim's public service is not meant for heavy traffic.

**How to present it**

- README top: one-line pitch, demo link, 60-second demo video, architecture diagram, eval results table.
- A short write-up (blog post or README section) on one hard decision, e.g. why numbers are computed in code and only cited by the LLM.
- In interviews, draw the parallel to recruiting: matching a property to comps is matching a candidate to roles; review RAG is CV and job-description RAG; the citation validator is how you stop an agent inventing candidate facts.
