"""Worker process: polls the mailbox and runs queued jobs."""

import logging
import os
import signal
import socket
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prometheus_client import start_http_server
from sqlalchemy.orm import Session, sessionmaker

from opsinbox import queue
from opsinbox.db import get_sessionmaker
from opsinbox.errors import MailError
from opsinbox.ingest import poll_inbox
from opsinbox.mail.imap import ImapInbox
from opsinbox.mail.smtp import SmtpOutbox
from opsinbox.observability import JOBS, get_tracer
from opsinbox.records.business import load_business
from opsinbox.records.repository import seed_business
from opsinbox.settings import Settings, get_settings
from opsinbox.storage import ObjectStore, get_object_store
from opsinbox.workflow.service import (
    build_graph,
    build_handlers,
    build_runtime,
    checkpointer,
    model_client,
)

logger = logging.getLogger(__name__)

JobHandler = Callable[[dict[str, Any]], None]


def run_one_job(
    sessions: sessionmaker[Session], handlers: dict[str, JobHandler], worker_id: str, s: Settings
) -> bool:
    """Claim and run one job. Return False when the queue had nothing ready."""
    with sessions() as session:
        job = queue.claim(session, worker_id, s.job_lock_timeout_seconds, list(handlers))
        if job is None:
            return False
        job_id, kind, payload = job.id, job.kind, dict(job.payload)
    try:
        handler = handlers.get(kind)
        if handler is None:
            raise LookupError(f"no handler for job kind {kind!r}")
        handler(payload)
    except Exception as exc:
        logger.exception("job failed: id=%s kind=%s", job_id, kind)
        JOBS.labels(kind=kind, outcome="failed").inc()
        with sessions() as session:
            queue.fail(session, job_id, f"{type(exc).__name__}: {exc}", s.job_max_attempts)
    else:
        JOBS.labels(kind=kind, outcome="done").inc()
        with sessions() as session:
            queue.complete(session, job_id)
    finally:
        get_tracer().flush()
    return True


def main() -> None:
    """Run until SIGTERM: ingest mail, then drain the queue, then sleep."""
    s = get_settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
    sessions = get_sessionmaker()
    store = get_object_store()
    inbox = ImapInbox(
        s.imap_host, s.imap_port, s.imap_user, s.imap_password, s.imap_mailbox, s.imap_ssl
    )
    outbox = SmtpOutbox(s.smtp_host, s.smtp_port, s.smtp_user, s.smtp_password, s.smtp_starttls)
    business = load_business()
    client = model_client(s, Path(s.model_cache_dir))
    runtime = build_runtime(s, sessions, store, business, client)
    with sessions() as session:
        seed_business(session, business)
        session.commit()
    with checkpointer(s.database_url) as saver:
        graph = build_graph(runtime, saver)
        handlers = build_handlers(runtime, graph, outbox)
        _loop(s, sessions, store, inbox, handlers, worker_id)


def _loop(
    s: Settings,
    sessions: sessionmaker[Session],
    store: ObjectStore,
    inbox: ImapInbox,
    handlers: dict[str, JobHandler],
    worker_id: str,
) -> None:

    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    start_http_server(s.worker_metrics_port)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("worker started: id=%s", worker_id)

    next_poll = 0.0
    while not stopping:
        if time.monotonic() >= next_poll:
            try:
                poll_inbox(sessions, store, inbox)
            except MailError as exc:
                logger.warning("mailbox not readable, will retry: %s", exc)
            next_poll = time.monotonic() + s.ingest_poll_seconds
        if not run_one_job(sessions, handlers, worker_id, s):
            time.sleep(s.worker_poll_seconds)
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
