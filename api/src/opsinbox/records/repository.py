"""Records in PostgreSQL: the database :class:`RecordsView` and the write operations.

Every write is idempotent, keyed by the case that causes it, so a workflow step that runs twice
(after a crash, or a duplicate job) changes nothing the second time.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsinbox.errors import ConflictError, NotFoundError
from opsinbox.records.business import Business
from opsinbox.records.models import (
    AuditEntry,
    Booking,
    BookingThread,
    CarRecord,
    Customer,
    NumberSequence,
    PayableInvoice,
    Supplier,
)
from opsinbox.records.view import TURNAROUND, BookingSnapshot, SupplierSnapshot


def audit(
    session: Session, actor: str, action: str, entity: str, entity_id: object, **detail: Any
) -> None:
    """Append an audit entry (same transaction as the change it records)."""
    session.add(
        AuditEntry(
            actor=actor,
            action=action,
            entity=entity,
            entity_id=str(entity_id),
            detail={
                k: str(v) if isinstance(v, (Decimal, datetime, date, uuid.UUID)) else v
                for k, v in detail.items()
            },
        )
    )


def next_number(session: Session, kind: str, year: int) -> int:
    """Take the next number of a gapless sequence. Locks the row until the transaction ends."""
    session.execute(
        insert(NumberSequence).values(kind=kind, year=year, last_value=0).on_conflict_do_nothing()
    )
    row = session.execute(
        select(NumberSequence)
        .where(NumberSequence.kind == kind, NumberSequence.year == year)
        .with_for_update()
    ).scalar_one()
    row.last_value += 1
    session.flush()
    return row.last_value


def _snapshot(booking: Booking, customer: Customer) -> BookingSnapshot:
    return BookingSnapshot(
        reference=booking.reference,
        car=booking.car_code,
        customer_name=customer.name,
        customer_email=customer.email,
        pickup_at=booking.pickup_at,
        return_at=booking.return_at,
        pickup_location=booking.pickup_location,
        extras=tuple(booking.extras),
        total=booking.total,
        status=booking.status,
        language=customer.language,
    )


class DbRecords:
    """:class:`opsinbox.records.view.RecordsView` over PostgreSQL."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _load(self, condition: Any) -> BookingSnapshot | None:
        row = self.session.execute(
            select(Booking, Customer)
            .join(Customer, Customer.id == Booking.customer_id)
            .where(condition)
        ).first()
        return _snapshot(row[0], row[1]) if row else None

    def booking(self, reference: str) -> BookingSnapshot | None:
        """Return the booking with this reference."""
        return self._load(Booking.reference == reference.strip().upper())

    def booking_for_thread(self, message_ids: list[str]) -> BookingSnapshot | None:
        """Return the booking whose conversation contains one of these message ids."""
        if not message_ids:
            return None
        booking_id = self.session.execute(
            select(BookingThread.booking_id)
            .where(BookingThread.message_id.in_(message_ids))
            .limit(1)
        ).scalar_one_or_none()
        return self._load(Booking.id == booking_id) if booking_id else None

    def conflicts(
        self, car: str, start: datetime, end: datetime, exclude: str | None = None
    ) -> list[str]:
        """Return references of bookings holding ``car`` during the period."""
        query = select(Booking.reference).where(
            Booking.car_code == car,
            Booking.status == "confirmed",
            Booking.pickup_at < end + TURNAROUND,
            Booking.return_at + TURNAROUND > start,
        )
        if exclude:
            query = query.where(Booking.reference != exclude)
        return list(self.session.execute(query).scalars())

    def duplicate_of(self, customer_email: str, car: str, pickup_day: str) -> str | None:
        """Return a booking of the same customer, car and pick-up day, if any."""
        return self.session.execute(
            select(Booking.reference)
            .join(Customer, Customer.id == Booking.customer_id)
            .where(
                func.lower(Customer.email) == customer_email.lower(),
                Booking.car_code == car,
                func.date(Booking.pickup_at) == date.fromisoformat(pickup_day),
                Booking.status == "confirmed",
            )
            .limit(1)
        ).scalar_one_or_none()

    def suppliers(self) -> list[SupplierSnapshot]:
        """Return the known suppliers."""
        return [
            SupplierSnapshot(s.name, s.email)
            for s in self.session.execute(select(Supplier)).scalars()
        ]

    def invoice_recorded(self, supplier_name: str, number: str) -> bool:
        """Tell whether this supplier invoice is already recorded."""
        return (
            self.session.execute(
                select(func.count())
                .select_from(PayableInvoice)
                .where(
                    func.lower(PayableInvoice.supplier_name) == supplier_name.lower(),
                    func.upper(func.replace(PayableInvoice.number, " ", ""))
                    == number.replace(" ", "").upper(),
                    PayableInvoice.status != "void",
                )
            ).scalar_one()
            > 0
        )


# ------------------------------------------------------------------------------------- writes


def upsert_customer(
    session: Session, email: str, name: str, phone: str | None, language: str
) -> Customer:
    """Find the customer by e-mail or create them; keep the newest name and phone."""
    session.execute(
        insert(Customer)
        .values(id=uuid.uuid4(), email=email.lower(), name=name, phone=phone, language=language)
        .on_conflict_do_nothing(index_elements=["email"])
    )
    customer = session.execute(select(Customer).where(Customer.email == email.lower())).scalar_one()
    customer.name = name or customer.name
    customer.phone = phone or customer.phone
    return customer


