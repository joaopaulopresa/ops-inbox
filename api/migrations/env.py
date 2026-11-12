"""Alembic environment: the URL comes from the application settings."""

from alembic import context
from sqlalchemy import create_engine

import opsinbox.models  # noqa: F401  (registers the tables)
from opsinbox.db import Base
from opsinbox.settings import get_settings

target_metadata = Base.metadata


def run_migrations() -> None:
    url = context.config.attributes.get("url") or get_settings().database_url
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations()
