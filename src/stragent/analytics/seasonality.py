"""Month-by-month demand signals for a set of listings.

Two complementary series:

* forward_calendar: from calendar.csv, the share of the next 12 months' nights that are not
  open for booking. That is booked OR blocked by the host, so it is an upper bound on
  occupancy, but its shape over the months shows which periods fill first.
* review_history: reviews posted per month before the snapshot. Reviews follow stays by a
  few days, so this is a lagging but real record of past demand and its seasonality.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd

from stragent.models import CalendarMonth, ReviewMonth


def forward_calendar(
    con: duckdb.DuckDBPyConnection, city: str, snapshot: str, listing_ids: list[int]
) -> tuple[list[CalendarMonth], int]:
    """Monthly unavailable share for the listings; calendars blocked end to end are dropped."""
    if not listing_ids:
        return [], 0
    rows = con.execute(
        """
        WITH cal AS (
            SELECT listing_id, date, available, price
            FROM calendar
            WHERE city = $city AND snapshot_date = CAST($snapshot AS DATE)
              AND list_contains($ids, listing_id)
        ),
        usable AS (  -- a calendar with no open night at all is almost always host-blocked
            SELECT listing_id FROM cal GROUP BY listing_id HAVING bool_or(available)
        )
        SELECT strftime(date, '%Y-%m') AS month,
               count(*) AS nights,
               avg(CASE WHEN available THEN 0.0 ELSE 1.0 END) AS unavailable_share,
               avg(price) AS avg_price,
               count(DISTINCT listing_id) AS listings
        FROM cal WHERE listing_id IN (SELECT listing_id FROM usable)
        GROUP BY 1 ORDER BY 1
        """,
        {"city": city, "snapshot": snapshot, "ids": [int(i) for i in listing_ids]},
    ).fetchall()
    usable = max((int(r[4]) for r in rows), default=0)
    months = [
        CalendarMonth(
            month=month,
            nights=int(nights),
            unavailable_share=round(float(share), 4),
            avg_calendar_price=None if price is None else round(float(price), 2),
        )
        for month, nights, share, price, _ in rows
    ]
    return months, usable


def _month_starts(end: date, months: int) -> list[str]:
    last = pd.Period(end, freq="M") - 1  # last complete month before the snapshot
    return [str(last - i) for i in range(months - 1, -1, -1)]


def review_history(
    con: duckdb.DuckDBPyConnection,
    city: str,
    snapshot: str,
    listing_ids: list[int],
    months: int = 24,
) -> list[ReviewMonth]:
    """Reviews per complete month before the snapshot, zero-filled."""
    if not listing_ids:
        return []
    labels = _month_starts(date.fromisoformat(snapshot), months)
    counts = dict(
        con.execute(
            """
            SELECT strftime(date, '%Y-%m') AS month, count(*) AS n
            FROM reviews
            WHERE city = $city AND snapshot_date = CAST($snapshot AS DATE)
              AND list_contains($ids, listing_id)
              AND strftime(date, '%Y-%m') BETWEEN $first AND $last
            GROUP BY 1
            """,
            {
                "city": city,
                "snapshot": snapshot,
                "ids": [int(i) for i in listing_ids],
                "first": labels[0],
                "last": labels[-1],
            },
        ).fetchall()
    )
    n = len(listing_ids)
    return [
        ReviewMonth(
            month=m,
            reviews=int(counts.get(m, 0)),
            reviews_per_listing=round(counts.get(m, 0) / n, 3),
        )
        for m in labels
    ]
