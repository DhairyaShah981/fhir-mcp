"""MCP server entry point.

Wires our tools into an ``mcp.server.fastmcp.FastMCP`` instance and exposes
``stdio`` and (optionally) ``sse`` transports. Tool input schemas are derived
from the Pydantic models defined alongside each tool.
"""

from __future__ import annotations

import structlog

from .audit import init as audit_init
from .config import get_settings
from .deid.vault import get_vault
from .tools.get_patient_summary import (
    GetPatientSummaryInput,
    GetPatientSummaryOutput,
    get_patient_summary,
)
from .tools.search_patients import (
    SearchPatientsInput,
    SearchPatientsOutput,
    search_patients,
)

log = structlog.get_logger(__name__)


def _build_app():
    """Construct and return a FastMCP application with our tools registered.

    Lazy import keeps the heavy ``mcp`` dependency out of unit tests that only
    exercise our pure-Python modules.
    """
    from mcp.server.fastmcp import FastMCP

    app = FastMCP(
        "fhir-mcp",
        instructions=(
            "MCP server exposing FHIR R4 resources with de-identified egress, "
            "audited tool calls, and CDS Hooks decision support."
        ),
    )

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
        result: SearchPatientsOutput = await search_patients(
            SearchPatientsInput(
                name=name,
                family=family,
                given=given,
                mrn=mrn,
                birthdate=birthdate,
                gender=gender,
                limit=limit,
            )
        )
        return result.model_dump(mode="json")

    @app.tool(
        name="get_patient_summary",
        description=(
            "Composite clinical summary for one patient: conditions, active "
            "medications, allergies, recent observations, and a heuristically-"
            "chosen primary diagnosis. Pass the pseudonym returned by "
            "search_patients."
        ),
    )
    async def _get_patient_summary(patient_pseudonym: str, max_observations: int = 10) -> dict:
        result: GetPatientSummaryOutput = await get_patient_summary(
            GetPatientSummaryInput(
                patient_pseudonym=patient_pseudonym,
                max_observations=max_observations,
            )
        )
        return result.model_dump(mode="json")

    return app


async def _bootstrap_storage() -> None:
    """Initialize audit DB + vault before serving any traffic."""
    await audit_init()
    await get_vault().init()


def run_stdio() -> None:
    """Run the MCP server over stdio (Claude Desktop, Claude Code, Cursor)."""
    import anyio

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
    """Run the MCP server over SSE (HTTP)."""
    import anyio

    log.info("fhir_mcp_starting_sse", host=host, port=port)
    anyio.run(_bootstrap_storage)
    app = _build_app()
    app.settings.host = host
    app.settings.port = port
    app.run("sse")
