"""Unit tests for create_clinical_note."""

from __future__ import annotations

import base64

from fhir_mcp.tools.create_clinical_note import (
    CreateClinicalNoteInput,
    create_clinical_note,
)


async def test_soap_template_default() -> None:
    out = await create_clinical_note(
        CreateClinicalNoteInput(
            patient_pseudonym="patient-diabetic-60yo",
            free_text="Patient feels tired; reports glucose readings ~180.",
        )
    )
    assert out.template == "soap"
    assert "SUBJECTIVE" in out.structured_text
    assert "OBJECTIVE" in out.structured_text
    assert "ASSESSMENT" in out.structured_text
    assert "PLAN" in out.structured_text
    assert out.persisted is False
    # DocumentReference must include base64-encoded structured text.
    attachment = out.document_reference["content"][0]["attachment"]
    decoded = base64.b64decode(attachment["data"]).decode()
    assert "SUBJECTIVE" in decoded


async def test_discharge_template() -> None:
    out = await create_clinical_note(
        CreateClinicalNoteInput(
            patient_pseudonym="p1",
            free_text="3-day admission for CHF exacerbation, diuresed 4L.",
            template="discharge_summary",
        )
    )
    assert "DISCHARGE SUMMARY" in out.structured_text
    assert "HOSPITAL COURSE" in out.structured_text


async def test_prior_auth_template() -> None:
    out = await create_clinical_note(
        CreateClinicalNoteInput(
            patient_pseudonym="p1",
            free_text="Patient failed metformin + GLP-1; requesting SGLT2.",
            template="prior_auth",
        )
    )
    assert "PRIOR AUTHORIZATION" in out.structured_text
    assert "CLINICAL JUSTIFICATION" in out.structured_text


async def test_encounter_id_attaches_context() -> None:
    out = await create_clinical_note(
        CreateClinicalNoteInput(
            patient_pseudonym="p1", free_text="x", encounter_id="enc-123"
        )
    )
    assert out.document_reference["context"]["encounter"][0]["reference"].endswith("enc-123")
