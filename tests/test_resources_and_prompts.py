"""Unit tests for MCP Resources and Prompts."""

from __future__ import annotations

import json

from fhir_mcp.prompts import discharge_summary, prior_auth_letter, soap_note
from fhir_mcp.resources import lab_trends, medications, patient_summary


async def test_patient_summary_resource_renders_markdown() -> None:
    md = await patient_summary.render("patient-diabetic-60yo")
    assert md.startswith("# Patient summary")
    assert "Primary diagnosis" in md
    assert "Active medications" in md
    assert "metformin" in md.lower() or "Metformin" in md
    assert "HbA1c" in md or "Hemoglobin" in md


async def test_lab_trends_resource_returns_valid_json() -> None:
    raw = await lab_trends.render("patient-diabetic-60yo", window_days=800)
    data = json.loads(raw)
    assert data["pseudonym"] == "patient-diabetic-60yo"
    assert data["window_days"] == 800
    assert "trends" in data
    assert "HbA1c" in data["trends"]
    assert data["trends"]["HbA1c"]["direction"] == "falling"


async def test_medications_resource_renders_with_interactions() -> None:
    md = await medications.render("patient-chf-warfarin-70yo")
    assert "Active medications" in md
    assert "Interaction flags" in md
    assert "bleeding" in md.lower()


async def test_medications_resource_empty_patient() -> None:
    # Use a pseudonym that won't resolve to any real patient -> empty list.
    md = await medications.render("nonexistent-patient-id")
    assert "No active medications" in md


def test_soap_prompt_includes_pseudonym() -> None:
    text = soap_note.render("PT_abc123", chief_complaint="cough", encounter_id="enc-1")
    assert soap_note.NAME == "soap_note"
    assert "PT_abc123" in text
    assert "cough" in text


def test_discharge_prompt_includes_admission_dx() -> None:
    text = discharge_summary.render("PT_abc", admission_diagnosis="CHF exacerbation")
    assert "CHF exacerbation" in text


def test_prior_auth_prompt_includes_intervention() -> None:
    text = prior_auth_letter.render(
        "PT_abc",
        requested_intervention="SGLT2 inhibitor (empagliflozin)",
        payer="Aetna",
        diagnosis="T2DM",
    )
    assert "SGLT2" in text
    assert "Aetna" in text
