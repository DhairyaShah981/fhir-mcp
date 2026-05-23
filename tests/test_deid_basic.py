"""Unit tests for the de-id pipeline + vault."""

from __future__ import annotations

from fhir_mcp.deid.pipeline import deidentify_resource, scan_for_phi_leaks
from fhir_mcp.deid.vault import get_vault

PATIENT = {
    "resourceType": "Patient",
    "id": "patient-1",
    "identifier": [{"system": "http://hospital/mrn", "value": "MRN-99887766"}],
    "name": [{"family": "Doe", "given": ["Jane", "Q"], "text": "Jane Q Doe"}],
    "telecom": [{"system": "phone", "value": "(415) 555-0142"}],
    "gender": "female",
    "birthDate": "1978-06-15",
    "address": [{"line": ["123 Market St"], "city": "San Francisco", "postalCode": "94103"}],
}


async def test_patient_phi_replaced_with_pseudonyms() -> None:
    deid = await deidentify_resource(PATIENT)
    leaks = scan_for_phi_leaks(
        deid,
        [
            "Doe",
            "Jane",
            "MRN-99887766",
            "(415) 555-0142",
            "1978-06-15",
            "123 Market St",
            "San Francisco",
            "94103",
        ],
    )
    assert leaks == []


async def test_pseudonyms_are_stable_per_input() -> None:
    a = await deidentify_resource(PATIENT)
    b = await deidentify_resource(PATIENT)
    assert a["id"] == b["id"]
    assert a["name"][0]["family"] == b["name"][0]["family"]


async def test_vault_reverse_lookup() -> None:
    deid = await deidentify_resource(PATIENT)
    family_pseudonym = deid["name"][0]["family"]
    original = await get_vault().lookup(family_pseudonym)
    assert original == "Doe"


async def test_reference_rewriting() -> None:
    obs = {
        "resourceType": "Observation",
        "id": "obs-1",
        "status": "final",
        "code": {"text": "HbA1c"},
        "subject": {"reference": "Patient/patient-1"},
        "valueQuantity": {"value": 7.8, "unit": "%"},
    }
    deid_patient = await deidentify_resource(PATIENT)
    deid_obs = await deidentify_resource(obs)
    assert deid_obs["subject"]["reference"] == f"Patient/{deid_patient['id']}"


async def test_freetext_phi_redacted() -> None:
    doc = {
        "resourceType": "DocumentReference",
        "id": "doc-1",
        "content": [
            {"attachment": {"title": "Note for patient Jane Doe, MRN: 9988-7766, DOB 1978-06-15"}}
        ],
        "note": [{"text": "Call patient at 415-555-0142 or jane@example.com"}],
    }
    deid = await deidentify_resource(doc)
    leaks = scan_for_phi_leaks(
        deid, ["415-555-0142", "jane@example.com", "1978-06-15"]
    )
    assert leaks == [], deid
