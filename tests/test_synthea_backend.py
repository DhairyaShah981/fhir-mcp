"""Unit tests for the Synthea in-memory backend."""

from __future__ import annotations

from fhir_mcp.backends.synthea import SyntheaBackend


async def test_loads_golden_bundle_and_indexes_resources() -> None:
    backend = SyntheaBackend()
    patients = await backend.search("Patient", {})
    assert len(patients) >= 1
    p = patients[0]
    assert p["resourceType"] == "Patient"

    # Conditions linked via Patient/<id> reference.
    conds = await backend.search("Condition", {"patient": p["id"]})
    assert any(
        any("diabetes" in (c.get("display") or "").lower() for c in cond["code"]["coding"])
        for cond in conds
    )

    # $everything returns the patient + all linked resources.
    everything = await backend.everything(p["id"])
    types = {r["resourceType"] for r in everything}
    assert {"Patient", "Condition", "MedicationStatement", "Observation"}.issubset(types)


async def test_search_by_name_finds_patient() -> None:
    backend = SyntheaBackend()
    matches = await backend.search("Patient", {"name": "Smith"})
    assert len(matches) >= 1


async def test_search_by_clinical_status_filters() -> None:
    backend = SyntheaBackend()
    active = await backend.search("Condition", {"clinical-status": "active"})
    assert len(active) >= 1
