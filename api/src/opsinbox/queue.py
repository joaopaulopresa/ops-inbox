"""PostgreSQL-backed work queue.

Jobs are claimed with ``FOR UPDATE SKIP LOCKED`` so several workers never take the same one.
Delivery is at-least-once (a worker can die after doing the work and before saying so); every job
handler is therefore idempotent, which makes the effect exactly-once.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from opsinbox.models import Job

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"


def enqueue(session: Session, kind: str, dedup_key: str, payload: dict[str, Any]) -> bool:
    """Add a job unless one with the same ``dedup_key`` exists. Return whether it was added."""
    statement = (
        insert(Job)
        .values(id=uuid.uuid4(), kind=kind, dedup_key=dedup_key, payload=payload)
        .on_conflict_do_nothing(index_elements=["dedup_key"])
        .returning(Job.id)
    )
    return session.execute(statement).first() is not None


def claim(
    session: Session, worker_id: str, lock_timeout_seconds: int, kinds: Sequence[str]
) -> Job | None:
    """Take the oldest ready job of one of ``kinds``, or one whose worker went silent.

    Commits the claim.
    """
    if not kinds:
        return None
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=lock_timeout_seconds)
    job = session.execute(
        select(Job)
        .where(
            Job.kind.in_(kinds),
            ((Job.status == QUEUED) & (Job.run_after <= now))
            | ((Job.status == RUNNING) & (Job.locked_at < stale)),
        )
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if job is None:
        session.rollback()
        return None
    job.status = RUNNING
    job.locked_by = worker_id
    job.locked_at = now
    job.attempts += 1
    session.commit()
    return job


def complete(session: Session, job_id: uuid.UUID) -> None:
    """Mark a job as done."""
    session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=DONE, finished_at=datetime.now(UTC), locked_by=None, last_error=None)
    )
    session.commit()


def fail(session: Session, job_id: uuid.UUID, error: str, max_attempts: int) -> None:
    """Record a failure: retry later with backoff, or give up after ``max_attempts``."""
    job = session.get(Job, job_id)
    if job is None:
        return
    job.last_error = error[:4000]
    job.locked_by = None
    if job.attempts >= max_attempts:
        job.status = FAILED
        job.finished_at = datetime.now(UTC)
    else:
        job.status = QUEUED
        job.run_after = datetime.now(UTC) + timedelta(seconds=min(300, 2**job.attempts))
    session.commit()


def depth(session: Session) -> dict[str, int]:
    """Return the number of jobs per status."""
    rows = session.execute(text("SELECT status, count(*) FROM jobs GROUP BY status")).all()
    return {str(status): int(count) for status, count in rows}
