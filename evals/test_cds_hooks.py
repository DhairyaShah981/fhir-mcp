"""Eval suite: CDS Hooks correctness."""

from __future__ import annotations

import pytest

from fhir_mcp.tools.run_cds_hook import RunCdsHookInput, run_cds_hook


@pytest.mark.eval
async def test_drug_drug_warfarin_aspirin_fires() -> None:
    """A warfarin patient with aspirin on the active list must trigger a bleeding warning."""
    result = await run_cds_hook(
        RunCdsHookInput(hook="patient-view", patient_pseudonym="patient-chf-warfarin-70yo")
    )
    summaries = " ".join(c.summary for c in result.cards).lower()
    assert "bleeding" in summaries, f"expected bleeding warning, got: {result.cards}"
    assert any(c.severity in {"warning", "critical"} for c in result.cards)


@pytest.mark.eval
async def test_drug_allergy_amoxicillin_penicillin_critical() -> None:
    """Proposing amoxicillin for a penicillin-allergic patient must produce a critical card."""
    result = await run_cds_hook(
        RunCdsHookInput(
            hook="medication-prescribe",
            patient_pseudonym="patient-diabetic-60yo",  # has penicillin allergy
            context={
                "draftOrders": {
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "MedicationRequest",
                                "medicationCodeableConcept": {"text": "Amoxicillin 500mg"},
                            }
                        }
                    ]
                }
            },
        )
    )
    severities = [c.severity for c in result.cards]
    assert "critical" in severities, f"expected critical card, got: {result.cards}"
    assert any("penicillin" in c.summary.lower() for c in result.cards)


@pytest.mark.eval
async def test_patient_view_diabetic_no_cards_for_well_controlled() -> None:
    """The diabetic_60yo patient's HbA1c is 7.8 — below the 9.0 trigger; should be quiet."""
    result = await run_cds_hook(
        RunCdsHookInput(hook="patient-view", patient_pseudonym="patient-diabetic-60yo")
    )
    glycemic_cards = [c for c in result.cards if "glycemic" in c.summary.lower()]
    assert glycemic_cards == [], f"unexpected glycemic warning: {glycemic_cards}"


@pytest.mark.eval
async def test_unknown_hook_returns_empty_cards() -> None:
    result = await run_cds_hook(RunCdsHookInput(hook="nonexistent-hook"))
    assert result.cards == []
    # Discovery still surfaces the available hooks even when one is unknown.
    assert "patient-view" in result.available_hooks
    assert "medication-prescribe" in result.available_hooks
