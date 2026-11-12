"""FastAPI application."""

import logging
import uuid
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from opsinbox import __version__, queue
from opsinbox.db import get_session
from opsinbox.errors import ObjectNotFoundError
from opsinbox.models import Attachment, Message
from opsinbox.routes import cases
from opsinbox.settings import get_settings
from opsinbox.storage import ObjectStore, get_object_store

logging.basicConfig(
    level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

app = FastAPI(title="Ops Inbox", version=__version__)
app.include_router(cases.router)


class AttachmentOut(BaseModel):
    """An attachment as shown to the web app."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size: int


class MessageOut(BaseModel):
    """A stored message as shown to the web app."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    direction: str
    from_address: str
    from_name: str
    to_address: str
    subject: str
    body_text: str
    sent_at: datetime | None
    received_at: datetime
    thread_key: str
    attachments: list[AttachmentOut]


class Health(BaseModel):
    """Liveness and a few counters."""

    status: str
    version: str
    messages: int
    jobs: dict[str, int]


@app.get("/api/health")
def health(session: Session = Depends(get_session)) -> Health:
    """Check the database and report basic counters."""
    session.execute(text("SELECT 1"))
    count = session.execute(select(func.count()).select_from(Message)).scalar_one()
    return Health(status="ok", version=__version__, messages=count, jobs=queue.depth(session))


@app.get("/api/messages")
def list_messages(limit: int = 50, session: Session = Depends(get_session)) -> list[MessageOut]:
    """Return the most recent messages."""
    rows = session.execute(
        select(Message)
        .options(selectinload(Message.attachments))
        .order_by(Message.received_at.desc())
        .limit(min(limit, 200))
    ).scalars()
    return [MessageOut.model_validate(row) for row in rows]


@app.get("/api/attachments/{attachment_id}")
def download_attachment(
    attachment_id: uuid.UUID,
    session: Session = Depends(get_session),
    store: ObjectStore = Depends(get_object_store),
) -> Response:
    """Return the bytes of an attachment."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    try:
        data = store.get(attachment.storage_key)
    except ObjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="attachment file is missing") from exc
    return Response(
        content=data,
        media_type=attachment.content_type,
        headers={"Content-Disposition": f'inline; filename="{attachment.filename}"'},
    )
