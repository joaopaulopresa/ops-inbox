"""The LangGraph workflow against PostgreSQL: pause, resume, crash and restart, duplicates.

The model and the reader are scripted fakes; everything else is real: the database, the
checkpointer, the queue, the rules, the records and the documents.
"""

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from opsinbox.errors import ModelError
from opsinbox.ingest import poll_inbox, store_message
from opsinbox.mail.base import FakeInbox, FakeOutbox
from opsinbox.records.business import load_business
from opsinbox.records.models import Booking, Case, Document, OutgoingMessage
from opsinbox.records.repository import seed_business
from opsinbox.settings import Settings
from opsinbox.storage import MemoryObjectStore
from opsinbox.worker import run_one_job
from opsinbox.workflow.graph import Runtime, build_graph, ensure_case, resume_case, run_case
from opsinbox.workflow.service import build_handlers, checkpointer, enqueue_send
from tests.conftest import make_mail
from tests.fakes import (
    BOOKING_BODY,
    LICENCE_TEXT,
    FixedReader,
    Script,
    ScriptedModel,
    booking_script,
    licence_script,
)

BUSINESS = load_business()


@pytest.fixture
def saver(database_url: str) -> Iterator[Any]:
    with checkpointer(database_url) as instance:
        yield instance


def runtime_for(
    sessions: sessionmaker[Session],
    store: MemoryObjectStore,
    model: ScriptedModel,
    expires: str = "01 MAY 2031",
) -> Runtime:
    with sessions() as session:
        seed_business(session, BUSINESS)
        session.commit()
    reader = FixedReader(LICENCE_TEXT.format(expires=expires))
    return Runtime(sessions, store, model, "fake-model", BUSINESS, reader, None)


def deliver(
    sessions: sessionmaker[Session],
    store: MemoryObjectStore,
    marker: str,
    pickup: str,
    message_id: str | None = None,
) -> uuid.UUID:
    raw = make_mail(
        message_id=message_id or f"<{marker}@customer.example>",
        sender="Ada Quill <ada@customer.example>",
        subject=f"Booking {marker}",
        body=BOOKING_BODY.format(pickup=pickup),
        attachments=[("licence.png", "image/png", b"\x89PNG licence " + marker.encode())],
    )
    with sessions() as session:
        result = store_message(session, store, "inbox", raw)
        session.commit()
    return result.message_pk


def count(sessions: sessionmaker[Session], model: type) -> int:
    with sessions() as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


def case_of(sessions: sessionmaker[Session], message_pk: uuid.UUID) -> Case:
    with sessions() as session:
        return session.execute(select(Case).where(Case.message_pk == message_pk)).scalar_one()


CLEAN = Script(
    booking=booking_script("12 June 2027", "2027-06-12"),
    licence=licence_script("2031-05-01", "01 MAY 2031"),
)
EXPIRED = Script(
    booking=booking_script("12 June 2027", "2027-06-12"),
    licence=licence_script("2027-01-31", "31 JAN 2027"),
)


def test_clean_booking_runs_straight_through_and_only_once(
    sessions: sessionmaker[Session], store: MemoryObjectStore, saver: Any
) -> None:
    model = ScriptedModel({"T1": CLEAN})
    graph = build_graph(runtime_for(sessions, store, model), saver)
    message_pk = deliver(sessions, store, "T1", "12 June 2027")
    with sessions() as session:
        case_id = ensure_case(session, message_pk)

    assert run_case(graph, case_id, message_pk) == "started"

    case = case_of(sessions, message_pk)
    assert (case.status, case.route, case.category) == ("ready", "auto", "new_booking")
    with sessions() as session:
        booking = session.execute(select(Booking)).scalar_one()
        assert (booking.car_code, booking.pickup_at.hour, booking.return_at.hour) == (
            "SPRITE",
            10,
            18,
        )
        assert str(booking.total) == "374.40"  # 1 day x 260 x 1.20 high season + 20% tax
        kinds = sorted(d.kind for d in session.execute(select(Document)).scalars())
        assert kinds == ["contract", "invoice"]
        reply = session.execute(select(OutgoingMessage)).scalar_one()
        assert reply.status == "draft" and booking.reference in reply.body
    calls = len(model.calls)

    # The same job delivered again: nothing runs, nothing is created twice.
    assert run_case(graph, case_id, message_pk) == "finished"
    assert len(model.calls) == calls
    assert count(sessions, Booking) == 1 and count(sessions, Document) == 2


