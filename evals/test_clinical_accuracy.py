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

from fhir_mcp.tools.get_medications import GetMedicationsInput, get_medications
from fhir_mcp.tools.get_patient_summary import (
    GetPatientSummaryInput,
    get_patient_summary,
)
from fhir_mcp.tools.search_observations import (
    SearchObservationsInput,
    search_observations,
)

# ---------- structural assertions (always run) -----------------------------


@pytest.mark.eval
async def test_primary_diagnosis_structural_diabetic() -> None:
    """diabetic_60yo summary must name T2DM as primary, surface metformin and HbA1c."""
    result = await get_patient_summary(
        GetPatientSummaryInput(patient_pseudonym="patient-diabetic-60yo")
    )
    assert result.primary_diagnosis is not None
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
    assert any(t in text_blob for t in ("diabetes", "44054006", "e11"))

    med_blob = " ".join(
        (m.code.text or "") + " " + (m.code.display or "") + " " + (m.code.code or "")
        for m in result.active_medications
    ).lower()
    assert "metformin" in med_blob or "860975" in med_blob

    obs_blob = " ".join(
        (o.code.text or "") + " " + (o.code.display or "") + " " + (o.code.code or "")
        for o in result.recent_observations
    ).lower()
    assert "hba1c" in obs_blob or "hemoglobin" in obs_blob or "4548-4" in obs_blob


@pytest.mark.eval
async def test_primary_diagnosis_structural_chf() -> None:
    """chf_warfarin_70yo summary must surface HTN/CHF/AFib and warfarin."""
    result = await get_patient_summary(
        GetPatientSummaryInput(patient_pseudonym="patient-chf-warfarin-70yo")
    )
    cond_blob = " ".join(
        (c.code.text or "") + " " + (c.code.display or "") for c in result.conditions
    ).lower()
    for term in ("hypertension", "heart failure", "atrial fibrillation"):
        assert term in cond_blob, f"missing {term}: {cond_blob}"

    med_blob = " ".join(
        (m.code.text or "") + " " + (m.code.display or "") for m in result.active_medications
    ).lower()
    assert "warfarin" in med_blob and "aspirin" in med_blob


@pytest.mark.eval
async def test_primary_diagnosis_structural_pregnant() -> None:
    """pregnant_with_htn_28yo summary must surface pregnancy + HTN."""
    result = await get_patient_summary(
        GetPatientSummaryInput(patient_pseudonym="patient-pregnant-htn-28yo")
    )
    cond_blob = " ".join(
        (c.code.text or "") + " " + (c.code.display or "") for c in result.conditions
    ).lower()
    assert "pregnan" in cond_blob
    assert "hypertension" in cond_blob


@pytest.mark.eval
async def test_no_phi_in_patient_summary() -> None:
    """The diabetic summary must contain zero PHI tokens from the source bundle."""
    from fhir_mcp.deid.pipeline import scan_for_phi_leaks

    result = await get_patient_summary(
        GetPatientSummaryInput(patient_pseudonym="patient-diabetic-60yo")
    )
    known_phi = [
        "Jonathan", "Smith", "MRN-12345678", "555-12-3456",
        "555) 412-9988", "jsmith@example.com", "Springfield",
        "Evergreen Terrace", "62704", "1964-08-12",
    ]
    leaks = scan_for_phi_leaks(result.model_dump(mode="json"), known_phi)
    assert leaks == [], f"PHI leaked: {leaks}"


@pytest.mark.eval
async def test_no_phi_in_chf_summary() -> None:
    """The CHF patient's PHI must not leak."""
    from fhir_mcp.deid.pipeline import scan_for_phi_leaks

    result = await get_patient_summary(
        GetPatientSummaryInput(patient_pseudonym="patient-chf-warfarin-70yo")
    )
    known_phi = [
        "Margaret", "O'Reilly", "MRN-77001122", "(617) 555-2244",
        "Beacon Hill", "Boston", "02114", "1955-11-03",
    ]
    leaks = scan_for_phi_leaks(result.model_dump(mode="json"), known_phi)
    assert leaks == [], f"PHI leaked: {leaks}"


@pytest.mark.eval
async def test_hba1c_trend_falling_for_diabetic() -> None:
    """HbA1c trend must surface multiple readings and a falling slope."""
    result = await search_observations(
        SearchObservationsInput(
            patient_pseudonym="patient-diabetic-60yo",
            loinc="4548-4",
            trend_window_days=800,
        )
    )
    assert result.mode == "trend"
    assert result.trend is not None
    assert result.trend.count >= 2
    # 8.6 → 8.2 → 7.8 — slope must be negative.
    assert result.trend.slope_per_day is not None
    assert result.trend.slope_per_day < 0
    assert result.trend.direction == "falling"


@pytest.mark.eval
async def test_medications_with_interactions_chf_patient() -> None:
    """The CHF/warfarin patient must show a warfarin+aspirin interaction warning."""
    result = await get_medications(
        GetMedicationsInput(patient_pseudonym="patient-chf-warfarin-70yo")
    )
    assert any(
        "warfarin" in (m.text or m.display or "").lower() for m in result.medications
    )
    assert any(
        "bleeding" in c.summary.lower() for c in result.interactions
    ), f"expected bleeding warning, got: {result.interactions}"


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
    raw = raw.strip().removeprefix("```json").removeprefix("```").rstrip("`")
    verdict = json.loads(raw[raw.find("{") : raw.rfind("}") + 1])
    score = int(verdict.get("score", 0))
    assert score >= 8, f"Judge gave {score}/10: {verdict}"
    assert verdict.get("phi_leak_observed") is False
