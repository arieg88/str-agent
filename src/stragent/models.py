"""The analysis result contract. Later stages (API, LLM memo, agent) build on this schema."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class ValueRange(BaseModel):
    p25: float
    median: float
    p75: float
    n: int = Field(description="Number of listings the range is computed from")


class Location(BaseModel):
    query: str
    latitude: float
    longitude: float
    display_name: str | None = None
    neighbourhood: str | None = None
    geocoder: str


class CompFilters(BaseModel):
    room_type: str
    bedrooms: int | None
    bedroom_tolerance: int
    radius_m: int
    radius_mode: str = Field("auto", description="'auto' (widened as needed) or 'fixed'")
    radius_widened: bool
    min_comps_target: int
    min_reviews_ltm: int = Field(0, description="Minimum reviews in the last 12 months")
    min_rating: float | None = None
    max_minimum_nights: int | None = None
    superhost_only: bool = False


class Comp(BaseModel):
    id: int
    url: str
    name: str | None
    neighbourhood: str | None
    latitude: float
    longitude: float
    distance_m: float
    room_type: str | None
    bedrooms: int | None
    price: float
    minimum_nights: int | None
    number_of_reviews_ltm: int | None
    number_of_reviews: int | None = Field(None, description="Reviews since the listing started")
    review_scores_rating: float | None
    active_since: date | None = Field(
        None, description="Date of the first review: the listing was taking bookings by then"
    )
    host_since: date | None = Field(None, description="When the host joined Airbnb")
    est_occupancy: float
    est_revenue: float | None


class CompStats(BaseModel):
    count: int
    active_count: int = Field(description="Comps with at least one review in the last 12 months")
    nightly_price: ValueRange | None
    review_score_median: float | None
    superhost_share: float | None
    minimum_nights_median: float | None


class RevenueEstimate(BaseModel):
    method: str
    assumptions: dict[str, float]
    occupancy: ValueRange | None
    occupied_nights: ValueRange | None
    annual_revenue: ValueRange | None
    capped_share: float | None = Field(
        None, description="Share of active comps whose estimate hit the occupancy cap"
    )
    inside_airbnb_revenue: ValueRange | None = Field(
        None, description="Inside Airbnb's own estimated_revenue_l365d, when the snapshot has it"
    )


class MarketStats(BaseModel):
    radius_m: int
    total_listings: int
    entire_home_share: float | None
    multi_listing_host_share: float | None = Field(
        description="Share of listings run by hosts with several listings"
    )
    top3_hosts_share: float | None = Field(
        description="Share of listings controlled by the three largest hosts in the area"
    )
    licensed_share: float | None
    new_listings_share: float | None = Field(
        None,
        description="Share of reviewed listings whose first review is within 12 months of the "
        "snapshot: a sign of new supply",
    )


class Analysis(BaseModel):
    city: str
    snapshot_date: str
    currency: str
    location: Location
    filters: CompFilters
    comp_stats: CompStats
    revenue: RevenueEstimate
    market: MarketStats
    comps: list[Comp]
    warnings: list[str] = Field(default_factory=list)
    data_source: str = "Inside Airbnb (insideairbnb.com), CC BY 4.0"


class CalendarMonth(BaseModel):
    month: str = Field(description="YYYY-MM")
    nights: int = Field(description="Listing-nights in this month covered by the calendar")
    unavailable_share: float = Field(
        description="Share of nights not open for booking: booked OR blocked by the host"
    )
    avg_calendar_price: float | None = None


class ReviewMonth(BaseModel):
    month: str = Field(description="YYYY-MM")
    reviews: int
    reviews_per_listing: float


class Seasonality(BaseModel):
    city: str
    snapshot_date: str
    location: Location
    filters: CompFilters
    listing_count: int = Field(description="Comps the series are computed from")
    calendar_listing_count: int = Field(
        description="Comps used for the calendar series (fully blocked calendars excluded)"
    )
    forward_calendar: list[CalendarMonth] = Field(
        description="The 12 months after the snapshot, from each listing's calendar"
    )
    review_history: list[ReviewMonth] = Field(
        description="Reviews posted per month before the snapshot: a lagging demand signal"
    )
    notes: list[str] = Field(default_factory=list)


# --- Memo (Stage 3): facts the LLM may cite, the memo it writes, and the check on it ---

FactUnit = Literal["money", "share", "count", "nights", "metres", "rating", "date", "text"]


class Fact(BaseModel):
    """One citable figure from an Analysis. The memo cites it as [F7]."""

    id: str = Field(description="Fixed per key: F7 means the same thing for every address")
    key: str = Field(description="Stable name, e.g. comp_stats.nightly_price.median")
    label: str
    value: int | float | str | None = Field(description="Full precision; None = not available")
    unit: FactUnit = Field(description="'share' values are fractions: 0.26 is shown as 26%")
    display: str = Field(description="The value as the memo should write it, e.g. €8,256 or 26%")


ProblemKind = Literal[
    "untagged_number",
    "unknown_fact",
    "value_mismatch",
    "unit_mismatch",
    "spelled_out_number",
    "too_many_tags",
]


class MemoProblem(BaseModel):
    kind: ProblemKind
    section: str
    text: str = Field(description="The number, date or tag as written in the memo")
    fact_ids: list[str] = Field(default_factory=list, description="The tags it was checked against")
    detail: str = Field(description="What is wrong, in a form that can be fed back to the model")
    context: str = Field("", description="The surrounding words")


class MemoSections(BaseModel):
    """The memo's fixed structure. Every number in the text carries a [F#] tag."""

    verdict: str
    pricing: str
    occupancy_revenue: str
    competition: str
    risks: str
    checks_before_buying: str


class MemoValidation(BaseModel):
    verified: bool = Field(description="True only if every number matched a cited fact")
    problems: list[MemoProblem] = Field(default_factory=list)
    numbers_checked: int = 0
    facts_cited: list[str] = Field(default_factory=list)
