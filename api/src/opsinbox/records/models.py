"""ORM models for business records, cases, review and audit.

Times of rentals are naive local business time (the business works in one time zone); moments
when something happened are timezone-aware.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from opsinbox.db import Base
from opsinbox.models import new_id

MONEY = Numeric(12, 2)


class CarRecord(Base):
    """A car of the fleet."""

    __tablename__ = "cars"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    make: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(String(32))
    colour: Mapped[str] = mapped_column(String(32))
    daily_rate: Mapped[Decimal] = mapped_column(MONEY)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Customer(Base):
    """Someone who hires cars."""

    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(8), default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Booking(Base):
    """A hire.

    The ``bookings_no_overlap`` exclusion constraint (see the migration) makes a double booking
    of a car impossible, whatever the number of workers.
    """

    __tablename__ = "bookings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    reference: Mapped[str] = mapped_column(String(32), unique=True)
    car_code: Mapped[str] = mapped_column(ForeignKey("cars.code"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"))
    pickup_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    return_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    pickup_location: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str | None] = mapped_column(String(32))
    extras: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    total: Mapped[Decimal] = mapped_column(MONEY)
    deposit: Mapped[Decimal] = mapped_column(MONEY)
    licence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: The case that created the booking: one case creates at most one booking.
    case_id: Mapped[uuid.UUID | None] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class BookingThread(Base):
    """A message id that belongs to the conversation about a booking."""

    __tablename__ = "booking_threads"

    message_id: Mapped[str] = mapped_column(String(998), primary_key=True)
    booking_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"))


class NumberSequence(Base):
    """Gapless document numbering: one row per kind and year, locked while a number is taken."""

    __tablename__ = "number_sequences"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


class Document(Base):
    """A generated contract or invoice. Immutable once issued; voiding keeps the row."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("case_id", "kind", name="uq_documents_case_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16))
    number: Mapped[str] = mapped_column(String(32), unique=True)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bookings.id"))
    case_id: Mapped[uuid.UUID] = mapped_column()
    storage_key: Mapped[str] = mapped_column(String(512))
    total: Mapped[Decimal | None] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16), default="issued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Supplier(Base):
    """A company the business pays."""

    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320))


class PayableInvoice(Base):
    """An invoice received from a supplier, recorded for payment."""

    __tablename__ = "payable_invoices"
    __table_args__ = (
        UniqueConstraint("supplier_name", "number", name="uq_payable_supplier_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    supplier_name: Mapped[str] = mapped_column(String(255))
    number: Mapped[str] = mapped_column(String(64))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    subtotal: Mapped[Decimal | None] = mapped_column(MONEY)
    tax: Mapped[Decimal | None] = mapped_column(MONEY)
    total: Mapped[Decimal | None] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16), default="to_pay")
    case_id: Mapped[uuid.UUID | None] = mapped_column(unique=True)
    attachment_id: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Case(Base):
    """The processing of one incoming e-mail, from arrival to filing."""

    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    message_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), unique=True)
    #: processing, review, ready (reply waiting to be sent), done, rejected, failed
    status: Mapped[str] = mapped_column(String(16), default="processing")
    category: Mapped[str | None] = mapped_column(String(32))
    route: Mapped[str | None] = mapped_column(String(8))
    reasons: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    #: When the matter becomes urgent (pick-up time); orders the review queue.
    urgent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    booking_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bookings.id"))
    outcome: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Correction(Base):
    """A field a person changed in review."""

    __tablename__ = "corrections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutgoingMessage(Base):
    """A reply drafted for a case. Sent only after a person approves it."""

    __tablename__ = "outgoing_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), unique=True)
    to_address: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    document_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    in_reply_to: Mapped[str] = mapped_column(String(998), default="")
    references: Mapped[list[str]] = mapped_column(JSONB, default=list)
    message_id: Mapped[str] = mapped_column(String(998))
    #: draft, approved, sent
    status: Mapped[str] = mapped_column(String(16), default="draft")
    approved_by: Mapped[str | None] = mapped_column(String(64))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEntry(Base):
    """Who or what did what, and when. Append-only."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class RuleRecord(Base):
    """A business rule as the owner edits it."""

    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AppSetting(Base):
    """A setting the owner can change at runtime (threshold, templates)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
