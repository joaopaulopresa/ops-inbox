"""Review queue and case endpoints."""

import io
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

import pypdfium2
from fastapi import APIRouter, Depends, HTTPException, Response
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import case as sql_case
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from opsinbox import review
from opsinbox.db import get_session
from opsinbox.documents import (
    DocumentContext,
    ReplyFacts,
    draft_reply,
    money,
    render_html,
    render_pdf,
    when,
)
from opsinbox.errors import ConflictError, NotFoundError
from opsinbox.models import Attachment, Message
from opsinbox.records.business import Business, load_business
from opsinbox.records.models import AuditEntry, Booking, Case, Correction, Document, OutgoingMessage
from opsinbox.records.pricing import quote
from opsinbox.routes.deps import get_model_client, get_store
from opsinbox.schemas import Extra
from opsinbox.settings import get_settings
from opsinbox.storage import ObjectStore
from opsinbox.workflow.facts import from_jsonable
from opsinbox.workflow.processing import CaseState

router = APIRouter(prefix="/api", tags=["cases"])

Status = Literal["review", "ready", "processing", "done", "rejected", "failed", "all"]


class CaseSummary(BaseModel):
    """A line of the queue."""

    id: uuid.UUID
    status: str
    category: str | None
    route: str | None
    subject: str
    from_name: str
    from_address: str
    received_at: datetime
    urgent_at: datetime | None
    reasons: list[dict[str, str]]
    outcome: str | None
    booking_reference: str | None = None


class Corrections(BaseModel):
    """Values a person typed in review, by field name (``licence.expires_on`` for documents)."""

    corrections: dict[str, Any] = Field(default_factory=dict)


class Approval(Corrections):
    """Approve a case."""

    actor: str = "operations"
    reply_body: str | None = None


class Rejection(BaseModel):
    """Reject a case."""

    actor: str = "operations"
    note: str = ""


class ReplyEdit(BaseModel):
    """New text of the drafted reply."""

    actor: str = "operations"
    body: str


class Actor(BaseModel):
    """Who clicked."""

    actor: str = "operations"


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


@router.get("/cases")
def list_cases(
    status: Status = "review", limit: int = 200, session: Session = Depends(get_session)
) -> list[CaseSummary]:
    """Cases by status. The review queue is ordered by urgency: the soonest pick-up first."""
    query = (
        select(Case, Message, Booking.reference)
        .join(Message, Message.id == Case.message_pk)
        .outerjoin(Booking, Booking.id == Case.booking_id)
    )
    if status != "all":
        query = query.where(Case.status == status)
    if status == "review":
        query = query.order_by(
            sql_case((Case.urgent_at.is_(None), 1), else_=0), Case.urgent_at, Message.received_at
        )
    else:
        query = query.order_by(Case.updated_at.desc())
    rows = session.execute(query.limit(min(limit, 500))).all()
    return [
        CaseSummary(
            id=c.id,
            status=c.status,
            category=c.category,
            route=c.route,
            subject=m.subject,
            from_name=m.from_name,
            from_address=m.from_address,
            received_at=m.received_at,
            urgent_at=c.urgent_at,
            reasons=c.reasons,
            outcome=c.outcome,
            booking_reference=ref,
        )
        for c, m, ref in rows
    ]


@router.get("/cases/counts")
def case_counts(session: Session = Depends(get_session)) -> dict[str, int]:
    """Number of cases per status, for the navigation badges."""
    rows = session.execute(select(Case.status, Case.id)).all()
    counts: dict[str, int] = {}
    for status, _ in rows:
        counts[status] = counts.get(status, 0) + 1
    return counts


def _attachment_info(attachment: Attachment, store: ObjectStore) -> dict[str, Any]:
    pages = 1
    if attachment.content_type == "application/pdf":
        document = pypdfium2.PdfDocument(store.get(attachment.storage_key))
        pages = len(document)
        document.close()
    return {
        "id": str(attachment.id),
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "size": attachment.size,
        "pages": pages,
    }


