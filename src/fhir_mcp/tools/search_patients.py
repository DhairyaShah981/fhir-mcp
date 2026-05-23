"""``search_patients`` — search by name, MRN, DOB, gender. De-identified output."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..audit import audited
from ..deid.pipeline import deidentify_resource
from ..fhir_client import get_backend
from ..observability import traced


class SearchPatientsInput(BaseModel):
    name: str | None = Field(
        default=None,
        description="Partial match against family, given, or full name (case-insensitive).",
    )
    family: str | None = Field(default=None, description="Family/last-name partial match.")
    given: str | None = Field(default=None, description="Given/first-name partial match.")
    mrn: str | None = Field(default=None, description="Medical record number (or partial).")
    birthdate: str | None = Field(default=None, description="ISO date, e.g. 1964-08-12")
    gender: str | None = Field(default=None, description="male | female | other | unknown")
    limit: int = Field(default=20, ge=1, le=100)


class PatientSummary(BaseModel):
    pseudonym: str = Field(description="Stable pseudonymized patient id. Use with other tools.")
    display_name: str = Field(description="De-identified display name, e.g. 'NM_a1b2c3'.")
    gender: str | None = None
    age_band: str | None = Field(
        default=None,
        description="Coarse age band ('60-69') derived from DOB. The exact DOB is never returned.",
    )


class SearchPatientsOutput(BaseModel):
    count: int
    patients: list[PatientSummary]


def _age_band(birth_date: str | None) -> str | None:
    if not birth_date or len(birth_date) < 4:
        return None
    try:
        from datetime import date

        y = int(birth_date[:4])
        m = int(birth_date[5:7]) if len(birth_date) >= 7 else 1
        d = int(birth_date[8:10]) if len(birth_date) >= 10 else 1
        today = date.today()
        age = today.year - y - ((today.month, today.day) < (m, d))
        floor = (age // 10) * 10
        return f"{floor}-{floor + 9}"
    except (ValueError, TypeError):
        return None


@traced("search_patients")
@audited("search_patients")
async def search_patients(payload: SearchPatientsInput) -> SearchPatientsOutput:
    backend = get_backend()
    params: dict[str, str | int] = {"_count": payload.limit}
    if payload.name:
        params["name"] = payload.name
    if payload.family:
        params["family"] = payload.family
    if payload.given:
        params["given"] = payload.given
    if payload.mrn:
        params["identifier"] = payload.mrn
    if payload.birthdate:
        params["birthdate"] = payload.birthdate
    if payload.gender:
        params["gender"] = payload.gender

    raw_patients = await backend.search("Patient", params)
    out: list[PatientSummary] = []
    for raw in raw_patients:
        # Compute the age band *before* de-id (DOB is removed from output).
        age = _age_band(raw.get("birthDate"))
        deid = await deidentify_resource(raw)
        names = deid.get("name") or [{}]
        first = names[0] if isinstance(names, list) and names else {}
        display = first.get("text") or " ".join(
            list(first.get("given", []) or []) + ([first.get("family")] if first.get("family") else [])
        ).strip() or deid.get("id", "")
        out.append(
            PatientSummary(
                pseudonym=str(deid.get("id", "")),
                display_name=display,
                gender=deid.get("gender"),
                age_band=age,
            )
        )

    return SearchPatientsOutput(count=len(out), patients=out)