def test_failing_rule_pauses_for_review_and_resumes_on_approval(
    sessions: sessionmaker[Session], store: MemoryObjectStore, saver: Any
) -> None:
    model = ScriptedModel({"T2": EXPIRED})
    graph = build_graph(runtime_for(sessions, store, model, expires="31 JAN 2027"), saver)
    message_pk = deliver(sessions, store, "T2", "12 June 2027")
    with sessions() as session:
        case_id = ensure_case(session, message_pk)

    assert run_case(graph, case_id, message_pk) == "started"
    case = case_of(sessions, message_pk)
    assert case.status == "review"
    assert "licence_valid_for_rental" in [r["code"] for r in case.reasons]
    assert count(sessions, Booking) == 0
    assert run_case(graph, case_id, message_pk) == "waiting"  # a duplicate job does not unpause it

    decision = {"action": "approve", "actor": "olivia", "state": None}
    assert resume_case(graph, case_id, decision) == "resumed"
    case = case_of(sessions, message_pk)
    assert (case.status, case.decided_by) == ("ready", "olivia")
    assert count(sessions, Booking) == 1
    assert resume_case(graph, case_id, decision) == "not_waiting"
    assert count(sessions, Booking) == 1


def test_reject_in_review_creates_nothing(
    sessions: sessionmaker[Session], store: MemoryObjectStore, saver: Any
) -> None:
    graph = build_graph(
        runtime_for(sessions, store, ScriptedModel({"T3": EXPIRED}), expires="31 JAN 2027"), saver
    )
    message_pk = deliver(sessions, store, "T3", "12 June 2027")
    with sessions() as session:
        case_id = ensure_case(session, message_pk)
    run_case(graph, case_id, message_pk)
    resume_case(graph, case_id, {"action": "reject", "actor": "olivia", "note": "expired"})
    assert case_of(sessions, message_pk).status == "rejected"
    assert count(sessions, Booking) == 0


def test_a_crash_mid_run_resumes_from_the_checkpoint_after_restart(
    sessions: sessionmaker[Session], store: MemoryObjectStore, database_url: str
) -> None:
    model = ScriptedModel({"T4": CLEAN})
    model.fail_on = {"booking"}  # the extraction step dies the first time
    message_pk = deliver(sessions, store, "T4", "12 June 2027")
    with sessions() as session:
        case_id = ensure_case(session, message_pk)

    with checkpointer(database_url) as first_saver:
        graph = build_graph(runtime_for(sessions, store, model), first_saver)
        with pytest.raises(ModelError):
            run_case(graph, case_id, message_pk)
    classified_before = model.calls.count("classify")

    # A new process: new checkpointer, new graph. The run continues, it does not start over.
    with checkpointer(database_url) as second_saver:
        graph = build_graph(runtime_for(sessions, store, model), second_saver)
        assert run_case(graph, case_id, message_pk) == "continued"

    assert model.calls.count("classify") == classified_before == 1
    assert case_of(sessions, message_pk).status == "ready"
    assert count(sessions, Booking) == 1


