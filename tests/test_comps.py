import pandas as pd
import pytest

from stragent.analytics.comps import comp_stats, filter_comps, market_stats
from stragent.config import CompsConfig, MarketConfig


def _area() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [1, 2, 3, 4, 5, 6],
            "host_id": [10, 10, 10, 20, 30, 40],
            "room_type": ["Entire home/apt"] * 4 + ["Private room", "Entire home/apt"],
            "bedrooms": [2, 3, 4, None, 1, 1],
            "price": [80.0, 95.0, 150.0, 70.0, 30.0, 5000.0],
            "calculated_host_listings_count": [3, 3, 3, 1, 1, 1],
            "license": ["123", "456", "", None, "789", "1"],
            "number_of_reviews_ltm": [10, 0, 5, 3, 2, 1],
            "review_scores_rating": [4.8, 4.6, None, 4.9, 4.5, 4.0],
            "host_is_superhost": [True, False, True, None, False, False],
            "minimum_nights": [2, 3, 2, 1, 1, 1],
        }
    )


def test_filter_comps_room_type_bedrooms_and_price():
    comps = filter_comps(_area(), "Entire home/apt", bedrooms=2, cfg=CompsConfig())
    # id 3 has 4 bedrooms (outside ±1); id 4 has unknown bedrooms; id 6 has a broken price
    assert comps["id"].tolist() == [1, 2]


def test_filter_comps_any_bedrooms():
    comps = filter_comps(_area(), "Entire home/apt", bedrooms=None, cfg=CompsConfig())
    assert comps["id"].tolist() == [1, 2, 3, 4]


def test_comp_stats():
    stats = comp_stats(filter_comps(_area(), "Entire home/apt", None, CompsConfig()))
    assert stats.count == 4
    assert stats.active_count == 3
    assert stats.nightly_price is not None and stats.nightly_price.median == pytest.approx(87.5)
    assert stats.review_score_median == pytest.approx(4.8)
    assert stats.superhost_share == pytest.approx(2 / 3)


def test_market_stats():
    m = market_stats(_area(), radius_m=500, cfg=MarketConfig(multi_listing_threshold=2))
    assert m.total_listings == 6
    assert m.entire_home_share == pytest.approx(5 / 6)
    assert m.multi_listing_host_share == pytest.approx(3 / 6)
    assert m.top3_hosts_share == pytest.approx(5 / 6)  # host 10 (3) + two single hosts
    assert m.licensed_share == pytest.approx(4 / 6)


def test_market_stats_empty():
    m = market_stats(_area().iloc[0:0], radius_m=500, cfg=MarketConfig())
    assert m.total_listings == 0 and m.entire_home_share is None


def test_quality_filters():
    from stragent.analytics.comps import CompQuality

    area = _area()
    cfg = CompsConfig()
    by = lambda q: filter_comps(area, "Entire home/apt", None, cfg, q)["id"].tolist()  # noqa: E731
    assert by(CompQuality(min_reviews_ltm=4)) == [1, 3]
    assert by(CompQuality(min_rating=4.7)) == [1, 4]  # id 3 has no rating
    assert by(CompQuality(max_minimum_nights=2)) == [1, 3, 4]
    assert by(CompQuality(superhost_only=True)) == [1, 3]
    assert by(CompQuality(min_reviews_ltm=1, min_rating=4.7)) == [1, 4]


def test_quality_describe():
    from stragent.analytics.comps import CompQuality

    assert CompQuality().describe() == []
    assert CompQuality(min_reviews_ltm=5, superhost_only=True).describe() == [
        "≥5 reviews in 12m",
        "superhosts only",
    ]
