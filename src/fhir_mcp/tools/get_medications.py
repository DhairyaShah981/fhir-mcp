"""``get_medications`` — active med list + drug-drug interaction flags via CDS Hooks."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..audit import audited
from ..cds_hooks.client import get_client as get_cds_client
from ..deid.pipeline import deidentify_resource
from ..deid.vault import get_vault
from ..fhir_client import get_backend
from ..observability import traced


class GetMedicationsInput(BaseModel):
    patient_pseudonym: str = Field(description="Pseudonymized patient id.")
    status: Literal["active", "completed", "stopped", "all"] = "active"
    include_interactions: bool = Field(
        default=True,
        description="When true, runs the medication-prescribe CDS hook to flag drug-drug interactions.",
    )


class MedicationRow(BaseModel):
    rxnorm: str | None
    display: str | None
    text: str | None
    status: str | None
    dosage: str | None
    started: str | None


class InteractionCard(BaseModel):
    severity: str
    summary: str
    detail: str


class GetMedicationsOutput(BaseModel):
    count: int
    medications: list[MedicationRow] = Field(default_factory=list)
    interactions: list[InteractionCard] = Field(default_factory=list)


def _med_row(deid_med: dict[str, Any]) -> MedicationRow:
    mc = deid_med.get("medicationCodeableConcept") or deid_med.get("medication") or {}
    coding_list = mc.get("coding") or [] if isinstance(mc, dict) else []
    coding = coding_list[0] if coding_list else {}
    dosage_list = deid_med.get("dosage") or []
    dosage_text = (
        dosage_list[0].get("text")
        if dosage_list and isinstance(dosage_list[0], dict)
        else None
    )
    return MedicationRow(
        rxnorm=coding.get("code"),
        display=coding.get("display"),
        text=mc.get("text") if isinstance(mc, dict) else None,
        status=deid_med.get("status"),
        dosage=dosage_text or (mc.get("text") if isinstance(mc, dict) else None),
        started=deid_med.get("effectiveDateTime"),
    )


@traced("get_medications")
@audited("get_medications")
async def get_medications(payload: GetMedicationsInput) -> GetMedicationsOutput:
    backend = get_backend()
    vault = get_vault()
    real_id = await vault.lookup(payload.patient_pseudonym) or payload.patient_pseudonym

    meds_raw = await backend.search("MedicationStatement", {"patient": real_id, "_count": 100})
    if not meds_raw:
        meds_raw = await backend.search("MedicationRequest", {"patient": real_id, "_count": 100})

    if payload.status != "all":
        meds_raw = [m for m in meds_raw if (m.get("status") or "").lower() == payload.status]

    rows = [_med_row(await deidentify_resource(m)) for m in meds_raw]

    interactions: list[InteractionCard] = []
    if payload.include_interactions and meds_raw:
        allergies_raw = await backend.search("AllergyIntolerance", {"patient": real_id, "_count": 50})
        prefetch = {"medications": meds_raw, "allergies": allergies_raw}
        client = get_cds_client()
        resp = await client.invoke("medication-prescribe", context={}, prefetch=prefetch)
        for card in resp.get("cards") or []:
            interactions.append(
                InteractionCard(
                    severity=str(card.get("indicator", "info")),
                    summary=str(card.get("summary", "")),
                    detail=str(card.get("detail", "")),
                )
            )

    return GetMedicationsOutput(count=len(rows), medications=rows, interactions=interactions)
