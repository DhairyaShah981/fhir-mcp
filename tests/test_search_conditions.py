"""Unit tests for the search_conditions tool."""

from __future__ import annotations

from fhir_mcp.tools.search_conditions import (
    SearchConditionsInput,
    search_conditions,
)


async def test_filter_by_snomed_code_returns_matches() -> None:
    out = await search_conditions(SearchConditionsInput(snomed="44054006"))
    assert out.count >= 1
    assert any(c.code == "44054006" for c in out.conditions)


async def test_filter_by_text_diabetes() -> None:
    out = await search_conditions(SearchConditionsInput(text="diabetes"))
    assert out.count >= 1
    assert all(
        "diabetes" in ((c.text or "") + (c.display or "")).lower()
        for c in out.conditions
    )


async def test_filter_by_clinical_status_active() -> None:
    out = await search_conditions(SearchConditionsInput(clinical_status="active"))
    assert out.count >= 1
    assert all((c.clinical_status or "active") == "active" for c in out.conditions)


async def test_filter_by_patient_pseudonym() -> None:
    out = await search_conditions(
        SearchConditionsInput(patient_pseudonym="patient-chf-warfarin-70yo")
    )
    assert out.count >= 1
    cond_texts = " ".join(
        (c.text or "") + " " + (c.display or "") for c in out.conditions
    ).lower()
    assert "heart failure" in cond_texts or "atrial fibrillation" in cond_texts


async def test_empty_filter_returns_all() -> None:
    out = await search_conditions(SearchConditionsInput())
    assert out.count >= 6  # diabetic (3) + chf (3) + pregnant (2)


async def test_text_filter_misses_returns_empty() -> None:
    out = await search_conditions(SearchConditionsInput(text="nonexistent-disease-name"))
    assert out.count == 0
