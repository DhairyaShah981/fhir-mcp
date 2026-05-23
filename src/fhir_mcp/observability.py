"""Langfuse tracer — no-op when keys are not configured.

Every tool entry point is wrapped so the audit log can link to the LLM trace
that initiated the call. When Langfuse is not configured we still mint a
synthetic trace id so audit-row joins stay consistent.
"""

from __future__ import annotations

import functools
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog

from .config import get_settings

log = structlog.get_logger(__name__)

_current_trace_id: ContextVar[str | None] = ContextVar("current_trace_id", default=None)


def current_trace_id() -> str:
    """Return the trace id for the active span, minting one if none exists."""
    tid = _current_trace_id.get()
    if tid is None:
        tid = f"tr_{uuid.uuid4().hex[:16]}"
        _current_trace_id.set(tid)
    return tid


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[str]:
    """Open a Langfuse span (or a no-op span) for ``name``.

    Yields the trace id, which is also written to the audit row by ``@audited``.
    """
    settings = get_settings()
    trace_id = f"tr_{uuid.uuid4().hex[:16]}"
    token = _current_trace_id.set(trace_id)

    if not settings.langfuse_enabled():
        try:
            yield trace_id
        finally:
            _current_trace_id.reset(token)
        return

    try:
        from langfuse import Langfuse  # type: ignore
    except ImportError:
        log.warning("langfuse_not_installed", hint="pip install fhir-mcp[cloud]")
        try:
            yield trace_id
        finally:
            _current_trace_id.reset(token)
        return

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    trace = client.trace(id=trace_id, name=name, metadata=attributes)
    try:
        yield trace_id
    except Exception as exc:
        trace.update(level="ERROR", status_message=str(exc))
        raise
    finally:
        _current_trace_id.reset(token)


def traced(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator wrapping a tool handler in an observability span."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with span(name):
                return await fn(*args, **kwargs)

        return wrapper

    return deco
