"""Analysis -> facts: fixed ids, full-precision values, the display the memo should copy."""

import pytest

from conftest import CENTRE, CoordsOnly
from memo_fixtures import make_analysis
from stragent.analysis import analyze
from stragent.db import connect
from stragent.llm.facts import CATALOGUE, build_facts, display, fact_lines, facts_hash, half_up


def by_key(facts):
    return {f.key: f for f in facts}


def test_ids_follow_the_catalogue_order():
    facts = build_facts(make_analysis())
    assert [f.id for f in facts] == [f"F{i}" for i in range(1, len(CATALOGUE) + 1)]
    assert [f.key for f in facts] == [e.key for e in CATALOGUE]
    assert len({f.key for f in facts}) == len(facts)


def test_first_ids_are_pinned():
    # Adding a fact anywhere but the end renumbers the rest and breaks cached memos and the
    # golden set. If this fails, move the new entry to the end of CATALOGUE.
    keys = [f.key for f in build_facts(make_analysis())]
    assert keys[:5] == [
        "city",
        "snapshot_date",
        "location.neighbourhood",
        "filters.room_type",
        "filters.bedrooms",
    ]
    assert keys.index("comp_stats.count") == 13
    assert keys.index("comp_stats.nightly_price.median") == 16
    assert keys.index("revenue.annual_revenue.median") == 28
    assert keys[-1] == "market.new_listings_share"


def test_inputs_are_facts():
    facts = by_key(build_facts(make_analysis()))
    assert facts["filters.bedrooms"].value == 2 and facts["filters.bedrooms"].unit == "count"
    assert facts["filters.radius_m"].display == "500 m"
    assert facts["snapshot_date"].value == "2026-09-25" and facts["snapshot_date"].unit == "date"
    assert facts["filters.radius_mode"].display == "automatic"


def test_values_keep_full_precision_and_displays_are_rounded():
    facts = by_key(build_facts(make_analysis()))
    revenue = facts["revenue.annual_revenue.median"]
    assert revenue.value == 8255.6 and revenue.display == "€8,256" and revenue.unit == "money"
    assert facts["comp_stats.nightly_price.median"].display == "€78"  # 77.5 rounds half up
    assert facts["revenue.occupancy.median"].value == 0.2634
    assert facts["revenue.occupancy.median"].display == "26%"
    assert facts["comp_stats.superhost_share"].display == "29%"  # 0.285, not float 28.4999
    assert facts["comp_stats.review_score_median"].display == "4.52"
    assert facts["revenue.assumptions.average_stay_nights"].display == "3"
    assert facts["revenue.occupied_nights.median"].display == "96.1"
    assert facts["market.total_listings"].display == "1,234"


def test_monthly_revenue_is_derived_in_python():
    facts = by_key(build_facts(make_analysis()))
    assert facts["revenue.monthly_revenue.median"].value == pytest.approx(8255.6 / 12)
    assert facts["revenue.monthly_revenue.median"].display == "€688"
    assert facts["revenue.monthly_revenue.p25"].display == "€460"


def test_missing_values_keep_their_id():
    full = build_facts(make_analysis())
    base = make_analysis()
    sparse = build_facts(
        make_analysis(
            filters=base.filters.model_copy(update={"bedrooms": None}),
            comp_stats=base.comp_stats.model_copy(update={"nightly_price": None, "count": 0}),
            location=base.location.model_copy(update={"neighbourhood": None}),
        )
    )
    assert [f.id for f in sparse] == [f.id for f in full]
    facts = by_key(sparse)
    assert facts["filters.bedrooms"].value is None
    assert facts["filters.bedrooms"].display == "any number of bedrooms"
    assert facts["comp_stats.nightly_price.median"].display == "not available"
    assert facts["location.neighbourhood"].display == "outside the mapped neighbourhoods"
    assert facts["revenue.inside_airbnb_revenue.median"].value is None
    assert facts["comp_stats.count"].value == 0 and facts["comp_stats.count"].display == "0"


def test_radius_mode_wording():
    base = make_analysis()
    widened = make_analysis(filters=base.filters.model_copy(update={"radius_widened": True}))
    fixed = make_analysis(filters=base.filters.model_copy(update={"radius_mode": "fixed"}))
    assert "widened" in by_key(build_facts(widened))["filters.radius_mode"].display
    assert by_key(build_facts(fixed))["filters.radius_mode"].display == "fixed by the user"


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (0.5, "count", "1"),
        (1.5, "count", "2"),
        (2.5, "count", "3"),  # round() would give 2
        (2.5, "nights", "2.5"),
        (255.5, "nights", "255.5"),
        (4.0, "rating", "4"),
        (4.456, "rating", "4.46"),
        (0.005, "share", "1%"),
        (1.0, "share", "100%"),
        (1234567.4, "money", "€1,234,567"),
        (None, "money", "not available"),
        ("Kentro", "text", "Kentro"),
    ],
)
def test_display(value, unit, expected):
    assert display(value, unit, "EUR") == expected


def test_display_other_currencies():
    assert display(1500, "money", "USD") == "$1,500"
    assert display(1500, "money", "CHF") == "CHF 1,500"


def test_half_up():
    assert str(half_up(0.125, 2)) == "0.13"
    assert str(half_up(-2.5)) == "-3"


def test_fact_lines():
    lines = fact_lines(build_facts(make_analysis())).splitlines()
    assert lines[0] == "[F1] City: Thessaloniki"
    assert "[F17] Nightly price of comps: median: €78" in lines
    assert len(lines) == len(CATALOGUE)


def test_hash_is_stable_and_sensitive():
    a, b = build_facts(make_analysis()), build_facts(make_analysis())
    assert facts_hash(a) == facts_hash(b) and len(facts_hash(a)) == 16
    base = make_analysis()
    other = make_analysis(comp_stats=base.comp_stats.model_copy(update={"count": 30}))
    assert facts_hash(build_facts(other)) != facts_hash(a)


@pytest.mark.integration
def test_facts_from_a_real_analysis(db, settings):
    con = connect(db, read_only=True)
    try:
        analysis = analyze(con, settings, "thessaloniki", CENTRE, CoordsOnly(), bedrooms=2)
    finally:
        con.close()
    facts = by_key(build_facts(analysis))
    assert facts["comp_stats.count"].value == analysis.comp_stats.count
    assert facts["revenue.annual_revenue.median"].value == analysis.revenue.annual_revenue.median
    assert facts["snapshot_date"].value == analysis.snapshot_date
    assert all(f.display for f in facts.values())
