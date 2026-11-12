"""Interfaces for reading and sending mail, with in-memory fakes."""

from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol


@dataclass(frozen=True)
class RawMail:
    """A message exactly as the mailbox holds it."""

    uid: int
    raw: bytes


class Inbox(Protocol):
    """A mailbox that can be read incrementally."""

    name: str

    def uid_validity(self) -> int:
        """Return the mailbox generation; a change means UIDs were renumbered."""

    def fetch_after(self, last_uid: int) -> list[RawMail]:
        """Return the messages whose UID is greater than ``last_uid``, oldest first."""


class Outbox(Protocol):
    """Something that can send a message."""

    def send(self, message: EmailMessage) -> None:
        """Send ``message`` or raise :class:`opsinbox.errors.MailError`."""


@dataclass
class FakeInbox:
    """In-memory mailbox for tests. ``deliver`` appends a message, duplicates allowed."""

    name: str = "fake"
    validity: int = 1
    mails: list[RawMail] = field(default_factory=list)

    def deliver(self, raw: bytes) -> int:
        """Append a raw message and return its UID."""
        uid = len(self.mails) + 1
        self.mails.append(RawMail(uid=uid, raw=raw))
        return uid

    def uid_validity(self) -> int:
        """Return the mailbox generation."""
        return self.validity

    def fetch_after(self, last_uid: int) -> list[RawMail]:
        """Return the messages after ``last_uid``."""
        return [m for m in self.mails if m.uid > last_uid]


@dataclass
class FakeOutbox:
    """Collects sent messages for assertions."""

    sent: list[EmailMessage] = field(default_factory=list)

    def send(self, message: EmailMessage) -> None:
        """Record the message as sent."""
        self.sent.append(message)
