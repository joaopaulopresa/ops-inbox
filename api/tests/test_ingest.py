"""Exactly-once storage of incoming mail."""

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from opsinbox.ingest import poll_inbox, store_message
from opsinbox.mail.base import FakeInbox
from opsinbox.models import Attachment, Job, MailboxCursor, Message
from opsinbox.storage import MemoryObjectStore
from tests.conftest import make_mail


def count(sessions: sessionmaker[Session], model: type) -> int:
    with sessions() as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


def test_message_with_attachment_is_stored_and_queued(
    sessions: sessionmaker[Session], store: MemoryObjectStore
) -> None:
    inbox = FakeInbox()
    inbox.deliver(make_mail(attachments=[("licence.png", "image/png", b"\x89PNG fake")]))

    result = poll_inbox(sessions, store, inbox)

    assert (result.fetched, result.stored, result.duplicates) == (1, 1, 0)
    with sessions() as session:
        message = session.execute(select(Message)).scalar_one()
        assert message.from_address == "ada@customer.example"
        assert message.subject == "Booking request"
        assert "Aldermoor Sprite" in message.body_text
        attachment = session.execute(select(Attachment)).scalar_one()
        assert store.get(attachment.storage_key) == b"\x89PNG fake"
        assert store.get(message.raw_key).startswith(b"Message-ID")
        job = session.execute(select(Job)).scalar_one()
        assert job.kind == "process_email" and job.payload == {"message": str(message.id)}


def test_duplicate_delivery_is_stored_once(
    sessions: sessionmaker[Session], store: MemoryObjectStore
) -> None:
    inbox = FakeInbox()
    raw = make_mail()
    inbox.deliver(raw)
    inbox.deliver(raw)
    # Same message through another hop: transport headers differ, the content does not.
    inbox.deliver(make_mail(extra_headers={"Received": "from relay.example by mx.example"}))

    result = poll_inbox(sessions, store, inbox)

    assert (result.stored, result.duplicates) == (1, 2)
    assert count(sessions, Message) == 1
    assert count(sessions, Job) == 1


def test_rereading_the_mailbox_adds_nothing(
    sessions: sessionmaker[Session], store: MemoryObjectStore
) -> None:
    inbox = FakeInbox()
    inbox.deliver(make_mail(message_id="<a@x.example>"))
    inbox.deliver(make_mail(message_id="<b@x.example>", body="Second message"))
    poll_inbox(sessions, store, inbox)

    assert poll_inbox(sessions, store, inbox).fetched == 0  # the cursor moved

    inbox.validity = 2  # the server renumbered: everything is read again
    again = poll_inbox(sessions, store, inbox)
    assert (again.fetched, again.stored, again.duplicates) == (2, 0, 2)
    assert count(sessions, Message) == 2
    with sessions() as session:
        cursor = session.get(MailboxCursor, inbox.name)
        assert cursor is not None and (cursor.uid_validity, cursor.last_uid) == (2, 2)


def test_same_message_id_with_different_content_is_a_different_message(
    sessions: sessionmaker[Session], store: MemoryObjectStore
) -> None:
    inbox = FakeInbox()
    inbox.deliver(make_mail(body="Pick-up on 4 June."))
    inbox.deliver(make_mail(body="Pick-up on 5 June."))

    assert poll_inbox(sessions, store, inbox).stored == 2


def test_concurrent_workers_store_each_message_once(
    sessions: sessionmaker[Session], store: MemoryObjectStore
) -> None:
    raws = [make_mail(message_id=f"<m{i}@x.example>", body=f"Request {i}") for i in range(20)]

    def deliver_all(_: int) -> int:
        created = 0
        for raw in raws:
            with sessions() as session:
                created += store_message(session, store, "inbox", raw).created
                session.commit()
        return created

    with ThreadPoolExecutor(max_workers=8) as pool:
        created = sum(pool.map(deliver_all, range(8)))

    assert created == 20
    assert count(sessions, Message) == 20
    assert count(sessions, Job) == 20


def test_unreadable_bytes_are_skipped_without_blocking_the_mailbox(
    sessions: sessionmaker[Session], store: MemoryObjectStore
) -> None:
    inbox = FakeInbox()
    inbox.deliver(b"")
    inbox.deliver(make_mail())

    result = poll_inbox(sessions, store, inbox)

    assert (result.stored, result.unreadable) == (1, 1)
