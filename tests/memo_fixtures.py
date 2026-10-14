"""A hand-written Analysis for the memo tests: every figure is easy to recognise."""

from stragent.models import (
    Analysis,
    CompFilters,
    CompStats,
    Location,
    MarketStats,
    RevenueEstimate,
    ValueRange,
)


def make_analysis(**changes) -> Analysis:
    analysis = Analysis(
        city="Thessaloniki",
        snapshot_date="2026-09-25",
        currency="EUR",
        location=Location(
            query="40.6326, 22.9416",
            latitude=40.6326,
            longitude=22.9416,
            neighbourhood="Kentro",
            geocoder="coordinates",
        ),
        filters=CompFilters(
            room_type="Entire home/apt",
            bedrooms=2,
            bedroom_tolerance=1,
            radius_m=500,
            radius_mode="auto",
            radius_widened=False,
            min_comps_target=10,
        ),
        comp_stats=CompStats(
            count=29,
            active_count=24,
            nightly_price=ValueRange(p25=60.0, median=77.5, p75=105.0, n=29),
            review_score_median=4.52,
            superhost_share=0.285,
            minimum_nights_median=2.0,
        ),
        revenue=RevenueEstimate(
            method="review-based occupancy model",
            assumptions={"review_rate": 0.5, "average_stay_nights": 3.0, "max_occupancy": 0.7},
            occupancy=ValueRange(p25=0.2137, median=0.2634, p75=0.3562, n=24),
            occupied_nights=ValueRange(p25=78.0, median=96.14, p75=130.0, n=24),
            annual_revenue=ValueRange(p25=5520.0, median=8255.6, p75=11800.0, n=24),
            capped_share=0.0,
            inside_airbnb_revenue=None,
        ),
        market=MarketStats(
            radius_m=500,
            total_listings=1234,
            entire_home_share=0.72,
            multi_listing_host_share=0.41,
            top3_hosts_share=0.08,
            licensed_share=0.9,
            new_listings_share=0.15,
        ),
        comps=[],
    )
    return analysis.model_copy(update=changes)
