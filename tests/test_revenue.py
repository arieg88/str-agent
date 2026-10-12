"""Hand-calculated checks of the occupancy model.

Defaults: review_rate 0.5, average stay 3 nights, cap 70% (255.5 nights).
"""

import pytest

from stragent.analytics.revenue import area_estimates, estimate_listing, percentile_range
from stragent.config import OccupancyConfig

CFG = OccupancyConfig(review_rate=0.5, average_stay_nights=3.0, max_occupancy=0.70)


def test_basic_listing():
    # 12 reviews / 0.5 = 24 bookings; 24 x 3 nights = 72 nights; 72 x 80 = 5,760
    est = estimate_listing(reviews_ltm=12, minimum_nights=2, price=80.0, cfg=CFG)
    assert est.bookings_per_year == 24
    assert est.nights_per_booking == 3
    assert est.occupied_nights == 72
    assert est.occupancy == pytest.approx(72 / 365)
    assert est.revenue == pytest.approx(5_760)
    assert not est.capped


def test_minimum_nights_above_average_stay_is_used():
    # 24 bookings x 5 nights = 120 nights; 120 x 80 = 9,600
    est = estimate_listing(reviews_ltm=12, minimum_nights=5, price=80.0, cfg=CFG)
    assert est.nights_per_booking == 5
    assert est.occupied_nights == 120
    assert est.revenue == pytest.approx(9_600)


def test_occupancy_is_capped():
    # 60 reviews -> 120 bookings x 3 = 360 nights, capped at 0.7 x 365 = 255.5
    est = estimate_listing(reviews_ltm=60, minimum_nights=1, price=100.0, cfg=CFG)
    assert est.capped
    assert est.occupied_nights == pytest.approx(255.5)
    assert est.occupancy == pytest.approx(0.70)
    assert est.revenue == pytest.approx(25_550)


def test_monthly_minimum_stay_hits_cap():
    # 4 reviews -> 8 bookings x 30 nights = 240, just under the cap
    est = estimate_listing(reviews_ltm=4, minimum_nights=30, price=50.0, cfg=CFG)
    assert est.occupied_nights == 240
    assert not est.capped


@pytest.mark.parametrize("reviews", [0, None])
def test_no_reviews_means_no_estimated_nights(reviews):
    est = estimate_listing(reviews_ltm=reviews, minimum_nights=2, price=80.0, cfg=CFG)
    assert est.occupied_nights == 0
    assert est.revenue == 0


def test_missing_price_gives_no_revenue():
    est = estimate_listing(reviews_ltm=10, minimum_nights=2, price=None, cfg=CFG)
    assert est.occupied_nights == 60
    assert est.revenue is None


def test_custom_assumptions_change_result():
    cfg = OccupancyConfig(review_rate=0.25, average_stay_nights=4.0, max_occupancy=0.9)
    # 10 / 0.25 = 40 bookings x 4 = 160 nights
    est = estimate_listing(reviews_ltm=10, minimum_nights=1, price=100.0, cfg=cfg)
    assert est.occupied_nights == 160


def test_percentile_range():
    r = percentile_range([10, 20, 30, 40, 50, None])
    assert r is not None
    assert (r.p25, r.median, r.p75, r.n) == (20, 30, 40, 5)


def test_percentile_range_empty():
    assert percentile_range([]) is None
    assert percentile_range([None]) is None


def test_area_estimates_ignores_missing_revenue():
    ests = [
        estimate_listing(12, 2, 80.0, CFG),  # 5,760
        estimate_listing(12, 2, None, CFG),  # no price
        estimate_listing(12, 2, 100.0, CFG),  # 7,200
    ]
    occ, nights, revenue = area_estimates(ests)
    assert occ is not None and occ.n == 3
    assert nights is not None and nights.median == 72
    assert revenue is not None and revenue.n == 2
    assert revenue.median == pytest.approx(6_480)
