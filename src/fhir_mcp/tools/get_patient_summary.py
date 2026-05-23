"""``get_patient_summary`` — composite clinical summary for a patient.

Calls Patient/$everything (Synthea backend) or four parallel searches (HAPI) and
assembles a structured summary: conditions, active medications, allergies,
recent observations. Output is de-identified.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from ..audit import audited
from ..deid.pipeline import deidentify_resource
from ..deid.vault import get_vault
from ..fhir_client import get_backend
from ..observability import traced


class GetPatientSummaryInput(BaseModel):
    patient_pseudonym: str = Field(
        description="Pseudonymized patient id returned from search_patients (e.g. PT_a1b2c3).",
    )
    max_observations: int = Field(default=10, ge=1, le=100)


class CodedConcept(BaseModel):
    code: str | None = None
    system: str | None = None
    display: str | None = None
    text: str | None = None


class ConditionEntry(BaseModel):
    code: CodedConcept
    clinical_status: str | None = None
    onset: str | None = None


class MedicationEntry(BaseModel):
    code: CodedConcept
    status: str | None = None
    dosage: str | None = None


class AllergyEntry(BaseModel):
    code: CodedConcept
    clinical_status: str | None = None
    recorded_date: str | None = None


class ObservationEntry(BaseModel):
    code: CodedConcept
    value: str | None = None
    unit: str | None = None
    effective: str | None = None


class GetPatientSummaryOutput(BaseModel):
    patient_pseudonym: str
    gender: str | None = None
    age_band: str | None = None
    primary_diagnosis: CodedConcept | None = Field(
        default=None,
        description="The earliest active confirmed condition — heuristically the primary Dx.",
    )
    conditions: list[ConditionEntry] = Field(default_factory=list)
    active_medications: list[MedicationEntry] = Field(default_factory=list)
    allergies: list[AllergyEntry] = Field(default_factory=list)
    recent_observations: list[ObservationEntry] = Field(default_factory=list)


def _first_coding(resource_code: dict[str, Any] | None) -> CodedConcept:
    if not isinstance(resource_code, dict):
        return CodedConcept()
    codings = resource_code.get("coding") or []
    c = codings[0] if codings else {}
    return CodedConcept(
        code=c.get("code"),
        system=c.get("system"),
        display=c.get("display"),
        text=resource_code.get("text") or c.get("display"),
    )


def _clinical_status(resource: dict[str, Any]) -> str | None:
    cs = resource.get("clinicalStatus")
    if not isinstance(cs, dict):
        return None
    codings = cs.get("coding") or []
    return codings[0].get("code") if codings else None


def _age_band_from_dob(dob: str | None) -> str | None:
    if not dob or len(dob) < 4:
        return None
    try:
        y = int(dob[:4])
    except ValueError:
        return None
    age = datetime.now(UTC).year - y
    floor = (age // 10) * 10
    return f"{floor}-{floor + 9}"


@traced("get_patient_summary")
@audited("get_patient_summary")
async def get_patient_summary(payload: GetPatientSummaryInput) -> GetPatientSummaryOutput:
    backend = get_backend()
    vault = get_vault()

    # Pseudonym → real patient id (only if the vault knows it; otherwise treat as a literal id).
    real_id = await vault.lookup(payload.patient_pseudonym) or payload.patient_pseudonym

    # Try Patient/$everything first (zero-cost on synthea backend).
    try:
        resources = await backend.everything(real_id)
    except Exception:
        resources = []

    if not resources:
        # Fall back to parallel searches.
        searches = await asyncio.gather(
            backend.read("Patient", real_id),
            backend.search("Condition", {"patient": real_id}),
            backend.search("MedicationStatement", {"patient": real_id}),
            backend.search("AllergyIntolerance", {"patient": real_id}),
            backend.search("Observation", {"patient": real_id, "_count": payload.max_observations}),
            return_exceptions=False,
        )
        patient_r = searches[0]
        resources = []
        if patient_r:
            resources.append(patient_r)
        for s in searches[1:]:
            resources.extend(s or [])

    # Bucket resources by type before de-id (we need raw DOB for age_band).
    patient: dict[str, Any] | None = None
    conditions: list[dict[str, Any]] = []
    meds: list[dict[str, Any]] = []
    allergies: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []

    for r in resources:
        rt = r.get("resourceType")
        if rt == "Patient":
            patient = r
        elif rt == "Condition":
            conditions.append(r)
        elif rt in ("MedicationStatement", "MedicationRequest"):
            meds.append(r)
        elif rt == "AllergyIntolerance":
            allergies.append(r)
        elif rt == "Observation":
            observations.append(r)

    gender = patient.get("gender") if patient else None
    age_band = _age_band_from_dob(patient.get("birthDate")) if patient else None

    # De-identify all resources before building the structured output.
    deid_conditions = [await deidentify_resource(c) for c in conditions]
    deid_meds = [await deidentify_resource(m) for m in meds]
    deid_allergies = [await deidentify_resource(a) for a in allergies]
    deid_obs = [await deidentify_resource(o) for o in observations]

    # Conditions
    cond_entries = [
        ConditionEntry(
            code=_first_coding(c.get("code")),
            clinical_status=_clinical_status(c),
            onset=c.get("onsetDateTime") or c.get("recordedDate"),
        )
        for c in deid_conditions
    ]
    # Primary diagnosis heuristic: earliest active confirmed condition.
    active_confirmed = [
        (c, _clinical_status(c) == "active") for c in deid_conditions
    ]
    primary: CodedConcept | None = None
    candidates = sorted(
        [c for c, is_active in active_confirmed if is_active],
        key=lambda r: r.get("onsetDateTime") or r.get("recordedDate") or "9999",
    )
    if candidates:
        primary = _first_coding(candidates[0].get("code"))

    # Medications
    med_entries: list[MedicationEntry] = []
    for m in deid_meds:
        mc = m.get("medicationCodeableConcept") or m.get("medication") or {}
        dosage_list = m.get("dosage") or []
        dosage_text = dosage_list[0].get("text") if dosage_list and isinstance(dosage_list[0], dict) else None
        med_entries.append(
            MedicationEntry(
                code=_first_coding(mc),
                status=m.get("status"),
                dosage=dosage_text or (mc.get("text") if isinstance(mc, dict) else None),
            )
        )

    # Allergies
    allergy_entries = [
        AllergyEntry(
            code=_first_coding(a.get("code")),
            clinical_status=_clinical_status(a),
            recorded_date=a.get("recordedDate"),
        )
        for a in deid_allergies
    ]

    # Observations — most-recent-first, capped.
    obs_entries: list[ObservationEntry] = []
    deid_obs_sorted = sorted(
        deid_obs,
        key=lambda o: o.get("effectiveDateTime") or o.get("issued") or "",
        reverse=True,
    )
    for o in deid_obs_sorted[: payload.max_observations]:
        v = o.get("valueQuantity") or {}
        value = str(v.get("value")) if v.get("value") is not None else None
        unit = v.get("unit")
        obs_entries.append(
            ObservationEntry(
                code=_first_coding(o.get("code")),
                value=value,
                unit=unit,
                effective=o.get("effectiveDateTime"),
            )
        )

    # Stabilize pseudonym: the caller's input may be the pseudonym already.
    out_pseudo = await vault.pseudonymize("patient_ref", real_id)

    return GetPatientSummaryOutput(
        patient_pseudonym=out_pseudo,
        gender=gender,
        age_band=age_band,
        primary_diagnosis=primary,
        conditions=cond_entries,
        active_medications=med_entries,
        allergies=allergy_entries,
        recent_observations=obs_entries,
    )
