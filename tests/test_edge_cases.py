"""Edge cases that close remaining coverage gaps."""

from __future__ import annotations

from fhir_mcp.backends.synthea import SyntheaBackend
from fhir_mcp.tools.get_patient_summary import (
    GetPatientSummaryInput,
    get_patient_summary,
)
from fhir_mcp.tools.search_patients import SearchPatientsInput, search_patients


async def test_synthea_search_by_family() -> None:
    backend = SyntheaBackend()
    out = await backend.search("Patient", {"family": "smith"})
    assert any(p["id"] == "patient-diabetic-60yo" for p in out)


async def test_synthea_search_by_given() -> None:
    backend = SyntheaBackend()
    out = await backend.search("Patient", {"given": "jonathan"})
    assert any(p["id"] == "patient-diabetic-60yo" for p in out)


async def test_synthea_search_by_identifier() -> None:
    backend = SyntheaBackend()
    out = await backend.search("Patient", {"identifier": "mrn-12345678"})
    assert any(p["id"] == "patient-diabetic-60yo" for p in out)


async def test_synthea_search_by_birthdate_and_gender() -> None:
    backend = SyntheaBackend()
    out = await backend.search("Patient", {"birthdate": "1964-08-12", "gender": "male"})
    assert len(out) == 1
    assert out[0]["id"] == "patient-diabetic-60yo"


async def test_synthea_search_by_code_text() -> None:
    backend = SyntheaBackend()
    out = await backend.search("Condition", {"code": "diabetes"})
    assert len(out) >= 1


async def test_synthea_search_by_date_range() -> None:
    backend = SyntheaBackend()
    out = await backend.search(
        "Observation",
        {"patient": "patient-diabetic-60yo", "date_ge": "2025-01-01", "date_le": "2025-12-31"},
    )
    # 2025-04-15 + 2025-10-04 readings expected.
    assert len(out) >= 2


async def test_synthea_skips_invalid_bundles(tmp_path) -> None:
    bad = tmp_path / "not_a_bundle.json"
    bad.write_text('{"resourceType": "Patient"}')
    backend = SyntheaBackend(bundle_paths=[bad])
    assert await backend.search("Patient", {}) == []


async def test_synthea_handles_unreadable_bundle(tmp_path) -> None:
    bad = tmp_path / "garbled.json"
    bad.write_text("{not-json")
    backend = SyntheaBackend(bundle_paths=[bad])
    assert await backend.search("Patient", {}) == []


async def test_search_patients_all_filter_combinations() -> None:
    out = await search_patients(
        SearchPatientsInput(
            name="Smith",
            family="Smith",
            given="Jonathan",
            mrn="MRN-12345678",
            birthdate="1964-08-12",
            gender="male",
            limit=5,
        )
    )
    assert out.count >= 1


async def test_get_patient_summary_falls_back_when_everything_fails(monkeypatch) -> None:
    """Force everything() to raise so the fallback search path runs."""
    from fhir_mcp import fhir_client as fc_mod

    real_backend = fc_mod.get_backend()

    class _FailingEverything:
        name = real_backend.name

        async def search(self, *args, **kwargs):
            return await real_backend.search(*args, **kwargs)

        async def read(self, *args, **kwargs):
            return await real_backend.read(*args, **kwargs)

        async def everything(self, *_a, **_kw):
            raise RuntimeError("simulated failure")

    monkeypatch.setattr(fc_mod, "_backend_cache", _FailingEverything())
    out = await get_patient_summary(
        GetPatientSummaryInput(patient_pseudonym="patient-diabetic-60yo")
    )
    assert out.primary_diagnosis is not None
