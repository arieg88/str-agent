"""The memo validator: every number must be a correctly cited fact.

Facts used throughout (from memo_fixtures.make_analysis):
  [F2] snapshot 2026-09-25      [F5] bedrooms 2            [F7] radius 500 m
  [F14] comps 29                [F15] active comps 24      [F16-F18] price €60 / €77.5 / €105
  [F19] review score 4.52       [F23] occupancy 0.2634     [F26] nights 96.14
  [F28-F30] revenue €5,520 / €8,255.6 / €11,800            [F32] monthly revenue 687.97
  [F37] occupancy cap 0.7       [F39] Inside Airbnb revenue: not available
  [F42] area listings 1,234     [F43] entire homes 0.72
"""

import pytest

from memo_fixtures import make_analysis
from stragent.llm.facts import build_facts
from stragent.llm.validate import validate_memo, validate_text
from stragent.models import MemoSections

FACTS = build_facts(make_analysis())


def problems(text, facts=FACTS):
    return [(p.kind, p.text) for p in validate_text(text, facts).problems]


def untagged(text):
    """The numbers the validator sees in a text with no tags at all."""
    found = validate_text(text, [])
    assert all(p.kind == "untagged_number" for p in found.problems), found.problems
    return [p.text for p in found.problems]


# --- what counts as a number ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("There are 29 comps", ["29"]),
        ("about €8,256 a year", ["€8,256"]),
        ("about 8.256 a year", ["8.256"]),
        ("about 8 256 a year", ["8 256"]),
        ("€1,234,567.89 in total", ["€1,234,567.89"]),
        ("1.234.567,89 in total", ["1.234.567,89"]),
        ("occupancy of 62%", ["62%"]),
        ("occupancy of 62 %", ["62 %"]),
        ("occupancy of 62 percent", ["62 percent"]),
        ("occupancy of 0.62", ["0.62"]),
        ("a rate of 12,5", ["12,5"]),
        ("between 50-70 a night", ["50", "70"]),
        ("between €60–€105 a night", ["€60", "€105"]),
        ("from 50 to 70 a night", ["50", "70"]),
        ("a 2-bedroom flat", ["2"]),
        ("a 2BR flat", ["2"]),
        ("roughly 8.3k a year", ["8.3k"]),
        ("roughly 8 thousand a year", ["8 thousand"]),
        ("78 euros a night", ["78 euros"]),
        ("EUR 78 a night", ["EUR 78"]),
        ("a drop of -5%", ["-5%"]),
        ("prices of 60, 78 and 105", ["60", "78", "105"]),
        ("It costs 78.", ["78"]),
        ("(29 comps)", ["29"]),
        ("3x the median", ["3"]),
    ],
)
def test_numbers_are_found(text, expected):
    assert untagged(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("the snapshot of 2026-09-25", ["2026-09-25"]),
        ("the snapshot of 25 September 2026", ["25 September 2026"]),
        ("the snapshot of September 25, 2026", ["September 25, 2026"]),
        ("the snapshot of 25th Sept. 2026", ["25th Sept. 2026"]),
        ("data from September 2026", ["September 2026"]),
        ("data from 2026-09", ["2026-09"]),
        ("data from 2026", ["2026"]),
        ("demand peaks in August", ["August"]),
        ("demand peaks in May", ["May"]),
        ("from May to September", ["May", "September"]),
        ("on 25 September", ["25 September"]),
    ],
)
def test_dates_are_found(text, expected):
    assert untagged(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "The 25th–75th percentile range is wide.",
        "the 3rd largest host",
        "reviews in the last 12 months",
        "a 12-month window",
        "available 365 days a year",
        "guests arrive 24/7",
        "the top 3 hosts",
        "Data: Inside Airbnb, CC BY 4.0",
        "1. Check the licence.\n2. Visit the street.\n 3) Ask about noise.",
        "Prices may rise.",
        "May be worth a visit.",
        "the v2 model and Q3 results",
        "one risk is noise, and one more is parking",
        "double-check the licence",
        "",
    ],
)
def test_not_numbers(text):
    assert problems(text) == []