@router.get("/cases/{case_id}")
def get_case(
    case_id: uuid.UUID,
    session: Session = Depends(get_session),
    store: ObjectStore = Depends(get_store),
) -> dict[str, Any]:
    """Everything the review screen shows about a case."""
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    message = session.execute(
        select(Message)
        .options(selectinload(Message.attachments))
        .where(Message.id == case.message_pk)
    ).scalar_one()
    state = CaseState.model_validate(case.state) if case.state else None
    booking = session.get(Booking, case.booking_id) if case.booking_id else None
    documents = list(session.execute(select(Document).where(Document.case_id == case.id)).scalars())
    reply = session.execute(
        select(OutgoingMessage).where(OutgoingMessage.case_id == case.id)
    ).scalar_one_or_none()
    corrections = list(
        session.execute(select(Correction).where(Correction.case_id == case.id)).scalars()
    )
    audit = list(
        session.execute(
            select(AuditEntry)
            .where(AuditEntry.entity_id.in_([str(case.id), booking.reference if booking else "-"]))
            .order_by(AuditEntry.at)
        ).scalars()
    )
    facts = from_jsonable(state.facts) if state else {}
    return {
        "id": str(case.id),
        "status": case.status,
        "category": case.category,
        "route": case.route,
        "reasons": case.reasons,
        "outcome": case.outcome,
        "decided_by": case.decided_by,
        "urgent_at": case.urgent_at.isoformat() if case.urgent_at else None,
        "message": {
            "id": str(message.id),
            "subject": message.subject,
            "from_name": message.from_name,
            "from_address": message.from_address,
            "reply_to": message.reply_to,
            "to": message.to_address,
            "sent_at": message.sent_at.isoformat() if message.sent_at else None,
            "body": message.body_text,
            "attachments": [_attachment_info(a, store) for a in message.attachments],
        },
        "state": state.model_dump(mode="json", exclude={"documents"}) if state else None,
        "documents_read": [
            {"attachment_id": d.attachment_id, "engine": d.engine, "kind": d.kind}
            for d in (state.documents if state else [])
        ],
        "facts": _jsonable(
            {
                k: v
                for k, v in facts.items()
                if k
                in {
                    "tariff_total",
                    "rental_days",
                    "conflicts",
                    "duplicate_of",
                    "pickup_at",
                    "return_at",
                    "booking_reference",
                    "known_supplier",
                    "lines_total",
                    "suspicions",
                    "sender_warnings",
                    "holder_name_similarity",
                    "quoted_total",
                }
            }
        ),
        "booking": {
            "reference": booking.reference,
            "status": booking.status,
            "total": str(booking.total),
            "car": booking.car_code,
        }
        if booking
        else None,
        "documents": [
            {"id": str(d.id), "kind": d.kind, "number": d.number, "status": d.status}
            for d in documents
        ],
        "reply": {
            "to": reply.to_address,
            "subject": reply.subject,
            "body": reply.body,
            "status": reply.status,
            "documents": reply.document_ids,
        }
        if reply
        else None,
        "corrections": [
            {
                "field": c.field,
                "old": c.old_value,
                "new": c.new_value,
                "actor": c.actor,
                "at": c.created_at.isoformat(),
            }
            for c in corrections
        ],
        "audit": [
            {"at": a.at.isoformat(), "actor": a.actor, "action": a.action, "detail": a.detail}
            for a in audit
        ],
    }


@router.post("/cases/{case_id}/revalidate")
def revalidate_case(
    case_id: uuid.UUID,
    body: Corrections,
    session: Session = Depends(get_session),
    store: ObjectStore = Depends(get_store),
    business: Business = Depends(load_business),
) -> dict[str, Any]:
    """The rules and the decision with the person's corrections, without saving anything."""
    try:
        case = review.get_case(session, case_id)
        state = review.apply_corrections(CaseState.model_validate(case.state), body.corrections)
        checked = review.revalidate(session, store, business, case, state)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.rollback()
    return {
        "rules": [r.model_dump() for r in checked.rules],
        "reasons": [r.model_dump() for r in checked.reasons],
        "blocking": [r.model_dump() for r in review.blocking(checked.rules)],
        "facts": _jsonable(
            {
                k: v
                for k, v in from_jsonable(checked.facts).items()
                if k in {"tariff_total", "rental_days", "conflicts"}
            }
        ),
    }


@router.post("/cases/{case_id}/approve")
def approve_case(
    case_id: uuid.UUID,
    body: Approval,
    session: Session = Depends(get_session),
    store: ObjectStore = Depends(get_store),
    business: Business = Depends(load_business),
) -> dict[str, str]:
    """Approve with corrections; the workflow resumes in the worker."""
    try:
        review.approve(
            session, store, business, case_id, body.actor, body.corrections, body.reply_body
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise _conflict(exc) from exc
    return {"status": "processing"}


@router.post("/cases/{case_id}/reject")
def reject_case(
    case_id: uuid.UUID, body: Rejection, session: Session = Depends(get_session)
) -> dict[str, str]:
    """Reject; nothing is created and nothing is sent."""
    try:
        review.reject(session, case_id, body.actor, body.note)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise _conflict(exc) from exc
    return {"status": "processing"}


@router.put("/cases/{case_id}/reply")
def edit_case_reply(
    case_id: uuid.UUID, body: ReplyEdit, session: Session = Depends(get_session)
) -> dict[str, str]:
    """Change the drafted reply."""
    try:
        review.edit_reply(session, case_id, body.actor, body.body)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConflictError as exc:
        raise _conflict(exc) from exc
    return {"status": "saved"}


@router.post("/cases/{case_id}/send")
def send_case_reply(
    case_id: uuid.UUID, body: Actor, session: Session = Depends(get_session)
) -> dict[str, str]:
    """Approve the reply; the worker sends it."""
    try:
        review.approve_send(session, case_id, body.actor)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "sending"}


