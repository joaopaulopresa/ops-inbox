"""Metrics (Prometheus) and traces (Langfuse): one trace per e-mail, one span per step.

Tracing is off unless Langfuse keys are configured; metrics are always collected. Traces carry
e-mail content to the Langfuse server, which in this project runs in the same Compose stack.
"""

import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from prometheus_client import Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import text

from opsinbox.llm import ModelClient, ModelRequest, ModelResponse
from opsinbox.settings import get_settings

logger = logging.getLogger(__name__)

STEP_SECONDS = Histogram(
    "opsinbox_step_seconds",
    "Time spent in a workflow step",
    ["step"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 80),
)
STEP_FAILURES = Counter("opsinbox_step_failures_total", "Workflow steps that raised", ["step"])
MODEL_CALLS = Counter(
    "opsinbox_model_calls_total", "Model requests by step and outcome", ["step", "outcome"]
)
MODEL_SECONDS = Histogram(
    "opsinbox_model_seconds",
    "Latency of model calls that reached the provider",
    ["step"],
    buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64),
)
MODEL_COST = Counter("opsinbox_model_cost_usd_total", "Model spend reported by the provider")
CASES_DECIDED = Counter(
    "opsinbox_cases_decided_total", "Cases routed by the decision step", ["route", "category"]
)
JOBS = Counter("opsinbox_jobs_total", "Jobs run by the worker", ["kind", "outcome"])


class DatabaseCollector(Collector):
    """Gauges read from PostgreSQL at scrape time: queue depth and the review backlog."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        """Yield the gauges; an unreachable database yields nothing rather than failing."""
        from opsinbox.db import get_engine

        try:
            with get_engine().connect() as connection:
                jobs = connection.execute(
                    text("SELECT kind, status, count(*) FROM jobs GROUP BY kind, status")
                ).all()
                cases = connection.execute(
                    text("SELECT status, count(*) FROM cases GROUP BY status")
                ).all()
                oldest = connection.execute(
                    text(
                        "SELECT extract(epoch FROM now() - min(created_at)) FROM cases "
                        "WHERE status = 'review'"
                    )
                ).scalar()
                routes = connection.execute(
                    text("SELECT coalesce(route, 'none'), count(*) FROM cases GROUP BY route")
                ).all()
        except Exception:
            logger.warning("metrics: database not reachable")
            return
        queue = GaugeMetricFamily(
            "opsinbox_queue_jobs", "Jobs by kind and status", labels=["kind", "status"]
        )
        for kind, status, count in jobs:
            queue.add_metric([kind, status], count)
        yield queue
        backlog = GaugeMetricFamily("opsinbox_cases", "Cases by status", labels=["status"])
        for status, count in cases:
            backlog.add_metric([status], count)
        yield backlog
        routed = GaugeMetricFamily("opsinbox_cases_by_route", "Cases by route", labels=["route"])
        for route, count in routes:
            routed.add_metric([route], count)
        yield routed
        age = GaugeMetricFamily(
            "opsinbox_review_oldest_seconds", "Age of the oldest case waiting for review"
        )
        age.add_metric([], float(oldest or 0))
        yield age


class Tracer:
    """Langfuse tracing, or nothing when it is not configured."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client: Any = None
        if settings.langfuse_public_key and settings.langfuse_secret_key:
            try:
                from langfuse import Langfuse

                self._client = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    base_url=settings.langfuse_host,
                    environment="demo",
                )
            except Exception as exc:
                logger.warning("Langfuse disabled: %s", exc)

    @property
    def enabled(self) -> bool:
        """Whether traces are sent."""
        return self._client is not None

    @contextmanager
    def step(self, case_id: str, name: str, **metadata: Any) -> Iterator[None]:
        """Time a workflow step and, when tracing, record it as a span of the case's trace."""
        started = time.monotonic()
        try:
            if self._client is None:
                yield
            else:
                trace_id = uuid.UUID(case_id).hex
                with self._client.start_as_current_observation(
                    name=name,
                    as_type="chain",
                    trace_context={"trace_id": trace_id},
                    metadata={"case_id": case_id, **metadata},
                ):
                    yield
        except Exception as exc:
            if type(exc).__name__ != "GraphInterrupt":  # a pause for review is not a failure
                STEP_FAILURES.labels(step=name).inc()
            raise
        finally:
            STEP_SECONDS.labels(step=name).observe(time.monotonic() - started)

    def generation(self, request: ModelRequest) -> Any:
        """A generation observation in the current trace, or None."""
        if self._client is None:
            return None
        return self._client.start_as_current_observation(
            name=request.tag or request.schema_name or "completion",
            as_type="generation",
            model=request.model,
            input=[
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user[:8000]},
            ],
            metadata={"schema": request.schema_name, "images": len(request.images)},
        )

    def flush(self) -> None:
        """Send buffered traces."""
        if self._client is not None:
            self._client.flush()


@lru_cache
def get_tracer() -> Tracer:
    """The process-wide tracer."""
    return Tracer()


class InstrumentedClient:
    """Counts, times and traces every model request of the inner client."""

    def __init__(self, inner: ModelClient, tracer: Tracer) -> None:
        self._inner = inner
        self._tracer = tracer

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Forward the request and record what happened."""
        step = (request.tag or request.schema_name or "text").split(":")[0]
        observation = self._tracer.generation(request)
        if observation is None:
            return self._record(step, request)
        with observation as generation:
            response = self._record(step, request)
            generation.update(
                output=response.text[:8000],
                usage_details={
                    "input": response.prompt_tokens,
                    "output": response.completion_tokens,
                },
                cost_details={"total": response.cost_usd},
                metadata={"replayed": response.replayed},
            )
            return response

    def _record(self, step: str, request: ModelRequest) -> ModelResponse:
        try:
            response = self._inner.complete(request)
        except Exception:
            MODEL_CALLS.labels(step=step, outcome="error").inc()
            raise
        if response.replayed:
            MODEL_CALLS.labels(step=step, outcome="replayed").inc()
        else:
            MODEL_CALLS.labels(step=step, outcome="called").inc()
            MODEL_SECONDS.labels(step=step).observe(response.latency_seconds)
            MODEL_COST.inc(response.cost_usd)
        return response
