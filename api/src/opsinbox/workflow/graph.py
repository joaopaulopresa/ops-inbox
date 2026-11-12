"""The per-e-mail workflow as a LangGraph graph with a PostgreSQL checkpointer.

    classify > read > extract > validate > decide
    decide: automatic > act > END
    decide: review > review (interrupt) > approve > act, or reject > reject

``review`` is a real pause: the run is checkpointed and resumes when a person decides, days
later or after a restart. A run interrupted by a crash continues from its last checkpoint; the
model calls behind it are cached and the writes are keyed by the case, so nothing is repeated.
"""

import functools
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload, sessionmaker

from opsinbox import queue
from opsinbox.errors import ConflictError
from opsinbox.extraction import Extractor
from opsinbox.llm import ModelClient
from opsinbox.models import Message
from opsinbox.observability import CASES_DECIDED, get_tracer
from opsinbox.reading import Reader
from opsinbox.records import repository as repo
from opsinbox.records.business import Business
from opsinbox.records.models import AppSetting, Case, OutgoingMessage, RuleRecord
from opsinbox.records.repository import DbRecords
from opsinbox.rules.engine import Rule, load_rules
from opsinbox.storage import ObjectStore
from opsinbox.workflow.actions import ActionContext, act
from opsinbox.workflow.facts import from_jsonable
from opsinbox.workflow.processing import (
    CaseState,
    IncomingAttachment,
    IncomingMail,
    Processor,
    Reason,
)

logger = logging.getLogger(__name__)
SYSTEM = "system"
DEFAULT_THRESHOLD = 0.8


class GraphState(TypedDict, total=False):
    """What the checkpoint holds: identifiers and the JSON form of the case state."""

    case_id: str
    message_pk: str
    case: dict[str, Any]
    decision: dict[str, Any] | None


@dataclass
class Runtime:
    """Dependencies of the workflow."""

    sessions: sessionmaker[Session]
    store: ObjectStore
    client: ModelClient | None
    model: str
    business: Business
    reader: Reader
    box_reader: Reader | None


def load_mail(
    session: Session, store: ObjectStore, message_pk: uuid.UUID
) -> tuple[IncomingMail, Message]:
    """Load a stored message as processing input."""
    message = session.execute(
        select(Message).options(selectinload(Message.attachments)).where(Message.id == message_pk)
    ).scalar_one()
    attachments = [
        IncomingAttachment(
            id=str(a.id),
            filename=a.filename,
            content_type=a.content_type,
            load=functools.partial(store.get, a.storage_key),
        )
        for a in message.attachments
    ]
    sent_at = message.sent_at or message.received_at
    mail = IncomingMail(
        id=str(message.id),
        subject=message.subject,
        body=message.body_text,
        from_address=message.from_address,
        from_name=message.from_name,
        reply_to=message.reply_to,
        sent_at=sent_at,
        in_reply_to=message.in_reply_to,
        references=list(message.references),
        attachments=attachments,
    )
    return mail, message


def current_rules(session: Session) -> list[Rule]:
    """Rules as the owner last saved them, or the shipped defaults."""
    rows = list(session.execute(select(RuleRecord).order_by(RuleRecord.position)).scalars())
    if not rows:
        return load_rules()
    return [Rule.model_validate(row.definition) for row in rows]


def current_threshold(session: Session) -> float:
    """The confidence threshold the owner chose."""
    row = session.get(AppSetting, "confidence_threshold")
    return float(row.value) if row is not None else DEFAULT_THRESHOLD


