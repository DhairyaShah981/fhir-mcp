"""Clinical-accuracy evals — judge-LLM scoring against golden Synthea bundles.

These tests are skipped unless ``ANTHROPIC_API_KEY`` is set. The lightweight
structural assertions still run without a judge so CI on PRs from forks isn't
broken.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fhir_mcp.tools.get_patient_summary import (
    GetPatientSummaryInput,
    get_patient_summary,
)

# ---------- structural assertions (always run) -----------------------------


@pytest.mark.eval
async def test_primary_diagnosis_structural() -> None:
    """Without a judge LLM, assert the structured output names T2DM as primary."""
    result = await get_patient_summary(
        GetPatientSummaryInput(patient_pseudonym="patient-diabetic-60yo")
    )
    assert result.primary_diagnosis is not None, "primary_diagnosis must be set"
    text_blob = " ".join(
        filter(
            None,
            [
                result.primary_diagnosis.text,
                result.primary_diagnosis.display,
                result.primary_diagnosis.code,
            ],
        )
    ).lower()
    assert any(t in text_blob for t in ("diabetes", "44054006", "e11")), (
        f"primary_diagnosis did not surface T2DM: {result.primary_diagnosis}"
    )

    # Metformin must be on the active medication list.
    med_blob = " ".join(
        (m.code.text or "") + " " + (m.code.display or "") + " " + (m.code.code or "")
        for m in result.active_medications
    ).lower()
    assert "metformin" in med_blob or "860975" in med_blob, "metformin missing from med list"

    # HbA1c must appear in recent observations.
    obs_blob = " ".join(
        (o.code.text or "") + " " + (o.code.display or "") + " " + (o.code.code or "")
        for o in result.recent_observations
    ).lower()
    assert "hba1c" in obs_blob or "hemoglobin" in obs_blob or "4548-4" in obs_blob, (
        "HbA1c missing from recent observations"
    )


@pytest.mark.eval
async def test_no_phi_in_patient_summary() -> None:
    """The structured summary must contain zero PHI tokens from the source bundle."""
    from fhir_mcp.deid.pipeline import scan_for_phi_leaks

    result = await get_patient_summary(
        GetPatientSummaryInput(patient_pseudonym="patient-diabetic-60yo")
    )
    payload = result.model_dump(mode="json")
    known_phi = [
        "Jonathan",
        "Smith",
        "MRN-12345678",
        "555-12-3456",
        "555) 412-9988",
        "jsmith@example.com",
        "Springfield",
        "Evergreen Terrace",
        "62704",
        "1964-08-12",
    ]
    leaks = scan_for_phi_leaks(payload, known_phi)
    assert leaks == [], f"PHI leaked into summary: {leaks}"


# ---------- judge-LLM scoring (optional) -----------------------------------


def _has_judge_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.mark.eval
@pytest.mark.skipif(not _has_judge_key(), reason="ANTHROPIC_API_KEY not set; skipping judge LLM")
async def test_primary_diagnosis_judge(judge_dir: Path, judge_model: str) -> None:
    """Judge-LLM scores the structured summary against the rubric."""
    import anthropic  # type: ignore

    result = await get_patient_summary(
        GetPatientSummaryInput(patient_pseudonym="patient-diabetic-60yo")
    )
    summary_json = json.dumps(result.model_dump(mode="json"), indent=2)
    rubric = (judge_dir / "primary_diagnosis.md").read_text()

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=judge_model,
        max_tokens=600,
        system="You are a clinical-informatics judge. Return only the JSON object specified.",
        messages=[
            {
                "role": "user",
                "content": (
                    rubric
                    + "\n\n---\n\n## Structured summary to judge\n```json\n"
                    + summary_json
                    + "\n```"
                ),
            }
        ],
    )
    raw = message.content[0].text  # type: ignore[union-attr]
    # Tolerate fenced or unfenced JSON.
    raw = raw.strip().removeprefix("```json").removeprefix("```").rstrip("`")
    verdict = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
    score = int(verdict.get("score", 0))
    assert score >= 8, f"Judge gave {score}/10: {verdict}"
    assert verdict.get("phi_leak_observed") is False
