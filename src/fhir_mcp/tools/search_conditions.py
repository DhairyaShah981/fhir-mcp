"""``search_conditions`` — query FHIR Conditions by SNOMED code / text / clinical status."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..audit import audited
from ..deid.pipeline import deidentify_resource
from ..deid.vault import get_vault
from ..fhir_client import get_backend
from ..observability import traced

ClinicalStatus = Literal["active", "recurrence", "relapse", "inactive", "remission", "resolved"]


class SearchConditionsInput(BaseModel):
    patient_pseudonym: str | None = None
    snomed: str | None = Field(default=None, description="SNOMED CT code or fragment.")
    text: str | None = Field(default=None, description="Free-text fragment matched against code.text.")
    clinical_status: ClinicalStatus | None = None
    limit: int = Field(default=50, ge=1, le=200)


class ConditionRow(BaseModel):
    code: str | None
    system: str | None
    display: str | None
    text: str | None
    clinical_status: str | None
    onset: str | None


class SearchConditionsOutput(BaseModel):
    count: int
    conditions: list[ConditionRow] = Field(default_factory=list)


def _clinical_status(resource: dict[str, Any]) -> str | None:
    cs = resource.get("clinicalStatus")
    if not isinstance(cs, dict):
        return None
    codings = cs.get("coding") or []
    return codings[0].get("code") if codings else None


@traced("search_conditions")
@audited("search_conditions")
async def search_conditions(payload: SearchConditionsInput) -> SearchConditionsOutput:
    backend = get_backend()
    vault = get_vault()

    params: dict[str, str | int] = {"_count": payload.limit}
    if payload.patient_pseudonym:
        real_id = await vault.lookup(payload.patient_pseudonym) or payload.patient_pseudonym
        params["patient"] = real_id
    if payload.clinical_status:
        params["clinical-status"] = payload.clinical_status
    if payload.snomed:
        params["code"] = payload.snomed

    raw = await backend.search("Condition", params)

    if payload.text:
        needle = payload.text.lower()
        raw = [
            r
            for r in raw
            if needle in (r.get("code", {}).get("text", "") or "").lower()
            or any(
                needle in (c.get("display", "") or "").lower()
                for c in (r.get("code", {}).get("coding") or [])
            )
        ]

    rows: list[ConditionRow] = []
    for r in raw[: payload.limit]:
        deid = await deidentify_resource(r)
        code = deid.get("code", {})
        coding_list = code.get("coding") or []
        coding = coding_list[0] if coding_list else {}
        rows.append(
            ConditionRow(
                code=coding.get("code"),
                system=coding.get("system"),
                display=coding.get("display"),
                text=code.get("text"),
                clinical_status=_clinical_status(deid),
                onset=deid.get("onsetDateTime") or deid.get("recordedDate"),
            )
        )
    return SearchConditionsOutput(count=len(rows), conditions=rows)
