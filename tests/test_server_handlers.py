"""Exercise every registered FastMCP tool / resource / prompt handler.

These tests close the coverage gap on server.py by calling the inner ``.fn``
attribute of each registered Tool, exercising the same decorated paths the
real MCP client would hit over stdio.
"""

from __future__ import annotations

import pytest

from fhir_mcp.server import _build_app


@pytest.fixture()
def app():
    return _build_app()


@pytest.fixture()
def tools(app):
    return app._tool_manager._tools


@pytest.fixture()
def resources(app):
    # FastMCP keeps resource templates under _resource_manager._templates.
    return app._resource_manager._templates


@pytest.fixture()
def prompts(app):
    return app._prompt_manager._prompts


# ---- Tools ---------------------------------------------------------------


async def test_handler_search_patients(tools) -> None:
    out = await tools["search_patients"].fn(name="Smith", limit=5)
    assert out["count"] >= 1


async def test_handler_get_patient_summary(tools) -> None:
    out = await tools["get_patient_summary"].fn(patient_pseudonym="patient-diabetic-60yo")
    assert out["primary_diagnosis"] is not None


async def test_handler_search_observations_raw(tools) -> None:
    out = await tools["search_observations"].fn(
        patient_pseudonym="patient-diabetic-60yo", loinc="4548-4"
    )
    assert out["mode"] == "raw"


async def test_handler_search_observations_trend(tools) -> None:
    out = await tools["search_observations"].fn(
        patient_pseudonym="patient-diabetic-60yo",
        loinc="4548-4",
        trend_window_days=800,
    )
    assert out["mode"] == "trend"
    assert out["trend"]["direction"] in {"falling", "rising", "stable", "unknown"}


async def test_handler_search_conditions(tools) -> None:
    out = await tools["search_conditions"].fn(text="diabetes")
    assert out["count"] >= 1


async def test_handler_get_medications(tools) -> None:
    out = await tools["get_medications"].fn(
        patient_pseudonym="patient-chf-warfarin-70yo"
    )
    assert out["count"] >= 1
    assert any("bleeding" in c["summary"].lower() for c in out["interactions"])


async def test_handler_validate_code(tools) -> None:
    out = await tools["validate_code"].fn(code="44054006", system="snomed")
    assert out["valid"] is True


async def test_handler_create_clinical_note(tools) -> None:
    out = await tools["create_clinical_note"].fn(
        patient_pseudonym="patient-diabetic-60yo",
        free_text="follow-up visit",
        template="soap",
    )
    assert "structured_text" in out
    assert "SUBJECTIVE" in out["structured_text"]


async def test_handler_run_cds_hook(tools) -> None:
    out = await tools["run_cds_hook"].fn(
        hook="patient-view", patient_pseudonym="patient-chf-warfarin-70yo"
    )
    assert out["used_mock"] is True


# ---- Resources -----------------------------------------------------------


async def test_resource_patient_summary(resources) -> None:
    tmpl = next(t for t in resources.values() if t.name == "patient_summary")
    md = await tmpl.fn(pseudonym="patient-diabetic-60yo")
    assert "Patient summary" in md


async def test_resource_lab_trends(resources) -> None:
    tmpl = next(t for t in resources.values() if t.name == "lab_trends")
    raw = await tmpl.fn(pseudonym="patient-diabetic-60yo")
    assert "HbA1c" in raw


async def test_resource_medications(resources) -> None:
    tmpl = next(t for t in resources.values() if t.name == "medications")
    md = await tmpl.fn(pseudonym="patient-chf-warfarin-70yo")
    assert "warfarin" in md.lower()


# ---- Prompts -------------------------------------------------------------


def test_prompt_soap_note(prompts) -> None:
    prompt = prompts["soap_note"]
    rendered = prompt.fn(patient_pseudonym="PT_test", chief_complaint="cough")
    assert "cough" in rendered


def test_prompt_discharge_summary(prompts) -> None:
    prompt = prompts["discharge_summary"]
    rendered = prompt.fn(patient_pseudonym="PT_test", admission_diagnosis="pneumonia")
    assert "pneumonia" in rendered


def test_prompt_prior_auth(prompts) -> None:
    prompt = prompts["prior_auth_letter"]
    rendered = prompt.fn(patient_pseudonym="PT_test", requested_intervention="MRI brain")
    assert "MRI brain" in rendered
