"""Occupancy and revenue estimates from review counts.

Inside Airbnb has no booking data, so occupancy is inferred from reviews:

    bookings per year  = reviews in last 12 months / review_rate
    nights per booking = max(average_stay_nights, minimum_nights)
    occupied nights    = min(bookings * nights per booking, max_occupancy * 365)
    revenue            = occupied nights * nightly price

This follows the approach on Inside Airbnb's "Data Assumptions" page. It is an estimate with
wide error bars, so the app always reports ranges, never a single number.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from stragent.config import OccupancyConfig

DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class ListingEstimate:
    bookings_per_year: float
    nights_per_booking: float
    occupied_nights: float
    occupancy: float
    revenue: float | None
    capped: bool


@dataclass(frozen=True)
class Range:
    p25: float
    median: float
    p75: float
    n: int


def estimate_listing(
    reviews_ltm: int | None,
    minimum_nights: int | None,
    price: float | None,
    cfg: OccupancyConfig,
) -> ListingEstimate:
    reviews = max(0, reviews_ltm or 0)
    bookings = reviews / cfg.review_rate
    nights_per_booking = max(cfg.average_stay_nights, float(minimum_nights or 0))
    raw_nights = bookings * nights_per_booking
    cap = cfg.max_occupancy * DAYS_PER_YEAR
    occupied = min(raw_nights, cap)
    revenue = None if price is None else occupied * price
    return ListingEstimate(
        bookings_per_year=bookings,
        nights_per_booking=nights_per_booking,
        occupied_nights=occupied,
        occupancy=occupied / DAYS_PER_YEAR,
        revenue=revenue,
        capped=raw_nights > cap,
    )


def percentile_range(values: Iterable[float | None]) -> Range | None:
    """25th / 50th / 75th percentiles, ignoring missing values. None if no data."""
    clean = np.array([v for v in values if v is not None and not np.isnan(v)], dtype=float)
    if clean.size == 0:
        return None
    p25, med, p75 = np.percentile(clean, [25, 50, 75])
    return Range(p25=float(p25), median=float(med), p75=float(p75), n=int(clean.size))


def area_estimates(
    estimates: Sequence[ListingEstimate],
) -> tuple[Range | None, Range | None, Range | None]:
    """Ranges for occupancy, occupied nights and revenue across active comps."""
    return (
        percentile_range(e.occupancy for e in estimates),
        percentile_range(e.occupied_nights for e in estimates),
        percentile_range(e.revenue for e in estimates),
    )