def create_booking(
    session: Session,
    *,
    case_id: uuid.UUID,
    car: str,
    customer: Customer,
    pickup_at: datetime,
    return_at: datetime,
    pickup_location: str,
    purpose: str | None,
    extras: list[str],
    total: Decimal,
    deposit: Decimal,
    licence: dict[str, Any] | None,
    thread_ids: list[str],
    actor: str,
) -> Booking:
    """Create the booking for a case, or return the one it already created.

    Raises:
        ConflictError: when the car was taken meanwhile (the exclusion constraint fired).

    """
    existing = session.execute(
        select(Booking).where(Booking.case_id == case_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    year = pickup_at.year
    number = next_number(session, "booking", year)
    booking = Booking(
        reference=f"LCH-B-{year}-{number:04d}",
        car_code=car,
        customer_id=customer.id,
        pickup_at=pickup_at,
        return_at=return_at,
        pickup_location=pickup_location,
        purpose=purpose,
        extras=sorted(extras),
        total=total,
        deposit=deposit,
        licence=licence,
        case_id=case_id,
    )
    try:
        with session.begin_nested():
            session.add(booking)
            session.flush()
    except IntegrityError as exc:
        if "bookings_no_overlap" in str(exc):
            raise ConflictError(f"car {car} is no longer free") from exc
        raise
    link_thread(session, booking.id, thread_ids)
    audit(
        session,
        actor,
        "booking.created",
        "booking",
        booking.reference,
        case=case_id,
        car=car,
        pickup_at=pickup_at,
        return_at=return_at,
        total=total,
    )
    return booking


def link_thread(session: Session, booking_id: uuid.UUID, message_ids: list[str]) -> None:
    """Remember that these messages belong to the booking's conversation."""
    for message_id in {m for m in message_ids if m}:
        session.execute(
            insert(BookingThread)
            .values(message_id=message_id, booking_id=booking_id)
            .on_conflict_do_nothing()
        )


def get_booking(session: Session, reference: str) -> Booking:
    """Return a booking or raise :class:`NotFoundError`."""
    booking = session.execute(
        select(Booking).where(Booking.reference == reference)
    ).scalar_one_or_none()
    if booking is None:
        raise NotFoundError(f"booking {reference}")
    return booking


def change_booking(
    session: Session,
    booking: Booking,
    *,
    car: str,
    pickup_at: datetime,
    return_at: datetime,
    total: Decimal,
    deposit: Decimal,
    actor: str,
    case_id: uuid.UUID,
) -> Booking:
    """Apply a change. Idempotent: applying the same values again changes nothing."""
    before = {
        "car": booking.car_code,
        "pickup_at": booking.pickup_at,
        "return_at": booking.return_at,
        "total": booking.total,
    }
    after = {"car": car, "pickup_at": pickup_at, "return_at": return_at, "total": total}
    if before == after:
        return booking
    try:
        with session.begin_nested():
            booking.car_code, booking.pickup_at, booking.return_at = car, pickup_at, return_at
            booking.total, booking.deposit = total, deposit
            session.flush()
    except IntegrityError as exc:
        if "bookings_no_overlap" in str(exc):
            raise ConflictError(f"car {car} is not free for the new dates") from exc
        raise
    audit(
        session,
        actor,
        "booking.changed",
        "booking",
        booking.reference,
        case=case_id,
        before={k: str(v) for k, v in before.items()},
        after={k: str(v) for k, v in after.items()},
    )
    return booking


def cancel_booking(session: Session, booking: Booking, actor: str, case_id: uuid.UUID) -> Booking:
    """Cancel a booking (reversible: the row stays, with an audit entry)."""
    if booking.status == "cancelled":
        return booking
    booking.status = "cancelled"
    audit(session, actor, "booking.cancelled", "booking", booking.reference, case=case_id)
    return booking


def record_payable(
    session: Session,
    *,
    case_id: uuid.UUID,
    supplier_name: str,
    number: str,
    invoice_date: date | None,
    subtotal: Decimal | None,
    tax: Decimal | None,
    total: Decimal | None,
    attachment_id: uuid.UUID | None,
    actor: str,
) -> PayableInvoice:
    """Record a supplier invoice for payment, once per case."""
    existing = session.execute(
        select(PayableInvoice).where(PayableInvoice.case_id == case_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    invoice = PayableInvoice(
        supplier_name=supplier_name,
        number=number,
        invoice_date=invoice_date,
        subtotal=subtotal,
        tax=tax,
        total=total,
        case_id=case_id,
        attachment_id=attachment_id,
    )
    try:
        with session.begin_nested():
            session.add(invoice)
            session.flush()
    except IntegrityError as exc:
        raise ConflictError(f"invoice {number} from {supplier_name} is already recorded") from exc
    audit(
        session,
        actor,
        "payable.recorded",
        "payable_invoice",
        f"{supplier_name}/{number}",
        case=case_id,
        total=total,
    )
    return invoice


def seed_business(session: Session, business: Business) -> None:
    """Insert the fleet if it is not there yet."""
    for car in business.cars:
        session.execute(
            insert(CarRecord)
            .values(
                code=car.code,
                year=car.year,
                make=car.make,
                model=car.model,
                body=car.body,
                colour=car.colour,
                daily_rate=car.daily_rate,
            )
            .on_conflict_do_nothing()
        )


def has_overlap_constraint(session: Session) -> bool:
    """Whether the database enforces no double booking (used by a test)."""
    return bool(
        session.execute(
            text("SELECT 1 FROM pg_constraint WHERE conname = 'bookings_no_overlap'")
        ).first()
    )
