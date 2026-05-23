"""Unit tests for the Synthea in-memory backend."""

from __future__ import annotations

from fhir_mcp.backends.synthea import SyntheaBackend


async def test_loads_golden_bundle_and_indexes_resources() -> None:
    backend = SyntheaBackend()
    patients = await backend.search("Patient", {})
    assert len(patients) >= 1
    # All golden bundles must register a Patient resource.
    assert all(p["resourceType"] == "Patient" for p in patients)

    # Locate the diabetic patient by name and verify its conditions/refs.
    diabetic = next(
        (
            p
            for p in patients
            if any(
                "smith" in (n.get("family", "") or "").lower() for n in p.get("name", [])
            )
        ),
        None,
    )
    assert diabetic is not None, "diabetic_60yo bundle missing"
    conds = await backend.search("Condition", {"patient": diabetic["id"]})
    assert any(
        any("diabetes" in (c.get("display") or "").lower() for c in cond["code"]["coding"])
        for cond in conds
    )

    # $everything returns the patient + all linked resources.
    everything = await backend.everything(diabetic["id"])
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
