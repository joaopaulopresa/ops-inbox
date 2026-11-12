"""Facts: everything the rules may look at, computed by code from the extraction and records.

Money, dates, availability and duplicates are worked out here. The model only supplied the
values it read; nothing in this module asks it anything.
"""

import re
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from opsinbox.extraction.extractor import MailContext, fold
from opsinbox.extraction.parsing import parse_dates, to_date, to_decimal
from opsinbox.extraction.suspicion import scan_sender, scan_text
from opsinbox.records.business import Business
from opsinbox.records.pricing import quote, rental_days
from opsinbox.records.view import BookingSnapshot, RecordsView
from opsinbox.schemas import Category, ClassificationRead, Extra, Extraction

REFERENCE = re.compile(r"\bLCH-B-\d{4}-\d{4}\b", re.IGNORECASE)


def _combine(day: str | None, clock: str | None) -> datetime | None:
    parsed = to_date(day)
    if parsed is None or clock is None:
        return None
    try:
        return datetime.combine(parsed, time.fromisoformat(clock))
    except ValueError:
        return None


def name_similarity(first: str | None, second: str | None) -> float | None:
    """How alike two person names are, ignoring case, accents and middle names."""
    if not first or not second:
        return None
    a, b = fold(first).split(), fold(second).split()
    if set(a) <= set(b) or set(b) <= set(a):
        return 1.0
    return round(SequenceMatcher(None, " ".join(a), " ".join(b)).ratio(), 3)


def find_booking(
    mail: MailContext,
    message_ids: list[str],
    classification: ClassificationRead | None,
    records: RecordsView,
) -> BookingSnapshot | None:
    """Find the booking a change or cancellation is about: thread, then cited reference."""
    found = records.booking_for_thread(message_ids)
    if found:
        return found
    for text in (mail.subject, mail.visible_body()):
        for match in REFERENCE.finditer(text):
            found = records.booking(match.group(0).upper())
            if found:
                return found
    if classification and classification.booking_reference:
        return records.booking(classification.booking_reference)
    return None


def booking_signals(mail: MailContext, business: Business) -> bool:
    """Whether the text names one of our cars or a rental-like date (used to guard archiving)."""
    text = f"{mail.subject}\n{mail.visible_body()}"
    if business.match_car(text) is not None:
        return True
    return any(d.value > mail.sent_on for d in parse_dates(text, mail.sent_on))


def build_facts(
    *,
    category: Category,
    mail: MailContext,
    message_ids: list[str],
    reply_to: str,
    received_at: datetime,
    classification: ClassificationRead | None,
    booking: Extraction | None,
    licences: list[Extraction],
    invoices: list[Extraction],
    document_texts: list[str],
    business: Business,
    records: RecordsView,
) -> dict[str, Any]:
    """Compute the facts of one case. ``received_at`` is naive local business time."""
    facts: dict[str, Any] = {
        "category": category.value,
        "sender": mail.from_address,
        "received_at": received_at,
        "required_licence_class": business.required_licence_class,
        "minimum_driver_age": business.minimum_driver_age,
        "price_tolerance": business.price_tolerance,
    }

    # Suspicion is computed for every e-mail, whatever its category.
    suspicions = [f"{s.kind}: {s.detail}" for s in scan_text(f"{mail.subject}\n{mail.body}")]
    for text in document_texts:
        suspicions += [f"{s.kind} (attachment): {s.detail}" for s in scan_text(text)]
    for document in (*licences, *invoices):
        if document.instructions:
            suspicions.append(
                f"text on the {document.document} directs processing: {document.instructions[:160]}"
            )
    if classification and classification.asks_to_bypass_process:
        evidence = classification.bypass_evidence or "the e-mail tries to bypass the process"
        suspicions.append(f"flagged by the reader: {evidence[:160]}")
    facts["suspicions"] = suspicions
    facts["sender_warnings"] = [
        f"{s.detail}"
        for s in scan_sender(
            mail.from_address,
            mail.from_name,
            reply_to,
            business.email.split("@")[1],
            business.name,
        )
    ]
    facts["booking_signals"] = booking_signals(mail, business)

    if category == Category.NEW_BOOKING and booking is not None:
        _new_booking_facts(facts, mail, booking, licences, business, records)
    elif category in (Category.CHANGE, Category.CANCELLATION):
        _existing_booking_facts(
            facts, category, mail, message_ids, classification, booking, business, records
        )
    elif category == Category.SUPPLIER_INVOICE:
        _invoice_facts(facts, mail, invoices, records)
    return facts


def _period_facts(
    facts: dict[str, Any],
    car: str | None,
    pickup_at: datetime | None,
    return_at: datetime | None,
    extras: list[Extra],
    business: Business,
    records: RecordsView,
    exclude: str | None = None,
) -> None:
    facts["pickup_at"], facts["return_at"] = pickup_at, return_at
    facts["conflicts"] = None
    facts["rental_days"] = None
    facts["tariff_total"] = None
    if pickup_at and return_at and return_at > pickup_at:
        facts["rental_days"] = rental_days(pickup_at, return_at)
        if car:
            facts["conflicts"] = records.conflicts(car, pickup_at, return_at, exclude=exclude)
            car_record = business.car(car)
            if car_record:
                facts["tariff_total"] = quote(
                    business, car_record, pickup_at, return_at, extras
                ).total


