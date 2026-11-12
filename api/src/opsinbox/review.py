"""Human review: corrections, re-validation, approval and rejection.

A person may approve a case whose rules failed (they are judgement calls: an unusual price, an
instruction-like sentence that turns out harmless), except for the *blocking* rules that would
make the action itself impossible or wrong: missing details, a car that is not free, a booking
that cannot be found.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsinbox.errors import ConflictError, NotFoundError
from opsinbox.reading import Reader
from opsinbox.records import repository as repo
from opsinbox.records.business import Business
from opsinbox.records.models import Case, Correction, OutgoingMessage
from opsinbox.records.repository import DbRecords
from opsinbox.rules.engine import RuleResult
from opsinbox.schemas import Category, ExtractedField, Extraction
from opsinbox.storage import ObjectStore
from opsinbox.workflow.graph import current_rules, current_threshold, load_mail
from opsinbox.workflow.processing import CaseState, Processor
from opsinbox.workflow.service import enqueue_resume, enqueue_send

#: Rules a person cannot override: the action would be impossible or certainly wrong.
BLOCKING_RULES = frozenset(
    {
        "required_booking_fields",
        "car_known",
        "dates_in_order",
        "car_available",
        "booking_found",
        "change_has_content",
        "invoice_attached",
        "required_invoice_fields",
        "rental_length",
    }
)


class _NoReader:
    def read(self, data: bytes, content_type: str) -> Any:
        raise ConflictError("re-validation does not read attachments")


def _extraction_for(state: CaseState, name: str) -> tuple[Extraction | None, str]:
    """Which extraction a field name like ``licence.expires_on`` belongs to."""
    if name.startswith("licence."):
        return (state.licences[0] if state.licences else None), name.split(".", 1)[1]
    if name.startswith("invoice."):
        return (state.invoices[0] if state.invoices else None), name.split(".", 1)[1]
    return state.booking, name


def apply_corrections(state: CaseState, corrections: dict[str, Any]) -> CaseState:
    """Return a copy of the state with the person's values; corrected fields are certain."""
    updated = state.model_copy(deep=True)
    for name, value in corrections.items():
        extraction, field = _extraction_for(updated, name)
        if extraction is None:
            raise NotFoundError(f"no extraction holds the field {name}")
        if field == "extras":
            values = value if isinstance(value, list) else [v for v in str(value).split(",") if v]
            extraction.fields = [f for f in extraction.fields if f.name != "extras"]
            extraction.fields += [
                ExtractedField(
                    name="extras",
                    value=v.strip(),
                    confidence=1.0,
                    corrected=True,
                    signals={"person": 1.0},
                )
                for v in values
            ]
            continue
        new_value = None if value in (None, "") else str(value).strip()
        current = extraction.get(field)
        if current is None:
            extraction.fields.append(
                ExtractedField(
                    name=field,
                    value=new_value,
                    confidence=1.0,
                    corrected=True,
                    signals={"person": 1.0},
                )
            )
        else:
            current.value = new_value
            current.confidence = 1.0
            current.corrected = True
            current.signals = {**current.signals, "person": 1.0}
    return updated


def revalidate(
    session: Session, store: ObjectStore, business: Business, case: Case, state: CaseState
) -> CaseState:
    """Run the rules again on a (corrected) state, against the records as they are now."""
    mail, _ = load_mail(session, store, case.message_pk)
    reader: Reader = _NoReader()
    processor = Processor(
        None,
        reader,
        None,
        business,
        current_rules(session),
        DbRecords(session),
        current_threshold(session),
    )
    return processor.decide(processor.validate(state, mail))


def blocking(results: list[RuleResult]) -> list[RuleResult]:
    """The failed rules a person cannot override."""
    return [r for r in results if r.outcome in ("failed", "error") and r.rule_id in BLOCKING_RULES]


def get_case(session: Session, case_id: uuid.UUID) -> Case:
    """Return the case or raise :class:`NotFoundError`."""
    case = session.get(Case, case_id)
    if case is None:
        raise NotFoundError(f"case {case_id}")
    return case


def approve(
    session: Session,
    store: ObjectStore,
    business: Business,
    case_id: uuid.UUID,
    actor: str,
    corrections: dict[str, Any],
    reply_body: str | None = None,
) -> CaseState:
    """Record corrections and queue the resumption of the paused workflow.

    Raises:
        ConflictError: when the case is not waiting for review, or a blocking rule still fails.

    """
    case = get_case(session, case_id)
    if case.status != "review":
        raise ConflictError(f"case is {case.status}, not waiting for review")
    state = CaseState.model_validate(case.state)
    corrected = revalidate(session, store, business, case, apply_corrections(state, corrections))
    stopping = blocking(corrected.rules)
    if stopping:
        raise ConflictError("; ".join(r.message or r.name for r in stopping))
    if corrected.category == Category.QUESTION and not reply_body:
        raise ConflictError("write the answer before approving a question")
    for name, value in corrections.items():
        extraction, field = _extraction_for(state, name)
        old = extraction.get(field) if extraction else None
        session.add(
            Correction(
                case_id=case.id,
                field=name,
                old_value=old.value if old else None,
                new_value=None if value is None else str(value),
                actor=actor,
            )
        )
    repo.audit(session, actor, "case.approved", "case", case.id, corrections=sorted(corrections))
    case.status = "resuming"  # the review node leaves this alone when it runs again on resume
    case.state = corrected.model_dump(mode="json")
    case.decided_by, case.decided_at = actor, datetime.now(UTC)
    decision = {"action": "approve", "actor": actor, "state": case.state, "reply_body": reply_body}
    enqueue_resume(session, case.id, decision)
    return corrected


def reject(session: Session, case_id: uuid.UUID, actor: str, note: str) -> None:
    """Queue the rejection of a paused case."""
    case = get_case(session, case_id)
    if case.status != "review":
        raise ConflictError(f"case is {case.status}, not waiting for review")
    case.status = "resuming"
    repo.audit(session, actor, "case.rejecting", "case", case.id, note=note)
    enqueue_resume(session, case.id, {"action": "reject", "actor": actor, "note": note})


def edit_reply(session: Session, case_id: uuid.UUID, actor: str, body: str) -> None:
    """Change the drafted reply before it is sent."""
    outgoing = session.execute(
        select(OutgoingMessage).where(OutgoingMessage.case_id == case_id)
    ).scalar_one_or_none()
    if outgoing is None:
        raise NotFoundError("this case has no reply")
    if outgoing.status != "draft":
        raise ConflictError(f"the reply is already {outgoing.status}")
    outgoing.body = body
    repo.audit(session, actor, "reply.edited", "case", case_id)


def approve_send(session: Session, case_id: uuid.UUID, actor: str) -> None:
    """Approve the drafted reply and queue it for sending."""
    outgoing = session.execute(
        select(OutgoingMessage).where(OutgoingMessage.case_id == case_id).with_for_update()
    ).scalar_one_or_none()
    if outgoing is None:
        raise NotFoundError("this case has no reply")
    if outgoing.status == "draft":
        outgoing.status = "approved"
        outgoing.approved_by = actor
        repo.audit(session, actor, "reply.approved", "case", case_id)
    if outgoing.status != "sent":
        enqueue_send(session, case_id)
