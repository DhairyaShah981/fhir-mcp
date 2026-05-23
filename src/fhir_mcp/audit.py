"""Audit trail — every MCP tool call writes a row here.

Default backend = SQLite. Supabase opt-in via env vars.
Rows are joinable to Langfuse traces via the ``trace_id`` column.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import JSON, Column, DateTime, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings
from .observability import current_trace_id

log = structlog.get_logger(__name__)


class _Base(DeclarativeBase):
    pass


class AuditEvent(_Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    actor = Column(String(128), nullable=False, default="local")
    tool = Column(String(64), nullable=False)
    args_hash = Column(String(64), nullable=False)
    args = Column(JSON, nullable=True)  # only populated when verbose_audit=true
    resource_refs = Column(JSON, nullable=True)  # list of FHIR refs touched
    trace_id = Column(String(64), nullable=False)
    outcome = Column(String(32), nullable=False, default="ok")
    error = Column(Text, nullable=True)


_engine: Any | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_initialized = False


async def init() -> None:
    """Create the engine + table on first use. Safe to call repeatedly."""
    global _engine, _session_factory, _initialized
    if _initialized:
        return
    settings = get_settings()
    _engine = create_async_engine(settings.resolved_audit_url(), future=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    _initialized = True


def _hash_args(args: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:32]


async def record(
    *,
    tool: str,
    args: dict[str, Any],
    resource_refs: list[str] | None = None,
    outcome: str = "ok",
    error: str | None = None,
    actor: str = "local",
) -> None:
    """Insert one audit row. Never raises — audit failures are logged, not fatal."""
    await init()
    assert _session_factory is not None
    settings = get_settings()
    row = AuditEvent(
        actor=actor,
        tool=tool,
        args_hash=_hash_args(args),
        args=args if settings.verbose_audit else None,
        resource_refs=resource_refs,
        trace_id=current_trace_id(),
        outcome=outcome,
        error=error,
    )
    try:
        async with _session_factory() as session:
            session.add(row)
            await session.commit()
    except Exception as exc:  # pragma: no cover — audit must not break tool flow
        log.error("audit_write_failed", tool=tool, error=str(exc))


def audited(tool_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator: wrap a tool handler so every invocation lands in the audit log."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            arg_dump = {"args": list(args[1:]) if args else [], "kwargs": kwargs}
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                await record(tool=tool_name, args=arg_dump, outcome="error", error=str(exc))
                raise
            refs: list[str] | None = None
            if isinstance(result, dict):
                refs = result.get("resource_refs")  # tools may opt in
            await record(tool=tool_name, args=arg_dump, resource_refs=refs)
            return result

        return wrapper

    return deco
