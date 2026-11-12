"""Wiring: the runtime, the checkpointer and the job handlers the worker runs."""

import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from pathlib import Path
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph.state import CompiledStateGraph
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from opsinbox import queue
from opsinbox.llm import ModelClient, OpenRouterClient, RecordingClient
from opsinbox.mail import Outbox
from opsinbox.reading import CachedReader, Reader, TesseractReader, VisionReader
from opsinbox.records import repository as repo
from opsinbox.records.business import Business
from opsinbox.records.models import Case, Document, OutgoingMessage
from opsinbox.settings import Settings
from opsinbox.storage import ObjectStore
from opsinbox.workflow.graph import Runtime, build_graph, ensure_case, resume_case, run_case

logger = logging.getLogger(__name__)

PROCESS_EMAIL = "process_email"
RESUME_CASE = "resume_case"
SEND_REPLY = "send_reply"

JobHandler = Callable[[dict[str, Any]], None]


def psycopg_url(sqlalchemy_url: str) -> str:
    """Turn ``postgresql+psycopg://`` into the plain URL psycopg expects."""
    return (
        make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


@contextmanager
def checkpointer(database_url: str) -> Iterator[PostgresSaver]:
    """A PostgreSQL checkpointer with its own small pool; creates its tables if needed."""
    pool = ConnectionPool(
        psycopg_url(database_url),
        min_size=1,
        max_size=8,
        open=True,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    try:
        saver = PostgresSaver(pool)  # type: ignore[arg-type]
        saver.setup()
        yield saver
    finally:
        pool.close()


def model_client(settings: Settings, cache_dir: Path) -> ModelClient | None:
    """The model client of the running product: every answer is cached on disk."""
    inner = None
    if settings.openrouter_api_key:
        inner = OpenRouterClient(
            settings.openrouter_api_key,
            settings.openrouter_base_url,
            settings.llm_timeout_seconds,
            settings.llm_max_retries,
        )
    extra = tuple(Path(p) for p in settings.model_cache_read_only.split(":") if p)
    return RecordingClient(inner, cache_dir, read_only=extra)


def build_runtime(
    settings: Settings,
    sessions: sessionmaker[Session],
    store: ObjectStore,
    business: Business,
    client: ModelClient | None,
) -> Runtime:
    """The runtime with the reader chosen in the reading spike (vision, OCR for boxes)."""
    readings = tuple(Path(p) for p in settings.readings_read_only.split(":") if p)
    ocr = CachedReader(TesseractReader(), "tesseract", Path(settings.model_cache_dir), readings)
    reader: Reader = VisionReader(client, settings.llm_model) if client else ocr
    return Runtime(sessions, store, client, settings.llm_model, business, reader, ocr)


def build_handlers(
    runtime: Runtime, graph: CompiledStateGraph[Any], outbox: Outbox
) -> dict[str, JobHandler]:
    """Job handlers. Each is idempotent: a job delivered twice does its work once."""

    def process_email(payload: dict[str, Any]) -> None:
        message_pk = uuid.UUID(payload["message"])
        with runtime.sessions() as session:
            case_id = ensure_case(session, message_pk)
        result = run_case(graph, case_id, message_pk)
        logger.info("case %s: %s", case_id, result)

    def resume(payload: dict[str, Any]) -> None:
        result = resume_case(graph, uuid.UUID(payload["case"]), payload["decision"])
        logger.info("case %s: %s", payload["case"], result)

    def send(payload: dict[str, Any]) -> None:
        send_reply(
            runtime.sessions, runtime.store, outbox, runtime.business, uuid.UUID(payload["case"])
        )

    return {PROCESS_EMAIL: process_email, RESUME_CASE: resume, SEND_REPLY: send}


def send_reply(
    sessions: sessionmaker[Session],
    store: ObjectStore,
    outbox: Outbox,
    business: Business,
    case_id: uuid.UUID,
) -> bool:
    """Send the approved reply of a case. Returns False if there was nothing to send.

    The status moves to ``sent`` in the same transaction that records the audit entry, after
    the SMTP server accepted the message. A crash between the two can send one message twice;
    the fixed Message-ID lets the recipient's mail client recognise the duplicate.
    """
    with sessions() as session:
        outgoing = session.execute(
            select(OutgoingMessage).where(OutgoingMessage.case_id == case_id).with_for_update()
        ).scalar_one_or_none()
        if outgoing is None or outgoing.status != "approved":
            return False
        message = EmailMessage()
        message["Message-ID"] = outgoing.message_id or make_msgid()
        message["Date"] = format_datetime(datetime.now(UTC))
        message["From"] = formataddr((business.name, business.email))
        message["To"] = outgoing.to_address
        message["Subject"] = outgoing.subject
        if outgoing.in_reply_to:
            message["In-Reply-To"] = outgoing.in_reply_to
            message["References"] = " ".join(outgoing.references)
        message.set_content(outgoing.body)
        for document_id in outgoing.document_ids:
            document = session.get(Document, uuid.UUID(document_id))
            if document is None:
                continue
            message.add_attachment(
                store.get(document.storage_key),
                maintype="application",
                subtype="pdf",
                filename=f"{document.number}.pdf",
            )
        outbox.send(message)
        outgoing.status = "sent"
        outgoing.sent_at = datetime.now(UTC)
        case = session.get(Case, case_id)
        if case is not None:
            case.status = "done"
        repo.audit(
            session,
            outgoing.approved_by or "unknown",
            "reply.sent",
            "case",
            case_id,
            to=outgoing.to_address,
        )
        session.commit()
    return True


def enqueue_resume(session: Session, case_id: uuid.UUID, decision: dict[str, Any]) -> None:
    """Queue the resumption of a paused case (one per decision)."""
    queue.enqueue(
        session,
        RESUME_CASE,
        f"{RESUME_CASE}:{case_id}:{uuid.uuid4()}",
        {"case": str(case_id), "decision": decision},
    )


def enqueue_send(session: Session, case_id: uuid.UUID) -> None:
    """Queue the sending of a case's approved reply (at most once per case)."""
    queue.enqueue(session, SEND_REPLY, f"{SEND_REPLY}:{case_id}", {"case": str(case_id)})


__all__ = ["build_graph", "build_handlers", "build_runtime", "checkpointer", "model_client"]
