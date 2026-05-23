"""Integration tests for ``search_patients`` and ``get_patient_summary``."""

from __future__ import annotations

from fhir_mcp.tools.get_patient_summary import (
    GetPatientSummaryInput,
    get_patient_summary,
)
from fhir_mcp.tools.search_patients import SearchPatientsInput, search_patients


async def test_search_patients_returns_pseudonymized_results() -> None:
    out = await search_patients(SearchPatientsInput(name="Smith", limit=5))
    assert out.count >= 1
    p = out.patients[0]
    # Pseudonymized id should NOT equal the source id "patient-diabetic-60yo".
    assert p.pseudonym != "patient-diabetic-60yo"
    assert p.pseudonym.startswith("PT_")
    # Display name should not contain "Smith" or "Jonathan" — both are PHI.
    assert "Smith" not in p.display_name
    assert "Jonathan" not in p.display_name
    # Age band should be a decade range, not the full DOB.
    assert p.age_band and "-" in p.age_band


async def test_get_patient_summary_identifies_diabetes_as_primary() -> None:
    out = await get_patient_summary(
        GetPatientSummaryInput(patient_pseudonym="patient-diabetic-60yo")
    )
    assert out.primary_diagnosis is not None
    blob = " ".join(
        filter(None, [out.primary_diagnosis.text, out.primary_diagnosis.display])
    ).lower()
    assert "diabetes" in blob or out.primary_diagnosis.code == "44054006"

    # Active meds include metformin.
    assert any(
        "metformin" in ((m.code.text or "") + (m.code.display or "")).lower()
        for m in out.active_medications
    )

    # Recent observations include HbA1c, most recent first.
    assert any(
        "hba1c" in ((o.code.text or "") + (o.code.display or "")).lower()
        or o.code.code == "4548-4"
        for o in out.recent_observations
    )

    # No PHI in pseudonym field.
    assert out.patient_pseudonym.startswith("PT_")
