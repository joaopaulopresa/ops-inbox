"""Parsing raw messages."""

import pytest

from opsinbox.errors import MessageParseError
from opsinbox.mail import parse_mail
from tests.conftest import make_mail


def test_headers_body_and_attachments() -> None:
    parsed = parse_mail(
        make_mail(
            sender="Ada Quill <Ada@Customer.example>",
            attachments=[("licence.pdf", "application/pdf", b"%PDF-1.7")],
        )
    )
    assert parsed.from_address == "ada@customer.example"
    assert parsed.from_name == "Ada Quill"
    assert parsed.sent_at is not None and parsed.sent_at.year == 2026
    assert [a.filename for a in parsed.attachments] == ["licence.pdf"]
    assert parsed.thread_key == "<m1@customer.example>"


def test_reply_joins_the_thread_of_the_first_message() -> None:
    reply = parse_mail(
        make_mail(
            message_id="<m3@customer.example>",
            extra_headers={
                "In-Reply-To": "<m2@larkspur.example>",
                "References": "<m1@customer.example> <m2@larkspur.example>",
            },
        )
    )
    assert reply.thread_key == "<m1@customer.example>"


def test_content_hash_ignores_transport_headers_but_not_content() -> None:
    base = parse_mail(make_mail())
    relayed = parse_mail(make_mail(extra_headers={"Received": "from a by b", "X-Spam": "no"}))
    changed = parse_mail(make_mail(body="Something else"))
    assert base.content_hash == relayed.content_hash
    assert base.content_hash != changed.content_hash


def test_html_only_message_becomes_text() -> None:
    raw = (
        b"Message-ID: <h@x.example>\r\nFrom: a@x.example\r\nSubject: Hi\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><style>p{}</style><p>Pick-up <b>4 June</b></p><p>Thanks</p></html>"
    )
    assert parse_mail(raw).body_text == "Pick-up 4 June\nThanks"


def test_empty_bytes_are_rejected() -> None:
    with pytest.raises(MessageParseError):
        parse_mail(b"")
