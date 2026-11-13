"""What happens after a case is cleared: records, documents and the drafted reply.

Every function here is idempotent for a given case: the booking, the documents and the reply
are each keyed by the case id, so running a step again finds the work already done.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from opsinbox.documents import (
    DocumentContext,
    ReplyFacts,
    draft_reply,
    money,
    render_html,
    render_pdf,
    template_source,
    when,
)
from opsinbox.errors import ConflictError
from opsinbox.extraction.parsing import to_date
from opsinbox.llm import ModelClient
from opsinbox.models import Message
from opsinbox.records import repository as repo
from opsinbox.records.business import Business
from opsinbox.records.models import Booking, Case, Customer, Document, OutgoingMessage
from opsinbox.records.pricing import Quote, quote
from opsinbox.schemas import Category, Extra
from opsinbox.storage import ObjectStore
from opsinbox.workflow.facts import from_jsonable
from opsinbox.workflow.processing import CaseState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionContext:
    """Dependencies of the act step."""

    session: Session
    store: ObjectStore
    business: Business
    client: ModelClient | None
    model: str
    actor: str


@dataclass(frozen=True)
class ActOutcome:
    """What the act step did."""

    status: str
    summary: str
    booking_reference: str | None = None


def act(ctx: ActionContext, case: Case, message: Message, state: CaseState) -> ActOutcome:
    """Carry out a cleared case.

    Raises:
        ConflictError: when the records changed under the case (car taken meanwhile); the
            caller sends the case back to review.

    """
    facts = from_jsonable(state.facts)
    if state.category == Category.NEW_BOOKING:
        return _new_booking(ctx, case, message, state, facts)
    if state.category == Category.CHANGE:
        return _change(ctx, case, message, state, facts)
    if state.category == Category.CANCELLATION:
        return _cancellation(ctx, case, message, state, facts)
    if state.category == Category.SUPPLIER_INVOICE:
        return _payable(ctx, case, message, facts)
    if state.category == Category.NOT_RELEVANT:
        repo.audit(ctx.session, ctx.actor, "message.archived", "message", message.id)
        return ActOutcome("done", "Archived: not relevant to the business.")
    return ActOutcome("done", "Handled by a person.")


# ----------------------------------------------------------------------------------- helpers


def _issue_documents(
    ctx: ActionContext,
    case: Case,
    booking: Booking,
    customer: Customer,
    priced: Quote,
    purpose: str,
) -> list[Document]:
    """Issue the contract and the invoice of a case (once), voiding older ones of the booking."""
    existing = list(
        ctx.session.execute(select(Document).where(Document.case_id == case.id)).scalars()
    )
    if existing:
        return existing
    for old in ctx.session.execute(
        select(Document).where(Document.booking_id == booking.id, Document.status == "issued")
    ).scalars():
        old.status = "void"
        repo.audit(ctx.session, ctx.actor, "document.voided", "document", old.number, case=case.id)
    car = ctx.business.car(booking.car_code)
    assert car is not None
    issued_on = datetime.now(UTC).astimezone(ctx.business.timezone).date()
    context = DocumentContext(
        business=ctx.business,
        car=car,
        customer={"name": customer.name, "email": customer.email, "phone": customer.phone},
        booking={
            "reference": booking.reference,
            "pickup_at": booking.pickup_at,
            "return_at": booking.return_at,
            "pickup_location": booking.pickup_location,
        },
        quote=priced,
        licence=booking.licence,
        purpose=purpose or "other",
        issued_on=issued_on,
    )
    documents: list[Document] = []
    for kind, prefix in (("contract", "LCH-C"), ("invoice", "LCH-INV")):
        number = (
            f"{prefix}-{issued_on.year}-{repo.next_number(ctx.session, kind, issued_on.year):04d}"
        )
        key = f"documents/{case.id}/{kind}.pdf"
        html = render_html(kind, number, context, source=template_source(ctx.session, kind))
        ctx.store.put(key, render_pdf(html), "application/pdf")
        document = Document(
            kind=kind,
            number=number,
            booking_id=booking.id,
            case_id=case.id,
            storage_key=key,
            total=priced.total,
        )
        ctx.session.add(document)
        documents.append(document)
        repo.audit(
            ctx.session,
            ctx.actor,
            "document.issued",
            "document",
            number,
            case=case.id,
            booking=booking.reference,
            total=priced.total,
        )
    ctx.session.flush()
    return documents


def _draft(
    ctx: ActionContext,
    case: Case,
    message: Message,
    state: CaseState,
    reply: ReplyFacts,
    documents: list[Document],
    booking: Booking | None,
) -> None:
    """Store the drafted reply for the case (once). It waits for a person to approve it."""
    if ctx.session.execute(
        select(OutgoingMessage.id).where(OutgoingMessage.case_id == case.id)
    ).first():
        return
    body, origin = draft_reply(ctx.client, ctx.model, reply)
    subject = (
        message.subject if message.subject.lower().startswith("re:") else f"Re: {message.subject}"
    )
    reply_id = f"<reply-{case.id}@{ctx.business.email.split('@')[1]}>"
    references = (
        [*message.references, message.message_id]
        if message.message_id
        else list(message.references)
    )
    ctx.session.execute(
        insert(OutgoingMessage)
        .values(
            id=uuid.uuid4(),
            case_id=case.id,
            to_address=message.reply_to or message.from_address,
            subject=subject,
            body=body,
            document_ids=[str(d.id) for d in documents],
            in_reply_to=message.message_id,
            references=references,
            message_id=reply_id,
        )
        .on_conflict_do_nothing(index_elements=["case_id"])
    )
    if booking is not None:
        repo.link_thread(ctx.session, booking.id, [message.message_id, reply_id])
    repo.audit(ctx.session, ctx.actor, "reply.drafted", "case", case.id, origin=origin)
    _ = state


def _period(facts: dict[str, Any]) -> tuple[datetime, datetime]:
    pickup_at, return_at = facts.get("pickup_at"), facts.get("return_at")
    if not isinstance(pickup_at, datetime) or not isinstance(return_at, datetime):
        raise ConflictError("the rental period is incomplete")
    return pickup_at, return_at


# ------------------------------------------------------------------------------- categories


def _new_booking(
    ctx: ActionContext, case: Case, message: Message, state: CaseState, facts: dict[str, Any]
) -> ActOutcome:
    car = ctx.business.car(str(facts.get("car")))
    if car is None:
        raise ConflictError("no car of the fleet was identified")
    pickup_at, return_at = _period(facts)
    extras = [Extra(e) for e in facts.get("extras", [])]
    priced = quote(ctx.business, car, pickup_at, return_at, extras)
    customer = repo.upsert_customer(
        ctx.session,
        message.reply_to or message.from_address,
        str(facts.get("customer_name") or message.from_name),
        facts.get("customer_phone"),
        state.language,
    )
    licence = facts.get("licence")
    location = facts.get("pickup_location")
    booking = repo.create_booking(
        ctx.session,
        case_id=case.id,
        car=car.code,
        customer=customer,
        pickup_at=pickup_at,
        return_at=return_at,
        pickup_location=ctx.business.garage_location
        if ctx.business.is_own_premises(location)
        else str(location),
        purpose=facts.get("purpose"),
        extras=[e.value for e in extras],
        total=priced.total,
        deposit=priced.deposit,
        licence={
            k: (v.isoformat() if isinstance(v, date) else " ".join(v) if isinstance(v, list) else v)
            for k, v in licence.items()
        }
        if isinstance(licence, dict)
        else None,
        thread_ids=[message.message_id, *message.references],
        actor=ctx.actor,
    )
    documents = _issue_documents(
        ctx, case, booking, customer, priced, str(facts.get("purpose") or "")
    )
    reply = ReplyFacts(
        language=state.language,
        customer_name=customer.name,
        situation="The booking is confirmed; the rental agreement and the invoice are attached.",
        facts=[
            f"Booking reference: {booking.reference}",
            f"Car: {car.name}",
            f"Pick-up: {when(pickup_at)} at {booking.pickup_location}",
            f"Return: {when(return_at)}",
            f"Total: {money(priced.total)} {ctx.business.currency} (tax included)",
            f"Deposit due within 7 days: {money(priced.deposit)} {ctx.business.currency}",
            "Please bring the original driving licence at pick-up.",
        ],
        required=[booking.reference, money(priced.total), money(priced.deposit)],
    )
    _draft(ctx, case, message, state, reply, documents, booking)
    case.booking_id = booking.id
    return ActOutcome(
        "ready",
        f"Booking {booking.reference} created; documents issued; reply drafted.",
        booking.reference,
    )


def _change(
    ctx: ActionContext, case: Case, message: Message, state: CaseState, facts: dict[str, Any]
) -> ActOutcome:
    booking = repo.get_booking(ctx.session, str(facts.get("booking_reference")))
    car = ctx.business.car(str(facts.get("car")))
    if car is None:
        raise ConflictError("the new car is not one of the fleet")
    pickup_at, return_at = _period(facts)
    priced = quote(ctx.business, car, pickup_at, return_at, [Extra(e) for e in booking.extras])
    repo.change_booking(
        ctx.session,
        booking,
        car=car.code,
        pickup_at=pickup_at,
        return_at=return_at,
        total=priced.total,
        deposit=priced.deposit,
        actor=ctx.actor,
        case_id=case.id,
    )
    customer = ctx.session.get(Customer, booking.customer_id)
    assert customer is not None
    documents = _issue_documents(ctx, case, booking, customer, priced, booking.purpose or "")
    reply = ReplyFacts(
        language=state.language,
        customer_name=customer.name,
        situation="The change to the booking is confirmed; the updated documents are attached.",
        facts=[
            f"Booking reference: {booking.reference}",
            f"Car: {car.name}",
            f"Pick-up: {when(pickup_at)}",
            f"Return: {when(return_at)}",
            f"New total: {money(priced.total)} {ctx.business.currency} (tax included)",
        ],
        required=[booking.reference, money(priced.total)],
    )
    _draft(ctx, case, message, state, reply, documents, booking)
    case.booking_id = booking.id
    return ActOutcome(
        "ready", f"Booking {booking.reference} changed; new documents issued.", booking.reference
    )


def _cancellation(
    ctx: ActionContext, case: Case, message: Message, state: CaseState, facts: dict[str, Any]
) -> ActOutcome:
    booking = repo.get_booking(ctx.session, str(facts.get("booking_reference")))
    repo.cancel_booking(ctx.session, booking, ctx.actor, case.id)
    ctx.session.execute(
        update(Document)
        .where(Document.booking_id == booking.id, Document.status == "issued")
        .values(status="void")
    )
    customer = ctx.session.get(Customer, booking.customer_id)
    assert customer is not None
    reply = ReplyFacts(
        language=state.language,
        customer_name=customer.name,
        situation="The booking is cancelled as requested.",
        facts=[
            f"Cancelled booking: {booking.reference}",
            "The deposit is refunded if the cancellation is more than 14 days before pick-up.",
        ],
        required=[booking.reference],
    )
    _draft(ctx, case, message, state, reply, [], booking)
    case.booking_id = booking.id
    return ActOutcome(
        "ready", f"Booking {booking.reference} cancelled; invoice voided.", booking.reference
    )


def _payable(ctx: ActionContext, case: Case, message: Message, facts: dict[str, Any]) -> ActOutcome:
    def amount(name: str) -> Decimal | None:
        value = facts.get(name)
        return value if isinstance(value, Decimal) else None

    first_attachment = message.attachments[0].id if message.attachments else None
    invoice = repo.record_payable(
        ctx.session,
        case_id=case.id,
        supplier_name=str(facts.get("known_supplier") or facts.get("supplier_name")),
        number=str(facts.get("invoice_number")),
        invoice_date=to_date(facts.get("invoice_date")),
        subtotal=amount("subtotal"),
        tax=amount("tax"),
        total=amount("total"),
        attachment_id=first_attachment,
        actor=ctx.actor,
    )
    return ActOutcome(
        "done", f"Invoice {invoice.number} from {invoice.supplier_name} recorded for payment."
    )
