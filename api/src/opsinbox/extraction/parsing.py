"""Deterministic parsers for dates, times and amounts as people write them.

The model reports a value and quotes the words it read. These parsers read the quote again, in
code. When code and model agree the value is trusted; when they differ the field goes to a person.
Year inference for a date written without a year is done here, never by the model.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

_MONTHS = {
    "january": 1, "jan": 1, "enero": 1, "ene": 1,
    "february": 2, "feb": 2, "febrero": 2,
    "march": 3, "mar": 3, "marzo": 3,
    "april": 4, "apr": 4, "abril": 4, "abr": 4,
    "may": 5, "mayo": 5,
    "june": 6, "jun": 6, "junio": 6,
    "july": 7, "jul": 7, "julio": 7,
    "august": 8, "aug": 8, "agosto": 8, "ago": 8,
    "september": 9, "sep": 9, "sept": 9, "septiembre": 9, "setiembre": 9,
    "october": 10, "oct": 10, "octubre": 10,
    "november": 11, "nov": 11, "noviembre": 11,
    "december": 12, "dec": 12, "diciembre": 12, "dic": 12,
}  # fmt: skip
_WEEKDAYS = {
    "monday": 0, "mon": 0, "lunes": 0,
    "tuesday": 1, "tue": 1, "tues": 1, "martes": 1,
    "wednesday": 2, "wed": 2, "miercoles": 2, "miércoles": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "jueves": 3,
    "friday": 4, "fri": 4, "viernes": 4,
    "saturday": 5, "sat": 5, "sabado": 5, "sábado": 5,
    "sunday": 6, "sun": 6, "domingo": 6,
}  # fmt: skip
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WEEKDAY_RE = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
_DAY_MONTH = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:de\s+|of\s+)?({_MONTH_RE})\b\.?(?:,?\s+(?:de\s+)?(\d{{4}}))?",
    re.IGNORECASE,
)
_MONTH_DAY = re.compile(
    rf"\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.IGNORECASE
)
_RANGE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:to|until|through|and|al|a|y|hasta el|-|–)\s*"
    rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:de\s+|of\s+)?({_MONTH_RE})\b\.?(?:,?\s+(?:de\s+)?(\d{{4}}))?",
    re.IGNORECASE,
)
_WEEKDAY = re.compile(rf"\b({_WEEKDAY_RE})\b", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedDate:
    """A date found in text."""

    value: date
    #: False when the text carried no year and it was inferred from the reference date.
    year_stated: bool
    #: False when the text names a weekday that the date does not fall on.
    weekday_consistent: bool


def _build(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _infer_year(month: int, day: int, reference: date) -> date | None:
    """First occurrence of this day and month on or after the reference date."""
    for year in (reference.year, reference.year + 1, reference.year + 2):
        candidate = _build(year, month, day)
        if candidate and candidate >= reference:
            return candidate
    return None


def parse_dates(text: str, reference: date) -> list[ParsedDate]:
    """Return every calendar date written in ``text``, in order of appearance.

    Numeric dates are read day first (``06/03/2027`` is 6 March). A date without a year becomes
    its first occurrence on or after ``reference``.
    """
    found: list[tuple[int, date, bool]] = []
    for match in _ISO.finditer(text):
        value = _build(int(match[1]), int(match[2]), int(match[3]))
        if value:
            found.append((match.start(), value, True))
    for match in _NUMERIC.finditer(text):
        value = _build(int(match[3]), int(match[2]), int(match[1]))
        if value:
            found.append((match.start(), value, True))
    for match in _RANGE.finditer(text):  # "from 4 to 6 March 2027": the first day of a range
        month = _MONTHS[match[3].lower()]
        if match[4]:
            value = _build(int(match[4]), month, int(match[1]))
        else:
            last = _infer_year(month, int(match[2]), reference)
            value = _build(last.year, month, int(match[1])) if last else None
        if value:
            found.append((match.start(), value, bool(match[4])))
    for pattern, day_group, month_group in ((_DAY_MONTH, 1, 2), (_MONTH_DAY, 2, 1)):
        for match in pattern.finditer(text):
            month = _MONTHS[match[month_group].lower()]
            day = int(match[day_group])
            if match[3]:
                value = _build(int(match[3]), month, day)
                stated = True
            else:
                value = _infer_year(month, day, reference)
                stated = False
            if value and not any(abs(pos - match.start()) < 3 for pos, _, _ in found):
                found.append((match.start(), value, stated))
    found.sort(key=lambda item: item[0])

    weekdays = [_WEEKDAYS[m[1].lower()] for m in _WEEKDAY.finditer(text)]
    results: list[ParsedDate] = []
    for _, value, stated in found:
        consistent = not weekdays or value.weekday() in weekdays
        if all(r.value != value for r in results):
            results.append(ParsedDate(value, stated, consistent))
    return results


_TIME_24 = re.compile(r"\b([01]?\d|2[0-3])[:.h]([0-5]\d)\b(?!\s*[ap]\.?m)", re.IGNORECASE)
_TIME_12 = re.compile(r"\b(1[0-2]|0?[1-9])(?:[:.]([0-5]\d))?\s*([ap])\.?\s?m\b\.?", re.IGNORECASE)
_TIME_WORDS = {"noon": "12:00", "midday": "12:00", "mediodía": "12:00", "mediodia": "12:00"}


def parse_times(text: str) -> list[str]:
    """Return every clock time written in ``text`` as ``HH:MM``."""
    found: list[tuple[int, str]] = []
    for match in _TIME_12.finditer(text):
        hour = int(match[1]) % 12 + (12 if match[3].lower() == "p" else 0)
        found.append((match.start(), f"{hour:02d}:{match[2] or '00'}"))
    for match in _TIME_24.finditer(text):
        if not any(abs(pos - match.start()) < 2 for pos, _ in found):
            found.append((match.start(), f"{int(match[1]):02d}:{match[2]}"))
    lowered = text.lower()
    for word, value in _TIME_WORDS.items():
        position = lowered.find(word)
        if position >= 0:
            found.append((position, value))
    found.sort()
    unique: list[str] = []
    for _, value in found:
        if value not in unique:
            unique.append(value)
    return unique


_AMOUNT = re.compile(r"(?<![\w.,])(\d{1,3}(?:[ ,.]\d{3})+|\d+)(?:([.,])(\d{2}))?(?![\d])")


def parse_amounts(text: str) -> list[Decimal]:
    """Return every money-like number in ``text`` (``1,164.00``, ``1.164,00``, ``1164``)."""
    results: list[Decimal] = []
    for match in _AMOUNT.finditer(text):
        whole = re.sub(r"[ ,.]", "", match[1])
        try:
            results.append(Decimal(f"{whole}.{match[3] or '00'}"))
        except InvalidOperation:
            continue
    return results


def to_decimal(value: str | None) -> Decimal | None:
    """Parse a plain or formatted amount, or return None."""
    if value is None:
        return None
    amounts = parse_amounts(value.strip())
    return amounts[0] if len(amounts) == 1 else None


def to_date(value: str | None) -> date | None:
    """Parse an ISO date, or return None."""
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def to_time(value: str | None) -> str | None:
    """Normalise a clock time to ``HH:MM``, or return None."""
    if not value:
        return None
    times = parse_times(value.strip())
    return times[0] if len(times) == 1 else None


def normalise_phone(value: str | None) -> str | None:
    """Keep the digits of a phone number, with a leading plus when present."""
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) < 6:
        return None
    return ("+" if value.strip().startswith("+") else "") + digits


def next_weekday(reference: date, weekday: int) -> date:
    """Return the first ``weekday`` strictly after ``reference`` (used by tests and the seed)."""
    return reference + timedelta(days=(weekday - reference.weekday() - 1) % 7 + 1)