@pytest.mark.parametrize(
    "word", ["two", "Three", "twenty", "twenty-five", "half", "twice", "a dozen", "hundred"]
)
def test_spelled_out_quantities_are_rejected(word):
    found = problems(f"There are {word} comps nearby [F14].")
    assert [kind for kind, _ in found] == ["spelled_out_number"]


def test_words_that_only_contain_a_number_word_are_fine():
    assert problems("The network is halfway there; thousands visit the waterfront often.") == []


# --- citing ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "There are 29 [F14] comps.",
        "There are 29 comps [F14].",
        "Comps charge €78 [F17] a night.",
        "Comps charge 78 euros [F17] a night.",
        "Comps charge €77.50 [F17] a night.",
        "Comps charge €77.5 [F17] a night.",
        "Prices run from €60 [F16] to €105 [F18], with a median of €78 [F17].",
        "Prices run from €60 to €105 [F16][F18].",
        "Prices run from €60–€105 [F16, F18].",
        "Prices run from €60–€105 [F16] [F18].",
        "Prices run from €60–€105 [F16 and F18].",
        "Prices are €60, €78 and €105 [F16-F18].",
        "Prices are €60, €78 and €105 [F16–18].",
        "Revenue is about €8,256 [F29] a year, or €688 [F32] a month.",
        "Revenue is about 8.256 [F29] a year.",
        "Revenue is about 8,256 [F29] a year.",
        "Revenue is about €8.3k [F29] a year.",
        "Revenue is about €8k [F29] a year.",
        "Revenue is about €8,255.60 [F29] a year.",
        "Occupancy is 26% [F23].",
        "Occupancy is 26.3% [F23].",
        "Occupancy is 26.34 percent [F23].",
        "Occupancy is 0.26 [F23].",
        "Occupancy is 0.2634 [F23].",
        "Occupancy is capped at 70% [F37].",
        "Occupancy runs from 21–36% [F22][F24].",
        "Occupancy is between 21 and 36% [F22, F24].",
        "Occupancy is 21 to 36 percent [F22][F24], median 26% [f23].",
        "**€78** [F17] a night, *€8,256* [F29] a year.",
        "- €78 [F17] a night\n- €8,256 [F29] a year",
        "Comps are booked 96 [F26] nights a year.",
        "Comps are booked 96.1 [F26] nights a year.",
        "A 2-bedroom [F5] flat within 500 m [F7].",
        "The area has 1,234 [F42] listings.",
        "The area has 1.234 [F42] listings.",
        "The area has 1234 [F42] listings.",
        "The review score is 4.52 [F19], or 4.5 [F19] rounded.",
        "Data as of 2026-09-25 [F2].",
        "Data as of 25 September 2026 [F2].",
        "Data as of September 25, 2026 [F2].",
        "Data as of September 2026 [F2].",
        "Data as of Sept 2026 [F2].",
        "Data from 2026 [F2].",
        "The September [F2] snapshot.",
        "In Kentro [F3], entire homes [F4] dominate.",
        "Of 29 comps, 24 had recent reviews [F14][F15].",
    ],
)
def test_correctly_cited_numbers_pass(text):
    result = validate_text(text, FACTS)
    assert result.problems == []
    assert result.verified


def test_counts_and_cited_facts_are_reported():
    result = validate_text("Prices run from €60 [F16] to €105 [F18]. In Kentro [F3].", FACTS)
    assert result.numbers_checked == 2
    assert result.facts_cited == ["F16", "F18", "F3"]


# --- the three adversarial cases ------------------------------------------------------------


def test_wrong_number_with_a_valid_tag():
    result = validate_text("Comps charge €99 [F17] a night.", FACTS)
    assert not result.verified
    (problem,) = result.problems
    assert (problem.kind, problem.text, problem.fact_ids) == ("value_mismatch", "€99", ["F17"])
    assert "€78" in problem.detail  # tells the model what the fact says
    assert "€99 [F17]" in problem.context


