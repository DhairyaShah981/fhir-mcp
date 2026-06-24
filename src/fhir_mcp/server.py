"""MCP server entry point.

Wires our tools, resources and prompts into an ``mcp.server.fastmcp.FastMCP``
instance and exposes ``stdio`` and (optionally) ``sse`` transports.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from . import __version__
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
    """Run the MCP server over SSE (HTTP) with a landing page at /."""
    import anyio
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse

    from .tools.get_medications import (
        GetMedicationsInput as _GMInput,
    )
    from .tools.get_medications import (
        get_medications as _gm_handler,
    )
    from .tools.get_patient_summary import (
        GetPatientSummaryInput as _GPInput,
    )
    from .tools.get_patient_summary import (
        get_patient_summary as _gp_handler,
    )
    from .tools.search_patients import (
        SearchPatientsInput as _SPInput,
    )
    from .tools.search_patients import (
        search_patients as _sp_handler,
    )
    from .tools.validate_code import (
        ValidateCodeInput as _VCInput,
    )
    from .tools.validate_code import (
        validate_code as _vc_handler,
    )

    _configure_logging()
    log.info("fhir_mcp_starting_sse", host=host, port=port)
    anyio.run(_bootstrap_storage)
    app = _build_app()

    _tools_table = [
        ("search_patients", "Find patients by demographics or fuzzy name match."),
        ("get_patient_summary", "Compact one-screen summary for a patient pseudonym."),
        ("get_medications", "Active medication list with interaction flags."),
        ("search_conditions", "Conditions / problem-list entries."),
        ("search_observations", "Vitals + labs (HbA1c, BP, LDL, creatinine, etc)."),
        ("validate_code", "LOINC / SNOMED / RxNorm / ICD-10 lookup."),
        ("run_cds_hook", "Run a CDS Hooks card (drug-drug, drug-allergy)."),
        ("create_clinical_note", "Write a SOAP / discharge / prior-auth note."),
    ]
    _patients_table = [
        ("pediatric_asthma_8yo", "8-year-old asthma exacerbation"),
        ("diabetic_60yo", "60-year-old T2DM, HbA1c trend"),
        ("chf_warfarin_70yo", "70-year-old CHF on warfarin (drug-drug)"),
        ("pregnant_with_htn_28yo", "28-year-old pregnant + hypertension"),
        ("geriatric_polypharmacy_82yo", "82-year-old polypharmacy (deprescribing)"),
    ]

    @app.custom_route("/", methods=["GET"])
    async def _landing(request):  # type: ignore[no-redef]
        # Negotiate: JSON for machine clients (curl with -H Accept: application/json,
        # MCP discovery, etc.). HTML for browsers.
        accept = request.headers.get("accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return JSONResponse({
                "name": "fhir-mcp",
                "description": (
                    "Trustworthy FHIR R4 MCP server with reproducible clinical evals, "
                    "reversible keyed de-identification, CDS Hooks, audit trails."
                ),
                "transport": "sse",
                "mcp_endpoint": "/sse",
                "tools": [t[0] for t in _tools_table],
                "synthetic_patients": [p[0] for p in _patients_table],
                "data": "Synthea-style synthetic, no real PHI",
                "github": "https://github.com/DhairyaShah981/fhir-mcp",
            })

        tool_rows = "\n".join(
            f"<tr><td><code>{n}</code></td><td>{d}</td></tr>" for n, d in _tools_table
        )
        patient_rows = "\n".join(
            f"<tr><td><code>{p}</code></td><td>{d}</td></tr>" for p, d in _patients_table
        )
        html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>fhir-mcp · live MCP server</title>
<style>
  * {{ box-sizing: border-box }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    margin: 0; padding: 0; background: #fafafa; color: #18181b; line-height: 1.6;
  }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 48px 24px 64px; }}
  header {{ border-bottom: 1px solid #e4e4e7; padding-bottom: 24px; margin-bottom: 32px; }}
  header h1 {{ font-size: 28px; margin: 0 0 8px; letter-spacing: -0.02em; }}
  header p {{ margin: 0; color: #71717a; font-size: 15px; }}
  .live {{ display: inline-block; background: #10b981; color: white; font-size: 11px;
           padding: 2px 8px; border-radius: 999px; font-weight: 600; letter-spacing: 0.05em;
           text-transform: uppercase; margin-right: 8px; vertical-align: 1px; }}
  .endpoint {{ background: #18181b; color: #f4f4f5; padding: 12px 16px;
               border-radius: 8px; font-family: ui-monospace, SFMono-Regular, Menlo,
               monospace; font-size: 13px; margin: 16px 0 8px;
               display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
  .endpoint .label {{ color: #71717a; font-size: 11px; text-transform: uppercase;
                       letter-spacing: 0.05em; }}
  section {{ margin-top: 32px; }}
  section h2 {{ font-size: 16px; margin: 0 0 12px; font-weight: 600;
                text-transform: uppercase; letter-spacing: 0.06em; color: #52525b; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  table tr {{ border-bottom: 1px solid #e4e4e7; }}
  table tr:last-child {{ border-bottom: none; }}
  table td {{ padding: 10px 8px; vertical-align: top; }}
  table td:first-child {{ width: 240px; }}
  table code {{ background: #f4f4f5; padding: 2px 6px; border-radius: 4px;
                font-size: 13px; color: #18181b; }}
  .footer {{ margin-top: 48px; padding-top: 24px; border-top: 1px solid #e4e4e7;
              font-size: 13px; color: #71717a; display: flex; gap: 16px; flex-wrap: wrap; }}
  .footer a {{ color: #18181b; text-decoration: none; border-bottom: 1px solid #d4d4d8; }}
  .footer a:hover {{ border-bottom-color: #18181b; }}
  .note {{ background: #fefce8; border-left: 3px solid #eab308; padding: 12px 16px;
            font-size: 14px; color: #713f12; border-radius: 4px; margin-top: 16px; }}
  .play {{ background: #fafafa; border: 1px solid #e4e4e7; border-radius: 8px;
            padding: 12px; margin-bottom: 12px; }}
  .play .row {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
  .play strong {{ font-family: ui-monospace, monospace; font-size: 13px; min-width: 170px; }}
  .play input, .play select {{ font: inherit; padding: 6px 10px; border: 1px solid #d4d4d8;
                                 border-radius: 6px; font-size: 13px; }}
  .play button {{ background: #18181b; color: white; border: 0; padding: 6px 14px;
                   border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 500; }}
  .play button:hover {{ background: #3f3f46; }}
  .play .out {{ margin-top: 10px; background: #18181b; color: #d4d4d8; padding: 10px 12px;
                 border-radius: 6px; font-family: ui-monospace, monospace; font-size: 12px;
                 max-height: 220px; overflow: auto; white-space: pre-wrap;
                 word-break: break-word; }}
</style>
<script>
async function runTry(tool, body, outId) {{
  const out = document.getElementById(outId);
  out.textContent = 'calling /try/' + tool + ' ...';
  try {{
    const res = await fetch('/try/' + tool, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body),
    }});
    const txt = await res.text();
    out.textContent = txt;
    // If this is search_patients output, pull pseudonyms into the dropdown.
    if (tool === 'search_patients') {{
      try {{
        const data = JSON.parse(txt);
        const sel = document.getElementById('gs-pseudo');
        sel.innerHTML = '<option value="">(pick a patient)</option>';
        (data.patients || []).forEach(p => {{
          const opt = document.createElement('option');
          opt.value = p.pseudonym;
          opt.textContent = p.pseudonym + ' — ' + (p.gender || '?') + ' / ' + (p.age_band || '?');
          sel.appendChild(opt);
        }});
      }} catch (e) {{}}
    }}
  }} catch (e) {{
    out.textContent = 'error: ' + e.message;
  }}
}}
</script>
</head>
<body>
<div class="wrap">
  <header>
    <h1><span class="live">live</span>fhir-mcp</h1>
    <p>Trustworthy FHIR R4 MCP server &middot; reproducible clinical evals &middot; reversible keyed de-identification &middot; CDS Hooks &middot; audit trails.</p>
  </header>

  <section>
    <h2>MCP endpoint</h2>
    <div class="endpoint">
      <span><span class="label">SSE</span> &nbsp; https://dhairya-fhir-mcp.fly.dev/sse</span>
    </div>
    <p style="font-size: 14px; color: #52525b; margin: 12px 0 0;">
      Point any MCP-compatible client at the URL above. Synthea-style synthetic data only &mdash; no real PHI.
    </p>
    <div class="note">
      <strong>Try in the terminal</strong> &mdash;
      <code>curl -N -H "Accept: text/event-stream" https://dhairya-fhir-mcp.fly.dev/sse</code><br>
      <strong>Try as JSON</strong> &mdash;
      <code>curl -H "Accept: application/json" https://dhairya-fhir-mcp.fly.dev/</code>
    </div>
  </section>

  <section>
    <h2>Try it · live playground</h2>
    <p style="font-size:14px;color:#52525b;margin:0 0 16px">
      These call the same handlers the MCP tools use against the Synthea backend. Try them right here.
    </p>

    <div class="play">
      <div class="row">
        <strong>search_patients</strong>
        <input id="sp-name" placeholder="name (e.g. 'sm')" style="width:200px">
        <button onclick="runTry('search_patients', {{name: document.getElementById('sp-name').value || null, limit: 5}}, 'sp-out')">Run &rarr;</button>
      </div>
      <pre id="sp-out" class="out">click Run to call the tool...</pre>
    </div>

    <div class="play">
      <div class="row">
        <strong>get_patient_summary</strong>
        <select id="gs-pseudo">
          <option value="">(pick a patient — run search_patients first to get a pseudonym)</option>
        </select>
        <button onclick="runTry('get_patient_summary', {{patient_pseudonym: document.getElementById('gs-pseudo').value}}, 'gs-out')">Run &rarr;</button>
      </div>
      <pre id="gs-out" class="out">first run search_patients above, then pick a pseudonym...</pre>
    </div>

    <div class="play">
      <div class="row">
        <strong>validate_code</strong>
        <select id="vc-system" style="width:140px">
          <option value="loinc">LOINC</option>
          <option value="snomed">SNOMED</option>
          <option value="rxnorm">RxNorm</option>
          <option value="icd10">ICD-10</option>
        </select>
        <input id="vc-code" placeholder="code (e.g. 4548-4)" style="width:160px" value="4548-4">
        <button onclick="runTry('validate_code', {{system: document.getElementById('vc-system').value, code: document.getElementById('vc-code').value}}, 'vc-out')">Run &rarr;</button>
      </div>
      <pre id="vc-out" class="out">click Run to validate (4548-4 is HbA1c)...</pre>
    </div>
  </section>

  <section>
    <h2>Tools ({len(_tools_table)})</h2>
    <table>
      {tool_rows}
    </table>
  </section>

  <section>
    <h2>Synthetic patients ({len(_patients_table)})</h2>
    <table>
      {patient_rows}
    </table>
  </section>

  <div class="footer">
    <a href="https://github.com/DhairyaShah981/fhir-mcp">GitHub &rarr;</a>
    <a href="https://github.com/DhairyaShah981/fhir-mcp/blob/main/DEPLOY.md">Deploy guide</a>
    <a href="/healthz">Healthz</a>
    <span style="margin-left: auto;">Apache-2.0 &middot; v{__version__}</span>
  </div>
</div>
</body>
</html>"""
        return HTMLResponse(html)

    @app.custom_route("/healthz", methods=["GET"])
    async def _healthz(_request):  # type: ignore[no-redef]
        return PlainTextResponse("ok")

    # ---- /try/* — browser-callable REST shims for the playground ----------
    # These call the same handlers the MCP tools use, so the playground
    # exercises the real production code path (de-id, audit, observability
    # included). Not part of the MCP protocol — pure REST for the demo UI.

    async def _safe_call(request: Request, handler, input_cls):
        try:
            body = await request.json() if await request.body() else {}
        except Exception:
            body = {}
        try:
            result = await handler(input_cls(**body))
            return JSONResponse(result.model_dump(mode="json"))
        except Exception as exc:
            return JSONResponse(
                {"error": f"{type(exc).__name__}: {exc}"}, status_code=400,
            )

    @app.custom_route("/try/search_patients", methods=["POST"])
    async def _try_search(request):  # type: ignore[no-redef]
        return await _safe_call(request, _sp_handler, _SPInput)

    @app.custom_route("/try/get_patient_summary", methods=["POST"])
    async def _try_summary(request):  # type: ignore[no-redef]
        return await _safe_call(request, _gp_handler, _GPInput)

    @app.custom_route("/try/get_medications", methods=["POST"])
    async def _try_meds(request):  # type: ignore[no-redef]
        return await _safe_call(request, _gm_handler, _GMInput)

    @app.custom_route("/try/validate_code", methods=["POST"])
    async def _try_code(request):  # type: ignore[no-redef]
        return await _safe_call(request, _vc_handler, _VCInput)

    app.settings.host = host
    app.settings.port = port
    app.run("sse")
