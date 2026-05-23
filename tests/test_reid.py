"""Unit tests for the privileged re-identification path."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from fhir_mcp import config as config_mod
from fhir_mcp.audit import AuditEvent
from fhir_mcp.audit import init as audit_init
from fhir_mcp.deid.pipeline import deidentify_resource
from fhir_mcp.deid.reid import (
    ReidDisabledError,
    ReidNotFoundError,
    ReidUnauthorizedError,
    reidentify,
)


async def _seed_pseudonym() -> str:
    deid = await deidentify_resource(
        {
            "resourceType": "Patient",
            "id": "reid-test-1",
            "name": [{"family": "Reidsmith", "given": ["Alex"]}],
        }
    )
    return deid["name"][0]["family"]


async def test_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("FHIR_MCP_ENABLE_REID", raising=False)
    config_mod.reset_settings_for_tests()
    p = await _seed_pseudonym()
    with pytest.raises(ReidDisabledError):
        await reidentify(p, key="anything")


async def test_unauthorized_when_key_mismatched(monkeypatch) -> None:
    monkeypatch.setenv("FHIR_MCP_ENABLE_REID", "true")
    monkeypatch.setenv("FHIR_MCP_REID_KEY", "correct-key")
    config_mod.reset_settings_for_tests()
    p = await _seed_pseudonym()
    with pytest.raises(ReidUnauthorizedError):
        await reidentify(p, key="wrong-key", reason="test")
    # An audit row with outcome=unauthorized must have been written.
    await audit_init()
    from fhir_mcp import audit as audit_mod

    async with audit_mod._session_factory() as s:  # type: ignore[union-attr]
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    assert any(r.tool == "reid" and r.outcome == "unauthorized" for r in rows)


async def test_not_found(monkeypatch) -> None:
    monkeypatch.setenv("FHIR_MCP_ENABLE_REID", "true")
    monkeypatch.setenv("FHIR_MCP_REID_KEY", "k")
    config_mod.reset_settings_for_tests()
    with pytest.raises(ReidNotFoundError):
        await reidentify("FM_nonexistent_pseudo", key="k")


async def test_success_writes_audit(monkeypatch) -> None:
    monkeypatch.setenv("FHIR_MCP_ENABLE_REID", "true")
    monkeypatch.setenv("FHIR_MCP_REID_KEY", "k")
    config_mod.reset_settings_for_tests()
    p = await _seed_pseudonym()
    original = await reidentify(p, key="k", reason="unit-test")
    assert original == "Reidsmith"

    await audit_init()
    from fhir_mcp import audit as audit_mod

    async with audit_mod._session_factory() as s:  # type: ignore[union-attr]
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    assert any(
        r.tool == "reid" and r.outcome == "ok" and r.resource_refs == [p] for r in rows
    )