def build_graph(
    runtime: Runtime, checkpointer: BaseCheckpointSaver[Any]
) -> CompiledStateGraph[Any]:
    """Compile the workflow."""

    def processor(session: Session) -> Processor:
        return Processor(
            Extractor(runtime.client, runtime.model, runtime.business) if runtime.client else None,
            runtime.reader,
            runtime.box_reader,
            runtime.business,
            current_rules(session),
            DbRecords(session),
            current_threshold(session),
        )

    def step(name: str) -> Any:
        def node(state: GraphState) -> GraphState:
            with runtime.sessions() as session:
                mail, _ = load_mail(session, runtime.store, uuid.UUID(state["message_pk"]))
                proc = processor(session)
                case = CaseState.model_validate(state["case"])
                case = getattr(proc, name)(case, mail)
                session.commit()
            return {"case": case.model_dump(mode="json")}

        return node

    def decide(state: GraphState) -> GraphState:
        with runtime.sessions() as session:
            case = processor(session).decide(CaseState.model_validate(state["case"]))
            row = session.get(Case, uuid.UUID(state["case_id"]))
            assert row is not None
            facts = from_jsonable(case.facts)
            urgent = facts.get("pickup_at")
            row.category = case.category.value if case.category else None
            row.route = case.route
            row.reasons = [r.model_dump() for r in case.reasons]
            row.state = case.model_dump(mode="json")
            row.urgent_at = urgent if isinstance(urgent, datetime) else None
            CASES_DECIDED.labels(route=case.route or "none", category=row.category or "none").inc()
            repo.audit(
                session,
                SYSTEM,
                "case.decided",
                "case",
                row.id,
                route=case.route,
                reasons=[r.code for r in case.reasons],
            )
            session.commit()
        return {"case": case.model_dump(mode="json")}

    def review(state: GraphState) -> GraphState:
        # Everything before interrupt() runs again on resume: keep it idempotent.
        with runtime.sessions() as session:
            row = session.get(Case, uuid.UUID(state["case_id"]))
            assert row is not None
            if row.status == "processing":
                row.status = "review"
                row.state = state["case"]
                session.commit()
        decision: dict[str, Any] = interrupt({"case_id": state["case_id"]})
        return {"decision": decision, "case": decision.get("state") or state["case"]}

    def act_node(state: GraphState) -> GraphState:
        decision = state.get("decision") or {}
        actor = decision.get("actor", SYSTEM)
        with runtime.sessions() as session:
            row = session.get(Case, uuid.UUID(state["case_id"]))
            assert row is not None
            _, message = load_mail(session, runtime.store, uuid.UUID(state["message_pk"]))
            case = CaseState.model_validate(state["case"])
            context = ActionContext(
                session, runtime.store, runtime.business, runtime.client, runtime.model, actor
            )
            try:
                outcome = act(context, row, message, case)
            except ConflictError as exc:
                session.rollback()
                row = session.get(Case, uuid.UUID(state["case_id"]))
                assert row is not None
                case.route = "review"
                case.reasons = [*case.reasons, Reason(code="records_changed", message=str(exc))]
                row.status, row.route = "processing", "review"
                row.reasons = [r.model_dump() for r in case.reasons]
                repo.audit(session, SYSTEM, "case.conflict", "case", row.id, detail=str(exc))
                session.commit()
                return {"case": case.model_dump(mode="json"), "decision": None}
            if decision.get("reply_body"):
                _person_reply(session, row, message, decision["reply_body"])
                outcome_status = "ready"
            else:
                outcome_status = outcome.status
            row.status = outcome_status
            row.outcome = outcome.summary
            row.state = case.model_dump(mode="json")
            if decision:
                row.decided_by = actor
                row.decided_at = datetime.now(UTC)
            repo.audit(session, actor, "case.acted", "case", row.id, outcome=outcome.summary)
            if outcome.booking_reference:
                queue.enqueue(session, "sync_sheet", f"sync_sheet:{row.id}", {})
            session.commit()
        return {}

    def reject(state: GraphState) -> GraphState:
        decision = state.get("decision") or {}
        with runtime.sessions() as session:
            row = session.get(Case, uuid.UUID(state["case_id"]))
            assert row is not None
            if row.status != "rejected":
                row.status = "rejected"
                row.outcome = decision.get("note") or "Rejected in review."
                row.decided_by = decision.get("actor", "unknown")
                row.decided_at = datetime.now(UTC)
                repo.audit(
                    session,
                    row.decided_by,
                    "case.rejected",
                    "case",
                    row.id,
                    note=decision.get("note", ""),
                )
                session.commit()
        return {}

    def after_decide(state: GraphState) -> str:
        return "act" if state["case"].get("route") == "auto" else "review"

    def after_review(state: GraphState) -> str:
        decision = state.get("decision") or {}
        return "act" if decision.get("action") == "approve" else "reject"

    def after_act(state: GraphState) -> str:
        return (
            "review"
            if state["case"].get("route") == "review" and not state.get("decision")
            else END
        )

    tracer = get_tracer()

    def traced(name: str, node: Any) -> Any:
        def run(state: GraphState) -> GraphState:
            with tracer.step(state["case_id"], name):
                result: GraphState = node(state)
                return result

        return run

    graph = StateGraph(GraphState)
    for name in ("classify", "read_attachments", "extract", "validate"):
        graph.add_node(name, traced(name, step(name)))
    graph.add_node("decide", traced("decide", decide))
    graph.add_node("review", traced("review", review))
    graph.add_node("act", traced("act", act_node))
    graph.add_node("reject", traced("reject", reject))
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "read_attachments")
    graph.add_edge("read_attachments", "extract")
    graph.add_edge("extract", "validate")
    graph.add_edge("validate", "decide")
    graph.add_conditional_edges("decide", after_decide, ["act", "review"])
    graph.add_conditional_edges("review", after_review, ["act", "reject"])
    graph.add_conditional_edges("act", after_act, ["review", END])
    graph.add_edge("reject", END)
    return graph.compile(checkpointer=checkpointer)


