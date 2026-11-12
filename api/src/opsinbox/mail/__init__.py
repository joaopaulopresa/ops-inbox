"""Mail: IMAP in, SMTP out, both behind interfaces with fakes for tests."""

from opsinbox.mail.base import Inbox, Outbox, RawMail
from opsinbox.mail.parse import ParsedAttachment, ParsedMail, parse_mail

__all__ = ["Inbox", "Outbox", "ParsedAttachment", "ParsedMail", "RawMail", "parse_mail"]
