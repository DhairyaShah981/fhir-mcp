"""Audit-row PHI safety — guards `@audited(phi_args=...)` even under verbose mode."""

from __future__ import annotations

from sqlalchemy import select

from fhir_mcp import config as config_mod
from fhir_mcp.audit import AuditEvent, audited
from fhir_mcp.audit import init as audit_init


async def test_phi_args_redacted_when_verbose_audit_enabled(monkeypatch) -> None:
    """`free_text` (PHI by definition) must not land in the audit DB even with verbose_audit=true."""
    monkeypatch.setenv("FHIR_MCP_VERBOSE_AUDIT", "true")
    config_mod.reset_settings_for_tests()

    @audited("phi_test_tool", phi_args=("free_text",))
    async def tool(payload: dict) -> dict:
        return {"ok": True}

    secret = "the-patient-said-they-feel-suicidal-this-is-PHI"
    await tool({"free_text": secret, "patient_id": "p1"})

    await audit_init()
    from fhir_mcp import audit as audit_mod

    async with audit_mod._session_factory() as s:  # type: ignore[union-attr]
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    target = next((r for r in rows if r.tool == "phi_test_tool"), None)
    assert target is not None
    assert target.args is not None, "verbose_audit was set; args should be populated"
    # The free_text value must be redacted — but the non-PHI fields must remain.
    serialized = str(target.args)
    assert secret not in serialized, "PHI leaked into audit DB"
    assert "<phi-redacted>" in serialized
    assert "p1" in serialized  # non-PHI fields still visible for debugging


async def test_phi_args_redacted_when_verbose_audit_disabled(monkeypatch) -> None:
    """When verbose_audit is off, args is None anyway — but no PHI should appear."""
    monkeypatch.delenv("FHIR_MCP_VERBOSE_AUDIT", raising=False)
    config_mod.reset_settings_for_tests()

    @audited("phi_test_tool_2", phi_args=("free_text",))
    async def tool(payload: dict) -> dict:
        return {"ok": True}

    secret = "another-PHI-string-that-must-never-be-logged"
    await tool({"free_text": secret})

    await audit_init()
    from fhir_mcp import audit as audit_mod

    async with audit_mod._session_factory() as s:  # type: ignore[union-attr]
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    target = next((r for r in rows if r.tool == "phi_test_tool_2"), None)
    assert target is not None
    assert target.args is None  # default mode
    # Belt and suspenders: serialized row must not contain the secret anywhere.
    full = str(target.__dict__)
    assert secret not in full


async def test_create_clinical_note_does_not_log_free_text(monkeypatch) -> None:
    """Integration: create_clinical_note's free_text is the canonical PHI carrier."""
    monkeypatch.setenv("FHIR_MCP_VERBOSE_AUDIT", "true")
    config_mod.reset_settings_for_tests()

    from fhir_mcp.tools.create_clinical_note import (
        CreateClinicalNoteInput,
        create_clinical_note,
    )

    secret = "Jane Doe MRN 99-88-77 presented with chest pain and depression"
    await create_clinical_note(
        CreateClinicalNoteInput(
            patient_pseudonym="patient-diabetic-60yo",
            free_text=secret,
        )
    )

    await audit_init()
    from fhir_mcp import audit as audit_mod

    async with audit_mod._session_factory() as s:  # type: ignore[union-attr]
        rows = (await s.execute(select(AuditEvent))).scalars().all()
    rows_for_tool = [r for r in rows if r.tool == "create_clinical_note"]
    assert rows_for_tool, "create_clinical_note audit row missing"
    for r in rows_for_tool:
        full = str(r.args)
        assert secret not in full, "free_text PHI leaked into audit DB"