def _person_reply(session: Session, case: Case, message: Message, body: str) -> None:
    """Store the reply a person wrote in review (questions, or an edited draft)."""
    existing = session.execute(
        select(OutgoingMessage).where(OutgoingMessage.case_id == case.id)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.status == "draft":
            existing.body = body
        return
    subject = (
        message.subject if message.subject.lower().startswith("re:") else f"Re: {message.subject}"
    )
    session.execute(
        insert(OutgoingMessage)
        .values(
            id=uuid.uuid4(),
            case_id=case.id,
            to_address=message.reply_to or message.from_address,
            subject=subject,
            body=body,
            document_ids=[],
            in_reply_to=message.message_id,
            references=[*message.references, message.message_id],
            message_id=f"<reply-{case.id}@larkspur.example>",
        )
        .on_conflict_do_nothing(index_elements=["case_id"])
    )


def config_for(case_id: uuid.UUID | str) -> RunnableConfig:
    """LangGraph run configuration: one thread per case."""
    return {"configurable": {"thread_id": str(case_id)}}


def ensure_case(session: Session, message_pk: uuid.UUID) -> uuid.UUID:
    """Create the case of a message if it does not exist; return its id."""
    session.execute(
        insert(Case)
        .values(id=uuid.uuid4(), message_pk=message_pk, status="processing")
        .on_conflict_do_nothing(index_elements=["message_pk"])
    )
    case_id = session.execute(select(Case.id).where(Case.message_pk == message_pk)).scalar_one()
    session.commit()
    return case_id


def run_case(graph: CompiledStateGraph[Any], case_id: uuid.UUID, message_pk: uuid.UUID) -> str:
    """Start, continue or leave alone the run of a case, depending on its checkpoint.

    Returns what was done: ``started``, ``continued``, ``waiting`` (paused for review) or
    ``finished``.
    """
    config = config_for(case_id)
    snapshot = graph.get_state(config)
    if not snapshot.values:
        initial: GraphState = {
            "case_id": str(case_id),
            "message_pk": str(message_pk),
            "case": CaseState(mail_id=str(message_pk)).model_dump(mode="json"),
            "decision": None,
        }
        graph.invoke(initial, config, durability="sync")
        return "started"
    if snapshot.interrupts:
        return "waiting"
    if snapshot.next:
        graph.invoke(None, config, durability="sync")
        return "continued"
    return "finished"


def resume_case(
    graph: CompiledStateGraph[Any], case_id: uuid.UUID, decision: dict[str, Any]
) -> str:
    """Resume a paused case with a person's decision. Ignored if it is not paused."""
    config = config_for(case_id)
    snapshot = graph.get_state(config)
    if not snapshot.interrupts:
        return "not_waiting"
    graph.invoke(Command(resume=decision), config, durability="sync")
    return "resumed"
