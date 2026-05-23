"""Unit tests for run_cds_hook."""

from __future__ import annotations

from fhir_mcp.tools.run_cds_hook import RunCdsHookInput, run_cds_hook


async def test_patient_view_auto_prefetches() -> None:
    out = await run_cds_hook(
        RunCdsHookInput(hook="patient-view", patient_pseudonym="patient-chf-warfarin-70yo")
    )
    assert out.used_mock is True
    assert "patient-view" in out.available_hooks
    # warfarin + aspirin must fire on auto-prefetch.
    assert any("bleeding" in c.summary.lower() for c in out.cards)


async def test_medication_prescribe_with_draft_order() -> None:
    out = await run_cds_hook(
        RunCdsHookInput(
            hook="medication-prescribe",
            patient_pseudonym="patient-diabetic-60yo",
            context={
                "draftOrders": {
                    "entry": [
                        {
                            "resource": {
                                "resourceType": "MedicationRequest",
                                "medicationCodeableConcept": {"text": "Amoxicillin"},
                            }
                        }
                    ]
                }
            },
        )
    )
    assert any(c.severity == "critical" for c in out.cards)


async def test_order_select_with_statin() -> None:
    out = await run_cds_hook(
        RunCdsHookInput(
            hook="order-select",
            context={
                "draftOrders": {
                    "entry": [
                        {"resource": {"medicationCodeableConcept": {"text": "Atorvastatin 40mg"}}}
                    ]
                }
            },
        )
    )
    assert any("statin" in c.summary.lower() for c in out.cards)


async def test_hook_with_explicit_prefetch_skips_backend() -> None:
    out = await run_cds_hook(
        RunCdsHookInput(
            hook="medication-prescribe",
            prefetch={
                "medications": [
                    {"medicationCodeableConcept": {"text": "Warfarin"}},
                    {"medicationCodeableConcept": {"text": "Aspirin"}},
                ],
                "allergies": [],
            },
        )
    )
    assert any("bleeding" in c.summary.lower() for c in out.cards)


async def test_no_patient_no_prefetch_returns_empty() -> None:
    out = await run_cds_hook(RunCdsHookInput(hook="medication-prescribe"))
    assert out.cards == []
