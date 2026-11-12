"""ORM models for ingestion and the work queue.

Records created by later steps (cases, bookings, documents, rules) live in their own modules and
share :class:`opsinbox.db.Base`.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from opsinbox.db import Base


def new_id() -> uuid.UUID:
    """Return a fresh random primary key."""
    return uuid.uuid4()


class Message(Base):
    """One e-mail, stored exactly once.

    The pair (``message_id``, ``content_hash``) identifies a message: the same message delivered
    twice maps to the same row, however many times the mailbox hands it over.
    """

    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("message_id", "content_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    direction: Mapped[str] = mapped_column(String(8), default="in")
    mailbox: Mapped[str] = mapped_column(String(255))
    message_id: Mapped[str] = mapped_column(String(998))
    content_hash: Mapped[str] = mapped_column(String(64))
    raw_key: Mapped[str] = mapped_column(String(512))
    from_address: Mapped[str] = mapped_column(String(320))
    from_name: Mapped[str] = mapped_column(String(255), default="")
    reply_to: Mapped[str] = mapped_column(String(320), default="")
    to_address: Mapped[str] = mapped_column(String(998), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    in_reply_to: Mapped[str] = mapped_column(String(998), default="")
    references: Mapped[list[str]] = mapped_column(JSONB, default=list)
    thread_key: Mapped[str] = mapped_column(String(998), index=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="message", order_by="Attachment.position", cascade="all, delete-orphan"
    )


class Attachment(Base):
    """A file attached to a message. The bytes live in object storage."""

    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    message_pk: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(127))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512))

    message: Mapped[Message] = relationship(back_populates="attachments")


class MailboxCursor(Base):
    """How far ingestion has read a mailbox. An optimisation only: dedup is in ``messages``."""

    __tablename__ = "mailbox_cursors"

    mailbox: Mapped[str] = mapped_column(String(255), primary_key=True)
    uid_validity: Mapped[int] = mapped_column(Integer, default=0)
    last_uid: Mapped[int] = mapped_column(Integer, default=0)


class Job(Base):
    """A unit of work for the workers, taken with ``FOR UPDATE SKIP LOCKED``."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("dedup_key"),
        Index("ix_jobs_ready", "status", "run_after"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(64))
    dedup_key: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
