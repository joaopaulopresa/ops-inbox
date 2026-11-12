"""Locate a quoted piece of evidence in the text it claims to come from."""

import re
from difflib import SequenceMatcher

from opsinbox.schemas import TextSpan

_DIGITS = re.compile(r"\d+")


def _flexible(quote: str) -> re.Pattern[str]:
    """Regex matching ``quote`` whatever the whitespace and quote characters."""
    tokens = [re.escape(token) for token in quote.split()]
    pattern = r"\s+".join(tokens)
    pattern = pattern.replace("'", "['’]").replace('"', '["“”]')
    return re.compile(pattern, re.IGNORECASE)


def locate_in_text(text: str, quote: str | None) -> tuple[TextSpan | None, float]:
    """Find ``quote`` in ``text``.

    Returns the span and a score between 0 and 1: 1.0 for a verbatim match (ignoring case and
    whitespace), otherwise the similarity of the closest passage, which gets a span only when
    it is close enough to be the same words with a slip.
    """
    if not quote or not quote.strip() or not text:
        return None, 0.0
    match = _flexible(quote.strip()).search(text)
    if match:
        return TextSpan(start=match.start(), end=match.end()), 1.0

    needle = " ".join(quote.split()).lower()
    haystack = text.lower()
    matcher = SequenceMatcher(None, haystack, needle, autojunk=False)
    block = matcher.find_longest_match(0, len(haystack), 0, len(needle))
    if block.size == 0:
        return None, 0.0
    start = max(0, block.a - block.b)
    end = min(len(text), start + len(needle))
    passage = " ".join(haystack[start:end].split())
    score = SequenceMatcher(None, passage, needle).ratio()
    # A slip in the words is tolerated; a different digit never is: digits are dates, times,
    # amounts and identifiers, and "at 09:00" is not evidence for "at 10:00".
    if _DIGITS.findall(passage) != _DIGITS.findall(needle):
        return None, min(score, 0.3)
    if score >= 0.85:
        return TextSpan(start=start, end=end), score
    return None, score
