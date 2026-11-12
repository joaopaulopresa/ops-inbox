"""Shared fixtures. Tests need a PostgreSQL server: the queue relies on SKIP LOCKED."""

import os
from collections.abc import Iterator
from email.message import EmailMessage

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from opsinbox.db import Base
from opsinbox.settings import get_settings
from opsinbox.storage import MemoryObjectStore

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _test_url() -> str:
    url = make_url(os.environ.get("TEST_DATABASE_URL") or get_settings().database_url)
    if os.environ.get("TEST_DATABASE_URL"):
        return url.render_as_string(hide_password=False)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def database_url() -> str:
    url = make_url(_test_url())
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        ).first()
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()
    config = Config(os.path.join(API_DIR, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(API_DIR, "migrations"))
    config.attributes["url"] = url.render_as_string(hide_password=False)
    command.upgrade(config, "head")
    return url.render_as_string(hide_password=False)


@pytest.fixture
def sessions(database_url: str) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(database_url, pool_size=20)
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


def make_mail(
    *,
    message_id: str = "<m1@customer.example>",
    sender: str = "Ada Quill <ada@customer.example>",
    subject: str = "Booking request",
    body: str = "Hello, I would like to hire the 1965 Aldermoor Sprite.",
    attachments: list[tuple[str, str, bytes]] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> bytes:
    """Build a raw message for tests."""
    message = EmailMessage()
    message["Message-ID"] = message_id
    message["From"] = sender
    message["To"] = "bookings@larkspur.example"
    message["Subject"] = subject
    message["Date"] = "Mon, 02 Nov 2026 09:30:00 +0000"
    for name, value in (extra_headers or {}).items():
        message[name] = value
    message.set_content(body)
    for filename, content_type, data in attachments or []:
        maintype, subtype = content_type.split("/")
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return message.as_bytes()
