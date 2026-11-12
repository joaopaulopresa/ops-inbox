"""Field-by-field comparison of extracted values with the ground truth."""

from typing import Any

from opsinbox.extraction.extractor import fold
from opsinbox.schemas import Extraction
from opsinbox.seedgen.labels import Label

Errors = dict[str, tuple[Any, Any]]
Counted = dict[str, int]


def _tokens(text: str) -> set[str]:
    return set(fold(text).replace(",", " ").replace("'", " ").split())


def same_value(name: str, expected: str | None, got: str | None) -> bool:
    """Whether an extracted value counts as correct."""
    if name == "pickup_location" and expected == "garage" and got is None:
        return True  # no location given means our garage (business rule)
    if expected is None or got is None:
        return expected is None and got is None
    if name in ("customer_name", "holder_name", "supplier_name"):
        return fold(expected) == fold(got)
    if name == "pickup_location":
        if expected == "garage":
            return bool(_tokens(got) & {"garage", "garaje", "premises", "larkspur"})
        want, have = _tokens(expected), _tokens(got)
        return want <= have or (bool(have) and have <= want and len(have) >= len(want) - 1)
    return expected == got


def booking_errors(label: Label, extraction: Extraction) -> tuple[Errors, Counted]:
    """Compare a booking (or the changed fields of one)."""
    assert label.booking is not None
    errors: Errors = {}
    counted: Counted = {}
    for name, expected in label.booking.items():
        got = extraction.value(name)
        counted[name] = 1
        if not same_value(name, expected, got):
            errors[name] = (expected, got)
    if label.category.value == "new_booking":
        got_extras = sorted(set(extraction.values("extras")))
        counted["extras"] = 1
        if got_extras != sorted(label.extras):
            errors["extras"] = (sorted(label.extras), got_extras)
    return errors, counted


def licence_errors(truth: dict[str, str], extraction: Extraction) -> tuple[Errors, Counted]:
    """Compare a driving licence."""
    errors: Errors = {}
    for name, expected in truth.items():
        got = extraction.value(name)
        if not same_value(name, expected, got):
            errors[name] = (expected, got)
    return errors, dict.fromkeys(truth, 1)


def invoice_errors(truth: dict[str, str], extraction: Extraction) -> tuple[Errors, Counted]:
    """Compare a supplier invoice; ``lines`` is the number of lines read."""
    errors: Errors = {}
    for name, expected in truth.items():
        got = str(len(extraction.lines)) if name == "lines" else extraction.value(name)
        if not same_value(name, expected, got):
            errors[name] = (expected, got)
    return errors, dict.fromkeys(truth, 1)
