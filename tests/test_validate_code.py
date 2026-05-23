"""Unit tests for validate_code."""

from __future__ import annotations

from fhir_mcp.tools.validate_code import ValidateCodeInput, validate_code


async def test_valid_known_loinc() -> None:
    r = await validate_code(ValidateCodeInput(code="4548-4", system="loinc"))
    assert r.valid is True
    assert "Hemoglobin A1c" in (r.display or "")
    assert "HbA1c" in r.alternatives


async def test_invalid_code() -> None:
    r = await validate_code(ValidateCodeInput(code="zzz", system="loinc"))
    assert r.valid is False
    assert r.display is None


async def test_invalid_system_name() -> None:
    r = await validate_code(ValidateCodeInput(code="123", system="invented"))
    assert r.valid is False


async def test_uri_form_accepted() -> None:
    r = await validate_code(
        ValidateCodeInput(code="44054006", system="http://snomed.info/sct")
    )
    assert r.valid is True
    assert r.system == "snomed"
