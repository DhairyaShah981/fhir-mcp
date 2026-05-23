"""``run_cds_hook`` — invoke a CDS Hooks decision-support service."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..audit import audited
from ..cds_hooks.client import get_client
from ..cds_hooks.mock_service import MOCK_HOOKS
from ..deid.vault import get_vault
from ..fhir_client import get_backend
from ..observability import traced


class RunCdsHookInput(BaseModel):
    hook: str = Field(description="Hook id, e.g. 'medication-prescribe' or 'patient-view'.")
    patient_pseudonym: str | None = Field(
        default=None,
        description="When provided, the patient's meds/allergies/recent obs are auto-prefetched.",
    )
    context: dict[str, Any] = Field(default_factory=dict)
    prefetch: dict[str, Any] = Field(default_factory=dict)
    allow_live: bool = Field(
        default=False,
        description="When true, attempt the configured live CDS Hooks endpoint with mock fallback.",
    )


class CdsCard(BaseModel):
    severity: str
    summary: str
    detail: str | None = None
    source_label: str | None = None


class RunCdsHookOutput(BaseModel):
    hook: str
    available_hooks: list[str]
    cards: list[CdsCard] = Field(default_factory=list)
    used_mock: bool
    fallback_reason: str | None = Field(
        default=None,
        description="Populated when allow_live=true but the live endpoint failed and we fell back to the mock.",
    )


@traced("run_cds_hook")
@audited("run_cds_hook")
async def run_cds_hook(payload: RunCdsHookInput) -> RunCdsHookOutput:
    backend = get_backend()
    vault = get_vault()
    prefetch = dict(payload.prefetch)

    if payload.patient_pseudonym and not prefetch:
        real_id = await vault.lookup(payload.patient_pseudonym) or payload.patient_pseudonym
        meds = await backend.search("MedicationStatement", {"patient": real_id, "_count": 100})
        allergies = await backend.search("AllergyIntolerance", {"patient": real_id, "_count": 50})
        obs = await backend.search("Observation", {"patient": real_id, "_count": 25})
        prefetch = {"medications": meds, "allergies": allergies, "observations": obs}

    client = get_client()
    resp = await client.invoke(
        payload.hook, context=payload.context, prefetch=prefetch, allow_live=payload.allow_live
    )
    cards: list[CdsCard] = []
    for card in resp.cards:
        cards.append(
            CdsCard(
                severity=str(card.get("indicator", "info")),
                summary=str(card.get("summary", "")),
                detail=card.get("detail"),
                source_label=(card.get("source") or {}).get("label"),
            )
        )

    return RunCdsHookOutput(
        hook=payload.hook,
        available_hooks=sorted(MOCK_HOOKS.keys()),
        cards=cards,
        used_mock=resp.used_mock,
        fallback_reason=resp.fallback_reason,
    )
