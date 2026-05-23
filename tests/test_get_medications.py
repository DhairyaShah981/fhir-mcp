"""Unit tests for get_medications + CDS interaction integration."""

from __future__ import annotations

from fhir_mcp.tools.get_medications import GetMedicationsInput, get_medications


async def test_active_medications_for_diabetic() -> None:
    out = await get_medications(
        GetMedicationsInput(patient_pseudonym="patient-diabetic-60yo")
    )
    assert out.count >= 3
    names = " ".join(
        (m.text or "") + " " + (m.display or "") for m in out.medications
    ).lower()
    for drug in ("metformin", "lisinopril", "atorvastatin"):
        assert drug in names


async def test_interactions_off_skips_cds_call() -> None:
    out = await get_medications(
        GetMedicationsInput(
            patient_pseudonym="patient-chf-warfarin-70yo",
            include_interactions=False,
        )
    )
    assert out.interactions == []


async def test_interactions_on_fires_warfarin_aspirin() -> None:
    out = await get_medications(
        GetMedicationsInput(patient_pseudonym="patient-chf-warfarin-70yo")
    )
    summaries = " ".join(c.summary for c in out.interactions).lower()
    assert "bleeding" in summaries


async def test_status_filter_all_returns_everything() -> None:
    out = await get_medications(
        GetMedicationsInput(patient_pseudonym="patient-diabetic-60yo", status="all")
    )
    # All meds in the fixture are active, so all/active should agree.
    assert out.count >= 3


async def test_status_filter_completed_returns_empty_for_fixture() -> None:
    out = await get_medications(
        GetMedicationsInput(patient_pseudonym="patient-diabetic-60yo", status="completed")
    )
    assert out.count == 0