def _new_booking_facts(
    facts: dict[str, Any],
    mail: MailContext,
    booking: Extraction,
    licences: list[Extraction],
    business: Business,
    records: RecordsView,
) -> None:
    for name in (
        "customer_name",
        "customer_phone",
        "pickup_date",
        "pickup_time",
        "return_date",
        "return_time",
        "pickup_location",
        "purpose",
    ):
        facts[name] = booking.value(name)
    car_field = booking.get("car")
    facts["car"] = car_field.value if car_field else None
    facts["car_words"] = (car_field.evidence or car_field.value) if car_field else None
    extras = [Extra(v) for v in booking.values("extras") if v in {e.value for e in Extra}]
    facts["extras"] = sorted(e.value for e in extras)
    pickup_at = _combine(facts["pickup_date"], facts["pickup_time"])
    return_at = _combine(facts["return_date"], facts["return_time"])
    _period_facts(facts, facts["car"], pickup_at, return_at, extras, business, records)
    facts["quoted_total"] = to_decimal(booking.value("quoted_total"))
    facts["duplicate_of"] = (
        records.duplicate_of(mail.from_address, facts["car"], pickup_at.date().isoformat())
        if facts["car"] and pickup_at
        else None
    )

    facts["licence_count"] = len(licences)
    licence: dict[str, Any] | None = None
    if licences:
        first = licences[0]
        classes = first.value("classes")
        licence = {
            "holder_name": first.value("holder_name"),
            "licence_number": first.value("licence_number"),
            "date_of_birth": to_date(first.value("date_of_birth")),
            "expires_on": to_date(first.value("expires_on")),
            "classes": classes.split() if classes else None,
        }
    facts["licence"] = licence
    facts["holder_name_similarity"] = name_similarity(
        licence["holder_name"] if licence else None, facts["customer_name"]
    )


def _existing_booking_facts(
    facts: dict[str, Any],
    category: Category,
    mail: MailContext,
    message_ids: list[str],
    classification: ClassificationRead | None,
    change: Extraction | None,
    business: Business,
    records: RecordsView,
) -> None:
    existing = find_booking(mail, message_ids, classification, records)
    facts["booking_reference"] = existing.reference if existing else None
    facts["booking_status"] = existing.status if existing else None
    facts["sender_is_customer"] = (
        existing.customer_email.lower() == mail.from_address.lower() if existing else None
    )
    if existing is None or category != Category.CHANGE:
        return
    changed = {f.name: f.value for f in (change.fields if change else []) if f.value is not None}
    changed.pop("customer_name", None)  # the signature, not a change
    changed.pop("customer_phone", None)
    car: str | None = existing.car
    car_field = change.get("car") if change else None
    if car_field is not None and (car_field.value or car_field.evidence):
        # A car was asked for: ours if recognised, otherwise unknown (the rule stops it).
        car = car_field.value
        changed["car"] = car_field.value or car_field.evidence or ""
        facts["car_words"] = car_field.evidence
    facts["changes"] = sorted(changed)
    facts["car"] = car
    pickup_at = _combine(
        changed.get("pickup_date", existing.pickup_at.date().isoformat()),
        changed.get("pickup_time", existing.pickup_at.strftime("%H:%M")),
    )
    return_at = _combine(
        changed.get("return_date", existing.return_at.date().isoformat()),
        changed.get("return_time", existing.return_at.strftime("%H:%M")),
    )
    extras = [Extra(e) for e in existing.extras]
    _period_facts(
        facts, car, pickup_at, return_at, extras, business, records, exclude=existing.reference
    )


def _invoice_facts(
    facts: dict[str, Any], mail: MailContext, invoices: list[Extraction], records: RecordsView
) -> None:
    facts["invoice_count"] = len(invoices)
    if not invoices:
        return
    invoice = invoices[0]
    for name in ("supplier_name", "invoice_number", "invoice_date"):
        facts[name] = invoice.value(name)
    for name in ("subtotal", "tax", "total"):
        facts[name] = to_decimal(invoice.value(name))
    amounts = [to_decimal(line.amount) for line in invoice.lines]
    facts["lines_total"] = (
        sum((a for a in amounts if a is not None), Decimal(0))
        if amounts and all(a is not None for a in amounts)
        else None
    )
    supplier_name = facts["supplier_name"] or ""
    known = next(
        (s for s in records.suppliers() if (name_similarity(s.name, supplier_name) or 0) >= 0.9),
        None,
    )
    facts["known_supplier"] = known.name if known else None
    sender_domain = mail.from_address.rsplit("@", 1)[-1]
    facts["sender_domain_matches_supplier"] = bool(
        known and known.email.rsplit("@", 1)[-1] == sender_domain
    )
    facts["invoice_already_recorded"] = (
        records.invoice_recorded(known.name, facts["invoice_number"])
        if known and facts["invoice_number"]
        else False
    )


def jsonable(facts: Mapping[str, Any]) -> dict[str, Any]:
    """Facts in a JSON-friendly form, tagged so :func:`from_jsonable` restores the types."""

    def encode(value: Any) -> Any:
        if isinstance(value, datetime):
            return {"$datetime": value.isoformat()}
        if isinstance(value, date):
            return {"$date": value.isoformat()}
        if isinstance(value, Decimal):
            return {"$decimal": str(value)}
        if isinstance(value, Mapping):
            return {k: encode(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [encode(v) for v in value]
        return value

    return {k: encode(v) for k, v in facts.items()}


def from_jsonable(data: Mapping[str, Any]) -> dict[str, Any]:
    """Inverse of :func:`jsonable`."""

    def decode(value: Any) -> Any:
        if isinstance(value, Mapping):
            if "$datetime" in value:
                return datetime.fromisoformat(value["$datetime"])
            if "$date" in value:
                return date.fromisoformat(value["$date"])
            if "$decimal" in value:
                return Decimal(value["$decimal"])
            return {k: decode(v) for k, v in value.items()}
        if isinstance(value, list):
            return [decode(v) for v in value]
        return value

    return {k: decode(v) for k, v in data.items()}
