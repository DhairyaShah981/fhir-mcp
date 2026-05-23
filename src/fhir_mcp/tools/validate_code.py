"""``validate_code`` — resolve a LOINC / SNOMED / RxNorm / ICD-10 code."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..audit import audited
from ..observability import traced
from ..terminology import lookup


class ValidateCodeInput(BaseModel):
    code: str = Field(description="The code to resolve, e.g. '44054006'.")
    system: str = Field(
        description="One of 'loinc', 'snomed', 'rxnorm', 'icd10', or the canonical URI."
    )
    allow_live: bool = Field(
        default=False,
        description="When the offline table misses, fall back to tx.fhir.org. Off by default for reproducibility.",
    )


class ValidateCodeOutput(BaseModel):
    valid: bool
    system: str | None = None
    code: str
    display: str | None = None
    alternatives: list[str] = Field(default_factory=list)


@traced("validate_code")
@audited("validate_code")
async def validate_code(payload: ValidateCodeInput) -> ValidateCodeOutput:
    hit = await lookup(payload.system, payload.code, allow_live=payload.allow_live)
    if hit is None:
        return ValidateCodeOutput(valid=False, code=payload.code, system=payload.system)
    return ValidateCodeOutput(
        valid=True,
        system=hit.system,
        code=hit.code,
        display=hit.display,
        alternatives=list(hit.alternatives),
    )
