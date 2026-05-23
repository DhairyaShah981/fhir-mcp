"""Tests for the SMART-token authorization path on reidentify()."""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from fhir_mcp import config as config_mod
from fhir_mcp.audit import AuditEvent
from fhir_mcp.audit import init as audit_init
from fhir_mcp.auth.scopes import parse_scopes
from fhir_mcp.auth.smart import SmartToken
from fhir_mcp.deid.pipeline import deidentify_resource
from fhir_mcp.deid.reid import ReidUnauthorizedError, reidentify


def _token(scope: str = "patient/*.read fhir-mcp/reid", expires_in: int = 600) -> SmartToken:
    return SmartToken(
        access_token="dummy",
        token_type="Bearer",
        expires_at=time.time() + expires_in,
        refresh_token=None,
        scopes=parse_scopes(scope),
        patient="Patient/smart-actor-1",
        raw={"client_id": "test-client"},
    )


async def _seed_pseudo() -> str:
    deid = await deidentify_resource(
        {
            "resourceType": "Patient",
            "id": "smart-reid-1",
            "name": [{"family": "Smartfamily", "given": ["Bob"]}],
        }
    )
    return deid["name"][0]["family"]


async def test_smart_token_with_reid_scope_authorizes(monkeypatch) -> None:
    monkeypatch.setenv("FHIR_MCP_ENABLE_REID", "true")
    monkeypatch.delenv("FHIR_MCP_REID_KEY", raising=False)
    config_mod.reset_settings_for_tests()
    pseudo = await _seed_pseudo()
    original = await reidentify(pseudo, smart_token=_token())
    assert original == "Smartfamily"

    await audit_init()
    from fhir_mcp import audit as audit_mod

    async with audit_mod._session_factory() as s:  # type: ignore[union-attr]
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    smart_rows = [r for r in rows if r.tool == "reid" and r.outcome == "ok"]
    assert smart_rows
    assert smart_rows[-1].actor == "Patient/smart-actor-1"


async def test_smart_token_without_reid_scope_denied(monkeypatch) -> None:
    monkeypatch.setenv("FHIR_MCP_ENABLE_REID", "true")
    monkeypatch.delenv("FHIR_MCP_REID_KEY", raising=False)
    config_mod.reset_settings_for_tests()
    pseudo = await _seed_pseudo()
    with pytest.raises(ReidUnauthorizedError):
        await reidentify(pseudo, smart_token=_token(scope="patient/*.read"))


async def test_expired_smart_token_denied(monkeypatch) -> None:
    monkeypatch.setenv("FHIR_MCP_ENABLE_REID", "true")
    monkeypatch.delenv("FHIR_MCP_REID_KEY", raising=False)
    config_mod.reset_settings_for_tests()
    pseudo = await _seed_pseudo()
    expired = _token(expires_in=-10)
    with pytest.raises(ReidUnauthorizedError):
        await reidentify(pseudo, smart_token=expired)


async def test_key_path_still_works(monkeypatch) -> None:
    """Regression: the old key-only path must keep working."""
    monkeypatch.setenv("FHIR_MCP_ENABLE_REID", "true")
    monkeypatch.setenv("FHIR_MCP_REID_KEY", "valid-key")
    config_mod.reset_settings_for_tests()
    pseudo = await _seed_pseudo()
    original = await reidentify(pseudo, key="valid-key")
    assert original == "Smartfamily"


async def test_both_paths_denied_without_either(monkeypatch) -> None:
    monkeypatch.setenv("FHIR_MCP_ENABLE_REID", "true")
    monkeypatch.delenv("FHIR_MCP_REID_KEY", raising=False)
    config_mod.reset_settings_for_tests()
    pseudo = await _seed_pseudo()
    with pytest.raises(ReidUnauthorizedError):
        await reidentify(pseudo)  # no key, no token
