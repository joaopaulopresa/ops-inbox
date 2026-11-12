"""Owner endpoints: dashboard, rules, automation level, templates, records, export."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from opsinbox.db import get_session
from opsinbox.documents import (
    DocumentContext,
    render_html,
    render_pdf,
    template_source,
    templates_dir,
)
from opsinbox.errors import RuleError
from opsinbox.exports import booking_rows, to_csv
from opsinbox.models import Message
from opsinbox.records import repository as repo
from opsinbox.records.business import load_business
from opsinbox.records.models import (
    AppSetting,
    AuditEntry,
    Booking,
    CarRecord,
    Case,
    Correction,
    Customer,
    PayableInvoice,
    RuleRecord,
)
from opsinbox.records.pricing import quote
from opsinbox.rules.engine import Rule, evaluate_rule, validate_rule
from opsinbox.schemas import Extra
from opsinbox.workflow.facts import from_jsonable
from opsinbox.workflow.graph import current_rules, current_threshold

router = APIRouter(prefix="/api", tags=["owner"])
OWNER = "owner"


def reports_dir() -> Path:
    """Where the evaluation reports live (repository or container)."""
    return templates_dir().parent / "reports"


# ------------------------------------------------------------------------------- dashboard


@router.get("/dashboard")
def dashboard(days: int = 30, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Throughput, automation, review time, corrections and errors found after the fact."""
    since = datetime.now(UTC) - timedelta(days=days)
    cases = list(session.execute(select(Case).where(Case.created_at >= since)).scalars())
    decided = [c for c in cases if c.route is not None]
    automatic = [c for c in decided if c.route == "auto"]
    reviewed = [c for c in decided if c.route == "review" and c.decided_at is not None]
    review_minutes = sorted(
        (c.decided_at - c.created_at).total_seconds() / 60 for c in reviewed if c.decided_at
    )
    per_day = session.execute(
        select(func.date(Message.received_at), func.count())
        .where(Message.received_at >= since)
        .group_by(func.date(Message.received_at))
        .order_by(func.date(Message.received_at))
    ).all()
    corrections = session.execute(
        select(Correction.field, func.count())
        .where(Correction.created_at >= since)
        .group_by(Correction.field)
        .order_by(func.count().desc())
    ).all()
    flagged = session.execute(
        select(func.count())
        .select_from(AuditEntry)
        .where(AuditEntry.action == "case.flagged_wrong", AuditEntry.at >= since)
    ).scalar_one()
    reasons: dict[str, int] = {}
    for case in decided:
        for reason in case.reasons:
            code = (
                reason["code"].split(":")[0]
                if reason["code"].startswith("low_confidence")
                else reason["code"]
            )
            reasons[code] = reasons.get(code, 0) + 1
    by_status: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for case in cases:
        by_status[case.status] = by_status.get(case.status, 0) + 1
        if case.category:
            by_category[case.category] = by_category.get(case.category, 0) + 1
    median = review_minutes[len(review_minutes) // 2] if review_minutes else None
    return {
        "days": days,
        "emails": len(cases),
        "decided": len(decided),
        "automatic": len(automatic),
        "straight_through_rate": round(len(automatic) / len(decided), 4) if decided else None,
        "reviewed": len(reviewed),
        "median_review_minutes": round(median, 1) if median is not None else None,
        "per_day": [{"day": str(day), "emails": n} for day, n in per_day],
        "corrections": [{"field": f, "count": n} for f, n in corrections],
        "stop_reasons": sorted(
            ({"code": k, "count": v} for k, v in reasons.items()), key=lambda item: -item["count"]
        ),
        "flagged_after_the_fact": flagged,
        "by_status": by_status,
        "by_category": by_category,
        "cost_usd": round(sum(float(c.state.get("cost_usd", 0)) for c in cases if c.state), 4),
    }


class Flag(BaseModel):
    """A problem found after a case was done."""

    actor: str = OWNER
    note: str


@router.post("/cases/{case_id}/flag")
def flag_case(
    case_id: uuid.UUID, body: Flag, session: Session = Depends(get_session)
) -> dict[str, str]:
    """Record that a finished case turned out wrong (counts as an error found after approval)."""
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    repo.audit(
        session, body.actor, "case.flagged_wrong", "case", case.id, note=body.note, route=case.route
    )
    return {"status": "flagged"}


# ------------------------------------------------------------------------- automation level


@router.get("/automation")
def automation(session: Session = Depends(get_session)) -> dict[str, Any]:
    """The current threshold and the measured accuracy-versus-automation curves."""

    def report(name: str) -> dict[str, Any] | None:
        path = reports_dir() / f"evaluation-{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    chosen = reports_dir() / "threshold.json"
    return {
        "threshold": current_threshold(session),
        "recommended": json.loads(chosen.read_text()) if chosen.exists() else None,
        "tuning": report("tuning"),
        "heldout": report("heldout"),
    }


class Threshold(BaseModel):
    """A new confidence threshold."""

    value: float


@router.put("/automation/threshold")
def set_threshold(body: Threshold, session: Session = Depends(get_session)) -> dict[str, float]:
    """Change how sure every field must be before an e-mail runs without a person."""
    if not 0 <= body.value <= 1:
        raise HTTPException(status_code=422, detail="the threshold is between 0 and 1")
    before = current_threshold(session)
    session.execute(
        insert(AppSetting)
        .values(key="confidence_threshold", value=body.value)
        .on_conflict_do_update(index_elements=["key"], set_={"value": body.value})
    )
    repo.audit(
        session,
        OWNER,
        "threshold.changed",
        "setting",
        "confidence_threshold",
        before=before,
        after=body.value,
    )
    return {"threshold": body.value}


# ------------------------------------------------------------------------------------ rules


@router.get("/rules")
def list_rules(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """The rules as they apply now."""
    return [rule.model_dump() for rule in current_rules(session)]


def _past_cases(session: Session, limit: int = 500) -> list[tuple[Case, dict[str, Any]]]:
    rows = session.execute(
        select(Case)
        .where(Case.state.isnot(None), Case.category.isnot(None))
        .order_by(Case.created_at.desc())
        .limit(limit)
    ).scalars()
    return [(c, from_jsonable(c.state.get("facts", {}))) for c in rows if c.state.get("facts")]


@router.post("/rules/test")
def test_rule(rule: Rule, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Run a rule (edited or new) on past cases to see which e-mails it would have stopped."""
    try:
        validate_rule(rule)
    except RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    existing = next((r for r in current_rules(session) if r.id == rule.id), None)
    results = []
    for case, facts in _past_cases(session):
        if case.category not in rule.applies_to and "*" not in rule.applies_to:
            continue
        now = [r for r in evaluate_rule(rule, facts) if r.outcome in ("failed", "error")]
        before = (
            [r for r in evaluate_rule(existing, facts) if r.outcome in ("failed", "error")]
            if existing
            else []
        )
        message = session.get(Message, case.message_pk)
        results.append(
            {
                "case_id": str(case.id),
                "subject": message.subject if message else "",
                "from": message.from_address if message else "",
                "category": case.category,
                "stops_now": bool(before),
                "would_stop": bool(now),
                "message": now[0].message if now else "",
            }
        )
    return {
        "checked": len(results),
        "would_stop": sum(r["would_stop"] for r in results),
        "stops_now": sum(r["stops_now"] for r in results),
        "newly_stopped": [r for r in results if r["would_stop"] and not r["stops_now"]],
        "no_longer_stopped": [r for r in results if r["stops_now"] and not r["would_stop"]],
    }


@router.put("/rules/{rule_id}")
def save_rule(rule_id: str, rule: Rule, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Save a rule. Applies to e-mails processed from now on."""
    if rule.id != rule_id:
        raise HTTPException(status_code=422, detail="the rule id cannot change")
    try:
        validate_rule(rule)
    except RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    rules = current_rules(session)
    if not session.execute(select(RuleRecord.id).limit(1)).first():
        for position, item in enumerate(rules):
            session.add(RuleRecord(id=item.id, position=position, definition=item.model_dump()))
        session.flush()
    row = session.get(RuleRecord, rule_id)
    before = row.definition if row else None
    if row is None:
        position = (
            session.execute(select(func.coalesce(func.max(RuleRecord.position), 0))).scalar_one()
            + 1
        )
        session.add(RuleRecord(id=rule_id, position=position, definition=rule.model_dump()))
    else:
        row.definition = rule.model_dump()
    repo.audit(
        session, OWNER, "rule.saved", "rule", rule_id, before=before, after=rule.model_dump()
    )
    return rule.model_dump()


# -------------------------------------------------------------------------------- templates

Kind = Literal["contract", "invoice"]


@router.get("/templates/{kind}")
def get_template(kind: Kind, session: Session = Depends(get_session)) -> dict[str, Any]:
    """The template's HTML source."""
    row = session.get(AppSetting, f"template:{kind}")
    return {"kind": kind, "source": template_source(session, kind), "customised": row is not None}


class TemplateSource(BaseModel):
    """HTML of a template."""

    source: str


@router.put("/templates/{kind}")
def save_template(
    kind: Kind, body: TemplateSource, session: Session = Depends(get_session)
) -> dict[str, str]:
    """Save a template; documents issued from now on use it."""
    _preview(session, kind, body.source)  # refuse a template that does not render
    session.execute(
        insert(AppSetting)
        .values(key=f"template:{kind}", value=body.source)
        .on_conflict_do_update(index_elements=["key"], set_={"value": body.source})
    )
    repo.audit(session, OWNER, "template.saved", "template", kind)
    return {"status": "saved"}


@router.delete("/templates/{kind}")
def reset_template(kind: Kind, session: Session = Depends(get_session)) -> dict[str, str]:
    """Go back to the shipped template."""
    row = session.get(AppSetting, f"template:{kind}")
    if row is not None:
        session.delete(row)
        repo.audit(session, OWNER, "template.reset", "template", kind)
    return {"status": "reset"}


def _preview(session: Session, kind: str, source: str) -> bytes:
    business = load_business()
    booking = session.execute(
        select(Booking).order_by(Booking.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=409, detail="there is no booking yet to preview with")
    customer = session.get(Customer, booking.customer_id)
    car = business.car(booking.car_code)
    assert customer is not None and car is not None
    priced = quote(
        business, car, booking.pickup_at, booking.return_at, [Extra(e) for e in booking.extras]
    )
    context = DocumentContext(
        business=business,
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
        purpose=booking.purpose or "other",
        issued_on=datetime.now(UTC).date(),
    )
    try:
        return render_pdf(render_html(kind, "PREVIEW", context, source=source))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"the template does not render: {exc}") from exc


@router.post("/templates/{kind}/preview")
def preview_template(
    kind: Kind, body: TemplateSource, session: Session = Depends(get_session)
) -> Response:
    """Render the edited template with the most recent real booking."""
    return Response(content=_preview(session, kind, body.source), media_type="application/pdf")


# ---------------------------------------------------------------------------------- records


@router.get("/records/bookings")
def list_bookings(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Bookings with their customer, by pick-up."""
    header = [
        "reference",
        "status",
        "car",
        "customer",
        "email",
        "phone",
        "pickup",
        "return",
        "pickup_location",
        "extras",
        "total",
        "deposit",
    ]
    return [dict(zip(header, row, strict=True)) for row in booking_rows(session)]


@router.get("/records/cars")
def list_cars(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """The fleet and how many confirmed bookings each car has."""
    counts = dict(
        session.execute(
            select(Booking.car_code, func.count())
            .where(Booking.status == "confirmed")
            .group_by(Booking.car_code)
        ).all()
    )
    return [
        {
            "code": c.code,
            "name": f"{c.year} {c.make} {c.model}",
            "body": c.body,
            "colour": c.colour,
            "daily_rate": str(c.daily_rate),
            "bookings": counts.get(c.code, 0),
        }
        for c in session.execute(select(CarRecord).order_by(CarRecord.code)).scalars()
    ]


@router.get("/records/payables")
def list_payables(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Supplier invoices recorded for payment."""
    return [
        {
            "supplier": p.supplier_name,
            "number": p.number,
            "date": p.invoice_date.isoformat() if p.invoice_date else None,
            "total": str(p.total) if p.total is not None else None,
            "status": p.status,
        }
        for p in session.execute(
            select(PayableInvoice).order_by(PayableInvoice.created_at.desc())
        ).scalars()
    ]


@router.get("/records/audit")
def list_audit(limit: int = 200, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """The most recent audit entries: who or what did what."""
    return [
        {
            "at": a.at.isoformat(),
            "actor": a.actor,
            "action": a.action,
            "entity": a.entity,
            "entity_id": a.entity_id,
            "detail": a.detail,
        }
        for a in session.execute(
            select(AuditEntry).order_by(AuditEntry.id.desc()).limit(min(limit, 1000))
        ).scalars()
    ]


@router.get("/export/bookings.csv")
def export_bookings(session: Session = Depends(get_session)) -> Response:
    """The bookings sheet, always current (built from the database on request)."""
    return Response(
        content=to_csv(booking_rows(session)),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="larkspur-bookings.csv"'},
    )
