"""MCP server entry point.

Wires our tools, resources and prompts into an ``mcp.server.fastmcp.FastMCP``
instance and exposes ``stdio`` and (optionally) ``sse`` transports.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from .audit import init as audit_init
from .config import get_settings
from .deid.vault import get_vault
from .prompts import discharge_summary as soap_discharge
from .prompts import prior_auth_letter as soap_prior
from .prompts import soap_note as soap_prompt
from .resources import lab_trends as res_lab_trends
from .resources import medications as res_medications
from .resources import patient_summary as res_patient_summary
from .tools.create_clinical_note import (
    CreateClinicalNoteInput,
    create_clinical_note,
)
from .tools.get_medications import GetMedicationsInput, get_medications
from .tools.get_patient_summary import (
    GetPatientSummaryInput,
    get_patient_summary,
)
from .tools.run_cds_hook import RunCdsHookInput, run_cds_hook
from .tools.search_conditions import SearchConditionsInput, search_conditions
from .tools.search_observations import (
    SearchObservationsInput,
    search_observations,
)
from .tools.search_patients import SearchPatientsInput, search_patients
from .tools.validate_code import ValidateCodeInput, validate_code

log = structlog.get_logger(__name__)


def _configure_logging() -> None:
    """Route all logs to stderr so they never contaminate the stdio MCP channel."""
    root = logging.getLogger()
    if any(getattr(h, "_fhir_mcp", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._fhir_mcp = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.KeyValueRenderer(key_order=["event"]),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def _build_app():
    """Construct a FastMCP application with all tools, resources, prompts registered."""
    from mcp.server.fastmcp import FastMCP

    app = FastMCP(
        "fhir-mcp",
        instructions=(
            "MCP server exposing FHIR R4 resources with de-identified egress, "
            "audited tool calls, and CDS Hooks decision support."
        ),
    )

    # ---- Tools ----------------------------------------------------------------

    @app.tool(
        name="search_patients",
        description=(
            "Search FHIR Patient resources by name, MRN, DOB, or gender. Returns "
            "de-identified summaries with stable pseudonyms — use the pseudonym "
            "with other tools to fetch more detail."
        ),
    )
    async def _search_patients(
        name: str | None = None,
        family: str | None = None,
        given: str | None = None,
        mrn: str | None = None,
        birthdate: str | None = None,
        gender: str | None = None,
        limit: int = 20,
    ) -> dict:
        return (
            await search_patients(
                SearchPatientsInput(
                    name=name, family=family, given=given, mrn=mrn,
                    birthdate=birthdate, gender=gender, limit=limit,
                )
            )
        ).model_dump(mode="json")

    @app.tool(
        name="get_patient_summary",
        description=(
            "Composite clinical summary: conditions, active medications, allergies, "
            "recent observations, and a heuristically-chosen primary diagnosis."
        ),
    )
    async def _get_patient_summary(patient_pseudonym: str, max_observations: int = 10) -> dict:
        return (
            await get_patient_summary(
                GetPatientSummaryInput(
                    patient_pseudonym=patient_pseudonym, max_observations=max_observations
                )
            )
        ).model_dump(mode="json")

    @app.tool(
        name="search_observations",
        description=(
            "Search FHIR Observations. Filter by LOINC code, date range, value comparison. "
            "When `trend_window_days` is set, returns aggregated trend stats (slope, mean, last) "
            "instead of raw observations."
        ),
    )
    async def _search_observations(
        patient_pseudonym: str | None = None,
        loinc: str | None = None,
        text: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        value_op: str | None = None,
        value: float | None = None,
        trend_window_days: int | None = None,
        limit: int = 25,
    ) -> dict:
        return (
            await search_observations(
                SearchObservationsInput(
                    patient_pseudonym=patient_pseudonym,
                    loinc=loinc, text=text,
                    date_from=date_from, date_to=date_to,
                    value_op=value_op, value=value,  # type: ignore[arg-type]
                    trend_window_days=trend_window_days,
                    limit=limit,
                )
            )
        ).model_dump(mode="json")

    @app.tool(
        name="search_conditions",
        description="Search FHIR Conditions by SNOMED code, free text, or clinical status.",
    )
    async def _search_conditions(
        patient_pseudonym: str | None = None,
        snomed: str | None = None,
        text: str | None = None,
        clinical_status: str | None = None,
        limit: int = 50,
    ) -> dict:
        return (
            await search_conditions(
                SearchConditionsInput(
                    patient_pseudonym=patient_pseudonym, snomed=snomed,
                    text=text, clinical_status=clinical_status,  # type: ignore[arg-type]
                    limit=limit,
                )
            )
        ).model_dump(mode="json")

    @app.tool(
        name="get_medications",
        description=(
            "Return the patient's medication list with RxNorm coding and dosing. "
            "When `include_interactions=true`, also returns drug-drug interaction flags "
            "from the medication-prescribe CDS hook."
        ),
    )
    async def _get_medications(
        patient_pseudonym: str, status: str = "active", include_interactions: bool = True
    ) -> dict:
        return (
            await get_medications(
                GetMedicationsInput(
                    patient_pseudonym=patient_pseudonym,
                    status=status,  # type: ignore[arg-type]
                    include_interactions=include_interactions,
                )
            )
        ).model_dump(mode="json")

    @app.tool(
        name="validate_code",
        description=(
            "Resolve a LOINC, SNOMED, RxNorm, or ICD-10 code to its canonical display name. "
            "Offline-first using an embedded table; set `allow_live=true` to fall back to tx.fhir.org."
        ),
    )
    async def _validate_code(code: str, system: str, allow_live: bool = False) -> dict:
        return (
            await validate_code(
                ValidateCodeInput(code=code, system=system, allow_live=allow_live)
            )
        ).model_dump(mode="json")

    @app.tool(
        name="create_clinical_note",
        description=(
            "Generate a structured clinical note (SOAP / discharge / prior-auth) as a "
            "FHIR DocumentReference. Always written locally in v0.1 — never POSTed to a live EHR."
        ),
    )
    async def _create_clinical_note(
        patient_pseudonym: str,
        free_text: str,
        template: str = "soap",
        encounter_id: str | None = None,
    ) -> dict:
        return (
            await create_clinical_note(
                CreateClinicalNoteInput(
                    patient_pseudonym=patient_pseudonym,
                    free_text=free_text,
                    template=template,  # type: ignore[arg-type]
                    encounter_id=encounter_id,
                )
            )
        ).model_dump(mode="json")

    @app.tool(
        name="run_cds_hook",
        description=(
            "Execute a CDS Hooks decision-support service (e.g. drug-drug interaction check). "
            "Uses the offline deterministic mock by default; set `allow_live=true` to call the "
            "configured CDS Hooks endpoint with mock fallback."
        ),
    )
    async def _run_cds_hook(
        hook: str,
        patient_pseudonym: str | None = None,
        context: dict[str, Any] | None = None,
        prefetch: dict[str, Any] | None = None,
        allow_live: bool = False,
    ) -> dict:
        return (
            await run_cds_hook(
                RunCdsHookInput(
                    hook=hook,
                    patient_pseudonym=patient_pseudonym,
                    context=context or {},
                    prefetch=prefetch or {},
                    allow_live=allow_live,
                )
            )
        ).model_dump(mode="json")

    # ---- Resources ----------------------------------------------------------

    @app.resource(
        res_patient_summary.URI_TEMPLATE,
        name="patient_summary",
        description="Markdown patient summary (conditions + meds + allergies + recent obs).",
        mime_type="text/markdown",
    )
    async def _patient_summary_resource(pseudonym: str) -> str:
        return await res_patient_summary.render(pseudonym)

    @app.resource(
        res_lab_trends.URI_TEMPLATE,
        name="lab_trends",
        description="JSON-formatted lab trends (HbA1c, BP, LDL, creatinine) for the last year.",
        mime_type="application/json",
    )
    async def _lab_trends_resource(pseudonym: str) -> str:
        return await res_lab_trends.render(pseudonym)

    @app.resource(
        res_medications.URI_TEMPLATE,
        name="medications",
        description="Markdown medication list with interaction flags.",
        mime_type="text/markdown",
    )
    async def _medications_resource(pseudonym: str) -> str:
        return await res_medications.render(pseudonym)

    # ---- Prompts ------------------------------------------------------------

    @app.prompt(name=soap_prompt.NAME, description=soap_prompt.DESCRIPTION)
    def _soap_prompt(patient_pseudonym: str, chief_complaint: str = "", encounter_id: str = "") -> str:
        return soap_prompt.render(patient_pseudonym, chief_complaint, encounter_id)

    @app.prompt(name=soap_discharge.NAME, description=soap_discharge.DESCRIPTION)
    def _discharge_prompt(
        patient_pseudonym: str, admission_diagnosis: str = "", encounter_id: str = ""
    ) -> str:
        return soap_discharge.render(patient_pseudonym, admission_diagnosis, encounter_id)

    @app.prompt(name=soap_prior.NAME, description=soap_prior.DESCRIPTION)
    def _prior_auth_prompt(
        patient_pseudonym: str,
        requested_intervention: str,
        payer: str = "",
        diagnosis: str = "",
    ) -> str:
        return soap_prior.render(patient_pseudonym, requested_intervention, payer, diagnosis)

    return app


async def _bootstrap_storage() -> None:
    """Initialize audit DB + vault before serving any traffic."""
    await audit_init()
    await get_vault().init()


def run_stdio() -> None:
    """Run the MCP server over stdio (Claude Desktop, Claude Code, Cursor)."""
    import anyio

    _configure_logging()
    settings = get_settings()
    log.info(
        "fhir_mcp_starting",
        backend=settings.backend,
        cloud_audit=bool(settings.supabase_url),
        langfuse=settings.langfuse_enabled(),
        reid_enabled=settings.enable_reid,
    )
    anyio.run(_bootstrap_storage)
    app = _build_app()
    app.run("stdio")


def run_sse(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the MCP server over SSE (HTTP) with a small landing page at /."""
    import anyio
    from starlette.responses import JSONResponse, PlainTextResponse

    _configure_logging()
    log.info("fhir_mcp_starting_sse", host=host, port=port)
    anyio.run(_bootstrap_storage)
    app = _build_app()

    @app.custom_route("/", methods=["GET"])
    async def _landing(_request):  # type: ignore[no-redef]
        return JSONResponse({
            "name": "fhir-mcp",
            "description": (
                "Trustworthy FHIR R4 MCP server — reproducible clinical evals, "
                "reversible keyed de-identification, CDS Hooks decision support, "
                "audit trails."
            ),
            "transport": "sse",
            "mcp_endpoint": "/sse",
            "tools": [
                "search_patients",
                "get_patient_summary",
                "get_medications",
                "search_conditions",
                "search_observations",
                "validate_code",
                "run_cds_hook",
                "create_clinical_note",
            ],
            "synthetic_patients": [
                "pediatric_asthma_8yo",
                "diabetic_60yo",
                "chf_warfarin_70yo",
                "pregnant_with_htn_28yo",
                "geriatric_polypharmacy_82yo",
            ],
            "data": "Synthea-style synthetic — no real PHI",
            "github": "https://github.com/DhairyaShah981/fhir-mcp",
        })

    @app.custom_route("/healthz", methods=["GET"])
    async def _healthz(_request):  # type: ignore[no-redef]
        return PlainTextResponse("ok")

    app.settings.host = host
    app.settings.port = port
    app.run("sse")