def test_tag_to_a_missing_fact():
    result = validate_text("Comps charge €78 [F99] a night.", FACTS)
    assert not result.verified
    (problem,) = result.problems
    assert (problem.kind, problem.text, problem.fact_ids) == ("unknown_fact", "[F99]", ["F99"])


def test_untagged_number():
    result = validate_text("Comps charge €78 a night. The area is busy [F14].", FACTS)
    assert not result.verified
    (problem,) = result.problems
    assert (problem.kind, problem.text) == ("untagged_number", "€78")


# --- more ways to get it wrong --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # rounding: only to the precision written
        ("Revenue is €8,300 [F29].", [("value_mismatch", "€8,300")]),
        ("Revenue is €8,250 [F29].", [("value_mismatch", "€8,250")]),
        ("Revenue is €8,257 [F29].", [("value_mismatch", "€8,257")]),
        ("Revenue is €8.4k [F29].", [("value_mismatch", "€8.4k")]),
        ("Occupancy is 27% [F23].", [("value_mismatch", "27%")]),
        ("Occupancy is 26.4% [F23].", [("value_mismatch", "26.4%")]),
        ("Occupancy is 0.27 [F23].", [("value_mismatch", "0.27")]),
        ("The score is 4.6 [F19].", [("value_mismatch", "4.6")]),
        # the right number cited to the wrong fact
        ("There are 24 [F14] comps.", [("value_mismatch", "24")]),
        (
            "Prices run from €60 [F18] to €105 [F16].",
            [("value_mismatch", "€60"), ("value_mismatch", "€105")],
        ),
        # one of two numbers is wrong
        ("Prices run from €60 to €99 [F16][F18].", [("value_mismatch", "€99")]),
        ("Prices are €60 and €99 [F16].", [("value_mismatch", "€99")]),
        # a tag covers only the numbers before it
        ("The median is €78 [F17], against €60 at the low end.", [("untagged_number", "€60")]),
        ("[F17] The median is €78.", [("untagged_number", "€78")]),
        ("The median is €78. [F17]", [("untagged_number", "€78")]),
        ("The median is €78 (F17).", [("untagged_number", "€78")]),
        ("The median is €78 F17.", [("untagged_number", "€78")]),
        # units
        ("Occupancy is 26 [F23].", [("value_mismatch", "26")]),
        ("Occupancy is €26 [F23].", [("unit_mismatch", "€26")]),
        ("There are 29% [F14] comps.", [("unit_mismatch", "29%")]),
        ("Comps charge 78% [F17].", [("unit_mismatch", "78%")]),
        ("There are €29 [F14] comps.", [("unit_mismatch", "€29")]),
        ("A 2-bedroom entire home [F4].", [("unit_mismatch", "2")]),
        ("About 500 [F2].", [("unit_mismatch", "500")]),
        ("In September [F14].", [("unit_mismatch", "September")]),
        # dates
        ("Data as of 2025 [F2].", [("value_mismatch", "2025")]),
        ("Data as of October 2026 [F2].", [("value_mismatch", "October 2026")]),
        ("Data as of 24 September 2026 [F2].", [("value_mismatch", "24 September 2026")]),
        ("Data as of 2026-09-24 [F2].", [("value_mismatch", "2026-09-24")]),
        ("Demand peaks in August.", [("untagged_number", "August")]),
        ("Demand peaks in August. Comps charge €78 [F17].", [("untagged_number", "August")]),
        # a fact that is not available cannot back a number
        ("Inside Airbnb puts it at €9,000 [F39].", [("value_mismatch", "€9,000")]),
        # computed by the model: no fact has it
        ("That is €22 [F29] a day.", [("value_mismatch", "€22")]),
        ("Prices vary by €45 [F16][F18].", [("value_mismatch", "€45")]),
        ("About 83% [F14][F15] of comps are active.", [("unit_mismatch", "83%")]),
        ("Occupancy runs from 21–40% [F22][F24].", [("value_mismatch", "40%")]),
        ("Occupancy runs from 2026–36% [F2][F24].", [("value_mismatch", "2026")]),
        # a catch-all tag group
        ("Superhosts run 29% [F14-F23] of comps.", [("too_many_tags", "[F14-F23]")]),
        (
            "Occupancy is 29% [F20][F21][F22][F23][F24].",
            [("too_many_tags", "[F20][F21][F22][F23][F24]")],
        ),
        # tags
        ("Comps charge €78 [F17][F99].", [("unknown_fact", "[F17][F99]")]),
        ("Comps charge €78 [F0].", [("unknown_fact", "[F0]")]),
    ],
)
def test_problems(text, expected):
    assert problems(text) == expected


