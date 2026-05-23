"""``create_clinical_note`` — generate a SOAP / discharge note as a FHIR DocumentReference.

The tool synthesizes a structured note from free text. For real EHR writes
SMART-on-FHIR authentication (v0.2) is required; in v0.1 we always emit the
note locally and never POST it to a live FHIR server.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..audit import audited
from ..deid.pipeline import deidentify_resource
from ..observability import traced

NoteTemplate = Literal["soap", "discharge_summary", "prior_auth"]


class CreateClinicalNoteInput(BaseModel):
    patient_pseudonym: str = Field(description="Patient pseudonym.")
    free_text: str = Field(description="Free-text content to structure.")
    template: NoteTemplate = "soap"
    encounter_id: str | None = None


class CreateClinicalNoteOutput(BaseModel):
    document_reference: dict[str, Any]
    structured_text: str
    template: NoteTemplate
    persisted: bool = Field(
        default=False, description="True only when a live FHIR write succeeded (v0.2+)."
    )


def _structure_soap(text: str) -> str:
    text = text.strip()
    return (
        "SUBJECTIVE:\n"
        f"{text}\n\n"
        "OBJECTIVE:\n"
        "  (Populate from the most recent vital signs and labs in this patient's record.)\n\n"
        "ASSESSMENT:\n"
        "  (Synthesize from active conditions and trajectory.)\n\n"
        "PLAN:\n"
        "  (Action items, follow-up, medication changes, patient education.)\n"
    )


def _structure_discharge(text: str) -> str:
    return (
        "DISCHARGE SUMMARY\n"
        "=================\n\n"
        "ADMISSION DIAGNOSIS:\n  (Primary admission diagnosis.)\n\n"
        "HOSPITAL COURSE:\n"
        f"{text.strip()}\n\n"
        "DISCHARGE MEDICATIONS:\n  (Reconcile from MAR.)\n\n"
        "FOLLOW-UP:\n  (Outpatient appointments, labs, imaging.)\n"
    )


def _structure_prior_auth(text: str) -> str:
    return (
        "PRIOR AUTHORIZATION REQUEST\n"
        "===========================\n\n"
        "CLINICAL JUSTIFICATION:\n"
        f"{text.strip()}\n\n"
        "REFERENCES:\n  (Cite guideline + page/section.)\n"
    )


_TEMPLATES: dict[NoteTemplate, Callable[[str], str]] = {
    "soap": _structure_soap,
    "discharge_summary": _structure_discharge,
    "prior_auth": _structure_prior_auth,
}


@traced("create_clinical_note")
@audited("create_clinical_note", phi_args=("free_text",))
async def create_clinical_note(payload: CreateClinicalNoteInput) -> CreateClinicalNoteOutput:
    structured = _TEMPLATES[payload.template](payload.free_text)
    now = datetime.now(UTC).isoformat()

    doc_ref = {
        "resourceType": "DocumentReference",
        "id": f"local-{uuid.uuid4().hex[:12]}",
        "status": "current",
        "type": {
            "coding": [
                {"system": "http://loinc.org", "code": "11506-3", "display": "Progress note"}
            ],
            "text": payload.template,
        },
        "subject": {"reference": f"Patient/{payload.patient_pseudonym}"},
        "date": now,
        "content": [
            {
                "attachment": {
                    "contentType": "text/plain",
                    "data": base64.b64encode(structured.encode()).decode(),
                    "title": f"{payload.template} note",
                }
            }
        ],
    }
    if payload.encounter_id:
        doc_ref["context"] = {"encounter": [{"reference": f"Encounter/{payload.encounter_id}"}]}

    # Run the document through the de-id pipeline as a safety net.
    safe_doc = await deidentify_resource(doc_ref)
    return CreateClinicalNoteOutput(
        document_reference=safe_doc,
        structured_text=structured,
        template=payload.template,
        persisted=False,
    )
