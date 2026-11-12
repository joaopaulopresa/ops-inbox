"""Turn raw RFC 5322 bytes into the parts the application uses.

The content hash deliberately ignores transport headers (``Received``, ``Delivered-To`` and the
like), so the same message redelivered through another hop is still recognised as the same.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from opsinbox.errors import MessageParseError

_WS = re.compile(r"[ \t]+\n")


@dataclass(frozen=True)
class ParsedAttachment:
    """One attached file."""

    filename: str
    content_type: str
    data: bytes

    @property
    def sha256(self) -> str:
        """Hex digest of the file bytes."""
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class ParsedMail:
    """The parts of a message the application cares about."""

    message_id: str
    from_address: str
    from_name: str
    reply_to: str
    to_address: str
    subject: str
    sent_at: datetime | None
    in_reply_to: str
    references: list[str]
    body_text: str
    attachments: list[ParsedAttachment] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        """Digest of the content that makes this message what it is.

        Sender, subject, body and attachment digests; never transport headers.
        """
        digest = hashlib.sha256()
        for part in (self.from_address, self.subject, self.body_text):
            digest.update(part.encode("utf-8", "replace"))
            digest.update(b"\x00")
        for attachment in self.attachments:
            digest.update(attachment.sha256.encode("ascii"))
        return digest.hexdigest()

    @property
    def thread_key(self) -> str:
        """Identifier shared by every message of a conversation.

        The first entry of ``References`` is the root of the thread; a message that starts a
        thread is its own root.
        """
        if self.references:
            return self.references[0]
        return self.in_reply_to or self.message_id


class _TextFromHtml(HTMLParser):
    """Minimal HTML to text conversion for messages without a plain part."""

    _BLOCK = frozenset({"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"})
    _SKIP = frozenset({"script", "style", "head"})

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Strip tags from ``html`` keeping line structure."""
    parser = _TextFromHtml()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _ids(value: str) -> list[str]:
    return re.findall(r"<[^<>\s]+>", value)


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _WS.sub("\n", text).strip()


def parse_mail(raw: bytes) -> ParsedMail:
    """Parse raw message bytes.

    Raises:
        MessageParseError: when the bytes carry no usable header at all.

    """
    message = message_from_bytes(raw, policy=policy.default)
    if not isinstance(message, EmailMessage) or not message.keys():
        raise MessageParseError("not an RFC 5322 message")

    from_name, from_address = parseaddr(str(message.get("From", "")))
    _, reply_to = parseaddr(str(message.get("Reply-To", "")))
    recipients = ", ".join(addr for _, addr in getaddresses([str(message.get("To", ""))]) if addr)

    sent_at: datetime | None = None
    if message.get("Date"):
        try:
            sent_at = parsedate_to_datetime(str(message["Date"]))
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            sent_at = None

    body = message.get_body(preferencelist=("plain", "html"))
    body_text = ""
    if body is not None:
        content = body.get_content()
        if isinstance(content, bytes):
            content = content.decode("utf-8", "replace")
        body_text = html_to_text(content) if body.get_content_subtype() == "html" else content

    attachments: list[ParsedAttachment] = []
    for index, part in enumerate(message.iter_attachments()):
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        attachments.append(
            ParsedAttachment(
                filename=part.get_filename() or f"attachment-{index + 1}",
                content_type=part.get_content_type(),
                data=payload,
            )
        )

    message_ids = _ids(str(message.get("Message-ID", "")))
    in_reply_to = _ids(str(message.get("In-Reply-To", "")))
    return ParsedMail(
        message_id=message_ids[0] if message_ids else "",
        from_address=from_address.lower(),
        from_name=from_name,
        reply_to=reply_to.lower(),
        to_address=recipients.lower(),
        subject=str(message.get("Subject", "")).strip(),
        sent_at=sent_at,
        in_reply_to=in_reply_to[0] if in_reply_to else "",
        references=_ids(str(message.get("References", ""))),
        body_text=_normalise(body_text),
        attachments=attachments,
    )
