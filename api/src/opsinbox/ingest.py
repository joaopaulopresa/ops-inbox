"""Mail ingestion: store every message exactly once and queue it for processing.

Order of operations for one message:

1. write the raw message and attachments to object storage (content-addressed, idempotent);
2. in one database transaction, insert the message row, its attachments and the processing job.

The insert uses the unique pair (message id, content hash). A duplicate delivery, a concurrent
worker or a rerun after a crash all reach the same row and add nothing.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from opsinbox import queue
from opsinbox.errors import MessageParseError
from opsinbox.mail import Inbox, ParsedMail, parse_mail
from opsinbox.models import Attachment, MailboxCursor, Message
from opsinbox.storage import ObjectStore, sha256_hex

logger = logging.getLogger(__name__)

PROCESS_EMAIL = "process_email"


@dataclass(frozen=True)
class StoreResult:
    """Outcome of storing one message."""

    message_pk: uuid.UUID
    created: bool


@dataclass(frozen=True)
class PollResult:
    """Outcome of one pass over a mailbox."""

    fetched: int
    stored: int
    duplicates: int
    unreadable: int


def store_message(session: Session, store: ObjectStore, mailbox: str, raw: bytes) -> StoreResult:
    """Store one raw message exactly once. The caller commits.

    Raises:
        MessageParseError: when ``raw`` is not a message.

    """
    parsed: ParsedMail = parse_mail(raw)
    content_hash = parsed.content_hash

    raw_key = f"raw/{sha256_hex(raw)}.eml"
    store.put(raw_key, raw, "message/rfc822")
    for attachment in parsed.attachments:
        store.put(f"attachments/{attachment.sha256}", attachment.data, attachment.content_type)

    message_pk = uuid.uuid4()
    inserted = session.execute(
        insert(Message)
        .values(
            id=message_pk,
            direction="in",
            mailbox=mailbox,
            message_id=parsed.message_id,
            content_hash=content_hash,
            raw_key=raw_key,
            from_address=parsed.from_address,
            from_name=parsed.from_name,
            reply_to=parsed.reply_to,
            to_address=parsed.to_address,
            subject=parsed.subject,
            body_text=parsed.body_text,
            sent_at=parsed.sent_at,
            in_reply_to=parsed.in_reply_to,
            references=parsed.references,
            thread_key=parsed.thread_key or f"<{content_hash}>",
        )
        .on_conflict_do_nothing(index_elements=["message_id", "content_hash"])
        .returning(Message.id)
    ).first()

    if inserted is None:
        existing = session.execute(
            select(Message.id).where(
                Message.message_id == parsed.message_id, Message.content_hash == content_hash
            )
        ).scalar_one()
        return StoreResult(message_pk=existing, created=False)

    for position, attachment in enumerate(parsed.attachments):
        session.add(
            Attachment(
                message_pk=message_pk,
                position=position,
                filename=attachment.filename,
                content_type=attachment.content_type,
                size=len(attachment.data),
                sha256=attachment.sha256,
                storage_key=f"attachments/{attachment.sha256}",
            )
        )
    session.flush()
    queue.enqueue(
        session, PROCESS_EMAIL, f"{PROCESS_EMAIL}:{message_pk}", {"message": str(message_pk)}
    )
    return StoreResult(message_pk=message_pk, created=True)


def poll_inbox(sessions: sessionmaker[Session], store: ObjectStore, inbox: Inbox) -> PollResult:
    """Read what is new in ``inbox`` and store it. Safe to run concurrently and to rerun."""
    with sessions() as session:
        cursor = session.get(MailboxCursor, inbox.name)
        last_uid = cursor.last_uid if cursor else 0
        known_validity = cursor.uid_validity if cursor else 0

    validity = inbox.uid_validity()
    if validity != known_validity:
        last_uid = 0  # the server renumbered the mailbox: read it all again, dedup protects us

    mails = inbox.fetch_after(last_uid)
    stored = duplicates = unreadable = 0
    for mail in mails:
        with sessions() as session:
            try:
                result = store_message(session, store, inbox.name, mail.raw)
            except MessageParseError:
                logger.warning(
                    "unreadable message skipped: mailbox=%s uid=%s", inbox.name, mail.uid
                )
                unreadable += 1
                session.rollback()
            else:
                stored += result.created
                duplicates += not result.created
            session.execute(
                insert(MailboxCursor)
                .values(mailbox=inbox.name, uid_validity=validity, last_uid=mail.uid)
                .on_conflict_do_update(
                    index_elements=["mailbox"],
                    set_={"uid_validity": validity, "last_uid": mail.uid},
                    where=(MailboxCursor.last_uid < mail.uid)
                    | (MailboxCursor.uid_validity != validity),
                )
            )
            session.commit()
    if mails:
        logger.info(
            "mailbox=%s fetched=%d stored=%d duplicates=%d unreadable=%d",
            inbox.name,
            len(mails),
            stored,
            duplicates,
            unreadable,
        )
    return PollResult(len(mails), stored, duplicates, unreadable)