def test_duplicate_delivery_and_concurrent_workers_process_each_email_once(
    sessions: sessionmaker[Session], store: MemoryObjectStore, saver: Any
) -> None:
    scripts = {
        f"C{i}": Script(
            booking=booking_script(f"{10 + i} July 2027", f"2027-07-{10 + i}"),
            licence=licence_script("2031-05-01", "01 MAY 2031"),
        )
        for i in range(6)
    }
    model = ScriptedModel(scripts)
    runtime = runtime_for(sessions, store, model)
    graph = build_graph(runtime, saver)
    handlers = build_handlers(runtime, graph, FakeOutbox())
    inbox = FakeInbox()
    for i in range(6):
        raw = make_mail(
            message_id=f"<c{i}@customer.example>",
            subject=f"Booking C{i}",
            sender="Ada Quill <ada@customer.example>",
            body=BOOKING_BODY.format(pickup=f"{10 + i} July 2027"),
            attachments=[("licence.png", "image/png", f"img{i}".encode())],
        )
        inbox.deliver(raw)
        inbox.deliver(raw)  # every message arrives twice
    poll_inbox(sessions, store, inbox)
    settings = Settings()

    def work(worker: int) -> int:
        done = 0
        while run_one_job(sessions, handlers, f"w{worker}", settings):
            done += 1
        return done

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = sum(pool.map(work, range(4)))

    assert jobs == 6
    assert count(sessions, Case) == 6
    assert count(sessions, Booking) == 6
    assert count(sessions, Document) == 12
    with sessions() as session:
        numbers = list(session.execute(select(Document.number)).scalars())
        assert len(set(numbers)) == 12  # gapless numbering never hands out a number twice


def test_two_requests_for_the_same_car_and_time_never_double_book(
    sessions: sessionmaker[Session], store: MemoryObjectStore, saver: Any
) -> None:
    model = ScriptedModel({"D1": CLEAN, "D2": CLEAN})
    graph = build_graph(runtime_for(sessions, store, model), saver)
    first = deliver(sessions, store, "D1", "12 June 2027")
    second = deliver(sessions, store, "D2", "12 June 2027")
    with sessions() as session:
        cases = [(ensure_case(session, m), m) for m in (first, second)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda pair: run_case(graph, *pair), cases))

    assert count(sessions, Booking) == 1
    statuses = sorted(case_of(sessions, m).status for m in (first, second))
    assert statuses == ["ready", "review"]


def test_approved_reply_is_sent_once_with_the_documents(
    sessions: sessionmaker[Session], store: MemoryObjectStore, saver: Any
) -> None:
    model = ScriptedModel({"S1": CLEAN})
    runtime = runtime_for(sessions, store, model)
    graph = build_graph(runtime, saver)
    outbox = FakeOutbox()
    handlers = build_handlers(runtime, graph, outbox)
    message_pk = deliver(sessions, store, "S1", "12 June 2027")
    with sessions() as session:
        case_id = ensure_case(session, message_pk)
    run_case(graph, case_id, message_pk)

    with sessions() as session:
        reply = session.execute(select(OutgoingMessage)).scalar_one()
        reply.status, reply.approved_by = "approved", "olivia"
        enqueue_send(session, case_id)
        enqueue_send(session, case_id)  # a double click
        session.commit()
    while run_one_job(sessions, handlers, "w", Settings()):
        pass

    assert len(outbox.sent) == 1
    sent = outbox.sent[0]
    assert sent["In-Reply-To"] == "<S1@customer.example>"
    assert sorted(a.get_filename() for a in sent.iter_attachments())[0].startswith("LCH-C-")
    assert case_of(sessions, message_pk).status == "done"


def test_an_unreadable_attachment_sends_the_case_to_review_instead_of_failing(
    sessions: sessionmaker[Session], store: MemoryObjectStore, saver: Any
) -> None:
    from opsinbox.reading import TesseractReader

    model = ScriptedModel({"U1": CLEAN})
    runtime = runtime_for(sessions, store, model)
    runtime.reader = TesseractReader()  # a real reader: the "image" below is not an image
    graph = build_graph(runtime, saver)
    message_pk = deliver(sessions, store, "U1", "12 June 2027")
    with sessions() as session:
        case_id = ensure_case(session, message_pk)

    assert run_case(graph, case_id, message_pk) == "started"
    case = case_of(sessions, message_pk)
    assert case.status == "review"
    assert "licence_readable" in [r["code"] for r in case.reasons]
