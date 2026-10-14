"""Check that every number in a memo is a cited fact. No LLM involved.

The rules, in full:

1. What counts as a number: digits in any of these forms: 62, 1,234, 1.234, 1 234 (no-break
   space), 12.5, 12,5, €78, 78 euros, 62%, 62 percent, 0.62, 8.3k, -5, both ends of a range
   (50-70, €60 to €105, also with an en dash) and the digit in "2-bedroom". Dates count too:
   2026-09-25, 25 September 2026, September 2026, a month name on its own, and a bare year.
2. Citing: tags follow the numbers they support. A tag group ([F3], [F3][F4], [F3, F4],
   [F3-F5]) covers every number between it and the previous tag group or the start of the
   sentence. Each of those numbers must match at least one fact in the group. A number with no
   tag group after it in its sentence is untagged. A group may cite at most 4 facts, so a
   catch-all like [F1-F40] cannot be used to wave numbers through.
3. Matching (the rounding rule): a number must be the fact rounded to the precision it is
   written with: |fact - number| <= half a unit of the last digit written. So a fact of
   8,255.6 may be written €8,256 or 8.3k, but not €8,300 or €8,250.
4. Units: shares are stored as fractions (0.26). With a percent sign the number is compared
   as a percentage (26%), without one as a fraction (0.26). In a range the sign counts for
   both ends (21-36%). A percent sign on a fact that is
   not a share, or a currency sign on a fact that is not money, is a unit mismatch.
5. Dates must agree with a date fact in every part that is written (year, month, day).
6. Not counted as numbers (EXEMPT below): ordinals (25th, 3rd), the fixed phrases "12 months",
   "365 days", "24/7", "top 3" and "CC BY 4.0", and list markers at the start of a line.
7. Spelled-out quantities (two, twenty, half, twice, ...) are always a problem: the memo must
   use digits so they can be checked. "one" is allowed, it is too common as a pronoun.
8. Digits glued to a letter before them (v2, Q3, an unbracketed F12) are part of a name and
   are skipped. An unbracketed "F12" is therefore not a citation.

What this does NOT check: words. "Higher than the median [F17]" passes whatever the numbers
say. That needs a judge (Stage 6).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation

from stragent.models import Fact, MemoProblem, MemoSections, MemoValidation, ProblemKind

# --- tokens ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Number:
    text: str
    start: int
    end: int
    # Possible readings as (value, decimals written): "1.234" is 1234 or 1.234.
    readings: tuple[tuple[Decimal, int], ...]
    percent: bool = False
    money: bool = False
    year: int | None = None  # set when it could also be a bare year


@dataclass(frozen=True)
class _Date:
    text: str
    start: int
    end: int
    year: int | None
    month: int | None
    day: int | None


@dataclass(frozen=True)
class _Tags:
    text: str
    start: int
    end: int
    ids: tuple[str, ...]


@dataclass(frozen=True)
class _Word:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class _Break:
    start: int
    end: int


_Token = _Number | _Date | _Tags | _Word | _Break
_Match = tuple[_Token | None, int] | None  # (token or None for "skip this text", end position)

# --- patterns -------------------------------------------------------------------------------

_MONTH_NAMES = [
    ("January", "Jan"),
    ("February", "Feb"),
    ("March", "Mar"),
    ("April", "Apr"),
    ("May", "May"),
    ("June", "Jun"),
    ("July", "Jul"),
    ("August", "Aug"),
    ("September", "Sep"),
    ("October", "Oct"),
    ("November", "Nov"),
    ("December", "Dec"),
]
_MONTH_NUMBER = {name: i for i, names in enumerate(_MONTH_NAMES, 1) for name in names}
_MONTH_NUMBER["Sept"] = 9
_MONTH = "(" + "|".join(sorted(_MONTH_NUMBER, key=len, reverse=True)) + r")\b"
_ORD = "(?:st|nd|rd|th)?"

_TAG = re.compile(r"\[\s*F\d+(?:\s*(?:,|;|-|–|—|and|to)\s*F?\d+)*\s*\]", re.IGNORECASE)
_TAG_PART = re.compile(r"F?(\d+)(?:\s*(?:-|–|—|to)\s*F?(\d+))?", re.IGNORECASE)
_MAX_TAG_RANGE = 12  # [F3-F5] is expanded; a huge range is taken as its two ends only
# Rule 2: a group that cites many facts would let any number through, so it is refused.
MAX_FACTS_PER_GROUP = 4

_LIST_MARKER = re.compile(r"[ \t]*\d{1,2}[.)](?=\s)")

# Rule 6. Checked after dates, before numbers.
EXEMPT = [
    re.compile(r"\d+(?:st|nd|rd|th)\b", re.IGNORECASE),  # ordinals: 25th percentile, 3rd
    re.compile(r"12[- ]months?\b", re.IGNORECASE),  # the review window used throughout
    re.compile(r"365[- ]days?\b", re.IGNORECASE),
    re.compile(r"24/7"),
    re.compile(r"top[- ]3\b", re.IGNORECASE),  # as in the fact "three largest hosts"
    re.compile(r"CC BY 4\.0"),  # the data licence
]

_DATES_FROM_DIGIT: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})(?!\d)"), ("year", "month", "day")),
    (re.compile(r"(\d{4})-(\d{2})(?![\d-])"), ("year", "month")),
    (re.compile(rf"(\d{{1,2}}){_ORD}\s+{_MONTH}\.?,?\s+(\d{{4}})(?!\d)"), ("day", "name", "year")),
    (re.compile(rf"(\d{{1,2}}){_ORD}\s+{_MONTH}"), ("day", "name")),
]
_DATES_FROM_MONTH: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(rf"{_MONTH}\.?\s+(\d{{1,2}}){_ORD},?\s+(\d{{4}})(?!\d)"), ("name", "day", "year")),
    (re.compile(rf"{_MONTH}\.?\s+(\d{{4}})(?!\d)"), ("name", "year")),
    (re.compile(rf"{_MONTH}\.?\s+(\d{{1,2}}){_ORD}(?![\d.,]\d)\b"), ("name", "day")),
    (re.compile(_MONTH), ("name",)),
]
# "May" on its own is usually the verb; it is a month only after a word like these.
_BEFORE_MAY = re.compile(
    r"\b(?:in|of|since|by|during|until|till|from|to|and|or|through|between|before|after|"
    r"early|late|mid|every|each|last|next|this)[\s-]+$",
    re.IGNORECASE,
)

_BODY = r"(?:\d{1,3}(?:[.,  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?!\d)"
_NUMBER = re.compile(
    r"(?P<sign>[-−])?"
    r"(?P<pre>(?:[€$£]|EUR|USD|GBP)\s?)?"
    rf"(?P<body>{_BODY})"
    r"(?P<scale>\s?(?:k|thousand|million)\b)?"
    r"(?P<pct>\s?(?:%|percent\b|per cent\b|percentage points?\b|pp\b))?"
    r"(?P<post>\s?(?:€|euros?\b|EUR\b))?",
    re.IGNORECASE,
)
_SCALE = {"k": 3, "thousand": 3, "million": 6}

# Rule 7.
_NUMBER_WORDS = re.compile(
    r"(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|"
    r"ninety|hundred|thousand|million|dozen|half|twice)(?:-[a-z]+)?\b",
    re.IGNORECASE,
)

_ABBREVIATION = re.compile(r"(?:\be\.g|\bi\.e|\bvs|\bapprox|\best|\bmin|\bmax|\bincl|\bavg)$", re.I)
_BETWEEN_TAGS = re.compile(r"(?:\s|,|;|&|and)*", re.IGNORECASE)
_RANGE_JOIN = re.compile(r"\s*(?:-|–|—|to|and|or)\s*", re.IGNORECASE)


# --- reading a token at a position ---------------------------------------------------------


def _readings(body: str) -> tuple[tuple[Decimal, int], ...]:
    """Every way to read the digits: (value, decimals). '1.234' and '1,234' are ambiguous."""
    body = body.replace(" ", "").replace(" ", "")
    separators = [c for c in body if c in ".,"]
    if not separators:
        return ((Decimal(body), 0),)
    if len(set(separators)) == 2:  # 1,234.56 or 1.234,56: the last one is the decimal point
        decimal_mark = separators[-1]
        whole, fraction = body.rsplit(decimal_mark, 1)
        whole = whole.replace("," if decimal_mark == "." else ".", "")
        return ((Decimal(f"{whole}.{fraction}"), len(fraction)),)
    mark = separators[0]
    parts = body.split(mark)
    if len(parts) > 2:  # 1,234,567 (or 1,234,5: thousands, then a decimal)
        if all(len(p) == 3 for p in parts[1:]):
            return ((Decimal("".join(parts)), 0),)
        return ((Decimal("".join(parts[:-1]) + "." + parts[-1]), len(parts[-1])),)
    whole, fraction = parts
    as_decimal = (Decimal(f"{whole}.{fraction}"), len(fraction))
    if len(fraction) == 3 and not whole.startswith("0"):  # 1.234: thousands or three decimals
        return ((Decimal(whole + fraction), 0), as_decimal)
    return (as_decimal,)


def _number_at(text: str, pos: int) -> _Match:
    m = _NUMBER.match(text, pos)
    if not m:
        return None
    if m["sign"] and pos > 0 and not (text[pos - 1].isspace() or text[pos - 1] in "(["):
        return None  # the hyphen of a range or a compound, not a minus sign
    sign = Decimal(-1) if m["sign"] else Decimal(1)
    shift = _SCALE[m["scale"].strip().lower()] if m["scale"] else 0
    readings = tuple((sign * v.scaleb(shift), d - shift) for v, d in _readings(m["body"]))
    money = bool(m["pre"] or m["post"])
    percent = bool(m["pct"])
    year = None
    plain = not (m["sign"] or money or percent or m["scale"])
    if plain and re.fullmatch(r"(?:19|20)\d\d", m["body"]):
        year = int(m["body"])
    token = _Number(m[0].strip(), pos, m.end(), readings, percent, money, year)
    return token, m.end()


def _date_at(
    text: str, pos: int, patterns: list[tuple[re.Pattern[str], tuple[str, ...]]]
) -> _Match:
    for pattern, names in patterns:
        m = pattern.match(text, pos)
        if not m:
            continue
        parts = dict(zip(names, m.groups(), strict=True))
        if names == ("name",) and parts["name"] == "May" and not _BEFORE_MAY.search(text[:pos]):
            return None
        month = _MONTH_NUMBER[parts["name"]] if "name" in parts else int(parts.get("month", 0))
        year = int(parts["year"]) if "year" in parts else None
        day = int(parts["day"]) if "day" in parts else None
        valid = 1 <= month <= 12 and (day is None or 1 <= day <= 31)
        if not valid or (year is not None and not 1900 <= year <= 2100):
            continue
        return _Date(m[0], pos, m.end(), year, month, day), m.end()
    return None


def _tags_at(text: str, pos: int) -> _Match:
    m = _TAG.match(text, pos)
    if not m:
        return None
    ids: list[str] = []
    for part in _TAG_PART.finditer(m[0]):
        first, last = int(part[1]), int(part[2] or part[1])
        numbers = range(first, last + 1) if 0 <= last - first <= _MAX_TAG_RANGE else (first, last)
        ids.extend(f"F{n}" for n in numbers)
    return _Tags(m[0], pos, m.end(), tuple(dict.fromkeys(ids))), m.end()


def _skip(patterns: list[re.Pattern[str]]) -> Callable[[str, int], _Match]:
    def at(text: str, pos: int) -> _Match:
        for pattern in patterns:
            m = pattern.match(text, pos)
            if m:
                return None, m.end()
        return None

    return at


_exempt_at = _skip(EXEMPT)


def _tokens(text: str) -> list[_Token]:
    """Scan left to right. At each position the first rule that matches wins."""
    out: list[_Token] = []
    pos, n = 0, len(text)
    while pos < n:
        ch = text[pos]
        before = text[pos - 1] if pos else " "
        inside_word = before.isalnum() or before == "_"
        line_start = pos == 0 or before == "\n"
        hit: _Match = None
        if ch == "[":
            hit = _tags_at(text, pos)
        elif ch == "\n":
            hit = (_Break(pos, pos + 1), pos + 1)
        elif ch in ".!?":
            after = text[pos + 1] if pos + 1 < n else " "
            if (after.isspace() or after in "\"')”’") and not _ABBREVIATION.search(text[:pos]):
                hit = (_Break(pos, pos + 1), pos + 1)
        elif not inside_word:
            if line_start and (marker := _LIST_MARKER.match(text, pos)):
                hit = (None, marker.end())
            elif ch.isdigit():
                hit = (
                    _date_at(text, pos, _DATES_FROM_DIGIT)
                    or _exempt_at(text, pos)
                    or _number_at(text, pos)
                )
            elif ch in "-−€$£":
                hit = _number_at(text, pos)
            elif ch.isalpha():
                hit = (
                    _date_at(text, pos, _DATES_FROM_MONTH)
                    or _exempt_at(text, pos)
                    or (_number_at(text, pos) if ch in "EUG" else None)
                )
                if hit is None and (word := _NUMBER_WORDS.match(text, pos)):
                    hit = (_Word(word[0], pos, word.end()), word.end())
        if hit is None:
            pos += 1
            continue
        token, pos = hit
        if token is not None:
            out.append(token)
    return _share_percent_signs(text, out)


def _share_percent_signs(text: str, tokens: list[_Token]) -> list[_Token]:
    """In "21-36%" the percent sign belongs to both ends of the range."""
    for i in range(len(tokens) - 1, 0, -1):
        first, second = tokens[i - 1], tokens[i]
        if (
            isinstance(first, _Number)
            and isinstance(second, _Number)
            and second.percent
            and not (first.percent or first.money)
            and _RANGE_JOIN.fullmatch(text[first.end : second.start])
        ):
            tokens[i - 1] = replace(first, percent=True, year=None)
    return tokens


# --- matching a number against a fact ------------------------------------------------------


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _fact_date(fact: Fact) -> date | None:
    if fact.unit != "date" or not isinstance(fact.value, str):
        return None
    try:
        return date.fromisoformat(fact.value)
    except ValueError:
        return None


def _close(fact_value: Decimal, written: Decimal, decimals: int) -> bool:
    """Rule 3: the written number is the fact rounded to the precision written."""
    return abs(fact_value - written) <= Decimal(5).scaleb(-decimals - 1)


def _number_matches(token: _Number, fact: Fact) -> bool:
    if fact.unit == "date":
        when = _fact_date(fact)
        return when is not None and token.year == when.year
    if fact.unit == "text":  # a digit that is part of a quoted name, e.g. a district number
        return token.text in fact.display
    value = _decimal(fact.value)
    if value is None:
        return False
    if token.percent and fact.unit != "share":
        return False
    if token.money and fact.unit != "money":
        return False
    if token.percent:  # 26% against 0.26; without the sign a share is compared as written
        value *= 100
    return any(_close(value, written, decimals) for written, decimals in token.readings)


def _date_matches(token: _Date, fact: Fact) -> bool:
    when = _fact_date(fact)
    if when is None:
        return False
    return (
        (token.year is None or token.year == when.year)
        and (token.month is None or token.month == when.month)
        and (token.day is None or token.day == when.day)
    )


def _matches(token: _Number | _Date, fact: Fact) -> bool:
    return (
        _number_matches(token, fact) if isinstance(token, _Number) else _date_matches(token, fact)
    )


def _wrong_unit(token: _Number | _Date, cited: list[Fact]) -> str | None:
    """A reason if the way the number is written cannot fit any cited fact."""
    units = {f.unit for f in cited}
    if isinstance(token, _Date) or (token.year is not None and units == {"date"}):
        return None if "date" in units else "it is a date, but no cited fact is a date"
    if token.percent and "share" not in units:
        return "it is a percentage, but no cited fact is a share"
    if token.money and "money" not in units:
        return "it is an amount of money, but no cited fact is money"
    if units <= {"date", "text"}:
        return "the cited fact is not a number"
    return None


# --- the check ------------------------------------------------------------------------------


def _context(text: str, start: int, end: int, width: int = 40) -> str:
    left = text[max(0, start - width) : start]
    right = text[end : end + width]
    return " ".join((left + text[start:end] + right).split())


def _cite(facts: list[Fact]) -> str:
    return "; ".join(f"[{f.id}] {f.label}: {f.display}" for f in facts)


def validate_text(text: str, facts: list[Fact], section: str = "") -> MemoValidation:
    """Check one piece of memo text against the facts. See the module docstring for the rules."""
    by_id = {f.id: f for f in facts}
    problems: list[MemoProblem] = []
    checked = 0
    cited_ids: list[str] = []

    def problem(
        kind: ProblemKind, token: _Number | _Date | _Tags | _Word, ids: list[str], detail: str
    ) -> None:
        problems.append(
            MemoProblem(
                kind=kind,
                section=section,
                text=token.text,
                fact_ids=ids,
                detail=detail,
                context=_context(text, token.start, token.end),
            )
        )

    def untagged(pending: list[_Number | _Date]) -> None:
        for token in pending:
            what = "date" if isinstance(token, _Date) else "number"
            problem(
                "untagged_number",
                token,
                [],
                f"The {what} '{token.text}' has no [F#] tag after it in its sentence. Cite the "
                "fact it comes from, or remove it.",
            )

    def check(pending: list[_Number | _Date], tags: _Tags) -> None:
        ids = list(tags.ids)
        if len(ids) > MAX_FACTS_PER_GROUP:
            problem(
                "too_many_tags",
                tags,
                ids,
                f"{len(ids)} facts are cited together; cite at most {MAX_FACTS_PER_GROUP}, each "
                "right after the number it supports.",
            )
            return
        cited_ids.extend(i for i in ids if i in by_id)
        for missing in (i for i in ids if i not in by_id):
            problem("unknown_fact", tags, [missing], f"[{missing}] is not in the list of facts.")
        cited = [by_id[i] for i in ids if i in by_id]
        if not cited:
            return  # nothing to compare with; the unknown tag is already reported
        for token in pending:
            if any(_matches(token, fact) for fact in cited):
                continue
            reason = _wrong_unit(token, cited)
            if reason:
                detail = f"'{token.text}' cannot be {_cite(cited)}: {reason}."
                problem("unit_mismatch", token, ids, detail)
            else:
                detail = (
                    f"'{token.text}' does not match the cited fact. The facts say: {_cite(cited)}."
                    " Write the value exactly as given, or cite the right fact."
                )
                problem("value_mismatch", token, ids, detail)

    tokens = _tokens(text)
    pending: list[_Number | _Date] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if isinstance(token, _Break):
            untagged(pending)
            pending = []
        elif isinstance(token, _Word):
            problem(
                "spelled_out_number",
                token,
                [],
                f"'{token.text}' is a quantity written as a word. Write it in digits with a "
                "[F#] tag, or reword the sentence.",
            )
        elif isinstance(token, _Tags):
            group = token
            # [F3] [F4] and [F3], [F4] are one group
            while i + 1 < len(tokens) and isinstance(nxt := tokens[i + 1], _Tags):
                if not _BETWEEN_TAGS.fullmatch(text[group.end : nxt.start]):
                    break
                ids = tuple(dict.fromkeys(group.ids + nxt.ids))
                group = _Tags(text[group.start : nxt.end], group.start, nxt.end, ids)
                i += 1
            checked += len(pending)
            check(pending, group)
            pending = []
        else:
            pending.append(token)
        i += 1
    checked += len(pending)
    untagged(pending)
    return MemoValidation(
        verified=not problems,
        problems=problems,
        numbers_checked=checked,
        facts_cited=list(dict.fromkeys(cited_ids)),
    )


def validate_memo(sections: MemoSections, facts: list[Fact]) -> MemoValidation:
    """Check every section. verified is True only if no section has a problem."""
    problems: list[MemoProblem] = []
    checked = 0
    cited: list[str] = []
    for name in MemoSections.model_fields:
        result = validate_text(getattr(sections, name), facts, section=name)
        problems.extend(result.problems)
        checked += result.numbers_checked
        cited.extend(result.facts_cited)
    return MemoValidation(
        verified=not problems,
        problems=problems,
        numbers_checked=checked,
        facts_cited=sorted(dict.fromkeys(cited), key=lambda i: int(i[1:])),
    )
