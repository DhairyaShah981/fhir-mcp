"""Unit tests for the audit trail."""

from __future__ import annotations

import contextlib

from sqlalchemy import select

from fhir_mcp.audit import (
    AuditEvent,
    audited,
    record,
)
from fhir_mcp.audit import init as audit_init


async def test_record_writes_a_row() -> None:
    await audit_init()
    await record(tool="unit_test", args={"k": "v"})
    from fhir_mcp import audit as a

    async with a._session_factory() as s:  # type: ignore[union-attr]
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    assert any(r.tool == "unit_test" for r in rows)


async def test_audited_decorator_records_success_and_failure() -> None:
    @audited("decorated_ok")
    async def good() -> dict:
        return {"resource_refs": ["Patient/abc"]}

    @audited("decorated_fail")
    async def bad() -> dict:
        raise ValueError("boom")

    await good()
    with contextlib.suppress(ValueError):
        await bad()

    from fhir_mcp import audit as a

    async with a._session_factory() as s:  # type: ignore[union-attr]
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    by_tool = {r.tool: r for r in rows}
    assert by_tool["decorated_ok"].outcome == "ok"
    assert by_tool["decorated_ok"].resource_refs == ["Patient/abc"]
    assert by_tool["decorated_fail"].outcome == "error"
    assert "boom" in (by_tool["decorated_fail"].error or "")