def test_year_like_counts_are_checked_as_numbers_too():
    base = make_analysis()
    facts = build_facts(
        make_analysis(market=base.market.model_copy(update={"total_listings": 2026}))
    )
    assert problems("The area has 2026 [F42] listings.", facts) == []
    assert problems("The area has 2025 [F42] listings.", facts) == [("value_mismatch", "2025")]


def test_digits_inside_a_quoted_name_are_allowed():
    base = make_analysis()
    facts = build_facts(
        make_analysis(location=base.location.model_copy(update={"neighbourhood": "District 5"}))
    )
    assert problems("The flat is in District 5 [F3].", facts) == []
    assert problems("The flat is in District 6 [F3].", facts) == [("unit_mismatch", "6")]


def test_sentence_breaks():
    assert problems("It earns approx. €8,256 [F29] a year.") == []
    assert problems("Comps (e.g. the nearest) charge €78 [F17].") == []
    assert problems("It earns €8,256\n[F29] a year.") == [("untagged_number", "€8,256")]
    assert problems("Is €78 fair? Yes [F17].") == [("untagged_number", "€78")]


def test_problem_detail_can_be_fed_back():
    (problem,) = validate_text("Occupancy is 31% [F23] here.", FACTS).problems
    assert "[F23]" in problem.detail and "26%" in problem.detail and "'31%'" in problem.detail
    (problem,) = validate_text("Occupancy is 31% here.", FACTS).problems
    assert "no [F#] tag" in problem.detail


# --- whole memo -----------------------------------------------------------------------------

GOOD = MemoSections(
    verdict="A 2-bedroom [F5] flat in Kentro [F3] looks viable, on data from September 2026 [F2].",
    pricing="Comps charge €60–€105 [F16][F18] a night, with a median of €78 [F17].",
    occupancy_revenue=(
        "Estimated occupancy is 26% [F23], about 96 [F26] nights, which gives roughly "
        "€8,256 [F29] a year (€5,520–€11,800 [F28][F30]) or €688 [F32] a month."
    ),
    competition=(
        "There are 29 [F14] comps within 500 m [F7]; 72% [F43] of area listings are entire homes."
    ),
    risks=(
        "1. Occupancy is inferred from reviews and capped at 70% [F37].\n"
        "2. Seasonality is not modelled."
    ),
    checks_before_buying="Check the licence, and compare with the 25th–75th percentile range.",
)


def test_a_clean_memo_is_verified():
    result = validate_memo(GOOD, FACTS)
    assert result.problems == []
    assert result.verified
    assert result.numbers_checked == 15
    assert result.facts_cited[:3] == ["F2", "F3", "F5"]


def test_one_bad_number_fails_the_memo_and_names_the_section():
    bad = GOOD.model_copy(update={"pricing": "Comps charge about €85 [F17] a night."})
    result = validate_memo(bad, FACTS)
    assert not result.verified
    assert [(p.section, p.kind, p.text) for p in result.problems] == [
        ("pricing", "value_mismatch", "€85")
    ]


def test_problems_are_collected_across_sections():
    bad = GOOD.model_copy(
        update={"verdict": "Expect 40% occupancy.", "risks": "Prices fell 12% [F99] last year."}
    )
    result = validate_memo(bad, FACTS)
    assert [(p.section, p.kind) for p in result.problems] == [
        ("verdict", "untagged_number"),
        ("risks", "unknown_fact"),
    ]
