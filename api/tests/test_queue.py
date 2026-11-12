"""The PostgreSQL work queue."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from opsinbox import queue
from opsinbox.models import Job
from opsinbox.settings import Settings
from opsinbox.worker import run_one_job


def add(sessions: sessionmaker[Session], key: str) -> None:
    with sessions() as session:
        queue.enqueue(session, "demo", key, {"key": key})
        session.commit()


def test_enqueue_is_idempotent(sessions: sessionmaker[Session]) -> None:
    with sessions() as session:
        assert queue.enqueue(session, "demo", "k", {}) is True
        assert queue.enqueue(session, "demo", "k", {}) is False
        session.commit()
        assert queue.depth(session) == {"queued": 1}


def test_concurrent_workers_never_share_a_job(sessions: sessionmaker[Session]) -> None:
    for i in range(40):
        add(sessions, f"job-{i}")
    handled: list[str] = []

    def work(worker: int) -> None:
        handlers = {"demo": lambda payload: handled.append(payload["key"])}
        while run_one_job(sessions, handlers, f"w{worker}", Settings()):
            pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(8)))

    assert sorted(handled) == sorted(f"job-{i}" for i in range(40))
    with sessions() as session:
        assert queue.depth(session) == {"done": 40}


def test_failed_job_is_retried_then_given_up(sessions: sessionmaker[Session]) -> None:
    add(sessions, "bad")
    settings = Settings(job_max_attempts=2)

    def boom(_: dict[str, object]) -> None:
        raise RuntimeError("no luck")

    assert run_one_job(sessions, {"demo": boom}, "w", settings)
    with sessions() as session:
        job = session.query(Job).one()
        assert job.status == "queued" and job.attempts == 1 and "no luck" in (job.last_error or "")
        assert job.run_after > datetime.now(UTC)  # backoff
        session.execute(update(Job).values(run_after=datetime.now(UTC) - timedelta(seconds=1)))
        session.commit()
    assert run_one_job(sessions, {"demo": boom}, "w", settings)
    with sessions() as session:
        assert session.query(Job).one().status == "failed"


def test_job_of_a_dead_worker_is_taken_over(sessions: sessionmaker[Session]) -> None:
    add(sessions, "orphan")
    with sessions() as session:
        assert queue.claim(session, "dead-worker", 300, ["demo"]) is not None
    with sessions() as session:
        assert queue.claim(session, "w2", 300, ["demo"]) is None  # still locked
        session.execute(update(Job).values(locked_at=datetime.now(UTC) - timedelta(seconds=301)))
        session.commit()
        job = queue.claim(session, "w2", 300, ["demo"])
        assert job is not None and job.locked_by == "w2" and job.attempts == 2