# ------------------------------------------------------------------------------- previews


def _preview_context(state: CaseState, business: Business) -> tuple[DocumentContext, Any]:
    facts = from_jsonable(state.facts)
    car = business.car(str(facts.get("car")))
    pickup_at, return_at = facts.get("pickup_at"), facts.get("return_at")
    if car is None or not isinstance(pickup_at, datetime) or not isinstance(return_at, datetime):
        raise HTTPException(status_code=409, detail="car and period are needed for a preview")
    priced = quote(business, car, pickup_at, return_at, [Extra(e) for e in facts.get("extras", [])])
    location = facts.get("pickup_location")
    licence = facts.get("licence") if isinstance(facts.get("licence"), dict) else None
    context = DocumentContext(
        business=business,
        car=car,
        customer={
            "name": facts.get("customer_name") or "",
            "email": facts.get("sender") or "",
            "phone": facts.get("customer_phone"),
        },
        booking={
            "reference": "DRAFT",
            "pickup_at": pickup_at,
            "return_at": return_at,
            "pickup_location": business.garage_location
            if business.is_own_premises(location)
            else location,
        },
        quote=priced,
        licence={k: (" ".join(v) if isinstance(v, list) else v) for k, v in licence.items()}
        if licence
        else None,
        purpose=str(facts.get("purpose") or "other"),
        issued_on=datetime.now(UTC).date(),
    )
    return context, priced


@router.post("/cases/{case_id}/preview/{kind}")
def preview_document(
    case_id: uuid.UUID,
    kind: Literal["contract", "invoice"],
    body: Corrections,
    session: Session = Depends(get_session),
    store: ObjectStore = Depends(get_store),
    business: Business = Depends(load_business),
) -> Response:
    """The contract or invoice as it would be issued with the current corrections (not numbered)."""
    case = review.get_case(session, case_id)
    state = review.revalidate(
        session,
        store,
        business,
        case,
        review.apply_corrections(CaseState.model_validate(case.state), body.corrections),
    )
    session.rollback()
    context, _ = _preview_context(state, business)
    pdf = render_pdf(render_html(kind, "DRAFT", context))
    return Response(content=pdf, media_type="application/pdf")


@router.post("/cases/{case_id}/preview/reply")
def preview_reply(
    case_id: uuid.UUID,
    body: Corrections,
    session: Session = Depends(get_session),
    store: ObjectStore = Depends(get_store),
    business: Business = Depends(load_business),
) -> dict[str, str]:
    """The reply that approval would draft (booking cases)."""
    case = review.get_case(session, case_id)
    state = review.revalidate(
        session,
        store,
        business,
        case,
        review.apply_corrections(CaseState.model_validate(case.state), body.corrections),
    )
    session.rollback()
    context, priced = _preview_context(state, business)
    facts = ReplyFacts(
        language=state.language,
        customer_name=str(context.customer["name"]),
        situation="The booking is confirmed; the rental agreement and the invoice are attached.",
        facts=[
            f"Car: {context.car.name}",
            f"Pick-up: {when(context.booking['pickup_at'])}",
            f"Return: {when(context.booking['return_at'])}",
            f"Total: {money(priced.total)} {business.currency} (tax included)",
            f"Deposit due within 7 days: {money(priced.deposit)} {business.currency}",
        ],
        required=[money(priced.total)],
    )
    text, origin = draft_reply(get_model_client(), get_settings().llm_model, facts)
    return {"body": text, "origin": origin}


# ---------------------------------------------------------------------------- files


@router.get("/documents/{document_id}/pdf")
def document_pdf(
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    store: ObjectStore = Depends(get_store),
) -> Response:
    """An issued contract or invoice."""
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return Response(
        content=store.get(document.storage_key),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{document.number}.pdf"'},
    )


@router.get("/attachments/{attachment_id}/page/{page}")
def attachment_page(
    attachment_id: uuid.UUID,
    page: int,
    session: Session = Depends(get_session),
    store: ObjectStore = Depends(get_store),
) -> Response:
    """One page of an attachment as an image, for showing evidence boxes on it."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    data = store.get(attachment.storage_key)
    if attachment.content_type == "application/pdf":
        document = pypdfium2.PdfDocument(data)
        try:
            image = document[page].render(scale=2.0).to_pil().convert("RGB")
        finally:
            document.close()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return Response(content=buffer.getvalue(), media_type="image/png")
    if attachment.content_type.startswith("image/"):
        Image.open(io.BytesIO(data)).verify()
        return Response(content=data, media_type=attachment.content_type)
    raise HTTPException(status_code=415, detail="not an image or a PDF")
