"""Analysis -> numbered facts the memo may cite.

The LLM never computes a figure: it gets this list and must tag every number it writes with the
fact it came from, e.g. "€78 [F17]". The validator (validate.py) checks each one.

Ids are fixed by the catalogue below, not by the address: F17 is always the median nightly
price. A figure that is missing for an address keeps its id and is listed as not available.
To keep ids stable, only ever add entries at the END of CATALOGUE, and bump FACTS_VERSION
when a label, unit or meaning changes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from stragent.models import Analysis, Fact, FactUnit, ValueRange

FACTS_VERSION = 2  # 2: room type limited to Inside Airbnb's own values

# The room type filter can be typed by a user; only these values reach the memo prompt.
ROOM_TYPES = ("Entire home/apt", "Private room", "Shared room", "Hotel room")
OTHER_ROOM_TYPE = "a custom room type"

MONTHS_PER_YEAR = 12
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}
NOT_AVAILABLE = "not available"

Value = int | float | str | None


@dataclass(frozen=True)
class _Entry:
    key: str
    label: str
    unit: FactUnit
    get: Callable[[Analysis], Value]
    missing: str = NOT_AVAILABLE  # how a None value is shown


def _range(
    key: str, what: str, unit: FactUnit, get: Callable[[Analysis], ValueRange | None]
) -> list[_Entry]:
    """Three facts for a ValueRange: 25th percentile, median, 75th percentile."""

    def part(name: str) -> Callable[[Analysis], Value]:
        def value(a: Analysis) -> Value:
            rng = get(a)
            return None if rng is None else float(getattr(rng, name))

        return value

    return [
        _Entry(f"{key}.p25", f"{what}: 25th percentile", unit, part("p25")),
        _Entry(f"{key}.median", f"{what}: median", unit, part("median")),
        _Entry(f"{key}.p75", f"{what}: 75th percentile", unit, part("p75")),
    ]


def _monthly_revenue(a: Analysis) -> ValueRange | None:
    """Derived here so the memo never divides: annual revenue / 12."""
    year = a.revenue.annual_revenue
    if year is None:
        return None
    return ValueRange(
        p25=year.p25 / MONTHS_PER_YEAR,
        median=year.median / MONTHS_PER_YEAR,
        p75=year.p75 / MONTHS_PER_YEAR,
        n=year.n,
    )


def _radius_mode(a: Analysis) -> str:
    if a.filters.radius_mode != "auto":
        return "fixed by the user"
    return "automatic, widened to find enough comps" if a.filters.radius_widened else "automatic"


CATALOGUE: list[_Entry] = [
    # --- inputs: what was asked ---
    _Entry("city", "City", "text", lambda a: a.city),
    _Entry("snapshot_date", "Date of the data snapshot", "date", lambda a: a.snapshot_date),
    _Entry(
        "location.neighbourhood",
        "Neighbourhood of the address",
        "text",
        lambda a: a.location.neighbourhood,
        missing="outside the mapped neighbourhoods",
    ),
    _Entry(
        "filters.room_type",
        "Room type of the comps",
        "text",
        lambda a: a.filters.room_type if a.filters.room_type in ROOM_TYPES else OTHER_ROOM_TYPE,
    ),
    _Entry(
        "filters.bedrooms",
        "Bedrooms asked for",
        "count",
        lambda a: a.filters.bedrooms,
        missing="any number of bedrooms",
    ),
    _Entry(
        "filters.bedroom_tolerance",
        "Comps may differ from the bedrooms asked for by up to",
        "count",
        lambda a: a.filters.bedroom_tolerance,
    ),
    _Entry("filters.radius_m", "Search radius for comps", "metres", lambda a: a.filters.radius_m),
    _Entry("filters.radius_mode", "How the search radius was chosen", "text", _radius_mode),
    _Entry(
        "filters.min_comps_target",
        "Number of comps the search aims for",
        "count",
        lambda a: a.filters.min_comps_target,
    ),
    _Entry(
        "filters.min_reviews_ltm",
        "Filter: minimum reviews in the last 12 months",
        "count",
        lambda a: a.filters.min_reviews_ltm,
    ),
    _Entry(
        "filters.min_rating",
        "Filter: minimum review score",
        "rating",
        lambda a: a.filters.min_rating,
        missing="no minimum",
    ),
    _Entry(
        "filters.max_minimum_nights",
        "Filter: longest minimum stay allowed",
        "nights",
        lambda a: a.filters.max_minimum_nights,
        missing="no limit",
    ),
    _Entry(
        "filters.superhost_only",
        "Filter: superhosts only",
        "text",
        lambda a: "yes" if a.filters.superhost_only else "no",
    ),
    # --- comps ---
    _Entry("comp_stats.count", "Comparable listings found", "count", lambda a: a.comp_stats.count),
    _Entry(
        "comp_stats.active_count",
        "Comps with at least one review in the last 12 months",
        "count",
        lambda a: a.comp_stats.active_count,
    ),
    *_range(
        "comp_stats.nightly_price",
        "Nightly price of comps",
        "money",
        lambda a: a.comp_stats.nightly_price,
    ),
    _Entry(
        "comp_stats.review_score_median",
        "Median review score of comps (out of 5)",
        "rating",
        lambda a: a.comp_stats.review_score_median,
    ),
    _Entry(
        "comp_stats.superhost_share",
        "Share of comps run by superhosts",
        "share",
        lambda a: a.comp_stats.superhost_share,
    ),
    _Entry(
        "comp_stats.minimum_nights_median",
        "Median minimum stay of comps",
        "nights",
        lambda a: a.comp_stats.minimum_nights_median,
    ),
    # --- occupancy and revenue (estimates, across comps with recent reviews) ---
    *_range(
        "revenue.occupancy",
        "Estimated occupancy of active comps",
        "share",
        lambda a: a.revenue.occupancy,
    ),
    *_range(
        "revenue.occupied_nights",
        "Estimated booked nights per year of active comps",
        "nights",
        lambda a: a.revenue.occupied_nights,
    ),
    *_range(
        "revenue.annual_revenue",
        "Estimated revenue per year of active comps",
        "money",
        lambda a: a.revenue.annual_revenue,
    ),
    *_range(
        "revenue.monthly_revenue",
        "Estimated revenue per month of active comps (yearly figure / 12)",
        "money",
        _monthly_revenue,
    ),
    _Entry(
        "revenue.capped_share",
        "Share of active comps whose estimate hit the occupancy cap",
        "share",
        lambda a: a.revenue.capped_share,
    ),
    _Entry(
        "revenue.assumptions.review_rate",
        "Assumption: share of stays that leave a review",
        "share",
        lambda a: a.revenue.assumptions.get("review_rate"),
    ),
    _Entry(
        "revenue.assumptions.average_stay_nights",
        "Assumption: average length of a stay",
        "nights",
        lambda a: a.revenue.assumptions.get("average_stay_nights"),
    ),
    _Entry(
        "revenue.assumptions.max_occupancy",
        "Assumption: occupancy is capped at",
        "share",
        lambda a: a.revenue.assumptions.get("max_occupancy"),
    ),
    *_range(
        "revenue.inside_airbnb_revenue",
        "Inside Airbnb's own revenue estimate per year for the comps",
        "money",
        lambda a: a.revenue.inside_airbnb_revenue,
    ),
    # --- market around the address (all listing types) ---
    _Entry("market.radius_m", "Radius of the market area", "metres", lambda a: a.market.radius_m),
    _Entry(
        "market.total_listings",
        "Listings of all types in the market area",
        "count",
        lambda a: a.market.total_listings,
    ),
    _Entry(
        "market.entire_home_share",
        "Share of area listings that are entire homes",
        "share",
        lambda a: a.market.entire_home_share,
    ),
    _Entry(
        "market.multi_listing_host_share",
        "Share of area listings run by hosts with several listings",
        "share",
        lambda a: a.market.multi_listing_host_share,
    ),
    _Entry(
        "market.top3_hosts_share",
        "Share of area listings held by the top 3 hosts",
        "share",
        lambda a: a.market.top3_hosts_share,
    ),
    _Entry(
        "market.licensed_share",
        "Share of area listings that show a licence number",
        "share",
        lambda a: a.market.licensed_share,
    ),
    _Entry(
        "market.new_listings_share",
        "Share of reviewed area listings first reviewed in the last 12 months (new supply)",
        "share",
        lambda a: a.market.new_listings_share,
    ),
]


def half_up(value: float | int | Decimal, decimals: int = 0) -> Decimal:
    """Round half away from zero (Python's round() is half-to-even, which reads oddly)."""
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def _plain(value: float | int, max_decimals: int) -> str:
    """Up to max_decimals, without trailing zeros: 3.0 -> '3', 2.5 -> '2.5'."""
    text = f"{half_up(value, max_decimals):,f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def display(value: Value, unit: FactUnit, currency: str, missing: str = NOT_AVAILABLE) -> str:
    """The value as the memo should write it. The validator accepts exactly this rounding."""
    if value is None:
        return missing
    if isinstance(value, str):
        return value
    if unit == "money":
        symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} ")
        return f"{symbol}{half_up(value):,}"
    if unit == "share":
        return f"{half_up(Decimal(str(value)) * 100)}%"
    if unit == "metres":
        return f"{_plain(value, 0)} m"
    if unit == "rating":
        return _plain(value, 2)
    if unit == "nights":
        return _plain(value, 1)
    return _plain(value, 0)


def build_facts(analysis: Analysis) -> list[Fact]:
    """Every catalogue entry, in order, as F1, F2, ... Same analysis -> same facts."""
    facts = []
    for number, entry in enumerate(CATALOGUE, start=1):
        value = entry.get(analysis)
        facts.append(
            Fact(
                id=f"F{number}",
                key=entry.key,
                label=entry.label,
                value=value,
                unit=entry.unit,
                display=display(value, entry.unit, analysis.currency, entry.missing),
            )
        )
    return facts


def fact_lines(facts: list[Fact]) -> str:
    """The list as the model sees it: one '[F17] Nightly price of comps: median: €78' per line."""
    return "\n".join(f"[{f.id}] {f.label}: {f.display}" for f in facts)


def facts_hash(facts: list[Fact]) -> str:
    """Identifies this exact set of facts (used in the memo cache key and recorded on the memo)."""
    payload = {
        "version": FACTS_VERSION,
        "facts": [[f.id, f.key, f.label, f.value, f.unit, f.display] for f in facts],
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
