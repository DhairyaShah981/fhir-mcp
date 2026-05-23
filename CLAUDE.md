# CLAUDE.md — contributor context for `fhir-mcp`

You are working on **fhir-mcp**, an MCP server that exposes FHIR R4 resources to AI assistants with trust guarantees: reproducible clinical evals, reversible keyed de-identification, CDS Hooks bridge, and LLM-trace ↔ audit-log correlation.

## Mental model

- **MCP surface = Tools + Resources + Prompts.** Most competitors ship Tools only. We ship all three. When adding capability, ask: is this an action (Tool), a readable context (Resource), or a template (Prompt)?
- **De-id is non-optional on egress.** Every FHIR resource that leaves the server passes through `src/fhir_mcp/deid/pipeline.py`. PHI becomes a pseudonym (`PT_a1b2`). Originals live in the vault (`deid/vault.py`).
- **Re-identification is a privileged path.** Gated by `FHIR_MCP_ENABLE_REID=true` plus `FHIR_MCP_REID_KEY`. Every re-id writes an audit row with the Langfuse trace ID.
- **Audit + observability are co-located.** `audit.py` writes structured rows; `observability.py` wraps every tool entry in a Langfuse span. The span ID is written into the audit row — that's the "open audit row → click LLM trace" magic.
- **Zero-config is sacred.** Adding a hard dependency on a cloud service is a P0 regression. Cloud opt-in only.

## Directory map

```
src/fhir_mcp/
  server.py           # MCP server entry point — registers tools/resources/prompts
  config.py           # pydantic-settings; all envs documented here
  cli.py              # `fhir-mcp serve` / `fhir-mcp eval`
  fhir_client.py      # backend-agnostic FHIR R4 wrapper
  backends/           # synthea (in-memory), hapi (public)
  tools/              # one file per MCP tool
  resources/          # MCP Resources (patient summary, lab trends, meds)
  prompts/            # MCP Prompts (SOAP, discharge, prior-auth)
  deid/               # Presidio + custom recognizers + vault + reid
  cds_hooks/          # client + offline mock
  terminology.py      # LOINC/SNOMED/RxNorm via tx.fhir.org
  audit.py            # SQLAlchemy audit_events; @audited decorator
  observability.py    # Langfuse wrapper (no-op when keys absent)
  auth/               # SMART on FHIR (v0.2)

evals/
  golden/             # frozen Synthea bundles + expected answers
  judge/              # judge LLM prompts and rubrics
  test_*.py           # clinical accuracy, de-id leakage, code validation, CDS hooks
  report.py           # renders eval scoreboard for README
```

## Conventions

- **Python 3.11+. uv for everything** (`uv sync`, `uv run pytest`, `uv add ...`).
- **Pydantic v2** for tool I/O. Every tool's input + output is a Pydantic model.
- **`ruff` + `pyright`** clean before merging. CI gates on both.
- **No PHI in logs by default.** Log argument hashes, not values. `FHIR_MCP_VERBOSE_AUDIT=true` for dev only.
- **Synthea fixtures are frozen.** Never regenerate at test time. If you need a new patient, add a new JSON to `evals/golden/` and document the seed.
- **Judge LLM model is pinned.** Don't bump it casually — it changes scores.

## When you add a new tool

1. Create `src/fhir_mcp/tools/<tool_name>.py` with Pydantic input/output models and an async handler.
2. Register it in `src/fhir_mcp/server.py`.
3. Wrap the handler in `@audited("tool_name")` (from `audit.py`) and `@traced("tool_name")` (from `observability.py`). Both are no-ops when their dependencies aren't configured.
4. Ensure every FHIR resource that leaves the handler goes through `deid.pipeline.deidentify(...)`.
5. Add at least one eval to `evals/test_clinical_accuracy.py` (or the relevant suite).
6. Update the README tool catalog table.

## Don't

- Don't expose write operations on real EHRs in v0.1. Read-only against HAPI; writes only against in-memory Synthea.
- Don't add a tool that bypasses de-id.
- Don't bump the judge model without recording the score delta.
- Don't add a cloud-required code path. Local mode must always work.
